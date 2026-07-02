"""
Zep实体读取与过滤服务
从Zep图谱中读取节点，筛选出符合预定义实体类型的节点
"""

import time
from typing import Dict, Any, List, Optional, Set, Callable, TypeVar
from dataclasses import dataclass, field

from .graphiti_client import Zep

from ..config import Config
from ..utils.logger import get_logger
from ..utils.zep_paging import fetch_all_nodes, fetch_all_edges
from ..utils.actors import (
    entity_archetype,
    entity_simulation_tier,
    is_agent_eligible,
    match_actor,
)

logger = get_logger('mirofish.zep_entity_reader')

# 用于泛型返回类型
T = TypeVar('T')

# 节点属性里可能承载本体分类的键名（含 graph_builder.safe_attr_name 给保留字加的
# ``entity_`` 前缀变体）。只有节点**显式**带这些键时才视作分类信号；缺失即无信号。
_NODE_ARCHETYPE_KEYS = ("archetype", "entity_archetype")
_NODE_TIER_KEYS = ("simulation_tier", "entity_simulation_tier")


def _explicit_classification(
    node: Dict[str, Any],
    actors: Optional[Any],
) -> Optional[Dict[str, Any]]:
    """为一个图谱节点找出**显式**的本体分类信号，合成一个 actor-like dict 供 actors.py
    的 ``is_agent_eligible`` 判定；没有任何显式信号时返回 None（→ 调用方据此保留该节点）。

    信号来源（优先级从高到低）：
    1. 节点自身 attributes 里显式写入的 archetype / simulation_tier（兼容
       graph_builder.safe_attr_name 加的 ``entity_`` 前缀变体）。
    2. 该节点匹配到的 actors.json 行，且该行**显式**携带 archetype / simulation_tier。

    关键 NO-OP 保证：只有在能读到「显式」的 archetype/simulation_tier 时才返回非 None。
    旧图谱 / 今日数据 / 离线测试夹具里没有任何节点携带这些字段，于是这里永远返回 None，
    准入门退化为完全放行（与现状逐字节一致）。
    """
    # 1) 节点 attributes 上的显式分类（某些写入路径会把本体属性直接挂到节点上）。
    attrs = node.get("attributes") or {}
    if isinstance(attrs, dict):
        signal: Dict[str, Any] = {}
        for k in _NODE_ARCHETYPE_KEYS:
            v = attrs.get(k)
            if isinstance(v, str) and v.strip():
                signal["archetype"] = v
                break
        for k in _NODE_TIER_KEYS:
            v = attrs.get(k)
            # bool 是 int 子类，需排除；字符串形态交给 entity_simulation_tier 解析
            if isinstance(v, bool):
                continue
            if isinstance(v, int) and v in (1, 2, 3, 4):
                signal["simulation_tier"] = v
                break
            if isinstance(v, str) and v.strip():
                signal["simulation_tier"] = v
                break
        if signal:
            return signal

    # 2) 匹配回 actors.json 的行，且该行**显式**带 archetype/simulation_tier 才算信号。
    if actors is not None:
        row = match_actor(node.get("name", ""), actors)
        if isinstance(row, dict) and (
            str(row.get("archetype", "") or "").strip()
            or row.get("simulation_tier") is not None
        ):
            return row

    return None


def _ontology_type_signals(ontology: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """ONT-7（SIM_TIER_FROM_ONTOLOGY，默认关）：从 project ontology 的 entity_types[] 提取
    「类型级」显式分类信号：type_name → {archetype?, simulation_tier?}。

    ONTOLOGY_RICH_SCHEMA 让 LLM 给每个实体类型标了 archetype/simulation_tier
    （如 MarketSignal=signal、PolicyInstrument=institution_rule），但此前无任何消费者——
    个体节点/actor 行都没带信号时，这些整型分类无法把实例挡在 agent 池外。此处只收
    **显式携带**字段的类型；无字段的类型不产生信号（保持「无信号=放行」的 NO-OP 保证）。
    """
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(ontology, dict):
        return out
    for et in ontology.get("entity_types", []) or []:
        if not isinstance(et, dict):
            continue
        name = str(et.get("name") or "").strip()
        if not name:
            continue
        signal: Dict[str, Any] = {}
        arch = et.get("archetype")
        if isinstance(arch, str) and arch.strip():
            signal["archetype"] = arch
        tier = et.get("simulation_tier")
        if not isinstance(tier, bool) and (
            (isinstance(tier, int) and tier in (1, 2, 3, 4))
            or (isinstance(tier, str) and tier.strip())
        ):
            signal["simulation_tier"] = tier
        if signal:
            out[name] = signal
    return out


@dataclass
class EntityNode:
    """实体节点数据结构"""
    uuid: str
    name: str
    labels: List[str]
    summary: str
    attributes: Dict[str, Any]
    # 相关的边信息
    related_edges: List[Dict[str, Any]] = field(default_factory=list)
    # 相关的其他节点信息
    related_nodes: List[Dict[str, Any]] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "labels": self.labels,
            "summary": self.summary,
            "attributes": self.attributes,
            "related_edges": self.related_edges,
            "related_nodes": self.related_nodes,
        }
    
    def get_entity_type(self) -> Optional[str]:
        """获取实体类型（排除默认的Entity标签）"""
        for label in self.labels:
            if label not in ["Entity", "Node"]:
                return label
        return None


@dataclass
class FilteredEntities:
    """过滤后的实体集合"""
    entities: List[EntityNode]
    entity_types: Set[str]
    total_count: int
    filtered_count: int
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "entities": [e.to_dict() for e in self.entities],
            "entity_types": list(self.entity_types),
            "total_count": self.total_count,
            "filtered_count": self.filtered_count,
        }


class ZepEntityReader:
    """
    Zep实体读取与过滤服务
    
    主要功能：
    1. 从Zep图谱读取所有节点
    2. 筛选出符合预定义实体类型的节点（Labels不只是Entity的节点）
    3. 获取每个实体的相关边和关联节点信息
    """
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or Config.ZEP_API_KEY
        if not self.api_key:
            raise ValueError("ZEP_API_KEY 未配置")
        
        self.client = Zep(api_key=self.api_key)
    
    def _call_with_retry(
        self, 
        func: Callable[[], T], 
        operation_name: str,
        max_retries: int = 3,
        initial_delay: float = 2.0
    ) -> T:
        """
        带重试机制的Zep API调用
        
        Args:
            func: 要执行的函数（无参数的lambda或callable）
            operation_name: 操作名称，用于日志
            max_retries: 最大重试次数（默认3次，即最多尝试3次）
            initial_delay: 初始延迟秒数
            
        Returns:
            API调用结果
        """
        last_exception = None
        delay = initial_delay
        
        for attempt in range(max_retries):
            try:
                return func()
            except Exception as e:
                last_exception = e
                if attempt < max_retries - 1:
                    logger.warning(
                        f"Zep {operation_name} 第 {attempt + 1} 次尝试失败: {str(e)[:100]}, "
                        f"{delay:.1f}秒后重试..."
                    )
                    time.sleep(delay)
                    delay *= 2  # 指数退避
                else:
                    logger.error(f"Zep {operation_name} 在 {max_retries} 次尝试后仍失败: {str(e)}")
        
        raise last_exception
    
    def get_all_nodes(self, graph_id: str) -> List[Dict[str, Any]]:
        """
        获取图谱的所有节点（分页获取）

        Args:
            graph_id: 图谱ID

        Returns:
            节点列表
        """
        logger.info(f"获取图谱 {graph_id} 的所有节点...")

        nodes = fetch_all_nodes(self.client, graph_id)

        nodes_data = []
        for node in nodes:
            nodes_data.append({
                "uuid": getattr(node, 'uuid_', None) or getattr(node, 'uuid', ''),
                "name": node.name or "",
                "labels": node.labels or [],
                "summary": node.summary or "",
                "attributes": node.attributes or {},
            })

        logger.info(f"共获取 {len(nodes_data)} 个节点")
        return nodes_data

    def get_all_edges(self, graph_id: str) -> List[Dict[str, Any]]:
        """
        获取图谱的所有边（分页获取）

        Args:
            graph_id: 图谱ID

        Returns:
            边列表
        """
        logger.info(f"获取图谱 {graph_id} 的所有边...")

        edges = fetch_all_edges(self.client, graph_id)

        edges_data = []
        for edge in edges:
            edges_data.append({
                "uuid": getattr(edge, 'uuid_', None) or getattr(edge, 'uuid', ''),
                "name": edge.name or "",
                "fact": edge.fact or "",
                "source_node_uuid": edge.source_node_uuid,
                "target_node_uuid": edge.target_node_uuid,
                "attributes": edge.attributes or {},
            })

        logger.info(f"共获取 {len(edges_data)} 条边")
        return edges_data
    
    def get_node_edges(self, node_uuid: str) -> List[Dict[str, Any]]:
        """
        获取指定节点的所有相关边（带重试机制）
        
        Args:
            node_uuid: 节点UUID
            
        Returns:
            边列表
        """
        try:
            # 使用重试机制调用Zep API
            edges = self._call_with_retry(
                func=lambda: self.client.graph.node.get_entity_edges(node_uuid=node_uuid),
                operation_name=f"获取节点边(node={node_uuid[:8]}...)"
            )
            
            edges_data = []
            for edge in edges:
                edges_data.append({
                    "uuid": getattr(edge, 'uuid_', None) or getattr(edge, 'uuid', ''),
                    "name": edge.name or "",
                    "fact": edge.fact or "",
                    "source_node_uuid": edge.source_node_uuid,
                    "target_node_uuid": edge.target_node_uuid,
                    "attributes": edge.attributes or {},
                })
            
            return edges_data
        except Exception as e:
            logger.warning(f"获取节点 {node_uuid} 的边失败: {str(e)}")
            return []
    
    def filter_defined_entities(
        self,
        graph_id: str,
        defined_entity_types: Optional[List[str]] = None,
        enrich_with_edges: bool = True,
        actors: Optional[Any] = None,
        ontology: Optional[Dict[str, Any]] = None,
    ) -> FilteredEntities:
        """
        筛选出符合预定义实体类型的节点

        筛选逻辑：
        - 如果节点的Labels只有一个"Entity"，说明这个实体不符合我们预定义的类型，跳过
        - 如果节点的Labels包含除"Entity"和"Node"之外的标签，说明符合预定义类型，保留
        - 本体准入门（CLAUDE §9 / GEMINI §6.3「只有 tier 1-2 才生成 persona」）：当
          ``Config.SIM_TIER_ELIGIBILITY`` 为真、且某节点带有**显式**的 archetype/simulation_tier
          信号时，剔除非能动节点（archetype 非 actor/collective，或 simulation_tier∈{3,4}），
          让记者/媒体出口/抽象概念/资源不再被实例化为有行动力的仿真 agent。

        关键 NO-OP：当**没有任何**候选节点携带 archetype/simulation_tier 信号（旧图谱、今日
        数据、离线测试夹具），准入门保留全部节点，与现状逐字节一致。准入门只作为「额外」
        过滤器存在，仅剔除带**显式非能动信号**的节点。

        Args:
            graph_id: 图谱ID
            defined_entity_types: 预定义的实体类型列表（可选，如果提供则只保留这些类型）
            enrich_with_edges: 是否获取每个实体的相关边信息
            actors: 深度研究 actors.json 顶层对象（可选）。提供时准入门可把图谱节点匹配回
                    actor 行读取其 archetype/simulation_tier 分类（缺省时仅靠节点自身属性信号）。
            ontology: project ontology 对象（可选，ONT-7）。仅当 SIM_TIER_FROM_ONTOLOGY=true
                    时启用：节点/actor 均无显式分类信号时，回退读取该节点实体类型在
                    entity_types[] 上的类型级 archetype/simulation_tier（默认关 → NO-OP）。

        Returns:
            FilteredEntities: 过滤后的实体集合
        """
        logger.info(f"开始筛选图谱 {graph_id} 的实体...")

        # 本体准入门是否启用（默认开；无显式分类信号时自动退化为完全放行 → NO-OP）。
        tier_gate_on = getattr(Config, 'SIM_TIER_ELIGIBILITY', True)
        gate_dropped = 0

        # ONT-7（SIM_TIER_FROM_ONTOLOGY，默认关）：类型级本体分类作为准入门的**第三级**
        # 信号（优先级：节点属性 > actor 行 > 实体类型）。开关关/无 ontology → 空表 = NO-OP。
        type_signals: Dict[str, Dict[str, Any]] = {}
        if tier_gate_on and getattr(Config, 'SIM_TIER_FROM_ONTOLOGY', False):
            type_signals = _ontology_type_signals(ontology)

        # 获取所有节点
        all_nodes = self.get_all_nodes(graph_id)
        total_count = len(all_nodes)
        
        # 获取所有边（用于后续关联查找）
        all_edges = self.get_all_edges(graph_id) if enrich_with_edges else []
        
        # 构建节点UUID到节点数据的映射
        node_map = {n["uuid"]: n for n in all_nodes}

        # KG-11: 一次遍历建边索引（uuid → 按 all_edges 原序的 (direction, edge) 桶），把
        # O(候选节点×全边表) 的重复扫描降为 O(边)+O(节点·邻边)——8000 节点×10000 边的上限
        # 规模下旧写法是千万级纯 Python 迭代。桶内保持与旧全表扫描逐字节一致的交错顺序
        # （同一节点的 outgoing/incoming 按边出现次序排列；自环沿用旧 if/elif 语义只记 outgoing）。
        edges_by_node: Dict[str, List[Any]] = {}
        if enrich_with_edges:
            for edge in all_edges:
                src = edge["source_node_uuid"]
                tgt = edge["target_node_uuid"]
                edges_by_node.setdefault(src, []).append(("outgoing", edge))
                if tgt != src:
                    edges_by_node.setdefault(tgt, []).append(("incoming", edge))

        # 筛选符合条件的实体
        filtered_entities = []
        entity_types_found = set()
        
        for node in all_nodes:
            labels = node.get("labels", [])
            
            # 筛选逻辑：Labels必须包含除"Entity"和"Node"之外的标签
            custom_labels = [l for l in labels if l not in ["Entity", "Node"]]
            
            if not custom_labels:
                # 只有默认标签，跳过
                continue
            
            # 如果指定了预定义类型，检查是否匹配
            if defined_entity_types:
                matching_labels = [l for l in custom_labels if l in defined_entity_types]
                if not matching_labels:
                    continue
                entity_type = matching_labels[0]
            else:
                entity_type = custom_labels[0]

            # 本体准入门（额外过滤器，仅剔除带「显式非能动信号」的节点）：
            # 只有当节点显式带 archetype/simulation_tier（或匹配到的 actor 行显式带），
            # 且按 actors.py 判定为「非能动」（tier 3/4，即记者/媒体/概念/资源）时才剔除。
            # 无任何显式信号时 _explicit_classification 返回 None → 节点照常保留（NO-OP）。
            if tier_gate_on:
                classification = _explicit_classification(node, actors)
                if classification is None and type_signals:
                    # ONT-7: 节点/actor 均无显式信号时才咨询「实体类型级」本体分类
                    # （无该类型的信号则仍为 None → 照常放行）。
                    classification = type_signals.get(entity_type)
                if classification is not None and not is_agent_eligible(classification):
                    gate_dropped += 1
                    logger.debug(
                        "[准入门] 剔除非能动节点 %r（archetype=%s tier=%s）",
                        node.get("name", ""),
                        entity_archetype(classification),
                        entity_simulation_tier(classification),
                    )
                    continue

            entity_types_found.add(entity_type)
            
            # 创建实体节点对象
            entity = EntityNode(
                uuid=node["uuid"],
                name=node["name"],
                labels=labels,
                summary=node["summary"],
                attributes=node["attributes"],
            )
            
            # 获取相关边和节点
            if enrich_with_edges:
                related_edges = []
                related_node_uuids = set()

                # KG-11: 只遍历该节点的邻边桶（预索引），输出与旧全表扫描完全一致。
                for direction, edge in edges_by_node.get(node["uuid"], ()):
                    if direction == "outgoing":
                        related_edges.append({
                            "direction": "outgoing",
                            "edge_name": edge["name"],
                            "fact": edge["fact"],
                            "target_node_uuid": edge["target_node_uuid"],
                        })
                        related_node_uuids.add(edge["target_node_uuid"])
                    else:
                        related_edges.append({
                            "direction": "incoming",
                            "edge_name": edge["name"],
                            "fact": edge["fact"],
                            "source_node_uuid": edge["source_node_uuid"],
                        })
                        related_node_uuids.add(edge["source_node_uuid"])

                entity.related_edges = related_edges
                
                # 获取关联节点的基本信息
                related_nodes = []
                for related_uuid in related_node_uuids:
                    if related_uuid in node_map:
                        related_node = node_map[related_uuid]
                        related_nodes.append({
                            "uuid": related_node["uuid"],
                            "name": related_node["name"],
                            "labels": related_node["labels"],
                            "summary": related_node.get("summary", ""),
                        })
                
                entity.related_nodes = related_nodes
            
            filtered_entities.append(entity)
        
        if gate_dropped:
            logger.info(
                f"[准入门] 本体分类剔除 {gate_dropped} 个非能动节点"
                f"（记者/媒体/概念/资源，tier 3/4 不进 agent 池）"
            )
        logger.info(f"筛选完成: 总节点 {total_count}, 符合条件 {len(filtered_entities)}, "
                   f"实体类型: {entity_types_found}")
        
        return FilteredEntities(
            entities=filtered_entities,
            entity_types=entity_types_found,
            total_count=total_count,
            filtered_count=len(filtered_entities),
        )
    
    def get_entity_with_context(
        self, 
        graph_id: str, 
        entity_uuid: str
    ) -> Optional[EntityNode]:
        """
        获取单个实体及其完整上下文（边和关联节点，带重试机制）
        
        Args:
            graph_id: 图谱ID
            entity_uuid: 实体UUID
            
        Returns:
            EntityNode或None
        """
        try:
            # 使用重试机制获取节点
            node = self._call_with_retry(
                func=lambda: self.client.graph.node.get(uuid_=entity_uuid),
                operation_name=f"获取节点详情(uuid={entity_uuid[:8]}...)"
            )
            
            if not node:
                return None
            
            # 获取节点的边
            edges = self.get_node_edges(entity_uuid)
            
            # 获取所有节点用于关联查找
            all_nodes = self.get_all_nodes(graph_id)
            node_map = {n["uuid"]: n for n in all_nodes}
            
            # 处理相关边和节点
            related_edges = []
            related_node_uuids = set()
            
            for edge in edges:
                if edge["source_node_uuid"] == entity_uuid:
                    related_edges.append({
                        "direction": "outgoing",
                        "edge_name": edge["name"],
                        "fact": edge["fact"],
                        "target_node_uuid": edge["target_node_uuid"],
                    })
                    related_node_uuids.add(edge["target_node_uuid"])
                else:
                    related_edges.append({
                        "direction": "incoming",
                        "edge_name": edge["name"],
                        "fact": edge["fact"],
                        "source_node_uuid": edge["source_node_uuid"],
                    })
                    related_node_uuids.add(edge["source_node_uuid"])
            
            # 获取关联节点信息
            related_nodes = []
            for related_uuid in related_node_uuids:
                if related_uuid in node_map:
                    related_node = node_map[related_uuid]
                    related_nodes.append({
                        "uuid": related_node["uuid"],
                        "name": related_node["name"],
                        "labels": related_node["labels"],
                        "summary": related_node.get("summary", ""),
                    })
            
            return EntityNode(
                uuid=getattr(node, 'uuid_', None) or getattr(node, 'uuid', ''),
                name=node.name or "",
                labels=node.labels or [],
                summary=node.summary or "",
                attributes=node.attributes or {},
                related_edges=related_edges,
                related_nodes=related_nodes,
            )
            
        except Exception as e:
            logger.error(f"获取实体 {entity_uuid} 失败: {str(e)}")
            return None
    
    def get_entities_by_type(
        self,
        graph_id: str,
        entity_type: str,
        enrich_with_edges: bool = True,
        actors: Optional[Any] = None,
        ontology: Optional[Dict[str, Any]] = None,
    ) -> List[EntityNode]:
        """
        获取指定类型的所有实体

        Args:
            graph_id: 图谱ID
            entity_type: 实体类型（如 "Student", "PublicFigure" 等）
            enrich_with_edges: 是否获取相关边信息
            actors: 深度研究 actors.json 顶层对象（可选）。透传给准入门做本体分类判定；
                    缺省时准入门只读节点自身属性信号（无信号即完全放行，NO-OP）。
            ontology: project ontology 对象（可选，ONT-7）。透传给准入门的类型级信号
                    （SIM_TIER_FROM_ONTOLOGY=true 时生效；默认关 → NO-OP）。

        Returns:
            实体列表
        """
        result = self.filter_defined_entities(
            graph_id=graph_id,
            defined_entity_types=[entity_type],
            enrich_with_edges=enrich_with_edges,
            actors=actors,
            ontology=ontology,
        )
        return result.entities


