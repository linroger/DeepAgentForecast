"""
图谱构建服务
接口2：使用Zep API构建Standalone Graph
"""

import os
import re
import uuid
import time
import logging
import threading
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass

from .graphiti_client import Zep
from .graphiti_client import EpisodeData, EntityEdgeSourceTarget

from ..config import Config
from ..models.task import TaskManager, TaskStatus
from ..utils.zep_paging import fetch_all_nodes, fetch_all_edges
from ..utils.actors import (
    extract_actor_rows,
    extract_relationship_rows,
    actor_briefing,
    normalize_name,
    REL_EDGE_NAME,
    entity_archetype,
    entity_simulation_tier,
    salience_score,
    relation_valence,
    relation_polarity,
)
from .text_processor import TextProcessor

logger = logging.getLogger("mirofish.graph_builder")

# 研究 actor.type → 图谱节点标签（自定义实体类型；未知归到通用 Entity）。
# Media/Government/Platform 都落到 Organization（与本体生成器的类型口径一致）。
ACTOR_TYPE_TO_LABEL = {
    "Person": "Person",
    "Organization": "Organization",
    "Media": "Organization",
    "Government": "Organization",
    "Platform": "Organization",
}

# Zep 保留属性名，不能作为节点属性名（与 set_ontology 内的同名集合一致）。
RESERVED_ATTR_NAMES = {'uuid', 'name', 'group_id', 'name_embedding', 'summary', 'created_at'}


# R2-KG-5: betweenness/articulation are O(V*E); skip the prior computation above this
# node count so a pathologically large graph can never stall the (gated) post-build pass.
# KG-3: 仅作 Config.GRAPH_CHOKEPOINT_MAX_NODES 缺失/非法时的兜底值（真正的旋钮在 Config）。
_CHOKEPOINT_MAX_NODES = 1500


def _brandes_betweenness(adj: Dict[Any, set]) -> Dict[Any, float]:
    """R2-KG-5: unweighted betweenness centrality (Brandes' algorithm) on an
    undirected adjacency map. O(V*E); caller caps V before invoking. Returns raw
    (un-normalized) scores; the undirected pair double-count is halved here."""
    import collections

    CB: Dict[Any, float] = dict.fromkeys(adj, 0.0)
    for s in adj:
        S: List[Any] = []
        P: Dict[Any, list] = {w: [] for w in adj}
        sigma = dict.fromkeys(adj, 0.0)
        sigma[s] = 1.0
        dist = dict.fromkeys(adj, -1)
        dist[s] = 0
        Q = collections.deque([s])
        while Q:
            v = Q.popleft()
            S.append(v)
            for w in adj[v]:
                if dist[w] < 0:
                    Q.append(w)
                    dist[w] = dist[v] + 1
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    P[w].append(v)
        delta = dict.fromkeys(adj, 0.0)
        while S:
            w = S.pop()
            for v in P[w]:
                if sigma[w]:
                    delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w])
            if w != s:
                CB[w] += delta[w]
    for v in CB:
        CB[v] /= 2.0  # undirected: each shortest path counted from both endpoints
    return CB


def _articulation_points(adj: Dict[Any, set]) -> set:
    """R2-KG-5: articulation points (cut vertices) via iterative Tarjan DFS — nodes
    whose removal disconnects the graph (structural chokepoints). Iterative to avoid
    Python recursion limits on large graphs."""
    visited: set = set()
    disc: Dict[Any, int] = {}
    low: Dict[Any, int] = {}
    ap: set = set()
    timer = 0
    for start in adj:
        if start in visited:
            continue
        root_children = 0
        stack = [(start, None, iter(adj[start]))]  # frames: (node, parent, neighbor_iter)
        visited.add(start)
        disc[start] = low[start] = timer
        timer += 1
        while stack:
            node, parent, it = stack[-1]
            pushed = False
            for nb in it:
                if nb == parent:
                    continue
                if nb not in visited:
                    visited.add(nb)
                    disc[nb] = low[nb] = timer
                    timer += 1
                    if node == start:
                        root_children += 1
                    stack.append((nb, node, iter(adj[nb])))
                    pushed = True
                    break
                else:
                    low[node] = min(low[node], disc[nb])
            if not pushed:
                stack.pop()
                if stack:
                    par = stack[-1][0]
                    low[par] = min(low[par], low[node])
                    if par != start and low[node] >= disc[par]:
                        ap.add(par)  # non-root articulation condition
        if root_children > 1:
            ap.add(start)
    return ap


def safe_attr_name(attr_name: str) -> str:
    """把保留名称转换为安全的节点属性名（与 set_ontology 内的局部实现口径一致）。

    C8（ONTOLOGY 富 schema）：种子节点的 archetype/simulation_tier/salience 等本体属性
    需要一个稳定的、避开 Zep 保留字的属性名空间。set_ontology 用同样的规则给实体类型
    的属性改名，这里抽到模块级供 seed_actors 复用，避免规则漂移。
    """
    if attr_name and attr_name.lower() in RESERVED_ATTR_NAMES:
        return f"entity_{attr_name}"
    return attr_name


def fold_priors_with_aliases(
    priors: Optional[Dict[str, float]],
    actors: Optional[Any],
) -> Dict[str, float]:
    """KG-7: 按 actors.json 的「策展别名组」折叠中心度先验，防止同一现实 actor 的
    表面形（"TSM"/"TSMC"、"Trump"/别名）把影响力信号打碎成任意小的分片。

    规则（GRAPH_PRIORS_ALIAS_FOLD，默认开）：
    * 只认策展别名组 = actor 行的 name + aliases[]（归一名**精确**相等才算组员）。
      刻意不做包含/启发式配对——"IEEEPA" vs "IEEPA-based tariffs"、"Trump"(人) vs
      "Trump Administration"(机构) 在本体上是不同实体，MAX 折叠会错误抬高它们。
    * 组内取 MAX，写回每个组员名下。ADD-only：已有键只升不降，别名缺键则补键，
      旧消费者（simulation_manager 按归一名查 [0,1] 值）不受影响。
    * 开关关闭 / 无 actors / 无先验时原样返回拷贝（degrade-safe）。
    """
    out: Dict[str, float] = dict(priors or {})
    if not out or not getattr(Config, "GRAPH_PRIORS_ALIAS_FOLD", True):
        return out
    rows = extract_actor_rows(actors)
    if not rows:
        return out
    # 归一名 → 原始 prior 键列表（同一归一名可能对应多个原始键，全部一起抬升）。
    norm_index: Dict[str, List[str]] = {}
    for k in out:
        if isinstance(k, str):
            nk = normalize_name(k)
            if nk:
                norm_index.setdefault(nk, []).append(k)
    for row in rows:
        members: List[str] = [str(row.get("name") or "")]
        raw_aliases = row.get("aliases")
        if isinstance(raw_aliases, list):
            members.extend(a for a in raw_aliases if isinstance(a, str))
        norm_members = {normalize_name(m) for m in members}
        norm_members.discard("")
        if len(norm_members) < 2:
            continue  # 无别名 → 无可折叠
        group_max = 0.0
        for nm in norm_members:
            for k in norm_index.get(nm, ()):
                v = out.get(k)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    group_max = max(group_max, float(v))
        if group_max <= 0.0:
            continue  # 组内无人带先验，无需补键
        seen_norms: set = set()
        for m in members:
            nm = normalize_name(m)
            if not nm or nm in seen_norms:
                continue
            seen_norms.add(nm)
            keys = norm_index.get(nm)
            if keys:
                for k in keys:
                    v = out.get(k)
                    cur = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0
                    out[k] = max(cur, group_max)
            else:
                # 缺键的组员补键（消费者按 actor 名/别名查表时不再落空）。
                mk = m.strip() or m
                out[mk] = group_max
                norm_index.setdefault(nm, []).append(mk)
    return out


@dataclass
class GraphInfo:
    """图谱信息"""
    graph_id: str
    node_count: int
    edge_count: int
    entity_types: List[str]
    # KG cookbook 结构完整性信号（均可选，ADD-only，旧消费者不受影响）：
    # components = 弱连通分量数（≈1 表示实体已良好合并；远多于预期 = 实体欠合并）；
    # top_hubs = 度数最高的若干节点名（枢纽合理性 sanity check）。
    components: int = 0
    top_hubs: Optional[List[str]] = None
    # NEXTSTEPS P3-7：归一度数中心度先验 node_name→[0,1]（复用上面已算出的 degree，此前丢弃）。
    # 高中心度比原始提及数更能反映"谁是关键节点"——一个便宜且接地的影响力先验，供 salience 融合。
    centrality: Optional[Dict[str, float]] = None
    # R2-KG-5（GRAPH_CHOKEPOINT_PRIORS，默认关）：度数中心度只看"邻居多不多"，看不出
    # "谁卡在传导路径的咽喉"。betweenness = 归一化介数中心度 node_name→[0,1]；chokepoints =
    # 介数高位 ∪ 关节点（移除即断图的桥接节点）。两者均为可选 ADD-only，关闭时为空。
    betweenness: Optional[Dict[str, float]] = None
    chokepoints: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "entity_types": self.entity_types,
            "components": self.components,
            "top_hubs": self.top_hubs or [],
            "centrality": self.centrality or {},
            "betweenness": self.betweenness or {},
            "chokepoints": self.chokepoints or [],
        }


class GraphBuilderService:
    """
    图谱构建服务
    负责调用Zep API构建知识图谱
    """
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or Config.ZEP_API_KEY
        if not self.api_key:
            raise ValueError("ZEP_API_KEY 未配置")
        
        self.client = Zep(api_key=self.api_key)
        self.task_manager = TaskManager()
        # KG-5: 最近一次 add_text_batches 的落库记账（total/failed/succeeded），供编排器
        # 把「部分文本块被跳过」写进 pipeline_state/health。None = 尚未跑过建图。
        self.last_ingest_stats: Optional[Dict[str, int]] = None
    
    def build_graph_async(
        self,
        text: str,
        ontology: Dict[str, Any],
        graph_name: str = "MiroFish Graph",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        batch_size: int = 3
    ) -> str:
        """
        异步构建图谱
        
        Args:
            text: 输入文本
            ontology: 本体定义（来自接口1的输出）
            graph_name: 图谱名称
            chunk_size: 文本块大小
            chunk_overlap: 块重叠大小
            batch_size: 每批发送的块数量
            
        Returns:
            任务ID
        """
        # 创建任务
        task_id = self.task_manager.create_task(
            task_type="graph_build",
            metadata={
                "graph_name": graph_name,
                "chunk_size": chunk_size,
                "text_length": len(text),
            }
        )
        
        # 在后台线程中执行构建
        thread = threading.Thread(
            target=self._build_graph_worker,
            args=(task_id, text, ontology, graph_name, chunk_size, chunk_overlap, batch_size)
        )
        thread.daemon = True
        thread.start()
        
        return task_id
    
    def _build_graph_worker(
        self,
        task_id: str,
        text: str,
        ontology: Dict[str, Any],
        graph_name: str,
        chunk_size: int,
        chunk_overlap: int,
        batch_size: int
    ):
        """图谱构建工作线程"""
        try:
            self.task_manager.update_task(
                task_id,
                status=TaskStatus.PROCESSING,
                progress=5,
                message="开始构建图谱..."
            )
            
            # 1. 创建图谱
            graph_id = self.create_graph(graph_name)
            self.task_manager.update_task(
                task_id,
                progress=10,
                message=f"图谱已创建: {graph_id}"
            )
            
            # 2. 设置本体
            self.set_ontology(graph_id, ontology)
            self.task_manager.update_task(
                task_id,
                progress=15,
                message="本体已设置"
            )
            
            # 3. 文本分块
            chunks = TextProcessor.split_text(text, chunk_size, chunk_overlap)
            total_chunks = len(chunks)
            self.task_manager.update_task(
                task_id,
                progress=20,
                message=f"文本已分割为 {total_chunks} 个块"
            )
            
            # 4. 分批发送数据
            episode_uuids = self.add_text_batches(
                graph_id, chunks, batch_size,
                lambda msg, prog: self.task_manager.update_task(
                    task_id,
                    progress=20 + int(prog * 0.4),  # 20-60%
                    message=msg
                )
            )
            
            # 5. 等待Zep处理完成
            self.task_manager.update_task(
                task_id,
                progress=60,
                message="等待Zep处理数据..."
            )
            
            self._wait_for_episodes(
                episode_uuids,
                lambda msg, prog: self.task_manager.update_task(
                    task_id,
                    progress=60 + int(prog * 0.3),  # 60-90%
                    message=msg
                )
            )
            
            # 6. 获取图谱信息
            self.task_manager.update_task(
                task_id,
                progress=90,
                message="获取图谱信息..."
            )
            
            graph_info = self._get_graph_info(graph_id)
            
            # 完成
            self.task_manager.complete_task(task_id, {
                "graph_id": graph_id,
                "graph_info": graph_info.to_dict(),
                "chunks_processed": total_chunks,
            })
            
        except Exception as e:
            import traceback
            error_msg = f"{str(e)}\n{traceback.format_exc()}"
            self.task_manager.fail_task(task_id, error_msg)
    
    def create_graph(self, name: str) -> str:
        """创建Zep图谱（公开方法）"""
        graph_id = f"mirofish_{uuid.uuid4().hex[:16]}"
        
        self.client.graph.create(
            graph_id=graph_id,
            name=name,
            description="MiroFish Social Simulation Graph"
        )
        
        return graph_id
    
    def set_ontology(self, graph_id: str, ontology: Dict[str, Any]):
        """设置图谱本体（公开方法）"""
        import warnings
        from typing import Optional
        from pydantic import Field
        from .graphiti_client.ontology import EntityModel, EntityText, EdgeModel
        
        # 抑制 Pydantic v2 关于 Field(default=None) 的警告
        # 这是 Zep SDK 要求的用法，警告来自动态类创建，可以安全忽略
        warnings.filterwarnings('ignore', category=UserWarning, module='pydantic')
        
        # Zep 保留名称，不能作为属性名
        RESERVED_NAMES = {'uuid', 'name', 'group_id', 'name_embedding', 'summary', 'created_at'}
        
        def safe_attr_name(attr_name: str) -> str:
            """将保留名称转换为安全名称"""
            if attr_name.lower() in RESERVED_NAMES:
                return f"entity_{attr_name}"
            return attr_name
        
        # 动态创建实体类型
        # F-3-1: 本体来自 LLM（不可信边界），且 resume/复用路径会直接喂入持久化本体而不再
        # 校验。任一 entity/attr/edge 缺 "name" 不能让整个 GRAPH 阶段崩溃——用 .get() 取名、
        # 跳过无名条目，并 per-item try/except 隔离单个坏类型（与 seed_actors 的逐项保护一致）。
        entity_types = {}
        for entity_def in ontology.get("entity_types", []):
            name = entity_def.get("name")
            if not name:
                logger.warning("skipping entity_def without name: %r", entity_def)  # F-3-1
                continue
            try:
                description = entity_def.get("description", f"A {name} entity.")

                # 创建属性字典和类型注解（Pydantic v2 需要）
                attrs = {"__doc__": description}
                annotations = {}

                for attr_def in entity_def.get("attributes", []):
                    raw_attr_name = attr_def.get("name")
                    if not raw_attr_name:  # F-3-1: 跳过无名属性而非 KeyError
                        continue
                    attr_name = safe_attr_name(raw_attr_name)  # 使用安全名称
                    attr_desc = attr_def.get("description", attr_name)
                    # Zep API 需要 Field 的 description，这是必需的
                    attrs[attr_name] = Field(description=attr_desc, default=None)
                    annotations[attr_name] = Optional[EntityText]  # 类型注解

                attrs["__annotations__"] = annotations

                # 动态创建类
                entity_class = type(name, (EntityModel,), attrs)
                entity_class.__doc__ = description
                entity_types[name] = entity_class
            except Exception as e:  # F-3-1: 单个坏类型不得中断整批
                logger.warning("skipping malformed entity type %s: %s", name, e)
                continue

        # 动态创建边类型
        edge_definitions = {}
        for edge_def in ontology.get("edge_types", []):
            name = edge_def.get("name")
            if not name:
                logger.warning("skipping edge_def without name: %r", edge_def)  # F-3-1
                continue
            try:
                description = edge_def.get("description", f"A {name} relationship.")

                # 创建属性字典和类型注解
                attrs = {"__doc__": description}
                annotations = {}

                for attr_def in edge_def.get("attributes", []):
                    raw_attr_name = attr_def.get("name")
                    if not raw_attr_name:  # F-3-1: 跳过无名属性而非 KeyError
                        continue
                    attr_name = safe_attr_name(raw_attr_name)  # 使用安全名称
                    attr_desc = attr_def.get("description", attr_name)
                    # Zep API 需要 Field 的 description，这是必需的
                    attrs[attr_name] = Field(description=attr_desc, default=None)
                    annotations[attr_name] = Optional[str]  # 边属性用str类型

                attrs["__annotations__"] = annotations

                # 动态创建类
                class_name = ''.join(word.capitalize() for word in name.split('_'))
                edge_class = type(class_name, (EdgeModel,), attrs)
                edge_class.__doc__ = description

                # 构建source_targets
                source_targets = []
                for st in edge_def.get("source_targets", []):
                    source_targets.append(
                        EntityEdgeSourceTarget(
                            source=st.get("source", "Entity"),
                            target=st.get("target", "Entity")
                        )
                    )

                if source_targets:
                    edge_definitions[name] = (edge_class, source_targets)
            except Exception as e:  # F-3-1: 单个坏边类型不得中断整批
                logger.warning("skipping malformed edge type %s: %s", name, e)
                continue
        
        # 调用Zep API设置本体
        if entity_types or edge_definitions:
            self.client.graph.set_ontology(
                graph_ids=[graph_id],
                entities=entity_types if entity_types else None,
                edges=edge_definitions if edge_definitions else None,
            )
    
    def seed_actors(
        self,
        graph_id: str,
        actors: Optional[Dict[str, Any]],
        valid_at: Any = None,
    ) -> int:
        """T2.2: 在文本抽取前，把研究确认的 actors + relationships 直接种入图谱。

        每条 ``relationships[]`` 写成一条 typed 边（``type`` 即边名）；落单的高信号 actor
        写成 ``IS_A``→类型 的节点（让没有关系边的关键 actor 也进图谱）。``add_triplet`` 按
        名字+embedding dedup，所以后续文本抽取会「富化」这些种子节点而非重复创建。
        返回写入的边数。幂等：重复种同一三元组不会产生重复（T2.7 依赖此性质）。
        """
        rows = extract_actor_rows(actors)
        rels = extract_relationship_rows(actors)
        if not rows and not rels:
            return 0
        # C8（ONTOLOGY 富 schema 开关）：控制是否把 archetype/simulation_tier/salience 与
        # 关系 valence/polarity 折进种子节点/边。默认开启；但所有新写入都另有「字段存在」
        # 守卫，故无新字段的旧档案即便开关为真也逐字节不变（degrade-safe）。
        rich_schema = getattr(Config, "ONTOLOGY_RICH_SCHEMA", True)
        label = {a["name"]: ACTOR_TYPE_TO_LABEL.get(str(a.get("type") or ""), "Entity") for a in rows}
        # 每个 actor 的一句话消歧 summary（KG cookbook：描述是实体解析的关键信号）。
        # 优先用研究给的 description，回退到 role；喂给 add_triplet 的端点 summary，
        # graphiti 的 LLM 去重器读 node.summary 来区分同名异体。
        summ = {a["name"]: (str(a.get("description") or a.get("role") or "").strip()[:200]) for a in rows}
        n = 0
        seeded_names: set = set()
        for r in rels:
            typ = str(r.get("type", "")).upper()
            etype = REL_EDGE_NAME.get(typ)
            if typ == "OTHER" or not etype:
                # OTHER / 未知类型：优先用研究给的 relation_label 清洗成具体边名（如
                # SUPPLIES/CO_INVESTS_IN_CAPACITY），保留语义；没有 label 才落到 RELATES_TO。
                # 绝不丢边（旧逻辑这里 continue，会静默丢弃所有非 7 类边）。
                rl = str(r.get("relation_label", "") or "").strip()
                sanitized = re.sub(r"[^0-9A-Za-z_]+", "_", rl).strip("_").upper() if rl else ""
                etype = sanitized or etype or "RELATES_TO"
            src, tgt = r.get("source"), r.get("target")
            # 把 sign/strength/grade 折进事实文本：边自带可检索的极性/强度/定级，无需改 Graphiti schema。
            fact = str(r.get("basis") or f"{src} {etype} {tgt}")
            sign, strength, grade = r.get("sign"), r.get("strength"), r.get("grade")
            # R2-KG-3: fold the relationship's transmission LAG into the fact text so
            # the seeded edge carries a retrievable lag the multi-hop traversal can
            # surface+sum (a causal spine needs "how long until it propagates", not
            # just "does it"). Only appended when the research row carries it, so
            # rows without lag stay byte-identical.
            lag = r.get("lag")
            extra = [p for p in (
                (f"sign={sign}" if sign else None),
                (f"strength={strength}" if strength else None),
                (f"grade={grade}" if grade else None),
                (f"lag={lag}" if lag not in (None, "") else None),
            ) if p]
            # C8（ONTOLOGY 富 schema）：把关系的价(valence)/极性(polarity)也折进事实文本，
            # 让种子边自带「盟友 != 对手 != 供应商」的可检索语义。仅在关系行**显式携带**
            # valence/polarity 时才追加——绝不据 type 反推，以保证旧档案（无新字段）的种子
            # 事实文本与今天逐字节一致（gated by ONTOLOGY_RICH_SCHEMA）。
            if rich_schema and isinstance(r, dict) and \
                    (r.get("valence") is not None or r.get("polarity") is not None):
                extra.append(f"valence={relation_valence(r)}")
                extra.append(f"polarity={relation_polarity(r):.2f}")
            if extra:
                fact = f"{fact}（{'，'.join(extra)}）"
            try:
                self.client.graph.add_triplet(
                    graph_id, src, etype, tgt,
                    fact,
                    valid_at=valid_at,
                    source_label=label.get(src, "Entity"),
                    target_label=label.get(tgt, "Entity"),
                    source_summary=summ.get(src, ""),
                    target_summary=summ.get(tgt, ""),
                )
                n += 1
                seeded_names |= {src, tgt}
            except Exception as e:  # one bad edge must never abort seeding
                logger.warning("[%s] seed edge skipped (%s-%s-%s): %s", graph_id, src, etype, tgt, e)
        for a in rows:  # isolated high-signal actors (no relationship edge)
            name = a["name"]
            if name in seeded_names:
                continue
            try:
                # F-3-3: add_triplet 会把 target 物化为一个节点；过去 target_label 缺省为
                # "Entity"，使类型字符串（"Person"/"Organization"…）成了仅带 "Entity" 标签的
                # 孤儿污染节点。这里显式给 target_label 打上正确的实体类型标签，让该合成端至少
                # 是一个合法分型的概念节点（彻底去除 IS_A 端节点需 runtime.add_node 节点级 upsert，
                # 跨文件改动，见 skipped）。
                actor_type = a.get("type") or "Entity"
                # 把 role/stance/influence/动机/记忆摘录折进种子节点事实，让规范 actor 节点
                # 自带调研属性，而不是只挂一个空壳 role 字符串。
                fact = str(a.get("role") or name)
                brief = actor_briefing(a, max_memory_chars=300)
                if brief:
                    body = brief.split("\n", 1)[-1].replace("\n", "；").strip()
                    if body:
                        fact = f"{fact}；{body}" if fact else body
                # C8（ONTOLOGY 富 schema）：把本体分类属性 archetype/simulation_tier/salience
                # 作为「节点属性」折进种子节点事实文本（add_triplet 不暴露节点 attributes，
                # 故沿用本文件既有「把调研属性折进 fact」的范式）。属性键经 safe_attr_name 规范，
                # 避开 Zep 保留字。仅在 actor **显式携带**这些字段时追加，否则逐字节不变。
                node_attrs: List[str] = []
                if rich_schema and isinstance(a, dict):
                    if str(a.get("archetype", "") or "").strip():
                        node_attrs.append(f"{safe_attr_name('archetype')}={entity_archetype(a)}")
                    if a.get("simulation_tier") is not None:
                        node_attrs.append(
                            f"{safe_attr_name('simulation_tier')}={entity_simulation_tier(a)}")
                    if a.get("salience") is not None:
                        node_attrs.append(
                            f"{safe_attr_name('salience')}={salience_score(a):.2f}")
                if node_attrs:
                    fact = f"{fact}（{'，'.join(node_attrs)}）" if fact else \
                        "（" + "，".join(node_attrs) + "）"
                self.client.graph.add_triplet(
                    graph_id, name, "IS_A", actor_type,
                    fact,
                    valid_at=valid_at,
                    source_label=label.get(name, "Entity"),
                    target_label=ACTOR_TYPE_TO_LABEL.get(str(a.get("type") or ""), "Entity"),
                    source_summary=summ.get(name, ""),
                )
                n += 1
            except Exception as e:
                logger.warning("[%s] seed node skipped (%s): %s", graph_id, name, e)
        # 别名桥（KG cookbook 别名解析）：为带 aliases 的 actor 显式播一条 ALSO_KNOWN_AS 边，
        # 让后续 prose 抽取里以别名出现的提及能去重到同一规范节点，而非另起孤儿节点。
        for a in rows:
            name = a["name"]
            aliases = a.get("aliases")
            if not isinstance(aliases, list):
                continue
            for al in aliases:
                if not (isinstance(al, str) and al.strip()):
                    continue
                if normalize_name(al) == normalize_name(name):
                    continue
                try:
                    self.client.graph.add_triplet(
                        graph_id, name, "ALSO_KNOWN_AS", al,
                        f"{name} 亦称 {al}",
                        valid_at=valid_at,
                        source_label=label.get(name, "Entity"),
                        target_label=label.get(name, "Entity"),
                        source_summary=summ.get(name, ""),
                        target_summary=summ.get(name, ""),
                    )
                    n += 1
                except Exception as e:
                    logger.warning("[%s] seed alias skipped (%s~%s): %s", graph_id, name, al, e)
        return n

    def add_text_batches(
        self,
        graph_id: str,
        chunks: List[str],
        batch_size: int = 3,
        progress_callback: Optional[Callable] = None,
        reference_time: Any = None,
    ) -> List[str]:
        """分批添加文本到图谱，返回所有 episode 的 uuid 列表。

        ``reference_time``（T2.3）会写入每个 chunk 的双时态 valid 锚点（缺省 = 落库时刻）。
        """
        episode_uuids = []
        total_chunks = len(chunks)
        failed_chunks = 0

        for i in range(0, total_chunks, batch_size):
            batch_chunks = chunks[i:i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (total_chunks + batch_size - 1) // batch_size
            progress = (i + len(batch_chunks)) / total_chunks

            if progress_callback:
                progress_callback(
                    f"发送第 {batch_num}/{total_batches} 批数据 ({len(batch_chunks)} 块)...",
                    progress
                )
            
            # 构建episode数据（T2.3: 统一锚定 reference_time）
            episodes = [
                EpisodeData(data=chunk, type="text", reference_time=reference_time)
                for chunk in batch_chunks
            ]
            
            # 发送到Zep
            try:
                batch_result = self.client.graph.add_batch(
                    graph_id=graph_id,
                    episodes=episodes
                )

                # 收集返回的 episode uuid（add_batch 现已逐 episode 隔离失败：返回的条数
                # 可能少于发送条数，差额即被跳过的坏 chunk）。
                added = 0
                if batch_result and isinstance(batch_result, list):
                    for ep in batch_result:
                        ep_uuid = getattr(ep, 'uuid_', None) or getattr(ep, 'uuid', None)
                        if ep_uuid:
                            episode_uuids.append(ep_uuid)
                            added += 1
                failed_chunks += max(0, len(batch_chunks) - added)

                # T2.6: 本地 FalkorDB 同步落库，无需限流停顿（纯死延迟）。仅远程后端保留。
                if Config.GRAPHITI_REMOTE:
                    time.sleep(1)

            except Exception as e:
                # RESILIENCE: 一整批失败（罕见——单 episode 失败已在 add_batch 内隔离）也不再
                # 中断整个建图阶段：跳过该批、记账、继续。仅当「全部 chunk 都失败」时才在循环后
                # 显式硬失败（见下），避免静默产出空图谱。
                failed_chunks += len(batch_chunks)
                logger.warning("[%s] 批次 %d/%d 抽取失败，已跳过: %s",
                               graph_id, batch_num, total_batches, str(e)[:160])
                if progress_callback:
                    progress_callback(f"批次 {batch_num} 跳过(失败): {str(e)[:80]}", progress)
                continue

        # KG-5: 记账落到实例（不改签名/返回值）：只丢一条 WARNING 时，30% 语料丢失的图谱
        # 在 pipeline_state 里显示为全绿。编排器读 last_ingest_stats 即可量化 ingest 丢失
        # （MiniMax 内容过滤 422 + claude-cli 兜底耗尽是当前主要失败形态）。
        self.last_ingest_stats = {
            "total": total_chunks,
            "failed": failed_chunks,
            "succeeded": len(episode_uuids),
        }

        # 硬护栏：所有文本块都抽取失败 → 不能静默产出空图谱（下游 prepare/模拟/报告会退化）。
        # 明确失败，给出可诊断信息（最常见诱因：模型 JSON 输出异常/回显 schema）。
        if total_chunks > 0 and not episode_uuids:
            raise ValueError(
                f"图谱构建失败：{total_chunks} 个文本块全部抽取失败（疑似模型 JSON 输出异常，"
                f"如回显 schema）。请检查 LLM 提供方/模型是否能稳定产出结构化 JSON。"
            )
        if failed_chunks:
            logger.warning("[%s] 建图完成但跳过了 %d/%d 个文本块（抽取失败）",
                           graph_id, failed_chunks, total_chunks)

        return episode_uuids
    
    def _wait_for_episodes(
        self,
        episode_uuids: List[str],
        progress_callback: Optional[Callable] = None,
        timeout: int = 600
    ):
        """T2.6: 本地 Graphiti 同步落库——``add_batch`` 返回时 episode 已抽取完毕，
        shim 的 ``episode.get`` 恒返回 ``processed=True``，原轮询循环是空转（且占用
        65→98% 进度带）。这里直接收尾，保留签名以兼容调用点；远程后端如需异步处理
        状态轮询可在此恢复（gate on ``Config.GRAPHITI_REMOTE``）。
        """
        if progress_callback:
            n = len(episode_uuids)
            progress_callback(
                f"文本块已处理完成（{n} 个）" if n else "无需等待（没有 episode）",
                1.0,
            )
    
    def build_communities(self, graph_id: str) -> List[Dict[str, Any]]:
        """T2.4: best-effort 社区发现（派系/联盟），返回 [{uuid,name,summary}]；失败返回 []。

        永不让社区发现的失败影响建图（catch+log）。重跑会清空旧社区（graphiti 语义）。
        """
        try:
            return self.client.graph.build_communities(graph_id) or []
        except Exception as e:
            logger.warning("[%s] build_communities skipped: %s", graph_id, e)
            return []

    def _get_graph_info(self, graph_id: str) -> GraphInfo:
        """获取图谱信息"""
        # 获取节点（分页）
        nodes = fetch_all_nodes(self.client, graph_id)

        # 获取边（分页）
        edges = fetch_all_edges(self.client, graph_id)

        # 统计实体类型
        entity_types = set()
        for node in nodes:
            if node.labels:
                for label in node.labels:
                    if label not in ["Entity", "Node"]:
                        entity_types.add(label)

        # KG cookbook 第4步：结构完整性检查。弱连通分量数（stdlib 并查集，无第三方依赖）
        # 是实体解析质量信号——分量数≈1 表示实体已良好合并；远多于预期 = 实体欠合并（重复孤点）。
        # 另统计度数最高的枢纽节点做 sanity check。纯内存计算，复用已取的 nodes/edges，无额外 IO。
        node_ids = {getattr(n, "uuid_", None) for n in nodes}
        node_ids.discard(None)
        parent: Dict[Any, Any] = {nid: nid for nid in node_ids}

        def _find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def _union(a, b):
            ra, rb = _find(a), _find(b)
            if ra != rb:
                parent[ra] = rb

        degree: Dict[Any, int] = {nid: 0 for nid in node_ids}
        for e in edges:
            s = getattr(e, "source_node_uuid", None)
            t = getattr(e, "target_node_uuid", None)
            if s in parent and t in parent and s != t:
                _union(s, t)
                degree[s] += 1
                degree[t] += 1
        num_components = len({_find(nid) for nid in node_ids}) if node_ids else 0
        name_by_id = {getattr(n, "uuid_", None): (getattr(n, "name", "") or "") for n in nodes}
        top_hubs = [name_by_id.get(nid, "") for nid, _ in
                    sorted(degree.items(), key=lambda kv: -kv[1])[:5] if name_by_id.get(nid)]
        logger.info("[%s] graph integrity: nodes=%d edges=%d components=%d top_hubs=%s",
                    graph_id, len(nodes), len(edges), num_components, top_hubs)
        try:
            warn_ratio = float(getattr(Config, "GRAPH_COMPONENT_WARN_RATIO", 0.5))
        except (TypeError, ValueError):
            warn_ratio = 0.5
        # KG-8: 旧的整数截断比较（> max(1, int(n*ratio))）在真实图上几乎永不触发（30 分量
        # /132 节点已明显碎裂却只发 INFO）。改浮点比较，保留 >1 下限（单分量=完全连通不告警）。
        # 阈值语义：分量数超过节点数×ratio 即告警；默认 ratio 仍偏松，建议经
        # GRAPH_COMPONENT_WARN_RATIO 调低（0.2 可让上述 30/132 触发）。
        if node_ids and num_components > 1 and num_components > len(node_ids) * warn_ratio:
            # 附带最大的 3 个分量的枢纽名（分量内度数最高节点），让操作者看见「什么被断开了」。
            comp_members: Dict[Any, List[Any]] = {}
            for nid in node_ids:
                comp_members.setdefault(_find(nid), []).append(nid)
            top_comps = sorted(comp_members.values(), key=len, reverse=True)[:3]
            comp_hubs = [
                f"{name_by_id.get(max(m, key=lambda x: degree.get(x, 0)), '?')}(n={len(m)})"
                for m in top_comps
            ]
            logger.warning(
                "[%s] possibly UNDER-MERGED entities: %d components over %d nodes; "
                "largest components' hubs: %s",
                graph_id, num_components, len(node_ids), comp_hubs)

        # P3-7: 把已算出的度数转成 [0,1] 归一中心度先验（node_name→centrality），此前被丢弃。
        max_deg = max(degree.values()) if degree else 0
        centrality: Dict[str, float] = {}
        if max_deg > 0:
            for nid, d in degree.items():
                nm = name_by_id.get(nid, "")
                if nm and d > 0:
                    centrality[nm] = max(centrality.get(nm, 0.0), round(d / max_deg, 4))

        # R2-KG-5（GRAPH_CHOKEPOINT_PRIORS，默认关）：在已取的 nodes/edges 上算介数中心度 +
        # 关节点，得到"谁卡在传导咽喉"的结构先验。纯内存、复用上面构造的连通信息；O(V*E) 故对
        # 大图设节点上限（GRAPH_CHOKEPOINT_MAX_NODES，默认 1500）保护，超限则跳过（degrade-safe）。
        betweenness: Dict[str, float] = {}
        chokepoints: List[str] = []
        if getattr(Config, "GRAPH_CHOKEPOINT_PRIORS", False) and node_ids and edges:
            try:
                # KG-3: O(V*E) 上限从 Config 读（GRAPH_CHOKEPOINT_MAX_NODES，默认 1500）——
                # 此前注释声称可调但实际是模块常量；非法/缺失/非正值兜底到常量。
                try:
                    cap = int(getattr(Config, "GRAPH_CHOKEPOINT_MAX_NODES",
                                      _CHOKEPOINT_MAX_NODES) or _CHOKEPOINT_MAX_NODES)
                except (TypeError, ValueError):
                    cap = _CHOKEPOINT_MAX_NODES
                if cap <= 0:
                    cap = _CHOKEPOINT_MAX_NODES
                if len(node_ids) <= cap:
                    adj: Dict[Any, set] = {nid: set() for nid in node_ids}
                    for e in edges:
                        s = getattr(e, "source_node_uuid", None)
                        t = getattr(e, "target_node_uuid", None)
                        if s in adj and t in adj and s != t:
                            adj[s].add(t)
                            adj[t].add(s)
                    raw = _brandes_betweenness(adj)
                    max_bc = max(raw.values()) if raw else 0.0
                    if max_bc > 0:
                        for nid, bc in raw.items():
                            nm = name_by_id.get(nid, "")
                            if nm and bc > 0:
                                betweenness[nm] = max(betweenness.get(nm, 0.0), round(bc / max_bc, 4))
                    aps = _articulation_points(adj)
                    # chokepoints = 介数高位 ∪ 关节点（节点名，去重，按介数降序）
                    choke_ids = set(aps) | {nid for nid, _ in
                                            sorted(raw.items(), key=lambda kv: -kv[1])[:10]}
                    seen: set = set()
                    for nid in sorted(choke_ids, key=lambda x: -raw.get(x, 0.0)):
                        nm = name_by_id.get(nid, "")
                        if nm and nm not in seen:
                            seen.add(nm)
                            chokepoints.append(nm)
                    logger.info("[%s] chokepoint priors: betweenness_nodes=%d articulation=%d",
                                graph_id, len(betweenness), len(aps))
                else:
                    logger.info("[%s] chokepoint priors skipped: %d nodes over cap %d",
                                graph_id, len(node_ids), cap)
            except Exception as exc:  # noqa: BLE001 — priors are best-effort, never fail the build
                logger.warning("[%s] chokepoint prior computation skipped: %s", graph_id, exc)

        return GraphInfo(
            graph_id=graph_id,
            node_count=len(nodes),
            edge_count=len(edges),
            entity_types=list(entity_types),
            components=num_components,
            top_hubs=top_hubs,
            centrality=centrality,
            betweenness=betweenness or None,
            chokepoints=chokepoints or None,
        )
    
    def get_graph_data(self, graph_id: str) -> Dict[str, Any]:
        """
        获取完整图谱数据（包含详细信息）
        
        Args:
            graph_id: 图谱ID
            
        Returns:
            包含nodes和edges的字典，包括时间信息、属性等详细数据
        """
        nodes = fetch_all_nodes(self.client, graph_id)
        edges = fetch_all_edges(self.client, graph_id)

        # 创建节点映射用于获取节点名称
        node_map = {}
        for node in nodes:
            node_map[node.uuid_] = node.name or ""
        
        nodes_data = []
        for node in nodes:
            # 获取创建时间
            created_at = getattr(node, 'created_at', None)
            if created_at:
                created_at = str(created_at)
            
            nodes_data.append({
                "uuid": node.uuid_,
                "name": node.name,
                "labels": node.labels or [],
                "summary": node.summary or "",
                "attributes": node.attributes or {},
                "created_at": created_at,
            })
        
        edges_data = []
        for edge in edges:
            # 获取时间信息
            created_at = getattr(edge, 'created_at', None)
            valid_at = getattr(edge, 'valid_at', None)
            invalid_at = getattr(edge, 'invalid_at', None)
            expired_at = getattr(edge, 'expired_at', None)
            
            # 获取 episodes
            episodes = getattr(edge, 'episodes', None) or getattr(edge, 'episode_ids', None)
            if episodes and not isinstance(episodes, list):
                episodes = [str(episodes)]
            elif episodes:
                episodes = [str(e) for e in episodes]
            
            # 获取 fact_type
            fact_type = getattr(edge, 'fact_type', None) or edge.name or ""
            
            edges_data.append({
                "uuid": edge.uuid_,
                "name": edge.name or "",
                "fact": edge.fact or "",
                "fact_type": fact_type,
                "source_node_uuid": edge.source_node_uuid,
                "target_node_uuid": edge.target_node_uuid,
                "source_node_name": node_map.get(edge.source_node_uuid, ""),
                "target_node_name": node_map.get(edge.target_node_uuid, ""),
                "attributes": edge.attributes or {},
                "created_at": str(created_at) if created_at else None,
                "valid_at": str(valid_at) if valid_at else None,
                "invalid_at": str(invalid_at) if invalid_at else None,
                "expired_at": str(expired_at) if expired_at else None,
                "episodes": episodes or [],
            })
        
        return {
            "graph_id": graph_id,
            "nodes": nodes_data,
            "edges": edges_data,
            "node_count": len(nodes_data),
            "edge_count": len(edges_data),
        }
    
    def delete_graph(self, graph_id: str):
        """删除图谱"""
        self.client.graph.delete(graph_id=graph_id)

