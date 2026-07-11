"""
OASIS模拟管理器
管理Twitter和Reddit双平台并行模拟
使用预设脚本 + LLM智能生成配置参数
"""

import os
import csv
import json
import hashlib
import shutil
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..config import Config
from ..utils.logger import get_logger
from .zep_entity_reader import ZepEntityReader, FilteredEntities
from .oasis_profile_generator import OasisProfileGenerator, OasisAgentProfile
from .simulation_config_generator import SimulationConfigGenerator, SimulationParameters

logger = get_logger('mirofish.simulation')


def _canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def ensure_dossier_actor_entities(
    entities: List[Any],
    actors: Optional[Dict[str, Any]],
) -> tuple[List[Any], Dict[str, Any]]:
    """Guarantee one graph/stand-in entity for every eligible dossier actor.

    Graph ingestion is allowed to be partial; actor identity is not.  When a
    curated DeerFlow actor row has no surviving graph node, create a bounded
    stand-in entity from that exact row so the actor can still receive its role
    contract. Non-agent/context rows remain explicit exclusions in the roster
    decision manifest rather than silently becoming generic personas.
    """
    from ..utils.actors import (
        extract_actor_rows,
        is_agent_eligible,
        match_actor,
        normalize_name,
    )
    from .zep_entity_reader import EntityNode

    base = list(entities or [])
    rows = extract_actor_rows(actors)
    dossier_sha = _canonical_json_sha256(actors) if isinstance(actors, dict) else ""
    decisions: List[Dict[str, Any]] = []
    matched_actor_names = {
        normalize_name(str(row.get("name") or ""))
        for entity in base
        for row in [match_actor(getattr(entity, "name", ""), actors)]
        if isinstance(row, dict)
    }

    for row in rows:
        name = str(row.get("name") or "").strip()
        canonical = normalize_name(name)
        actor_id = str(row.get("actor_id") or row.get("id") or "").strip()
        if not actor_id:
            actor_id = "actor_" + hashlib.sha256(
                canonical.encode("utf-8")
            ).hexdigest()[:16]
        decision: Dict[str, Any] = {
            "actor_id": actor_id,
            "actor_name": name,
            "eligible": bool(is_agent_eligible(row)),
            "synthetic_entity": False,
        }
        if not decision["eligible"]:
            decision["status"] = "context_only"
            decision["reason"] = "dossier classification is not an active simulation actor"
            decisions.append(decision)
            continue
        if canonical in matched_actor_names:
            decision["status"] = "candidate"
            decision["reason"] = "matched existing graph entity"
            decisions.append(decision)
            continue

        entity_type = str(row.get("type") or "Organization").strip() or "Organization"
        entity_uuid = "dossier-" + hashlib.sha256(
            (actor_id + "\0" + name).encode("utf-8")
        ).hexdigest()[:24]
        summary = str(
            row.get("description") or row.get("role") or f"Dossier actor: {name}"
        ).strip()
        attributes = {
            "dossier_actor_id": actor_id,
            "dossier_actor_name": name,
        }
        for key in ("archetype", "simulation_tier", "role_class", "influence"):
            if row.get(key) is not None:
                attributes[key] = row.get(key)
        base.append(EntityNode(
            uuid=entity_uuid,
            name=name,
            labels=["Entity", entity_type],
            summary=summary,
            attributes=attributes,
            related_edges=[],
            related_nodes=[],
        ))
        matched_actor_names.add(canonical)
        decision.update({
            "status": "candidate",
            "reason": "synthesized from dossier after graph omission",
            "synthetic_entity": True,
            "entity_uuid": entity_uuid,
        })
        decisions.append(decision)

    return base, {
        "schema_version": "actor-cast/v1",
        "dossier_sha256": dossier_sha,
        "dossier_actor_count": len(rows),
        "eligible_actor_count": sum(1 for row in decisions if row["eligible"]),
        "decisions": decisions,
    }


def select_agent_pool(
    entities: List[Any],
    actors: Optional[Dict[str, Any]] = None,
    graph_priors: Optional[Dict[str, float]] = None,
) -> List[Any]:
    """选择进入模拟 agent 池的实体（T3.13 上限 + ACTOR-CAST discipline）。

    两条路径（按 Config 旗标取值二选一）：

    * **主阵容路径（默认）** —— 当 ``ACTOR_CAST_MAX``>0 且小于 ``OASIS_MAX_AGENTS``
      （或 OASIS_MAX_AGENTS=0 不设上限）、且至少有一个实体匹配到研究 actor 时：
      agent 池只从**主阵容**派生 —— 匹配研究 actor 且过 tier 1/2 能动准入门
      （``is_agent_eligible``；ACTOR_EXCLUDE_MEDIA 时媒体/观察者推断 tier 3 被挡）
      的实体，按 (salience×tier 权重+中心度先验, 影响力, 邻边数) 排序取
      top ≤ ACTOR_CAST_MAX。**不再**向 OASIS_MAX_AGENTS 填充未匹配的图谱通用节点
      （这正是此前池子被填充到 ~80、persona 与每轮模拟 LLM 调用 4 倍膨胀的根因）；
      「受众/填充」persona 改由 SIM_AUDIENCE_AGENTS 程序化生成（零 LLM 成本）。
      全部匹配 actor 都被媒体准入门挡下时回退保留全部匹配者（安全网：池子绝不因
      过滤而空掉）。

    * **旧 T3.13 路径（degrade-safe 恢复口）** —— ACTOR_CAST_MAX<=0、或设为
      ≥ OASIS_MAX_AGENTS（unset-high）、或没有任何实体匹配到研究 actor 时：
      与现状逐字节一致 —— 仅当实体数超过有效上限（min(OASIS_MAX_AGENTS,
      ACTOR_CAST_MAX)，取正值）时按 (是否匹配 actor, 影响力, 邻边数)（或
      salience/tier 元组）排序保留 top N，且所有匹配 actor 必留。

    Args:
        entities: ZepEntityReader 过滤后的实体列表（EntityNode）。
        actors: 深度研究 actors.json 顶层对象（可选）。
        graph_priors: 建图阶段算出的中心度先验 {实体名: [0,1]}（可选，P3-7）。

    Returns:
        保留的实体列表（原列表不被修改）。
    """
    from ..utils.actors import (
        match_actor as _match_actor,
        influence_weight as _influence_weight,
        salience_score as _salience_score,
        is_agent_eligible as _is_agent_eligible,
        entity_simulation_tier as _entity_simulation_tier,
        normalize_name as _normalize_name,
    )

    entities = list(entities or [])
    if not entities:
        return entities

    # NEXTSTEPS P3-7: 中心度先验查找（GRAPH 阶段算出，按 NFKC 归一名匹配实体→[0,1]）。
    # 缺失/旗标关 → _centrality 恒返回 0.0，排序逐字节退化为现状（degrade-safe）。
    _cent_on = (bool(getattr(Config, 'GRAPH_CENTRALITY_PRIORS', True))
                and isinstance(graph_priors, dict) and bool(graph_priors))
    _cent_lookup = {
        _normalize_name(str(k)): float(v)
        for k, v in (graph_priors or {}).items()
        if _cent_on and isinstance(v, (int, float))
    }

    def _centrality(e) -> float:
        if not _cent_lookup:
            return 0.0
        return _cent_lookup.get(_normalize_name(getattr(e, "name", "") or ""), 0.0)

    _oasis_max = int(getattr(Config, 'OASIS_MAX_AGENTS', 0) or 0)
    try:
        _cast_max = int(getattr(Config, 'ACTOR_CAST_MAX', 20) or 0)
    except (TypeError, ValueError):
        _cast_max = 20

    _matched = {id(e): (_match_actor(e.name, actors) if actors else None) for e in entities}
    _matched_count = sum(1 for v in _matched.values() if v)

    _salience_rank_on = getattr(Config, 'SIM_SALIENCE_RANKING', True)
    _tier_eligibility_on = getattr(Config, 'SIM_TIER_ELIGIBILITY', True)

    # 显式 salience 字段是否真实存在：只有任一匹配 actor 携带 dict 形态的 salience 时，
    # 才启用 salience 主排序；否则强制退回现状元组，保证旧数据逐字节一致。
    def _has_explicit_salience(a) -> bool:
        return isinstance(a, dict) and isinstance(a.get("salience"), dict)

    _use_salience = _salience_rank_on and any(
        _has_explicit_salience(_matched.get(id(e))) for e in entities
    )

    # tier 偏好：仅当开启 tier 准入门、且确有实体携带本体分类信号（archetype/
    # simulation_tier）时才生效；否则今天的数据 tier 恒为 1，该项对所有实体相同，
    # 排序结果与现状一致（degrade-safe）。
    def _has_tier_signal(a) -> bool:
        return isinstance(a, dict) and (
            a.get("archetype") is not None or a.get("simulation_tier") is not None
        )

    _use_tier = _tier_eligibility_on and any(
        _has_tier_signal(_matched.get(id(e))) for e in entities
    )

    def _legacy_rank(e):
        """现状排序键：(是否匹配 actor, 影响力权重+中心度先验, 邻边度数)。"""
        a = _matched.get(id(e))
        iw = (_influence_weight(a) or 0.0) if a else 0.0
        # P3-7: 把中心度先验叠加到影响力轴（缺失时 +0.0 → 与现状逐字节一致）。
        return (1 if a else 0, iw + 0.5 * _centrality(e), len(e.related_edges or []))

    def _rank(e):
        if not _use_salience and not _use_tier:
            # 无任何本体信号：逐字节退化为现状元组。
            return _legacy_rank(e)
        a = _matched.get(id(e))
        matched_flag = 1 if a else 0
        # tier 偏好：能动 agent（tier 1/2）优先于被动信息源/抽象概念（tier 3/4）。
        eligible_flag = 1
        if _use_tier:
            eligible_flag = 1 if (a is None or _is_agent_eligible(a)) else 0
        # salience 主键：核心决策者(tier=1)的决策权重高于利益相关方(tier=2)，
        # 让同 salience 下 principal 不被 amplifier/stakeholder 盖过。
        if _use_salience:
            sal = _salience_score(a) if a else _salience_score(None)
            tier = _entity_simulation_tier(a) if a else 1
            # tier 1 → 1.0；tier 2 → 0.85；tier 3 → 0.5；tier 4 → 0.3（核心方更重）
            _tier_weight = {1: 1.0, 2: 0.85, 3: 0.5, 4: 0.3}.get(tier, 1.0)
            sal_key = sal * _tier_weight
        else:
            sal_key = 0.0
        iw = (_influence_weight(a) or 0.0) if a else 0.0
        # P3-7: 中心度先验叠加到 salience 键（高中心度=更枢纽，比原始度数更强的影响力先验）。
        sal_key = sal_key + 0.5 * _centrality(e)
        return (matched_flag, eligible_flag, sal_key, iw, len(e.related_edges or []))

    # ---- 主阵容路径（ACTOR-CAST discipline，默认开）----
    _cast_discipline = (
        _cast_max > 0
        and (_oasis_max <= 0 or _cast_max < _oasis_max)
        and _matched_count > 0
    )
    if _cast_discipline:
        matched_entities = [e for e in entities if _matched.get(id(e))]
        # tier 1/2 能动准入门：ACTOR_EXCLUDE_MEDIA 时媒体/观察者（type=Media/媒体类
        # role，无显式 tier 1/2）被 entity_simulation_tier→3 挡下；无任何分类信号的
        # 旧档案行推断 tier 1，全部放行（degrade-safe）。
        eligible = [
            e for e in matched_entities if _is_agent_eligible(_matched.get(id(e)))
        ]
        excluded_n = len(matched_entities) - len(eligible)
        if not eligible:
            # 安全网：过滤绝不把池子清空（全是媒体/tier3-4 时保留全部匹配者）。
            logger.warning(
                "[actor-cast] 全部 %d 个匹配 actor 都被 tier/媒体准入门挡下，"
                "回退保留全部匹配者", len(matched_entities)
            )
            eligible = matched_entities
            excluded_n = 0

        # 2026-07-03 live-surfaced（真实 forecast run）：图谱实体解析未把同一真实主体
        # 的多个别名表面形（如「China」「CCP」「Beijing」「MOFCOM」「Government of the
        # People's Republic of China」）合并为一个节点时，它们各自都会通过 _match_actor
        # 命中 actors.json 里同一条记录（该记录已正确把这些列为 aliases），却是不同的
        # 图谱节点（不同 id(e)）——过滤/排序对它们一视同仁，导致同一个真实 actor 挤占
        # ≤ACTOR_CAST_MAX 里的多个席位（实测一次 20 席的阵容里，中国政府一家就占了
        # China/CCP×2/Beijing×2/Government of PRC/MOFCOM 共 6 席，把其余真实主体挤出）。
        # 这里按「匹配到的 actor 记录」的规范名去重——同一 actor 的多个图谱节点只保留
        # 排序最靠前的一个，再进入 top-N 截断；未匹配到 actor 的节点各自独立（不互相合并）。
        _by_actor: Dict[str, Any] = {}
        _dedup_dropped = 0
        for e in sorted(eligible, key=_rank, reverse=True):
            a = _matched.get(id(e))
            actor_key = _normalize_name(str(a.get("name", "")) if isinstance(a, dict) else "")
            key = actor_key if actor_key else f"__unmatched_{id(e)}"
            if key in _by_actor:
                _dedup_dropped += 1
                continue
            _by_actor[key] = e
        eligible = list(_by_actor.values())

        kept = sorted(eligible, key=_rank, reverse=True)
        truncated_n = max(0, len(kept) - _cast_max)
        kept = kept[:_cast_max]
        logger.info(
            f"[actor-cast] 主阵容纪律（cast_max={_cast_max}）: 实体 {len(entities)} 个"
            f"（匹配研究 actor {_matched_count} 个）→ 保留 {len(kept)} 个主阵容 agent"
            f"（媒体/tier 排除 {excluded_n}，同一 actor 别名节点去重 {_dedup_dropped}，"
            f"超上限裁剪 {truncated_n}，未匹配图谱节点不再填充 {len(entities) - _matched_count} 个）"
        )
        return kept

    # ---- 旧 T3.13 路径（无匹配 actor / 旗标恢复旧行为）----
    _max_agents = min((m for m in (_oasis_max, _cast_max) if m > 0), default=0)
    if not _max_agents or len(entities) <= _max_agents:
        return entities

    kept = [e for e in entities if _matched.get(id(e))]  # 所有匹配 actor 必留
    kept_ids = {id(e) for e in kept}
    for e in sorted(entities, key=_rank, reverse=True):
        if len(kept) >= _max_agents:
            break
        if id(e) not in kept_ids:
            kept.append(e)
            kept_ids.add(id(e))
    _rank_mode = "salience" if _use_salience else ("tier" if _use_tier else "legacy")
    logger.info(
        f"[T3.13] 智能体上限 {_max_agents}: {len(entities)} → 保留 {len(kept)} "
        f"(匹配研究 actor {_matched_count} 个全部保留，排序={_rank_mode})，"
        f"丢弃 {len(entities) - len(kept)}"
    )
    return kept


class SimulationStatus(str, Enum):
    """模拟状态"""
    CREATED = "created"
    PREPARING = "preparing"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"      # 模拟被手动停止
    COMPLETED = "completed"  # 模拟自然完成
    FAILED = "failed"


class PlatformType(str, Enum):
    """平台类型"""
    TWITTER = "twitter"
    REDDIT = "reddit"


@dataclass
class SimulationState:
    """模拟状态"""
    simulation_id: str
    project_id: str
    graph_id: str
    
    # 平台启用状态
    enable_twitter: bool = True
    enable_reddit: bool = True
    
    # 状态
    status: SimulationStatus = SimulationStatus.CREATED
    
    # 准备阶段数据
    entities_count: int = 0
    profiles_count: int = 0
    entity_types: List[str] = field(default_factory=list)
    actor_role_contract_version: Optional[str] = None
    actor_role_count: int = 0
    actor_cast_manifest_sha256: Optional[str] = None
    actor_role_manifest_sha256: Dict[str, str] = field(default_factory=dict)
    
    # 配置生成信息
    config_generated: bool = False
    config_reasoning: str = ""
    
    # 运行时数据
    current_round: int = 0
    twitter_status: str = "not_started"
    reddit_status: str = "not_started"
    
    # 时间戳
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    # 错误信息
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """完整状态字典（内部使用）"""
        return {
            "simulation_id": self.simulation_id,
            "project_id": self.project_id,
            "graph_id": self.graph_id,
            "enable_twitter": self.enable_twitter,
            "enable_reddit": self.enable_reddit,
            "status": self.status.value,
            "entities_count": self.entities_count,
            "profiles_count": self.profiles_count,
            "entity_types": self.entity_types,
            "actor_role_contract_version": self.actor_role_contract_version,
            "actor_role_count": self.actor_role_count,
            "actor_cast_manifest_sha256": self.actor_cast_manifest_sha256,
            "actor_role_manifest_sha256": self.actor_role_manifest_sha256,
            "config_generated": self.config_generated,
            "config_reasoning": self.config_reasoning,
            "current_round": self.current_round,
            "twitter_status": self.twitter_status,
            "reddit_status": self.reddit_status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
        }
    
    def to_simple_dict(self) -> Dict[str, Any]:
        """简化状态字典（API返回使用）"""
        return {
            "simulation_id": self.simulation_id,
            "project_id": self.project_id,
            "graph_id": self.graph_id,
            "status": self.status.value,
            "entities_count": self.entities_count,
            "profiles_count": self.profiles_count,
            "entity_types": self.entity_types,
            "actor_role_contract_version": self.actor_role_contract_version,
            "actor_role_count": self.actor_role_count,
            "actor_role_manifest_sha256": self.actor_role_manifest_sha256,
            "config_generated": self.config_generated,
            "error": self.error,
        }


class SimulationManager:
    """
    模拟管理器
    
    核心功能：
    1. 从Zep图谱读取实体并过滤
    2. 生成OASIS Agent Profile
    3. 使用LLM智能生成模拟配置参数
    4. 准备预设脚本所需的所有文件
    """
    
    # 模拟数据存储目录
    SIMULATION_DATA_DIR = os.path.join(
        os.path.dirname(__file__), 
        '../../uploads/simulations'
    )
    
    def __init__(self):
        # 确保目录存在
        os.makedirs(self.SIMULATION_DATA_DIR, exist_ok=True)
        
        # 内存中的模拟状态缓存
        self._simulations: Dict[str, SimulationState] = {}
    
    def _get_simulation_dir(self, simulation_id: str) -> str:
        """获取模拟数据目录"""
        sim_dir = os.path.join(self.SIMULATION_DATA_DIR, simulation_id)
        os.makedirs(sim_dir, exist_ok=True)
        return sim_dir
    
    def _save_simulation_state(self, state: SimulationState):
        """保存模拟状态到文件"""
        from ..utils.atomic import write_json_atomic
        from .simulation_runner import SimulationRunner
        sim_dir = self._get_simulation_dir(state.simulation_id)
        state_file = os.path.join(sim_dir, "state.json")

        state.updated_at = datetime.now().isoformat()

        # 原子写入 + 与 SimulationRunner 共享同一把锁（EXECPLAN2 F-6-9）：关闭清理 /
        # 孤儿回收时 SimulationRunner 也会写同一个 state.json，串行化避免基于陈旧快照的覆盖。
        with SimulationRunner._run_state_lock:
            write_json_atomic(state_file, state.to_dict())
            self._simulations[state.simulation_id] = state
    
    def _load_simulation_state(self, simulation_id: str) -> Optional[SimulationState]:
        """从文件加载模拟状态"""
        if simulation_id in self._simulations:
            return self._simulations[simulation_id]
        
        sim_dir = self._get_simulation_dir(simulation_id)
        state_file = os.path.join(sim_dir, "state.json")
        
        if not os.path.exists(state_file):
            return None
        
        with open(state_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        state = SimulationState(
            simulation_id=simulation_id,
            project_id=data.get("project_id", ""),
            graph_id=data.get("graph_id", ""),
            enable_twitter=data.get("enable_twitter", True),
            enable_reddit=data.get("enable_reddit", True),
            status=SimulationStatus(data.get("status", "created")),
            entities_count=data.get("entities_count", 0),
            profiles_count=data.get("profiles_count", 0),
            entity_types=data.get("entity_types", []),
            actor_role_contract_version=data.get("actor_role_contract_version"),
            actor_role_count=int(data.get("actor_role_count", 0) or 0),
            actor_cast_manifest_sha256=data.get("actor_cast_manifest_sha256"),
            actor_role_manifest_sha256=(
                data.get("actor_role_manifest_sha256")
                if isinstance(data.get("actor_role_manifest_sha256"), dict)
                else {}
            ),
            config_generated=data.get("config_generated", False),
            config_reasoning=data.get("config_reasoning", ""),
            current_round=data.get("current_round", 0),
            twitter_status=data.get("twitter_status", "not_started"),
            reddit_status=data.get("reddit_status", "not_started"),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
            error=data.get("error"),
        )
        
        self._simulations[simulation_id] = state
        return state
    
    def create_simulation(
        self,
        project_id: str,
        graph_id: str,
        enable_twitter: bool = True,
        enable_reddit: bool = True,
    ) -> SimulationState:
        """
        创建新的模拟
        
        Args:
            project_id: 项目ID
            graph_id: Zep图谱ID
            enable_twitter: 是否启用Twitter模拟
            enable_reddit: 是否启用Reddit模拟
            
        Returns:
            SimulationState
        """
        import uuid
        simulation_id = f"sim_{uuid.uuid4().hex[:12]}"
        
        state = SimulationState(
            simulation_id=simulation_id,
            project_id=project_id,
            graph_id=graph_id,
            enable_twitter=enable_twitter,
            enable_reddit=enable_reddit,
            status=SimulationStatus.CREATED,
        )
        
        self._save_simulation_state(state)
        logger.info(f"创建模拟: {simulation_id}, project={project_id}, graph={graph_id}")
        
        return state
    
    def prepare_simulation(
        self,
        simulation_id: str,
        simulation_requirement: str,
        document_text: str,
        defined_entity_types: Optional[List[str]] = None,
        use_llm_for_profiles: bool = True,
        progress_callback: Optional[callable] = None,
        parallel_profile_count: int = 3,
        actors: Optional[Dict[str, Any]] = None,
        graph_priors: Optional[Dict[str, float]] = None,
        # PREP-1/PREP-4: 编排器把 options.max_rounds 与 research_language 透传到配置生成——
        # 定时事件按真实执行窗排期、英文调研自动切 global_market 画像/英文人设。None=旧行为。
        max_rounds: Optional[int] = None,
        research_language: Optional[str] = None,
    ) -> SimulationState:
        """
        准备模拟环境（全程自动化）

        步骤：
        1. 从Zep图谱读取并过滤实体
        2. 为每个实体生成OASIS Agent Profile（可选LLM增强，支持并行）
        3. 使用LLM智能生成模拟配置参数（时间、活跃度、发言频率等）
        4. 保存配置文件和Profile文件
        5. 复制预设脚本到模拟目录

        Args:
            simulation_id: 模拟ID
            simulation_requirement: 模拟需求描述（用于LLM生成配置）
            document_text: 原始文档内容（用于LLM理解背景）
            defined_entity_types: 预定义的实体类型（可选）
            use_llm_for_profiles: 是否使用LLM生成详细人设
            progress_callback: 进度回调函数 (stage, progress, message)
            parallel_profile_count: 并行生成人设的数量，默认3
            actors: 深度研究 actors.json 顶层对象（可选）。提供时 persona 与
                    模拟配置都会以调研实证（立场/影响力/记忆/时间线）为准

        Returns:
            SimulationState
        """
        state = self._load_simulation_state(simulation_id)
        if not state:
            raise ValueError(f"模拟不存在: {simulation_id}")
        
        try:
            state.status = SimulationStatus.PREPARING
            self._save_simulation_state(state)
            
            sim_dir = self._get_simulation_dir(simulation_id)
            
            # ========== 阶段1: 读取并过滤实体 ==========
            if progress_callback:
                progress_callback("reading", 0, "正在连接Zep图谱...")
            
            reader = ZepEntityReader()
            
            if progress_callback:
                progress_callback("reading", 30, "正在读取节点数据...")
            
            filtered = reader.filter_defined_entities(
                graph_id=state.graph_id,
                defined_entity_types=defined_entity_types,
                enrich_with_edges=True,
            )

            # LOOP-011: the curated actor dossier is an authoritative cast
            # handoff. Partial graph ingestion may not silently erase an actor.
            # Add bounded dossier stand-ins before cast selection, then record
            # every include/context decision for audit and reuse validation.
            filtered.entities, actor_cast_manifest = ensure_dossier_actor_entities(
                filtered.entities, actors
            )
            # T3.13 + ACTOR-CAST discipline：agent 池选择（抽取为模块级 select_agent_pool，
            # 便于单测）。默认（ACTOR_CAST_MAX=20 < OASIS_MAX_AGENTS=80）：池子从主阵容派生
            # （匹配研究 actor 且 tier 1/2 能动者，按 salience/影响力排序取 top ≤cast_max），
            # 不再向 80 填充图谱通用节点；ACTOR_CAST_MAX 设为 ≥ OASIS_MAX_AGENTS（或 0）时
            # 走旧的 T3.13 路径，行为与现状逐字节一致（degrade-safe）。
            filtered.entities = select_agent_pool(
                filtered.entities, actors=actors, graph_priors=graph_priors
            )
            selected_rows: Dict[str, Any] = {}
            from ..utils.actors import match_actor as _match_selected_actor
            for entity in filtered.entities:
                row = _match_selected_actor(entity.name, actors)
                if isinstance(row, dict):
                    selected_rows[str(row.get("name") or "").strip()] = entity
            selected_names = set(selected_rows)
            missing_eligible: List[str] = []
            for decision in actor_cast_manifest["decisions"]:
                if decision["actor_name"] in selected_names:
                    entity = selected_rows[decision["actor_name"]]
                    decision["status"] = "selected"
                    decision["entity_uuid"] = getattr(entity, "uuid", None)
                    if not decision.get("eligible"):
                        decision["reason"] = (
                            "selected by the all-context safety fallback; role remains explicit"
                        )
                elif decision.get("eligible"):
                    decision["status"] = "excluded"
                    decision["reason"] = "eligible dossier actor was dropped by cast selection"
                    missing_eligible.append(decision["actor_name"])
            if missing_eligible:
                raise RuntimeError(
                    "eligible dossier actors missing from simulation cast: "
                    + ", ".join(missing_eligible[:12])
                )
            actor_cast_manifest["selected_actor_count"] = len(selected_names)
            actor_cast_manifest["selected_actor_names"] = sorted(selected_names)
            actor_cast_path = os.path.join(sim_dir, "actor_cast_manifest.json")
            from ..utils.atomic import write_json_atomic
            write_json_atomic(actor_cast_path, actor_cast_manifest, allow_nan=False)
            with open(actor_cast_path, "rb") as actor_cast_handle:
                actor_cast_sha = hashlib.sha256(actor_cast_handle.read()).hexdigest()
            filtered.filtered_count = len(filtered.entities)

            state.entities_count = filtered.filtered_count
            state.entity_types = list(filtered.entity_types)
            
            if progress_callback:
                progress_callback(
                    "reading", 100, 
                    f"完成，共 {filtered.filtered_count} 个实体",
                    current=filtered.filtered_count,
                    total=filtered.filtered_count
                )
            
            if filtered.filtered_count == 0:
                state.status = SimulationStatus.FAILED
                state.error = "没有找到符合条件的实体，请检查图谱是否正确构建"
                self._save_simulation_state(state)
                return state
            
            # ========== 阶段2: 生成Agent Profile ==========
            total_entities = len(filtered.entities)
            
            if progress_callback:
                progress_callback(
                    "generating_profiles", 0, 
                    "开始生成...",
                    current=0,
                    total=total_entities
                )
            
            # 传入graph_id以启用Zep检索功能，获取更丰富的上下文
            # PREP-4(2): 英文调研 → 人设生成默认英文（persona_language='en'）；否则 None=
            # 生成器自己的探测逻辑，行为不变。
            generator = OasisProfileGenerator(
                graph_id=state.graph_id,
                persona_language=("en" if str(research_language or "").strip().lower().startswith("en") else None),
            )
            generator.role_dossier_sha256 = actor_cast_manifest.get("dossier_sha256") or ""
            generator.role_cast_manifest_sha256 = actor_cast_sha
            
            def profile_progress(current, total, msg):
                if progress_callback:
                    progress_callback(
                        "generating_profiles", 
                        int(current / total * 100), 
                        msg,
                        current=current,
                        total=total,
                        item_name=msg
                    )
            
            # 设置实时保存的文件路径（优先使用 Reddit JSON 格式）
            realtime_output_path = None
            realtime_platform = "reddit"
            if state.enable_reddit:
                realtime_output_path = os.path.join(sim_dir, "reddit_profiles.json")
                realtime_platform = "reddit"
            elif state.enable_twitter:
                realtime_output_path = os.path.join(sim_dir, "twitter_profiles.csv")
                realtime_platform = "twitter"
            
            profiles = generator.generate_profiles_from_entities(
                entities=filtered.entities,
                use_llm=use_llm_for_profiles,
                progress_callback=profile_progress,
                graph_id=state.graph_id,  # 传入graph_id用于Zep检索
                parallel_count=parallel_profile_count,  # 并行生成数量
                realtime_output_path=realtime_output_path,  # 实时保存路径
                output_platform=realtime_platform,  # 输出格式
                actors=actors  # 深度研究档案（可选）：实证立场/记忆注入 persona
            )

            role_count = int(
                (getattr(generator, "last_generation_stats", {}) or {}).get(
                    "role_prompts", 0
                ) or 0
            )
            expected_roles = int(actor_cast_manifest.get("selected_actor_count", 0) or 0)
            if role_count != expected_roles:
                raise RuntimeError(
                    f"actor role coverage mismatch: expected {expected_roles}, got {role_count}"
                )
            if role_count:
                from .actor_role_prompt import ROLE_CONTRACT_VERSION
                state.actor_role_contract_version = ROLE_CONTRACT_VERSION
            else:
                state.actor_role_contract_version = None
            state.actor_role_count = role_count
            state.actor_cast_manifest_sha256 = actor_cast_sha
            
            state.profiles_count = len(profiles)
            
            # 保存Profile文件（注意：Twitter使用CSV格式，Reddit使用JSON格式）
            # Reddit 已经在生成过程中实时保存了，这里再保存一次确保完整性
            if progress_callback:
                progress_callback(
                    "generating_profiles", 95, 
                    "保存Profile文件...",
                    current=total_entities,
                    total=total_entities
                )
            
            if state.enable_reddit:
                generator.save_profiles(
                    profiles=profiles,
                    file_path=os.path.join(sim_dir, "reddit_profiles.json"),
                    platform="reddit"
                )
            
            if state.enable_twitter:
                # Twitter使用CSV格式！这是OASIS的要求
                generator.save_profiles(
                    profiles=profiles,
                    file_path=os.path.join(sim_dir, "twitter_profiles.csv"),
                    platform="twitter"
                )
            
            if progress_callback:
                progress_callback(
                    "generating_profiles", 100, 
                    f"完成，共 {len(profiles)} 个Profile",
                    current=len(profiles),
                    total=len(profiles)
                )
            
            # ========== 阶段3: LLM智能生成模拟配置 ==========
            if progress_callback:
                progress_callback(
                    "generating_config", 0, 
                    "正在分析模拟需求...",
                    current=0,
                    total=3
                )
            
            config_generator = SimulationConfigGenerator()
            
            if progress_callback:
                progress_callback(
                    "generating_config", 30, 
                    "正在调用LLM生成配置...",
                    current=1,
                    total=3
                )
            
            sim_params = config_generator.generate_config(
                simulation_id=simulation_id,
                project_id=state.project_id,
                graph_id=state.graph_id,
                simulation_requirement=simulation_requirement,
                document_text=document_text,
                entities=filtered.entities,
                enable_twitter=state.enable_twitter,
                enable_reddit=state.enable_reddit,
                actors=actors,  # 深度研究档案（可选）：实证立场/热点/时间线进配置
                max_rounds=max_rounds,  # PREP-1: 定时事件按真实执行轮数窗排期
                research_language=research_language,  # PREP-4: 英文调研→global_market 画像
            )
            
            if progress_callback:
                progress_callback(
                    "generating_config", 70,
                    "正在保存配置文件...",
                    current=2,
                    total=3
                )

            # ========== ACTOR-CAST：受众填充 persona（SIM_AUDIENCE_AGENTS，零 LLM）==========
            # 配置生成器把 M 个受众 agent 配置追加在具名角色之后（agent_id 连续）；OASIS 按
            # profile 数组下标建 agent_id（F-5-1 不变式），故 profile 也须按同序追加 M 个
            # 规则生成的受众 persona 并重存，否则受众 agent 在 agent 图中不存在、激活时被
            # 静默跳过。SIM_AUDIENCE_AGENTS=0（默认）→ 无受众配置 → 本段整体 no-op。
            try:
                _aud_type = SimulationConfigGenerator.AUDIENCE_ENTITY_TYPE
                _aud_cfgs = [
                    c for c in (sim_params.agent_configs or [])
                    if str(getattr(c, "entity_type", "") or "") == _aud_type
                ]
                if _aud_cfgs:
                    _aud_profiles = generator.generate_audience_profiles(
                        _aud_cfgs, start_user_id=len(profiles)
                    )
                    profiles.extend(_aud_profiles)
                    state.profiles_count = len(profiles)
                    if state.enable_reddit:
                        generator.save_profiles(
                            profiles=profiles,
                            file_path=os.path.join(sim_dir, "reddit_profiles.json"),
                            platform="reddit",
                        )
                    if state.enable_twitter:
                        generator.save_profiles(
                            profiles=profiles,
                            file_path=os.path.join(sim_dir, "twitter_profiles.csv"),
                            platform="twitter",
                        )
                    logger.info(
                        f"受众填充 persona: +{len(_aud_profiles)} 个（合计 {len(profiles)}，零 LLM 成本）"
                    )
            except Exception as e:
                logger.warning(f"受众 persona 生成失败（降级跳过，受众 agent 不会被激活）: {e}")

            # 保存配置文件（原子写，避免并发/中断时读到半写的 simulation_config.json）
            from ..utils.atomic import write_text_atomic
            config_path = os.path.join(sim_dir, "simulation_config.json")
            write_text_atomic(config_path, sim_params.to_json())
            
            state.config_generated = True
            state.config_reasoning = sim_params.generation_reasoning
            
            if progress_callback:
                progress_callback(
                    "generating_config", 100, 
                    "配置生成完成",
                    current=3,
                    total=3
                )
            
            # 注意：运行脚本保留在 backend/scripts/ 目录，不再复制到模拟目录
            # 启动模拟时，simulation_runner 会从 scripts/ 目录运行脚本

            if state.actor_role_count:
                role_profile_paths: List[str] = []
                if state.enable_reddit:
                    role_profile_paths.append(os.path.join(sim_dir, "reddit_profiles.json"))
                if state.enable_twitter:
                    role_profile_paths.append(os.path.join(sim_dir, "twitter_profiles.csv"))
                role_manifest_shas: Dict[str, str] = {}
                for role_profile_path in role_profile_paths:
                    OasisProfileGenerator.validate_role_prompt_manifest(
                        role_profile_path,
                        expected_role_count=state.actor_role_count,
                        expected_cast_manifest_sha256=state.actor_cast_manifest_sha256,
                    )
                    role_manifest_path = OasisProfileGenerator._role_manifest_path(
                        role_profile_path
                    )
                    with open(role_manifest_path, "rb") as role_manifest_handle:
                        role_manifest_shas[
                            "twitter" if role_profile_path.endswith(".csv") else "reddit"
                        ] = hashlib.sha256(role_manifest_handle.read()).hexdigest()
                state.actor_role_manifest_sha256 = role_manifest_shas
            else:
                state.actor_role_manifest_sha256 = {}
            
            # 更新状态
            state.status = SimulationStatus.READY
            self._save_simulation_state(state)
            
            logger.info(f"模拟准备完成: {simulation_id}, "
                       f"entities={state.entities_count}, profiles={state.profiles_count}")
            
            return state
            
        except Exception as e:
            logger.error(f"模拟准备失败: {simulation_id}, error={str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            state.status = SimulationStatus.FAILED
            state.error = str(e)
            self._save_simulation_state(state)
            raise
    
    def get_simulation(self, simulation_id: str) -> Optional[SimulationState]:
        """获取模拟状态"""
        return self._load_simulation_state(simulation_id)
    
    def list_simulations(self, project_id: Optional[str] = None) -> List[SimulationState]:
        """列出所有模拟"""
        simulations = []
        
        if os.path.exists(self.SIMULATION_DATA_DIR):
            for sim_id in os.listdir(self.SIMULATION_DATA_DIR):
                # 跳过隐藏文件（如 .DS_Store）和非目录文件
                sim_path = os.path.join(self.SIMULATION_DATA_DIR, sim_id)
                if sim_id.startswith('.') or not os.path.isdir(sim_path):
                    continue
                
                state = self._load_simulation_state(sim_id)
                if state:
                    if project_id is None or state.project_id == project_id:
                        simulations.append(state)
        
        return simulations
    
    def get_profiles(self, simulation_id: str, platform: str = "reddit") -> List[Dict[str, Any]]:
        """获取模拟的Agent Profile"""
        state = self._load_simulation_state(simulation_id)
        if not state:
            raise ValueError(f"模拟不存在: {simulation_id}")
        
        sim_dir = self._get_simulation_dir(simulation_id)

        # Twitter 人设保存为 CSV（OASIS 要求），Reddit 保存为 JSON。
        # 与 /profiles/realtime 的 CSV 解析保持一致：直接 csv.DictReader -> list（不做数值/JSON 转换），
        # 字段名沿用 twitter_profiles.csv 的表头，避免前端拿到形状不一致的数据。
        if platform == "twitter":
            profile_path = os.path.join(sim_dir, "twitter_profiles.csv")

            if not os.path.exists(profile_path):
                return []

            try:
                with open(profile_path, 'r', encoding='utf-8') as f:
                    return list(csv.DictReader(f))
            except Exception as e:
                # 文件可能正在写入中或行损坏：保持防御性，返回空列表而非抛错
                logger.warning(f"读取 twitter_profiles.csv 失败: {e}")
                return []

        profile_path = os.path.join(sim_dir, f"{platform}_profiles.json")

        if not os.path.exists(profile_path):
            return []

        with open(profile_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def get_simulation_config(self, simulation_id: str) -> Optional[Dict[str, Any]]:
        """获取模拟配置"""
        sim_dir = self._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        
        if not os.path.exists(config_path):
            return None
        
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def get_run_instructions(self, simulation_id: str) -> Dict[str, str]:
        """获取运行说明"""
        sim_dir = self._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../scripts'))
        
        return {
            "simulation_dir": sim_dir,
            "scripts_dir": scripts_dir,
            "config_file": config_path,
            "commands": {
                "twitter": f"python {scripts_dir}/run_twitter_simulation.py --config {config_path}",
                "reddit": f"python {scripts_dir}/run_reddit_simulation.py --config {config_path}",
                "parallel": f"python {scripts_dir}/run_parallel_simulation.py --config {config_path}",
            },
            "instructions": (
                f"1. 进入后端目录并使用 uv 隔离环境（项目已从 conda 迁移到 uv）:\n"
                f"   - 前端开发服务器: npm run dev\n"
                f"   - 后端脚本统一通过 uv 运行: uv run python <脚本> （在 backend/ 目录下执行）\n"
                f"2. 运行模拟 (脚本位于 {scripts_dir}):\n"
                f"   - 单独运行Twitter: uv run python {scripts_dir}/run_twitter_simulation.py --config {config_path}\n"
                f"   - 单独运行Reddit: uv run python {scripts_dir}/run_reddit_simulation.py --config {config_path}\n"
                f"   - 并行运行双平台: uv run python {scripts_dir}/run_parallel_simulation.py --config {config_path}"
            )
        }
