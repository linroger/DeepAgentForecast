"""
OASIS Agent Profile生成器
将Zep图谱中的实体转换为OASIS模拟平台所需的Agent Profile格式

优化改进：
1. 调用Zep检索功能二次丰富节点信息
2. 优化提示词生成非常详细的人设
3. 区分个人实体和抽象群体实体
"""

import json
import os
import random
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime

from .graphiti_client import Zep

from ..config import Config
from ..utils.actors import (
    actor_briefing,
    behavioral_dna_block,
    influence_weight,
    match_actor,
    relationship_briefing,
    roster_block,
)
from ..utils.atomic import write_text_atomic, write_json_atomic  # EXECPLAN2 F-5-0/F-5-1 原子写
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
from .zep_entity_reader import EntityNode, ZepEntityReader

logger = get_logger('mirofish.oasis_profile')


@dataclass
class OasisAgentProfile:
    """OASIS Agent Profile数据结构"""
    # 通用字段
    user_id: int
    user_name: str
    name: str
    bio: str
    persona: str
    
    # 可选字段 - Reddit风格
    karma: int = 1000
    
    # 可选字段 - Twitter风格
    friend_count: int = 100
    follower_count: int = 150
    statuses_count: int = 500
    
    # 额外人设信息
    age: Optional[int] = None
    gender: Optional[str] = None
    mbti: Optional[str] = None
    country: Optional[str] = None
    profession: Optional[str] = None
    interested_topics: List[str] = field(default_factory=list)
    
    # 来源实体信息
    source_entity_uuid: Optional[str] = None
    source_entity_type: Optional[str] = None
    
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))

    # PREP-8: 人设生成路径（"llm" / "rule" / "rule_fallback" / "error_stub"），
    # 用于统计静默降级的比例——不进入任何平台产物（reddit/twitter 写入器不读它）。
    generation_path: str = "llm"

    # NEXTSTEPS SIM_PERSONA_DESIGN: 结构化人格设计（逐角色情境工程的产物）。
    # 仅对匹配到调研档案的真实角色生成；字段见 OasisProfileGenerator.PERSONA_DESIGN_KEYS。
    # 无档案/开关关闭 → None（to_dict/reddit 产物省略，schema 与旧版逐字节一致）。
    persona_design: Optional[Dict[str, Any]] = None

    def to_reddit_format(self) -> Dict[str, Any]:
        """转换为Reddit平台格式"""
        profile = {
            "user_id": self.user_id,
            "username": self.user_name,  # OASIS 库要求字段名为 username（无下划线）
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "karma": self.karma,
            "created_at": self.created_at,
        }
        
        # 添加额外人设信息（如果有）
        if self.age:
            profile["age"] = self.age
        if self.gender:
            profile["gender"] = self.gender
        if self.mbti:
            profile["mbti"] = self.mbti
        if self.country:
            profile["country"] = self.country
        if self.profession:
            profile["profession"] = self.profession
        if self.interested_topics:
            profile["interested_topics"] = self.interested_topics
        
        return profile
    
    def to_twitter_format(self) -> Dict[str, Any]:
        """转换为Twitter平台格式"""
        profile = {
            "user_id": self.user_id,
            "username": self.user_name,  # OASIS 库要求字段名为 username（无下划线）
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "friend_count": self.friend_count,
            "follower_count": self.follower_count,
            "statuses_count": self.statuses_count,
            "created_at": self.created_at,
        }
        
        # 添加额外人设信息
        if self.age:
            profile["age"] = self.age
        if self.gender:
            profile["gender"] = self.gender
        if self.mbti:
            profile["mbti"] = self.mbti
        if self.country:
            profile["country"] = self.country
        if self.profession:
            profile["profession"] = self.profession
        if self.interested_topics:
            profile["interested_topics"] = self.interested_topics
        
        return profile
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为完整字典格式"""
        data = {
            "user_id": self.user_id,
            "user_name": self.user_name,
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "karma": self.karma,
            "friend_count": self.friend_count,
            "follower_count": self.follower_count,
            "statuses_count": self.statuses_count,
            "age": self.age,
            "gender": self.gender,
            "mbti": self.mbti,
            "country": self.country,
            "profession": self.profession,
            "interested_topics": self.interested_topics,
            "source_entity_uuid": self.source_entity_uuid,
            "source_entity_type": self.source_entity_type,
            "created_at": self.created_at,
        }
        # NEXTSTEPS SIM_PERSONA_DESIGN: 结构化人格设计随档持久化到 other_info，
        # 供下游（决策通道/遥测）核查阵容的认知多样性；缺失即省略（旧 schema 不变）。
        if self.persona_design:
            data["other_info"] = {"persona_design": self.persona_design}
        return data


class OasisProfileGenerator:
    """
    OASIS Profile生成器
    
    将Zep图谱中的实体转换为OASIS模拟所需的Agent Profile
    
    优化特性：
    1. 调用Zep图谱检索功能获取更丰富的上下文
    2. 生成非常详细的人设（包括基本信息、职业经历、性格特征、社交媒体行为等）
    3. 区分个人实体和抽象群体实体
    """
    
    # MBTI类型列表
    MBTI_TYPES = [
        "INTJ", "INTP", "ENTJ", "ENTP",
        "INFJ", "INFP", "ENFJ", "ENFP",
        "ISTJ", "ISFJ", "ESTJ", "ESFJ",
        "ISTP", "ISFP", "ESTP", "ESFP"
    ]
    
    # 常见国家列表
    COUNTRIES = [
        "China", "US", "UK", "Japan", "Germany", "France", 
        "Canada", "Australia", "Brazil", "India", "South Korea"
    ]
    
    # 个人类型实体（需要生成具体人设）
    INDIVIDUAL_ENTITY_TYPES = [
        "student", "alumni", "professor", "person", "publicfigure", 
        "expert", "faculty", "official", "journalist", "activist"
    ]
    
    # 群体/机构类型实体（需要生成群体代表人设）
    GROUP_ENTITY_TYPES = [
        "university", "governmentagency", "organization", "ngo",
        "mediaoutlet", "company", "institution", "group", "community"
    ]

    # EXECPLAN2 I-1-5: 社会关系类边类型——用于 ego 网检索的 edge_types 过滤，
    # 让 persona 的「实际位置」只聚焦立场/结盟/对抗等关系边（而非随机事实边）。
    # 与 utils/actors.py 的关系类型表 / 本体 edge_types 命名保持一致。
    RELATIONSHIP_EDGE_TYPES = [
        "ALLY_OF", "OPPOSES", "COMPETES_WITH", "REGULATES",
        "DEPENDS_ON", "PARTNERS_WITH", "INFLUENCES",
    ]

    # NEXTSTEPS SIM_PERSONA_DESIGN: 结构化人格设计的字段契约。每个字段都必须是对该真实
    # 角色调研实证（档案立场/记忆、行为 DNA、关系网、局势简报）的提炼——禁止发明档案外的
    # 立场或事实。认知多样性来自真实角色本身的差异，而非人为指派的分析流派。
    PERSONA_DESIGN_KEYS = (
        "identity",               # 它是谁、在体系中的角色定位
        "views_beliefs",          # 世界观与对当前局势的解读（它认为什么是真的）
        "incentives",             # 不同结果下的得失、正在优化的目标函数
        "objectives",             # 目标与策略路径
        "relations",              # 盟友/对手/依赖对象及原因（据调研关系数据）
        "constraints_red_lines",  # 约束与红线
        "decision_style",         # 决策风格（如何权衡、行动节奏）
        "rhetoric",               # 典型公开话术特征
    )

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
        zep_api_key: Optional[str] = None,
        graph_id: Optional[str] = None,
        provider: Optional[str] = None,
        persona_language: Optional[str] = None
    ):
        # PREP-4(2): persona 语言口径。显式传入优先（'en'/'English' → 英文人设）；未传时，
        # 若显式设置了非中文活动画像（SIM_ACTIVITY_PROFILE=us_business/global_market），
        # personas 跟随英文。默认（不传 + 未设 env）→ None，提示词与产物逐字节不变。
        if persona_language is None:
            _env_profile = str(os.environ.get("SIM_ACTIVITY_PROFILE", "") or "").strip().lower()
            if _env_profile in ("us_business", "global_market"):
                persona_language = "en"
        self.persona_language = persona_language

        self.provider = (provider or Config.LLM_PROVIDER or "claude-cli").lower()
        self.api_key = api_key or Config.LLM_API_KEY
        self.base_url = base_url or Config.LLM_BASE_URL
        self.model_name = model_name or Config.LLM_MODEL_NAME

        # 统一通过 LLMClient 调用（claude-cli / codex-cli / openai）
        self.llm = LLMClient(
            provider=self.provider,
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model_name,
        )
        
        # Zep客户端用于检索丰富上下文
        self.zep_api_key = zep_api_key or Config.ZEP_API_KEY
        self.zep_client = None
        self.graph_id = graph_id
        
        if self.zep_api_key:
            try:
                self.zep_client = Zep(api_key=self.zep_api_key)
            except Exception as e:
                logger.warning(f"Zep客户端初始化失败: {e}")
    
    def generate_profile_from_entity(
        self,
        entity: EntityNode,
        user_id: int,
        use_llm: bool = True,
        actor: Optional[Dict[str, Any]] = None,
        actors: Optional[Dict[str, Any]] = None,
    ) -> OasisAgentProfile:
        """
        从Zep实体生成OASIS Agent Profile

        Args:
            entity: Zep实体节点
            user_id: 用户ID（用于OASIS）
            use_llm: 是否使用LLM生成详细人设
            actor: 深度研究 actors.json 中匹配到的结构化角色行（可选；
                   含 role/stance/influence/memory，注入人设提示词作为实证依据）
            actors: 完整研究档案对象（含 relationships[]），用于注入该 actor 的社会关系网（T3.1）

        Returns:
            OasisAgentProfile
        """
        entity_type = entity.get_entity_type() or "Entity"

        # 基础信息
        name = entity.name
        user_name = self._generate_username(name)

        # 构建上下文信息
        context = self._build_entity_context(entity)

        if use_llm:
            # 使用LLM生成详细人设
            profile_data = self._generate_profile_with_llm(
                entity_name=name,
                entity_type=entity_type,
                entity_summary=entity.summary,
                entity_attributes=entity.attributes,
                context=context,
                actor=actor,
                actors=actors,
            )
            # PREP-8: LLM 3 次尝试全败时 _generate_profile_with_llm 会静默退回规则模板，
            # 此前对任何健康门不可见——这里读出并剥离路径标记，落到 profile 上供聚合统计。
            generation_path = str(profile_data.pop("_generation_path", "llm") or "llm")
        else:
            # 使用规则生成基础人设（带研究 actor 覆盖）
            profile_data = self._generate_profile_rule_based(
                entity_name=name,
                entity_type=entity_type,
                entity_summary=entity.summary,
                entity_attributes=entity.attributes,
                actor=actor,
                actors=actors,
            )
            generation_path = "rule"

        # QUALITY-OPT C5: person-only demographics (age/gender/MBTI) are nonsensical on
        # organization/government/asset nodes (the corpus had Samsung profiled as "MBTI ISTJ,
        # age 30, country 中国"). For non-individual entities, drop them so the agent reads as the
        # institutional actor it is, not a fabricated person. Individuals keep them.
        # XRUN-10(c): country is intentionally KEPT for non-individuals — the prompt now derives
        # it from the entity's actual nationality/HQ (TSMC→台湾), which matters in geopolitics sims.
        is_individual = self._is_individual_entity(entity_type)
        age = profile_data.get("age") if is_individual else None
        gender = profile_data.get("gender") if is_individual else None
        mbti = profile_data.get("mbti") if is_individual else None

        # NEXTSTEPS SIM_PERSONA_DESIGN: 结构化人格设计从 profile_data 剥离到独立字段
        # （不留在自由 kv 里），非法形状（非 dict/空）→ None（产物 schema 不变）。
        persona_design = profile_data.pop("persona_design", None)
        if not isinstance(persona_design, dict) or not persona_design:
            persona_design = None

        return OasisAgentProfile(
            user_id=user_id,
            user_name=user_name,
            name=name,
            bio=profile_data.get("bio", f"{entity_type}: {name}"),
            persona=profile_data.get("persona", entity.summary or f"A {entity_type} named {name}."),
            karma=profile_data.get("karma", random.randint(500, 5000)),
            friend_count=profile_data.get("friend_count", random.randint(50, 500)),
            follower_count=profile_data.get("follower_count", random.randint(100, 1000)),
            statuses_count=profile_data.get("statuses_count", random.randint(100, 2000)),
            age=age,
            gender=gender,
            mbti=mbti,
            country=profile_data.get("country"),
            profession=profile_data.get("profession"),
            interested_topics=profile_data.get("interested_topics", []),
            source_entity_uuid=entity.uuid,
            source_entity_type=entity_type,
            generation_path=generation_path,
            persona_design=persona_design,
        )

    def _generate_username(self, name: str) -> str:
        """生成用户名"""
        # 移除特殊字符，转换为小写
        username = name.lower().replace(" ", "_")
        username = ''.join(c for c in username if c.isalnum() or c == '_')
        
        # 添加随机后缀避免重复
        suffix = random.randint(100, 999)
        return f"{username}_{suffix}"
    
    def _search_zep_for_entity(self, entity: EntityNode) -> Dict[str, Any]:
        """
        使用Zep图谱混合搜索功能获取实体相关的丰富信息
        
        Zep没有内置混合搜索接口，需要分别搜索edges和nodes然后合并结果。
        使用并行请求同时搜索，提高效率。
        
        Args:
            entity: 实体节点对象
            
        Returns:
            包含facts, node_summaries, context的字典
        """
        import concurrent.futures
        
        if not self.zep_client:
            return {"facts": [], "node_summaries": [], "context": ""}
        
        entity_name = entity.name
        
        results = {
            "facts": [],
            "node_summaries": [],
            "context": ""
        }
        
        # 必须有graph_id才能进行搜索
        if not self.graph_id:
            logger.debug(f"跳过Zep检索：未设置graph_id")
            return results
        
        comprehensive_query = f"关于{entity_name}的所有信息、活动、事件、关系和背景"

        # EXECPLAN2 F-5-4: 重试预算与退避改由 Config 驱动（默认收紧到 2 次/0.5s 起步），
        # 显著削减 Zep 抖动时的串行退避延迟；这是 best-effort 富集，失败即降级空上下文。
        # 缺失 Config 字段时回退旧行为（3 次 / 2.0s），保持向后兼容。
        max_retries = max(1, int(getattr(Config, "PROFILE_ZEP_SEARCH_MAX_RETRIES", 3)))
        base_delay = float(getattr(Config, "PROFILE_ZEP_SEARCH_RETRY_DELAY_SECONDS", 2.0))

        def search_edges():
            """搜索边（事实/关系）- 带重试机制"""
            last_exception = None
            delay = base_delay

            for attempt in range(max_retries):
                try:
                    return self.zep_client.graph.search(
                        query=comprehensive_query,
                        graph_id=self.graph_id,
                        limit=30,
                        scope="edges",
                        reranker="rrf"
                    )
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        logger.debug(f"Zep边搜索第 {attempt + 1} 次失败: {str(e)[:80]}, 重试中...")
                        time.sleep(delay)
                        delay *= 2
                    else:
                        logger.debug(f"Zep边搜索在 {max_retries} 次尝试后仍失败: {e}")
            return None

        def search_nodes():
            """搜索节点（实体摘要）- 带重试机制"""
            last_exception = None
            delay = base_delay

            for attempt in range(max_retries):
                try:
                    return self.zep_client.graph.search(
                        query=comprehensive_query,
                        graph_id=self.graph_id,
                        limit=20,
                        scope="nodes",
                        reranker="rrf"
                    )
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        logger.debug(f"Zep节点搜索第 {attempt + 1} 次失败: {str(e)[:80]}, 重试中...")
                        time.sleep(delay)
                        delay *= 2
                    else:
                        logger.debug(f"Zep节点搜索在 {max_retries} 次尝试后仍失败: {e}")
            return None
        
        try:
            # 并行执行edges和nodes搜索
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                edge_future = executor.submit(search_edges)
                node_future = executor.submit(search_nodes)
                
                # 获取结果
                edge_result = edge_future.result(timeout=30)
                node_result = node_future.result(timeout=30)
            
            # 处理边搜索结果
            all_facts = set()
            if edge_result and hasattr(edge_result, 'edges') and edge_result.edges:
                for edge in edge_result.edges:
                    if hasattr(edge, 'fact') and edge.fact:
                        all_facts.add(edge.fact)
            results["facts"] = list(all_facts)
            
            # 处理节点搜索结果
            all_summaries = set()
            if node_result and hasattr(node_result, 'nodes') and node_result.nodes:
                for node in node_result.nodes:
                    if hasattr(node, 'summary') and node.summary:
                        all_summaries.add(node.summary)
                    if hasattr(node, 'name') and node.name and node.name != entity_name:
                        all_summaries.add(f"相关实体: {node.name}")
            results["node_summaries"] = list(all_summaries)
            
            # 构建综合上下文
            context_parts = []
            if results["facts"]:
                context_parts.append("事实信息:\n" + "\n".join(f"- {f}" for f in results["facts"][:20]))
            if results["node_summaries"]:
                context_parts.append("相关实体:\n" + "\n".join(f"- {s}" for s in results["node_summaries"][:10]))
            results["context"] = "\n\n".join(context_parts)

            # EXECPLAN2 I-1-2: 给人设附上「群体身份」(in/out-group)，让 agent 能基于派系归属
            # 推理（图谱社区检测的产物）。默认关（GRAPH_COMMUNITY_RETRIEVAL）→ 不附加，逐字节一致。
            if getattr(Config, "GRAPH_COMMUNITY_RETRIEVAL", False):
                try:
                    grp = self._community_for_entity(entity_name)
                    if grp:
                        block = f"群体身份: 属于派系「{grp.get('name', '')}」。{(grp.get('summary') or '')[:300]}".strip()
                        results["context"] = (results["context"] + "\n\n" + block).strip()
                        results["community"] = grp.get("name")
                except Exception as e:
                    logger.debug(f"社区身份附加失败 ({entity_name}): {e}")

            logger.info(f"Zep混合检索完成: {entity_name}, 获取 {len(results['facts'])} 条事实, {len(results['node_summaries'])} 个相关节点")
            
        except concurrent.futures.TimeoutError:
            logger.warning(f"Zep检索超时 ({entity_name})")
        except Exception as e:
            logger.warning(f"Zep检索失败 ({entity_name}): {e}")

        return results

    def _community_for_entity(self, entity_name: str) -> Optional[Dict[str, Any]]:
        """EXECPLAN2 I-1-2: 找出实体所属社区（带实例缓存，避免每个实体都重新拉取全部社区）。

        匹配复用 actors.normalize_name 的规范化 + 双向包含（≥2 字符），与实体消解/actor 匹配
        口径一致。任一步失败/无果返回 None（调用方据此跳过群体身份块，无回归）。
        """
        if not entity_name or not self.graph_id:
            return None
        if getattr(self, "_communities_cache", None) is None:
            try:
                from .graphiti_client.runtime import get_runtime
                self._communities_cache = get_runtime().list_communities(self.graph_id) or []
            except Exception as e:
                logger.debug(f"社区列表获取失败: {e}")
                self._communities_cache = []
        from ..utils.actors import normalize_name
        target = normalize_name(entity_name)
        if not target:
            return None
        for c in self._communities_cache:
            for m in (c.get("members") or []):
                mn = normalize_name(m)
                if not mn:
                    continue
                if mn == target or (len(target) >= 2 and (target in mn or mn in target)):
                    return c
        return None

    def _resolve_entity_uuid(self, entity: EntityNode) -> Optional[str]:
        """EXECPLAN2 I-1-5: 解析实体到图谱节点 uuid（ego 网检索的中心节点）。

        优先用实体自带的 uuid（来自 ZepEntityReader 的筛选/枚举，已是规范节点）；
        缺失时退化到按名字搜索 nodes，取与实体名最匹配的 top 结果。任一步失败/无果
        返回 None —— 调用方据此降级回当前的名字关键词检索（无回归）。
        """
        # 实体通常已携带规范 uuid，直接复用，省一次网络往返。
        if getattr(entity, "uuid", None):
            return entity.uuid

        if not self.zep_client or not self.graph_id:
            return None

        try:
            node_result = self.zep_client.graph.search(
                query=entity.name,
                graph_id=self.graph_id,
                limit=5,
                scope="nodes",
                reranker="rrf",
            )
        except Exception as e:
            logger.debug(f"解析实体 uuid 失败（按名搜索 nodes）: {entity.name}: {str(e)[:80]}")
            return None

        nodes = getattr(node_result, "nodes", None) or []
        if not nodes:
            return None

        # 先取精确同名节点；否则退化到 top-1（reranker 已按相关度排序）。
        target = entity.name.strip().lower()
        for node in nodes:
            if (getattr(node, "name", "") or "").strip().lower() == target:
                uuid = getattr(node, "uuid_", None) or getattr(node, "uuid", None)
                if uuid:
                    return uuid
        top = nodes[0]
        return getattr(top, "uuid_", None) or getattr(top, "uuid", None)

    def _retrieve_ego_network(self, entity: EntityNode, center_uuid: str) -> List[str]:
        """EXECPLAN2 I-1-5: 以 center_node_uuid 为中心检索 ego 网（图距离重排）。

        相比 `_search_zep_for_entity` 的名字关键词检索，这里以解析出的节点 uuid 为中心
        做 node-distance 重排的边检索，并用 RELATIONSHIP_EDGE_TYPES 过滤，得到「最近的
        N 个邻居 + 连接它们的关系边」。同时会拾取模拟反馈写回的新关系边（静态 actors.json
        关系块拿不到的涌现拓扑）。返回已格式化的若干行，供 persona 提示词使用。

        注意：底层 search facade 当提案 1（center_node_uuid / search_filter 透传）落地后，
        这两个参数才真正生效并做图距离重排；在此之前 facade 会忽略它们（不报错），此路径
        默认关闭（PERSONA_EGO_RETRIEVAL=false），故不会引入回归。
        """
        if not self.zep_client or not self.graph_id or not center_uuid:
            return []

        # 复用 _search_zep_for_entity 的重试预算配置（保持一致的退避/超时行为）。
        max_retries = max(1, int(getattr(Config, "PROFILE_ZEP_SEARCH_MAX_RETRIES", 3)))
        base_delay = float(getattr(Config, "PROFILE_ZEP_SEARCH_RETRY_DELAY_SECONDS", 2.0))
        # 邻居上限（控制每实体一次额外检索的延迟/噪声）。缺失则默认 10。
        max_neighbors = max(1, int(getattr(Config, "PERSONA_EGO_MAX_NEIGHBORS", 10)))

        search_filter = {"edge_types": self.RELATIONSHIP_EDGE_TYPES}
        last_exception = None
        delay = base_delay
        edge_result = None

        for attempt in range(max_retries):
            try:
                edge_result = self.zep_client.graph.search(
                    query=entity.name,
                    graph_id=self.graph_id,
                    limit=max_neighbors,
                    scope="edges",
                    reranker="node_distance",          # 提案 1 落地后生效：按图距离重排
                    center_node_uuid=center_uuid,       # 提案 1 落地后生效：ego 网中心
                    search_filter=search_filter,        # 仅保留关系类边
                )
                break
            except Exception as e:
                last_exception = e
                if attempt < max_retries - 1:
                    logger.debug(
                        f"Zep ego 网检索第 {attempt + 1} 次失败: {str(e)[:80]}, 重试中..."
                    )
                    time.sleep(delay)
                    delay *= 2
                else:
                    logger.debug(f"Zep ego 网检索在 {max_retries} 次尝试后仍失败: {e}")

        if edge_result is None:
            if last_exception is not None:
                logger.debug(f"ego 网检索降级（{entity.name}）: {last_exception}")
            return []

        edges = getattr(edge_result, "edges", None) or []
        center_name = entity.name.strip()
        lines: List[str] = []
        seen = set()
        for edge in edges:
            fact = (getattr(edge, "fact", "") or "").strip()
            edge_name = (getattr(edge, "name", "") or "").strip()
            line = fact if fact else (
                f"{center_name} --[{edge_name}]--> (关系网邻居)" if edge_name else ""
            )
            if not line or line in seen:
                continue
            seen.add(line)
            lines.append(line)
            if len(lines) >= max_neighbors:
                break

        if lines:
            logger.info(
                f"ego 网检索完成: {center_name}, 获取 {len(lines)} 条关系网邻居边"
            )
        return lines

    def _build_entity_context(self, entity: EntityNode) -> str:
        """
        构建实体的完整上下文信息
        
        包括：
        1. 实体本身的边信息（事实）
        2. 关联节点的详细信息
        3. Zep混合检索到的丰富信息
        """
        context_parts = []
        
        # 1. 添加实体属性信息
        if entity.attributes:
            attrs = []
            for key, value in entity.attributes.items():
                if value and str(value).strip():
                    attrs.append(f"- {key}: {value}")
            if attrs:
                context_parts.append("### 实体属性\n" + "\n".join(attrs))
        
        # 2. 添加相关边信息（事实/关系）
        existing_facts = set()
        if entity.related_edges:
            # T3.1: 用 related_nodes 把边端点 uuid 解析回真实邻居名 —— 修复原先在缺少自由文本
            # fact 时输出字面 "(相关实体)" 的名称黑洞（让 persona 看到的是 "X 监管 某大学" 而非
            # "X 监管 (相关实体)"）。related_nodes 已随实体携带 uuid/name/labels。
            nb = {n.get("uuid"): n for n in (entity.related_nodes or [])}
            relationships = []
            for edge in entity.related_edges:  # 不限制数量
                fact = edge.get("fact", "")
                edge_name = edge.get("edge_name", "")
                direction = edge.get("direction", "")

                if fact:
                    relationships.append(f"- {fact}")
                    existing_facts.add(fact)
                elif edge_name:
                    nbr = nb.get(edge.get("target_node_uuid")) or nb.get(edge.get("source_node_uuid")) or {}
                    nbr_name = nbr.get("name") or "(未知实体)"
                    nbr_label = next(
                        (l for l in (nbr.get("labels") or []) if l not in ("Entity", "Node")), ""
                    )
                    nbr_disp = f"{nbr_name}（{nbr_label}）" if nbr_label else nbr_name
                    if direction == "outgoing":
                        relationships.append(f"- {entity.name} --[{edge_name}]--> {nbr_disp}")
                    else:
                        relationships.append(f"- {nbr_disp} --[{edge_name}]--> {entity.name}")

            if relationships:
                context_parts.append("### 相关事实和关系\n" + "\n".join(relationships))
        
        # 3. 添加关联节点的详细信息
        if entity.related_nodes:
            related_info = []
            for node in entity.related_nodes:  # 不限制数量
                node_name = node.get("name", "")
                node_labels = node.get("labels", [])
                node_summary = node.get("summary", "")
                
                # 过滤掉默认标签
                custom_labels = [l for l in node_labels if l not in ["Entity", "Node"]]
                label_str = f" ({', '.join(custom_labels)})" if custom_labels else ""
                
                if node_summary:
                    related_info.append(f"- **{node_name}**{label_str}: {node_summary}")
                else:
                    related_info.append(f"- **{node_name}**{label_str}")
            
            if related_info:
                context_parts.append("### 关联实体信息\n" + "\n".join(related_info))
        
        # 4. 使用Zep混合检索获取更丰富的信息
        # EXECPLAN2 F-5-4: 实体已自带 related_edges/related_nodes 上下文时跳过这次冗余的 Zep 二次检索
        # （省掉每实体一次网络往返 + 嵌套线程池 + 抖动时的串行退避）。默认开启（PROFILE_ZEP_SKIP_WHEN_CONTEXT=true）；
        # 关闭或冷上下文（既无边也无邻居节点）时仍走原有检索路径，保持富集能力不降级。
        skip_zep = bool(getattr(Config, "PROFILE_ZEP_SKIP_WHEN_CONTEXT", False)) and bool(
            entity.related_edges or entity.related_nodes
        )
        if skip_zep:
            zep_results = {"facts": [], "node_summaries": [], "context": ""}
            logger.debug(f"跳过Zep二次检索（实体已自带上下文）: {entity.name}")
        else:
            zep_results = self._search_zep_for_entity(entity)

        if zep_results.get("facts"):
            # 去重：排除已存在的事实
            new_facts = [f for f in zep_results["facts"] if f not in existing_facts]
            if new_facts:
                context_parts.append("### Zep检索到的事实信息\n" + "\n".join(f"- {f}" for f in new_facts[:15]))
        
        if zep_results.get("node_summaries"):
            context_parts.append("### Zep检索到的相关节点\n" + "\n".join(f"- {s}" for s in zep_results["node_summaries"][:10]))

        # 5. EXECPLAN2 I-1-5: ego 网检索——以解析出的节点 uuid 为中心做图距离重排的关系边检索，
        # 把「你在关系网中的实际位置」（最近邻居 + 关系边，含模拟反馈写回的涌现边）注入 persona，
        # 与上面静态的研究关系块互补。默认关闭（PERSONA_EGO_RETRIEVAL=false）以满足可降级不变式：
        # 关闭、解析不到 uuid、或检索无果时均回退到当前行为，无回归。
        if bool(getattr(Config, "PERSONA_EGO_RETRIEVAL", False)):
            try:
                center_uuid = self._resolve_entity_uuid(entity)
                if center_uuid:
                    ego_lines = self._retrieve_ego_network(entity, center_uuid)
                    if ego_lines:
                        context_parts.append(
                            "## 你在关系网中的实际位置\n"
                            + "\n".join(f"- {line}" for line in ego_lines)
                        )
                else:
                    logger.debug(f"ego 网检索跳过（未解析到节点 uuid）: {entity.name}")
            except Exception as e:
                # best-effort 富集，失败即降级，绝不影响主流程。
                logger.debug(f"ego 网检索异常（{entity.name}）: {str(e)[:80]}")

        return "\n\n".join(context_parts)
    
    def _english_persona(self) -> bool:
        """PREP-4(2): 本次 persona 是否用英文口径（提示词语言 + country 缺省）。

        __new__ 构造的实例（测试）无 persona_language 属性 → getattr 缺省 → False，行为不变。
        """
        lang = str(getattr(self, "persona_language", "") or "").strip().lower()
        return lang.startswith("en")

    def _is_individual_entity(self, entity_type: str) -> bool:
        """判断是否是个人类型实体"""
        return entity_type.lower() in self.INDIVIDUAL_ENTITY_TYPES
    
    def _is_group_entity(self, entity_type: str) -> bool:
        """判断是否是群体/机构类型实体"""
        return entity_type.lower() in self.GROUP_ENTITY_TYPES

    def _persona_design_enabled(self) -> bool:
        """NEXTSTEPS SIM_PERSONA_DESIGN: 逐角色情境工程开关（默认开）。

        config.py 归另一分桶所有——用 getattr 读取（字段不存在 → 默认 True）；
        置 false 时提示词与产物与旧版逐字节一致（可降级不变式）。
        """
        raw = getattr(Config, "SIM_PERSONA_DESIGN", True)
        return str(raw).strip().lower() not in ("false", "0", "no", "off")

    def _build_persona_design_block(self) -> str:
        """NEXTSTEPS SIM_PERSONA_DESIGN: 人格设计指令块（拼在提示词尾部，与实证档案同区）。

        该实体是真实世界的行动者（如国家/公司/政府/机构/个人）——要求同一次 LLM 调用把
        调研档案里关于它的一切提炼为结构化 persona_design，并让 persona 正文体现这份设计。
        """
        return (
            "## 人格设计（逐角色情境工程，全部字段必须落在上方调研实证之内）\n"
            "该实体是现实世界中的真实行动者。除上述字段外，额外输出一个 persona_design 对象，"
            "把调研档案中关于它的一切提炼为结构化人格设计，字段（每项为 1-3 句纯文本字符串）：\n"
            "- identity: 它是谁、在这个体系中的角色定位\n"
            "- views_beliefs: 它的世界观与对当前局势的解读（它认为什么是真的）\n"
            "- incentives: 它在不同结果下的得与失、正在优化的目标函数\n"
            "- objectives: 它的目标与策略路径\n"
            "- relations: 盟友/对手/依赖对象及其原因（严格依据上方关系网实证）\n"
            "- constraints_red_lines: 它受到的约束与不可逾越的红线\n"
            "- decision_style: 决策风格（如何权衡取舍、行动节奏）\n"
            "- rhetoric: 典型公开话术特征\n"
            "铁律：只允许提炼上方调研实证（实证档案/行为DNA/关系网/局势简报），"
            "绝不发明档案之外的立场或事实；立场以实证档案为准。persona 正文必须体现（embody）"
            "这份设计——以该角色自己的视角写出它的身份、信念、得失、目标、关系、红线、"
            "决策风格与话术。"
        )

    def _normalize_persona_design(
        self,
        raw: Any,
        actor: Optional[Dict[str, Any]],
        entity_name: str,
        actors: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, str]]:
        """把 LLM 产出的 persona_design 规整为字段契约。

        规则：只保留 PERSONA_DESIGN_KEYS 白名单键；list → '；' 连接；空值/未知键丢弃。
        LLM 未产出或产出为空 → 退回 _design_from_actor 的规则提炼（保证有档案的
        真实角色必有设计，degrade-safe）。
        """
        out: Dict[str, str] = {}
        if isinstance(raw, dict):
            for key in self.PERSONA_DESIGN_KEYS:
                val = raw.get(key)
                if isinstance(val, list):
                    val = "；".join(str(x).strip() for x in val if str(x).strip())
                text = str(val or "").strip()
                if text:
                    out[key] = text
        if out:
            return out
        return self._design_from_actor(actor, entity_name, actors=actors)

    def _design_from_actor(
        self,
        actor: Optional[Dict[str, Any]],
        entity_name: str,
        actors: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, str]]:
        """从调研档案确定性提炼 persona_design（无 LLM 调用，规则回退路径也可用）。

        逐字段映射档案实证：role/worldview.identity → identity；stance/beliefs/frame →
        views_beliefs；incentives 的 driver/gains_if/loses_if → incentives；goals →
        objectives；roster_block（去掉标题行）→ relations；constraints/vulnerabilities →
        constraints_red_lines；risk_tolerance → decision_style；stated_vs_revealed →
        rhetoric。档案为空或全字段缺失 → None。
        """
        if not isinstance(actor, dict) or not actor:
            return None

        def _joined(value: Any, limit: int = 4) -> str:
            if isinstance(value, list):
                return "；".join(str(x).strip() for x in value[:limit] if str(x).strip())
            return str(value or "").strip()

        worldview = actor.get("worldview") if isinstance(actor.get("worldview"), dict) else {}

        identity = "；".join(s for s in (
            str(actor.get("role", "") or "").strip(),
            str(worldview.get("identity", "") or "").strip(),
        ) if s)

        views = "；".join(s for s in (
            str(actor.get("stance", "") or "").strip(),
            _joined(worldview.get("beliefs")),
            str(worldview.get("frame", "") or "").strip(),
        ) if s)

        incentive_parts: List[str] = []
        raw_incentives = actor.get("incentives")
        for inc in (raw_incentives if isinstance(raw_incentives, list) else [])[:4]:
            if isinstance(inc, dict):
                driver = str(inc.get("driver", "") or "").strip()
                gains = str(inc.get("gains_if", "") or "").strip()
                loses = str(inc.get("loses_if", "") or "").strip()
                bits = [b for b in (
                    driver,
                    f"得益于「{gains}」" if gains else "",
                    f"受损于「{loses}」" if loses else "",
                ) if b]
                if bits:
                    incentive_parts.append("，".join(bits))
            else:
                text = str(inc or "").strip()
                if text:
                    incentive_parts.append(text)

        relations = ""
        if actors is not None:
            try:
                roster = roster_block(entity_name, actors)
                if roster and "\n" in roster:
                    # 去掉「## 关系网角色…」标题行，压成一行紧凑关系描述。
                    relations = roster.split("\n", 1)[1].replace("- ", "").replace("\n", "；")
            except Exception:  # noqa: BLE001 — 关系提炼失败不影响其余字段
                relations = ""

        constraints = "；".join(s for s in (
            _joined(actor.get("constraints"), 3),
            _joined(actor.get("vulnerabilities"), 3),
        ) if s)

        risk = str(actor.get("risk_tolerance", "") or "").strip()
        design = {
            "identity": identity,
            "views_beliefs": views,
            "incentives": "；".join(incentive_parts),
            "objectives": _joined(actor.get("goals")),
            "relations": relations,
            "constraints_red_lines": constraints,
            "decision_style": f"风险偏好：{risk}" if risk else "",
            "rhetoric": str(actor.get("stated_vs_revealed", "") or "").strip(),
        }
        design = {k: v for k, v in design.items() if v}
        return design or None

    def _generate_profile_with_llm(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str,
        actor: Optional[Dict[str, Any]] = None,
        actors: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        使用LLM生成非常详细的人设

        根据实体类型区分：
        - 个人实体：生成具体的人物设定
        - 群体/机构实体：生成代表性账号设定
        """

        is_individual = self._is_individual_entity(entity_type)

        # 深度研究实证档案：作为最高优先级上下文拼到提示词尾部，
        # 让 persona 的立场/记忆以真实调研数据为准（而非凭报告行文再猜）。
        actor_block = actor_briefing(actor)
        # T3.1: 该 actor 的社会关系网（命名真实盟友/对手），让 persona 在互动时
        # 知道该 @ 谁、与谁结盟/对立——联盟形成不再是随机涌现。
        rel_block = relationship_briefing(entity_name, actors)
        # C6: 行为 DNA（价值观/信念/激励得失/资源/风险偏好）+ 结构化关系网角色（按盟友/
        # 对手/客户/供应商/出资方等分桶命名真实对手方），让 persona 提示词从「立场标签」升级为
        # GEMINI 式的「你是 X；你的激励是…；你相信…；你对立 Y 并依赖 Z」。受 PERSONA_BEHAVIORAL_DNA
        # 控制（默认开）。CRITICAL: 当 actor 不带任何新字段时（今天的数据），两个 helper 均返回空串，
        # 拼接为 no-op，提示词与今日逐字节一致。
        dna_block = ""
        roster_struct_block = ""
        if getattr(Config, "PERSONA_BEHAVIORAL_DNA", True):
            dna_block = behavioral_dna_block(actor)
            roster_struct_block = roster_block(entity_name, actors)

        if is_individual:
            prompt = self._build_individual_persona_prompt(
                entity_name, entity_type, entity_summary, entity_attributes, context
            )
        else:
            prompt = self._build_group_persona_prompt(
                entity_name, entity_type, entity_summary, entity_attributes, context
            )
        # T3.9(5): 共享局势基线——把局势简报的「当前态势 + 张力」压缩成一行前置到所有 persona，
        # 让全体 Agent 共享同一份事实背景（而非各自从摘要里臆测情境）。缺失则跳过。
        if isinstance(actors, dict):
            sb = actors.get("situation_brief")
            if isinstance(sb, dict):
                cs = str(sb.get("current_situation") or "").strip()
                dy = str(sb.get("dynamics") or "").strip()
                seg = " ".join(s for s in (cs, dy) if s)[:600]
                if seg:
                    prompt = f"【共同背景·局势简报（调研实证）】{seg}\n\n" + prompt

        # 关系网与实证档案都拼在提示词尾部（不进入 context[:3000] 截断区），hub actor 的关系块永不被截。
        # C6: 行为 DNA（人格底座）先于关系块注入——让 persona 先确立稳定的价值观/激励/风险偏好，
        # 再叠加「你与谁结盟/对立/供货」的关系网角色；二者均在 actor 不带新字段时为空串（no-op）。
        if dna_block:
            prompt += f"\n\n{dna_block}"
        if roster_struct_block:
            prompt += f"\n\n{roster_struct_block}"
        if rel_block:
            prompt += f"\n\n{rel_block}"
        if actor_block:
            prompt += (
                f"\n\n{actor_block}\n"
                "上述实证档案与其它上下文冲突时，以实证档案为准："
                "persona 的「立场观点」必须与档案立场一致，"
                "「个人记忆/机构记忆」必须涵盖档案中的已知事实/记忆。"
            )

        # NEXTSTEPS SIM_PERSONA_DESIGN: 对匹配到调研档案的真实角色做逐角色情境工程——同一次
        # LLM 调用里额外产出结构化 persona_design，并要求 persona 正文体现这份设计。认知多样性
        # 来自真实角色本身的差异（各自的调研档案），而非人为指派的分析流派。无档案/开关关闭 →
        # 不拼指令块，提示词与产物与旧版逐字一致（可降级不变式）。
        design_requested = (
            self._persona_design_enabled() and isinstance(actor, dict) and bool(actor)
        )
        if design_requested:
            prompt += f"\n\n{self._build_persona_design_block()}"

        # 尝试多次生成，直到成功或达到最大重试次数
        max_attempts = 3
        last_error = None
        
        for attempt in range(max_attempts):
            try:
                content = self.llm.chat(
                    messages=[
                        {"role": "system", "content": self._get_system_prompt(is_individual)},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.7 - (attempt * 0.1),  # 每次重试降低温度
                )

                # 尝试解析JSON
                try:
                    result = json.loads(content)
                    
                    # 验证必需字段
                    if "bio" not in result or not result["bio"]:
                        result["bio"] = entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}"
                    if "persona" not in result or not result["persona"]:
                        result["persona"] = entity_summary or f"{entity_name}是一个{entity_type}。"

                    # NEXTSTEPS SIM_PERSONA_DESIGN: 规整结构化设计（白名单键/list 拼接/空值剔除）；
                    # LLM 漏产 → 从档案确定性提炼兜底。未请求设计时剔除幻觉键，保持旧 schema。
                    if design_requested:
                        result["persona_design"] = self._normalize_persona_design(
                            result.get("persona_design"), actor, entity_name, actors=actors
                        )
                    else:
                        result.pop("persona_design", None)

                    return result

                except json.JSONDecodeError as je:
                    logger.warning(f"JSON解析失败 (attempt {attempt+1}): {str(je)[:80]}")

                    # 尝试修复JSON
                    result = self._try_fix_json(content, entity_name, entity_type, entity_summary)
                    if result.get("_fixed"):
                        del result["_fixed"]
                        # NEXTSTEPS SIM_PERSONA_DESIGN: 修复路径同样规整/兜底结构化设计。
                        if design_requested:
                            result["persona_design"] = self._normalize_persona_design(
                                result.get("persona_design"), actor, entity_name, actors=actors
                            )
                        else:
                            result.pop("persona_design", None)
                        return result
                    
                    last_error = je
                    
            except Exception as e:
                logger.warning(f"LLM调用失败 (attempt {attempt+1}): {str(e)[:80]}")
                last_error = e
                import time
                time.sleep(1 * (attempt + 1))  # 指数退避
        
        logger.warning(f"LLM生成人设失败（{max_attempts}次尝试）: {last_error}, 使用规则生成")
        # PREP-8: 标记静默降级路径，由 generate_profile_from_entity 剥离并聚合到运行级统计。
        fallback_data = self._generate_profile_rule_based(
            entity_name, entity_type, entity_summary, entity_attributes,
            actor=actor, actors=actors,
        )
        fallback_data["_generation_path"] = "rule_fallback"
        return fallback_data
    
    def _fix_truncated_json(self, content: str) -> str:
        """修复被截断的JSON（输出被max_tokens限制截断）"""
        import re
        
        # 如果JSON被截断，尝试闭合它
        content = content.strip()
        
        # 计算未闭合的括号
        open_braces = content.count('{') - content.count('}')
        open_brackets = content.count('[') - content.count(']')
        
        # 检查是否有未闭合的字符串
        # 简单检查：如果最后一个引号后没有逗号或闭合括号，可能是字符串被截断
        if content and content[-1] not in '",}]':
            # 尝试闭合字符串
            content += '"'
        
        # 闭合括号
        content += ']' * open_brackets
        content += '}' * open_braces
        
        return content
    
    def _try_fix_json(self, content: str, entity_name: str, entity_type: str, entity_summary: str = "") -> Dict[str, Any]:
        """尝试修复损坏的JSON"""
        import re
        
        # 1. 首先尝试修复被截断的情况
        content = self._fix_truncated_json(content)
        
        # 2. 尝试提取JSON部分
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            json_str = json_match.group()
            
            # 3. 处理字符串中的换行符问题
            # 找到所有字符串值并替换其中的换行符
            def fix_string_newlines(match):
                s = match.group(0)
                # 替换字符串内的实际换行符为空格
                s = s.replace('\n', ' ').replace('\r', ' ')
                # 替换多余空格
                s = re.sub(r'\s+', ' ', s)
                return s
            
            # 匹配JSON字符串值
            json_str = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', fix_string_newlines, json_str)
            
            # 4. 尝试解析
            try:
                result = json.loads(json_str)
                result["_fixed"] = True
                return result
            except json.JSONDecodeError as e:
                # 5. 如果还是失败，尝试更激进的修复
                try:
                    # 移除所有控制字符
                    json_str = re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', json_str)
                    # 替换所有连续空白
                    json_str = re.sub(r'\s+', ' ', json_str)
                    result = json.loads(json_str)
                    result["_fixed"] = True
                    return result
                except:
                    pass
        
        # 6. 尝试从内容中提取部分信息
        bio_match = re.search(r'"bio"\s*:\s*"([^"]*)"', content)
        persona_match = re.search(r'"persona"\s*:\s*"([^"]*)', content)  # 可能被截断
        
        bio = bio_match.group(1) if bio_match else (entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}")
        persona = persona_match.group(1) if persona_match else (entity_summary or f"{entity_name}是一个{entity_type}。")
        
        # 如果提取到了有意义的内容，标记为已修复
        if bio_match or persona_match:
            logger.info(f"从损坏的JSON中提取了部分信息")
            return {
                "bio": bio,
                "persona": persona,
                "_fixed": True
            }
        
        # 7. 完全失败，返回基础结构
        logger.warning(f"JSON修复失败，返回基础结构")
        return {
            "bio": entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}",
            "persona": entity_summary or f"{entity_name}是一个{entity_type}。"
        }
    
    def _get_system_prompt(self, is_individual: bool) -> str:
        """获取系统提示词"""
        # PREP-4(2): 英文口径下人设语言与调研语言一致，避免中文 persona 驱动英文帖子的错位；
        # 默认（非英文口径）与历史措辞逐字节一致。
        if self._english_persona():
            return (
                "你是社交媒体用户画像生成专家。生成详细、真实的人设用于舆论模拟,最大程度还原已有现实情况。"
                "必须返回有效的JSON格式，所有字符串值不能包含未转义的换行符。"
                "人设文本与调研语言一致：本次使用英文（English）撰写 bio/persona。"
            )
        base_prompt = "你是社交媒体用户画像生成专家。生成详细、真实的人设用于舆论模拟,最大程度还原已有现实情况。必须返回有效的JSON格式，所有字符串值不能包含未转义的换行符。使用中文。"
        return base_prompt
    
    def _build_individual_persona_prompt(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str
    ) -> str:
        """构建个人实体的详细人设提示词"""

        attrs_str = json.dumps(entity_attributes, ensure_ascii=False) if entity_attributes else "无"
        context_str = context[:3000] if context else "无额外上下文"

        # XRUN-10(a/b): 结构化字段从档案/上下文抽取而非套默认值（Lutnick 档案写明 ENTJ 却落
        # ISTJ；US Congress 落 country 中国）。示例国家去掉「中国」锚定；无法推断的字段省略、
        # 不编造。PERSONA_FIELD_EXTRACTION=false → 回到旧措辞。
        if getattr(Config, "PERSONA_FIELD_EXTRACTION", True):
            demo_fields = (
                '3. age: 年龄数字（整数；优先从档案/上下文推断，无法推断则省略，不要编造）\n'
                '4. gender: 性别，必须是英文: "male" 或 "female"（无法判断则省略）\n'
                '5. mbti: MBTI类型（仅当档案/上下文可推断该人物性格时填写，如档案写明"MBTI偏向ENTJ"则填ENTJ；无法推断则省略，不要编造）\n'
                '6. country: 国家/地区（填该人物的实际国籍或常驻地，从档案/上下文推断；无法判断则省略，不要编造）'
            )
            demo_rule = '- 给出的age必须是有效整数，gender必须是"male"或"female"；无依据的字段宁可省略，不要编造'
        else:
            demo_fields = (
                '3. age: 年龄数字（必须是整数）\n'
                '4. gender: 性别，必须是英文: "male" 或 "female"\n'
                '5. mbti: MBTI类型（如INTJ、ENFP等）\n'
                '6. country: 国家（使用中文，如"中国"）'
            )
            demo_rule = '- age必须是有效的整数，gender必须是"male"或"female"'

        # PREP-4(2): 英文口径下 bio/persona 与调研语言一致；默认与历史措辞逐字一致。
        lang_rule = (
            "- bio/persona 使用英文撰写（与调研语言一致；gender字段用英文male/female）"
            if self._english_persona()
            else "- 使用中文（除了gender字段必须用英文male/female）"
        )

        return f"""为实体生成详细的社交媒体用户人设,最大程度还原已有现实情况。

实体名称: {entity_name}
实体类型: {entity_type}
实体摘要: {entity_summary}
实体属性: {attrs_str}

上下文信息:
{context_str}

请生成JSON，包含以下字段:

1. bio: 社交媒体简介，200字
2. persona: 详细人设描述（2000字的纯文本），需包含:
   - 基本信息（年龄、职业、教育背景、所在地）
   - 人物背景（重要经历、与事件的关联、社会关系）
   - 性格特征（MBTI类型、核心性格、情绪表达方式）
   - 社交媒体行为（发帖频率、内容偏好、互动风格、语言特点）
   - 立场观点（对话题的态度、可能被激怒/感动的内容）
   - 独特特征（口头禅、特殊经历、个人爱好）
   - 个人记忆（人设的重要部分，要介绍这个个体与事件的关联，以及这个个体在事件中的已有动作与反应）
{demo_fields}
7. profession: 职业
8. interested_topics: 感兴趣话题数组

可选字段（若上下文足以推断则补充，否则省略，不要编造）:
- values: 核心价值观数组（驱动其判断的底层价值，如"言论自由""国家安全"）
- beliefs: 核心信念数组（其对该议题的基本预设/世界观）
- incentives: 激励结构数组，每项为对象 {{"driver": 动机, "gains_if": 何事得益, "loses_if": 何事受损}}

重要:
- 所有字段值必须是字符串或数字，不要使用换行符
- persona必须是一段连贯的文字描述
{lang_rule}
- 内容要与实体信息保持一致
{demo_rule}
"""

    def _build_group_persona_prompt(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str
    ) -> str:
        """构建群体/机构实体的详细人设提示词"""

        attrs_str = json.dumps(entity_attributes, ensure_ascii=False) if entity_attributes else "无"
        context_str = context[:3000] if context else "无额外上下文"

        # XRUN-10(b/c): 机构 country 按总部/主要管辖地推断（TSMC→台湾），不再以「中国」示例
        # 锚定。PERSONA_FIELD_EXTRACTION=false → 回到旧措辞。
        country_field = (
            '6. country: 机构总部或主要管辖所在国家/地区（按实际情况从档案/上下文推断；无法判断则省略，不要编造）'
            if getattr(Config, "PERSONA_FIELD_EXTRACTION", True)
            else '6. country: 国家（使用中文，如"中国"）'
        )
        # PREP-4(2): 英文口径下 bio/persona 与调研语言一致；默认与历史措辞逐字一致。
        lang_rule = (
            '- bio/persona 使用英文撰写（与调研语言一致；gender字段固定英文"other"）'
            if self._english_persona()
            else '- 使用中文（除了gender字段必须用英文"other"）'
        )

        return f"""为机构/群体实体生成详细的社交媒体账号设定,最大程度还原已有现实情况。

实体名称: {entity_name}
实体类型: {entity_type}
实体摘要: {entity_summary}
实体属性: {attrs_str}

上下文信息:
{context_str}

请生成JSON，包含以下字段:

1. bio: 官方账号简介，200字，专业得体
2. persona: 详细账号设定描述（2000字的纯文本），需包含:
   - 机构基本信息（正式名称、机构性质、成立背景、主要职能）
   - 账号定位（账号类型、目标受众、核心功能）
   - 发言风格（语言特点、常用表达、禁忌话题）
   - 发布内容特点（内容类型、发布频率、活跃时间段）
   - 立场态度（对核心话题的官方立场、面对争议的处理方式）
   - 特殊说明（代表的群体画像、运营习惯）
   - 机构记忆（机构人设的重要部分，要介绍这个机构与事件的关联，以及这个机构在事件中的已有动作与反应）
3. age: 固定填30（机构账号的虚拟年龄）
4. gender: 固定填"other"（机构账号使用other表示非个人）
5. mbti: MBTI类型，用于描述账号风格，如ISTJ代表严谨保守
{country_field}
7. profession: 机构职能描述
8. interested_topics: 关注领域数组

可选字段（若上下文足以推断则补充，否则省略，不要编造）:
- values: 机构核心价值观数组（如"行业自律""公共利益"）
- beliefs: 机构对该议题的官方信念/立场预设数组
- incentives: 激励结构数组，每项为对象 {{"driver": 动机, "gains_if": 何事得益, "loses_if": 何事受损}}

重要:
- 所有字段值必须是字符串或数字，不允许null值
- persona必须是一段连贯的文字描述，不要使用换行符
{lang_rule}
- age必须是整数30，gender必须是字符串"other"
- 机构账号发言要符合其身份定位"""
    
    def _generate_profile_rule_based(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        actor: Optional[Dict[str, Any]] = None,
        actors: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """规则人设；当匹配到深度研究 actor 时，用调研实证（角色/立场/动机/社会关系网/
        影响力）覆盖通用模板，让非 LLM 路径也产出有据可依的角色，而非泛泛的「业内专家」。

        actor 缺失时行为与旧实现完全一致（返回纯类型模板）。
        """
        base = self._rule_based_base(entity_name, entity_type, entity_summary, entity_attributes)
        if not isinstance(actor, dict) or not actor:
            return base

        overlay: Dict[str, Any] = {}
        role = str(actor.get("role", "") or "").strip()
        stance = str(actor.get("stance", "") or "").strip()
        if role or stance:
            overlay["bio"] = (f"{role}；立场：{stance}" if (role and stance) else (role or stance))[:200]

        # persona 叠加：调研档案（角色/立场/动机/软肋）+ 社会关系网（命名真实对手方）。
        brief = actor_briefing(actor)
        rels = relationship_briefing(entity_name, actors) if actors else ""
        grounded = "\n\n".join(p for p in (brief, rels) if p)
        if grounded:
            base_persona = str(base.get("persona", "") or "").strip()
            overlay["persona"] = (base_persona + "\n\n" + grounded).strip() if base_persona else grounded

        # 影响力 → follower/karma 确定性缩放（取代纯随机），让研究的高影响力角色在网络中更突出。
        iw = influence_weight(actor)
        if iw:
            overlay["follower_count"] = int(300 * iw)
            overlay["friend_count"] = int(120 * iw)
            overlay["karma"] = int(1500 * iw)

        # NEXTSTEPS SIM_PERSONA_DESIGN: 规则路径（含 LLM 3 连败回退）不丢人格设计——从档案
        # 确定性提炼结构化设计，并在 persona 末尾追加 1-2 句设计摘要（身份 + 得失/信念线），
        # 让该真实角色的分析身份在降级路径下依然进入 system prompt。
        if self._persona_design_enabled():
            design = self._design_from_actor(actor, entity_name, actors=actors)
            if design:
                overlay["persona_design"] = design
                summary = "。".join(s for s in (
                    design.get("identity", ""),
                    design.get("incentives", "") or design.get("views_beliefs", ""),
                ) if s)
                if summary:
                    persona_text = str(overlay.get("persona") or base.get("persona") or "").strip()
                    overlay["persona"] = (
                        f"{persona_text}\n\n【人格设计·实证提炼】{summary}。"
                    ).strip()

        return {**base, **overlay}

    def _rule_based_base(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any]
    ) -> Dict[str, Any]:
        """使用规则生成基础人设（纯类型模板，不含研究覆盖）"""

        # 根据实体类型生成不同的人设
        entity_type_lower = entity_type.lower()
        
        if entity_type_lower in ["student", "alumni"]:
            return {
                "bio": f"{entity_type} with interests in academics and social issues.",
                "persona": f"{entity_name} is a {entity_type.lower()} who is actively engaged in academic and social discussions. They enjoy sharing perspectives and connecting with peers.",
                "age": random.randint(18, 30),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(self.MBTI_TYPES),
                "country": random.choice(self.COUNTRIES),
                "profession": "Student",
                "interested_topics": ["Education", "Social Issues", "Technology"],
            }
        
        elif entity_type_lower in ["publicfigure", "expert", "faculty"]:
            return {
                "bio": f"Expert and thought leader in their field.",
                "persona": f"{entity_name} is a recognized {entity_type.lower()} who shares insights and opinions on important matters. They are known for their expertise and influence in public discourse.",
                "age": random.randint(35, 60),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(["ENTJ", "INTJ", "ENTP", "INTP"]),
                "country": random.choice(self.COUNTRIES),
                "profession": entity_attributes.get("occupation", "Expert"),
                "interested_topics": ["Politics", "Economics", "Culture & Society"],
            }
        
        elif entity_type_lower in ["mediaoutlet", "socialmediaplatform"]:
            return {
                "bio": f"Official account for {entity_name}. News and updates.",
                "persona": f"{entity_name} is a media entity that reports news and facilitates public discourse. The account shares timely updates and engages with the audience on current events.",
                "age": 30,  # 机构虚拟年龄
                "gender": "other",  # 机构使用other
                "mbti": "ISTJ",  # 机构风格：严谨保守
                "country": "中国",
                "profession": "Media",
                "interested_topics": ["General News", "Current Events", "Public Affairs"],
            }
        
        elif entity_type_lower in ["university", "governmentagency", "ngo", "organization"]:
            return {
                "bio": f"Official account of {entity_name}.",
                "persona": f"{entity_name} is an institutional entity that communicates official positions, announcements, and engages with stakeholders on relevant matters.",
                "age": 30,  # 机构虚拟年龄
                "gender": "other",  # 机构使用other
                "mbti": "ISTJ",  # 机构风格：严谨保守
                "country": "中国",
                "profession": entity_type,
                "interested_topics": ["Public Policy", "Community", "Official Announcements"],
            }
        
        else:
            # 默认人设
            return {
                "bio": entity_summary[:150] if entity_summary else f"{entity_type}: {entity_name}",
                "persona": entity_summary or f"{entity_name} is a {entity_type.lower()} participating in social discussions.",
                "age": random.randint(25, 50),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(self.MBTI_TYPES),
                "country": random.choice(self.COUNTRIES),
                "profession": entity_type,
                "interested_topics": ["General", "Social Issues"],
            }
    
    def set_graph_id(self, graph_id: str):
        """设置图谱ID用于Zep检索"""
        self.graph_id = graph_id
    
    def generate_profiles_from_entities(
        self,
        entities: List[EntityNode],
        use_llm: bool = True,
        progress_callback: Optional[callable] = None,
        graph_id: Optional[str] = None,
        parallel_count: int = 5,
        realtime_output_path: Optional[str] = None,
        output_platform: str = "reddit",
        actors: Optional[Dict[str, Any]] = None
    ) -> List[OasisAgentProfile]:
        """
        批量从实体生成Agent Profile（支持并行生成）

        Args:
            entities: 实体列表
            use_llm: 是否使用LLM生成详细人设
            progress_callback: 进度回调函数 (current, total, message)
            graph_id: 图谱ID，用于Zep检索获取更丰富上下文
            parallel_count: 并行生成数量，默认5
            realtime_output_path: 实时写入的文件路径（如果提供，每生成一个就写入一次）
            output_platform: 输出平台格式 ("reddit" 或 "twitter")
            actors: 深度研究 actors.json 顶层对象（可选）。按名字匹配到实体后，
                    其 role/stance/influence/memory 会注入对应 persona 提示词

        Returns:
            Agent Profile列表
        """
        import concurrent.futures
        from threading import Lock
        
        # 设置graph_id用于Zep检索
        if graph_id:
            self.graph_id = graph_id
        
        total = len(entities)
        profiles = [None] * total  # 预分配列表保持顺序
        completed_count = [0]  # 使用列表以便在闭包中修改
        lock = Lock()
        
        # 实时写入文件的辅助函数
        def save_profiles_realtime():
            """实时保存已生成的 profiles 到文件（preview-only 预览产物）。

            EXECPLAN2 F-5-0 / F-5-1:
            - OASIS generate_reddit_agent_graph 按 JSON 数组下标分配 agent_id（忽略行内 user_id），
              且对 mbti/gender/age/country 做无条件 key 访问。因此实时产物必须满足两点：
              (1) 与最终写入器同一套 OASIS-loadable schema（带默认值），否则中断/早读会触发 KeyError；
              (2) 数组下标 == 实体枚举下标，否则 poster_agent_id/initial_follows 会指向错误 persona。
            - 修复：不再写「压实/重排」的产物，改为只写「连续已完成前缀」（遇到第一个 None 即停止），
              使下标永远等于实体下标；并复用 _save_reddit_json / _save_twitter_csv 与最终写入器保持
              逐字节 schema 一致（默认 age→30 / gender 归一化 / mbti→'ISTJ' / country→'中国'，
              英文口径下 country 缺省为空串）。
            """
            if not realtime_output_path:
                return

            with lock:
                # 只取连续已完成前缀：遇到第一个 None 即停止，保证 下标==实体下标 (F-5-1)
                prefix_profiles = []
                for p in profiles:
                    if p is None:
                        break
                    prefix_profiles.append(p)
                if not prefix_profiles:
                    return

                try:
                    # 复用最终写入器，保证实时产物与最终产物 schema 完全一致 (F-5-0)；
                    # 两个写入器内部均已改为原子写盘。
                    if output_platform == "reddit":
                        self._save_reddit_json(prefix_profiles, realtime_output_path)
                    else:
                        self._save_twitter_csv(prefix_profiles, realtime_output_path)
                except Exception as e:
                    logger.warning(f"实时保存 profiles 失败: {e}")
        
        def generate_single_profile(idx: int, entity: EntityNode) -> tuple:
            """生成单个profile的工作函数"""
            entity_type = entity.get_entity_type() or "Entity"

            try:
                # 深度研究档案匹配：命中则把实证立场/记忆注入 persona 提示词
                actor = match_actor(entity.name, actors)
                if actor is not None:
                    logger.info(f"实体 {entity.name} 匹配到研究档案 actor: {actor.get('name')}")
                profile = self.generate_profile_from_entity(
                    entity=entity,
                    user_id=idx,
                    use_llm=use_llm,
                    actor=actor,
                    actors=actors,  # 完整研究档案 → 注入社会关系网（T3.1）
                )
                
                # 实时输出生成的人设到控制台和日志
                self._print_generated_profile(entity.name, entity_type, profile)
                
                return idx, profile, None
                
            except Exception as e:
                logger.error(f"生成实体 {entity.name} 的人设失败: {str(e)}")
                # 创建一个基础profile
                fallback_profile = OasisAgentProfile(
                    user_id=idx,
                    user_name=self._generate_username(entity.name),
                    name=entity.name,
                    bio=f"{entity_type}: {entity.name}",
                    persona=entity.summary or f"A participant in social discussions.",
                    source_entity_uuid=entity.uuid,
                    source_entity_type=entity_type,
                    generation_path="error_stub",  # PREP-8: 异常桩，计入降级统计
                )
                return idx, fallback_profile, str(e)
        
        logger.info(f"开始并行生成 {total} 个Agent人设（并行数: {parallel_count}）...")
        print(f"\n{'='*60}")
        print(f"开始生成Agent人设 - 共 {total} 个实体，并行数: {parallel_count}")
        print(f"{'='*60}\n")
        
        # 使用线程池并行执行
        with concurrent.futures.ThreadPoolExecutor(max_workers=parallel_count) as executor:
            # 提交所有任务
            future_to_entity = {
                executor.submit(generate_single_profile, idx, entity): (idx, entity)
                for idx, entity in enumerate(entities)
            }
            
            # 收集结果
            for future in concurrent.futures.as_completed(future_to_entity):
                idx, entity = future_to_entity[future]
                entity_type = entity.get_entity_type() or "Entity"
                
                try:
                    result_idx, profile, error = future.result()
                    profiles[result_idx] = profile
                    
                    with lock:
                        completed_count[0] += 1
                        current = completed_count[0]
                    
                    # 实时写入文件
                    save_profiles_realtime()
                    
                    if progress_callback:
                        progress_callback(
                            current, 
                            total, 
                            f"已完成 {current}/{total}: {entity.name}（{entity_type}）"
                        )
                    
                    if error:
                        logger.warning(f"[{current}/{total}] {entity.name} 使用备用人设: {error}")
                    else:
                        logger.info(f"[{current}/{total}] 成功生成人设: {entity.name} ({entity_type})")
                        
                except Exception as e:
                    logger.error(f"处理实体 {entity.name} 时发生异常: {str(e)}")
                    with lock:
                        completed_count[0] += 1
                    profiles[idx] = OasisAgentProfile(
                        user_id=idx,
                        user_name=self._generate_username(entity.name),
                        name=entity.name,
                        bio=f"{entity_type}: {entity.name}",
                        persona=entity.summary or "A participant in social discussions.",
                        source_entity_uuid=entity.uuid,
                        source_entity_type=entity_type,
                        generation_path="error_stub",  # PREP-8: 异常桩，计入降级统计
                    )
                    # 实时写入文件（即使是备用人设）
                    save_profiles_realtime()
        
        # PREP-8: 聚合静默降级统计——LLM 3 连败的规则回退（rule_fallback）与异常桩
        # （error_stub）此前只有逐条 log warning，任何健康门都看不见「5 ok / 75 fallback」
        # 这种大面积降级。这里汇总成一行运行级指标：log + 进度回调 + 实例属性
        # （last_generation_stats，供 simulation_manager 落到 config_reasoning）。
        done = [p for p in profiles if p is not None]
        n_fallback = sum(1 for p in done if getattr(p, "generation_path", "llm") == "rule_fallback")
        n_stub = sum(1 for p in done if getattr(p, "generation_path", "llm") == "error_stub")
        n_ok = len(done) - n_fallback - n_stub
        summary = f"personas: {n_ok} ok / {n_fallback} rule-fallback / {n_stub} error-stub"
        self.last_generation_stats = {
            "total": total, "ok": n_ok,
            "rule_fallback": n_fallback, "error_stub": n_stub,
        }
        logger.info(f"人设生成统计: {summary}")
        if use_llm and total and (n_fallback + n_stub) / total > 0.25:
            logger.warning(
                f"人设降级比例过高（>25%）: {summary} —— LLM persona 大面积失败，"
                "产出将是模板化人设（关键角色可能退化为泛泛简介）"
            )
        if progress_callback:
            try:
                progress_callback(total, total, summary)
            except Exception:  # noqa: BLE001 回调异常不阻断主流程
                pass

        print(f"\n{'='*60}")
        print(f"人设生成完成！共生成 {len(done)} 个Agent（{summary}）")
        print(f"{'='*60}\n")

        return profiles
    
    def _print_generated_profile(self, entity_name: str, entity_type: str, profile: OasisAgentProfile):
        """实时输出生成的人设到控制台（完整内容，不截断）"""
        separator = "-" * 70
        
        # 构建完整输出内容（不截断）
        topics_str = ', '.join(profile.interested_topics) if profile.interested_topics else '无'
        
        output_lines = [
            f"\n{separator}",
            f"[已生成] {entity_name} ({entity_type})",
            f"{separator}",
            f"用户名: {profile.user_name}",
            f"",
            f"【简介】",
            f"{profile.bio}",
            f"",
            f"【详细人设】",
            f"{profile.persona}",
            f"",
            f"【基本属性】",
            f"年龄: {profile.age} | 性别: {profile.gender} | MBTI: {profile.mbti}",
            f"职业: {profile.profession} | 国家: {profile.country}",
            f"兴趣话题: {topics_str}",
            separator
        ]
        
        output = "\n".join(output_lines)
        
        # 只输出到控制台（避免重复，logger不再输出完整内容）
        print(output)
    
    def save_profiles(
        self,
        profiles: List[OasisAgentProfile],
        file_path: str,
        platform: str = "reddit"
    ):
        """
        保存Profile到文件（根据平台选择正确格式）
        
        OASIS平台格式要求：
        - Twitter: CSV格式
        - Reddit: JSON格式
        
        Args:
            profiles: Profile列表
            file_path: 文件路径
            platform: 平台类型 ("reddit" 或 "twitter")
        """
        if platform == "twitter":
            self._save_twitter_csv(profiles, file_path)
        else:
            self._save_reddit_json(profiles, file_path)
    
    def _save_twitter_csv(self, profiles: List[OasisAgentProfile], file_path: str):
        """
        保存Twitter Profile为CSV格式（符合OASIS官方要求）
        
        OASIS Twitter要求的CSV字段：
        - user_id: 用户ID（根据CSV顺序从0开始）
        - name: 用户真实姓名
        - username: 系统中的用户名
        - user_char: 详细人设描述（注入到LLM系统提示中，指导Agent行为）
        - description: 简短的公开简介（显示在用户资料页面）
        
        user_char vs description 区别：
        - user_char: 内部使用，LLM系统提示，决定Agent如何思考和行动
        - description: 外部显示，其他用户可见的简介
        """
        import csv
        import io

        # 确保文件扩展名是.csv
        if not file_path.endswith('.csv'):
            file_path = file_path.replace('.json', '.csv')

        # EXECPLAN2 F-5-0: 先在内存缓冲构建完整 CSV，再原子写盘，避免读取者看到半写文件。
        buf = io.StringIO()
        writer = csv.writer(buf)

        # 写入OASIS要求的表头
        headers = ['user_id', 'name', 'username', 'user_char', 'description']
        writer.writerow(headers)

        # 写入数据行
        for idx, profile in enumerate(profiles):
            # user_char: 完整人设（bio + persona），用于LLM系统提示
            user_char = profile.bio
            if profile.persona and profile.persona != profile.bio:
                user_char = f"{profile.bio} {profile.persona}"
            # 处理换行符（CSV中用空格替代）
            user_char = user_char.replace('\n', ' ').replace('\r', ' ')

            # description: 简短简介，用于外部显示
            description = profile.bio.replace('\n', ' ').replace('\r', ' ')

            row = [
                idx,                    # user_id: 从0开始的顺序ID
                profile.name,           # name: 真实姓名
                profile.user_name,      # username: 用户名
                user_char,              # user_char: 完整人设（内部LLM使用）
                description             # description: 简短简介（外部显示）
            ]
            writer.writerow(row)

        write_text_atomic(file_path, buf.getvalue())

        logger.info(f"已保存 {len(profiles)} 个Twitter Profile到 {file_path} (OASIS CSV格式)")
    
    def _normalize_gender(self, gender: Optional[str]) -> str:
        """
        标准化gender字段为OASIS要求的英文格式
        
        OASIS要求: male, female, other
        """
        if not gender:
            return "other"
        
        gender_lower = gender.lower().strip()
        
        # 中文映射
        gender_map = {
            "男": "male",
            "女": "female",
            "机构": "other",
            "其他": "other",
            # 英文已有
            "male": "male",
            "female": "female",
            "other": "other",
        }
        
        return gender_map.get(gender_lower, "other")
    
    def _save_reddit_json(self, profiles: List[OasisAgentProfile], file_path: str):
        """
        保存Reddit Profile为JSON格式
        
        使用与 to_reddit_format() 一致的格式，确保 OASIS 能正确读取。
        必须包含 user_id 字段，这是 OASIS agent_graph.get_agent() 匹配的关键！
        
        必需字段：
        - user_id: 用户ID（整数，用于匹配 initial_posts 中的 poster_agent_id）
        - username: 用户名
        - name: 显示名称
        - bio: 简介
        - persona: 详细人设
        - age: 年龄（整数）
        - gender: "male", "female", 或 "other"
        - mbti: MBTI类型
        - country: 国家
        """
        # PREP-4(2): 英文口径下 country 缺省不再回填「中国」（56/80 个英文地缘政治角色曾被
        # 打成中国籍，含 US Congress）；默认（中文口径）与历史逐字节一致。
        default_country = "" if self._english_persona() else "中国"
        # XRUN-10(d): 统计走了默认值回填的字段数，让「结构化字段未从档案抽取」的回归可见。
        n_age_default = n_mbti_default = n_country_default = 0
        data = []
        for idx, profile in enumerate(profiles):
            # 使用与 to_reddit_format() 一致的格式
            # PREP-8: bio 截断从 150 提到 300——提示词要求 200 字简介，旧上限把成功生成的
            # bio 也在句中拦腰截断（Twitter CSV 一直是全量 bio，两产物口径不一致）。
            item = {
                "user_id": profile.user_id if profile.user_id is not None else idx,  # 关键：必须包含 user_id
                "username": profile.user_name,
                "name": profile.name,
                "bio": profile.bio[:300] if profile.bio else f"{profile.name}",
                "persona": profile.persona or f"{profile.name} is a participant in social discussions.",
                "karma": profile.karma if profile.karma else 1000,
                "created_at": profile.created_at,
                # OASIS必需字段 - 确保都有默认值
                "age": profile.age if profile.age else 30,
                "gender": self._normalize_gender(profile.gender),
                "mbti": profile.mbti if profile.mbti else "ISTJ",
                "country": profile.country if profile.country else default_country,
            }
            n_age_default += 0 if profile.age else 1
            n_mbti_default += 0 if profile.mbti else 1
            n_country_default += 0 if profile.country else 1

            # 可选字段
            if profile.profession:
                item["profession"] = profile.profession
            if profile.interested_topics:
                item["interested_topics"] = profile.interested_topics

            # NEXTSTEPS SIM_PERSONA_DESIGN: 结构化人格设计随档持久化到 other_info。
            # OASIS 加载器只读白名单字段（persona/mbti/gender/age/country…），多余 key
            # 被忽略——不影响加载；下游遥测/校验可据此核查阵容的认知多样性。
            if profile.persona_design:
                item["other_info"] = {"persona_design": profile.persona_design}

            data.append(item)

        if n_age_default or n_mbti_default or n_country_default:
            logger.info(
                f"Reddit Profile 默认值回填: age={n_age_default}, mbti={n_mbti_default}, "
                f"country={n_country_default} / 共 {len(profiles)} 个"
            )

        # EXECPLAN2 F-5-0: 原子写，避免 watchdog SIGKILL 或轮询读取者看到半写文件。
        write_json_atomic(file_path, data, ensure_ascii=False, indent=2)

        logger.info(f"已保存 {len(profiles)} 个Reddit Profile到 {file_path} (JSON格式，包含user_id字段)")
    
    # 保留旧方法名作为别名，保持向后兼容
    def save_profiles_to_json(
        self,
        profiles: List[OasisAgentProfile],
        file_path: str,
        platform: str = "reddit"
    ):
        """[已废弃] 请使用 save_profiles() 方法"""
        logger.warning("save_profiles_to_json已废弃，请使用save_profiles方法")
        self.save_profiles(profiles, file_path, platform)

