"""report_lint — 确定性编辑纪律 lint + 修复（WAVE9）。

把成稿/研究档案中的「机器语法与方法学泄漏」按可判定规则清理掉，让交付物读起来是
关于现实世界的机构级预测，而不是对内部多智能体推演的叙述。全部为**纯函数**
（无 ReportAgent 状态、无 LLM、无 IO），报告链与编排器均可直接调用：

    cleaned_md, lint_report = lint_report(md, lang, mode="final", spine=spine)

修复类别（确定性，围栏代码块内一律跳过）：
  * [citation:...](url) 残留 → 删除；
  * 原始图谱边转储「（依据：A --[REL]--> B）」→ 自然语言关系括注；孤悬的
    「(According to：」「（依据：」→ 删除；
  * 旧版模拟标签「模拟代理人「X」推演：」/「Simulation Agent「X」Deduction/Reasoning:」
    → 新专家小组转述规范（情景推演专家视角——「X」： / Analytical perspective — X
    (scenario panel):）；
  * 引文被删后孤悬的归因行（以 :/： 结尾、后接标题/普通段落）→ 删除；
  * [simulation_outcomes] 等原始工具名引用记号 → 删除；
  * 流水线 pass 叙述（[Pass 2 working notes] 等括注）→ 删除；散文中的提及 → 仅标记；
  * 引用记号变体（【S1】/[S1-a]/[S1 / fact 8]）→ 规整为 [S1]；
  * 重复整句（跨章节逐字重复的长句）→ 去重（保首删后）。

检测类别（只记数/采样，不改写——修复责任在上游翻译/重写通道）：
  * 跨语言污染行（英文稿中的 CJK 行 / 中文稿中的长英文散文行）；
  * 表格单元疑似截断（超长且无句末标点）；
  * 情景概率与预测骨架不一致（传入 spine 时交叉核对）；
  * Tier-2 模拟机制泄漏句（模式清单导出给 report_agent 的泄漏修复通道复用）。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# ──────────────────────────────────────────────────────────────
# 基础：围栏感知的行遍历
# ──────────────────────────────────────────────────────────────

def _fence_mask(lines: List[str]) -> List[bool]:
    """返回与 lines 等长的布尔表：True = 该行处于（或本身是）围栏代码块。"""
    mask: List[bool] = []
    in_fence = False
    for ln in lines:
        s = ln.lstrip()
        if s.startswith("```") or s.startswith("~~~"):
            mask.append(True)          # 围栏标记行本身也不参与改写
            in_fence = not in_fence
            continue
        mask.append(in_fence)
    return mask


# ──────────────────────────────────────────────────────────────
# 模式：Tier-1 机器语法 + Tier-2 泄漏检测（EN + ZH）
# ──────────────────────────────────────────────────────────────

# [citation:...](url) / 无 URL 的 [citation:...] 残留
_CITATION_RESIDUE_RE = re.compile(r"\[citation:[^\]]*\](?:\([^)\s]*\))?")

# 括注式边转储：（依据：A --[REL]--> B；C --[REL2]--> D）
_EDGE_DUMP_PAREN_RE = re.compile(
    r"[（(]\s*(?:依据|According to)\s*[:：]?\s*([^（）()]*?--\[[A-Z_]+\]-->[^（）()]*?)\s*[）)]")
# 散文内联的裸箭头（括注外兜底）：只把 ' --[REL]--> ' 箭头换成关系动词，不猜实体边界。
_EDGE_ARROW_RE = re.compile(r"\s*--\[([A-Z_]+)\]-->\s*")
# 孤悬的「（依据：」「(According to：」（后无内容直到行尾）
_DANGLING_EDGE_INTRO_RE = re.compile(r"[（(]\s*(?:依据|According to)\s*[:：]?\s*$")

# 原始工具名引用记号（[simulation_outcomes] 被当作来源引用）
_TOOL_TOKEN_RE = re.compile(
    r"[\[【]\s*(?:simulation_outcomes|coalition_map|scenario_diff|opinion_shift|"
    r"insight_forge|panorama_search|interview_agents|trace_cascade|faction_brief)\s*[\]】]")

# 旧版模拟标签（末尾冒号捕获——引导引文的标签保留冒号，句中内联用法改成从句式转述）
_SIM_LABEL_EN_RE = re.compile(
    r"(?:模拟)?Simulation Agent\s*[「『\"']?([^」』\"'\n:：]{1,60}?)[」』\"']?\s*"
    r"(?:Deduction(?:\s*/\s*Reasoning)?|Reasoning|推演)((?:\s*[:：])?)", re.I)
_SIM_LABEL_ZH_RE = re.compile(
    r"模拟代理人\s*[「『\"']?([^」』\"'\n:：]{1,60}?)[」』\"']?\s*推演((?:\s*[:：])?)")
_SIM_LABEL_ZH_ALT_RE = re.compile(
    r"仿真\s*Agent\s*[「『\"']?(?P<name>[^」』\"'\n:：]{1,60}?)[」』\"']?\s*"
    r"(?P<role>[（(][^）)\n]{1,80}[）)])?\s*"
    r"(?:演绎\s*/\s*推理|推演|推理)(?P<colon>(?:\s*[:：])?)",
    re.I,
)
_SIM_LABEL_ZH_STATEMENT_RE = re.compile(
    r"仿真\s*Agent\s*[「『\"']?(?P<name>[^」』\"'\n:：]{1,60}?)[」』\"']?\s*"
    r"(?P<role>[（(][^）)\n]{1,80}[）)])?\s*的(?:表述|观点|判断|评估)",
    re.I,
)
_SIM_LABEL_GARBLE_RE = re.compile(r"模拟\s*Deduction(?:\s*/\s*Reasoning)?")

# pass 叙述：括注/方括号内含 Pass N / working notes → 可安全删除
_PASS_BRACKET_RE = re.compile(
    r"[\[（(【]\s*[^\]\n）)】]{0,80}?(?:\bPass\s*\d+\b|working notes|工作笔记)[^\]\n）)】]{0,80}?[\]）)】]",
    re.I)
_PASS_PROSE_RE = re.compile(r"\bPass\s+\d+\b|working notes|工作笔记", re.I)

# 引用记号变体 → [S{n}]
_CITE_VARIANT_RES: List[re.Pattern] = [
    re.compile(r"【\s*S(\d+)[^】]*】"),                 # 【S1】/【S1-a】
    re.compile(r"\[\s*S(\d+)-[A-Za-z0-9]+\s*\]"),      # [S1-a]
    re.compile(r"\[\s*S(\d+)\s*/[^\]]*\]"),            # [S1 / fact 8]
    re.compile(r"\(\s*S(\d+)\s*\)"),                  # (S1)
]

# CJK 字符类（与 report_agent._CJK_CHAR 同源）
_CJK_CHAR = r"一-鿿㐀-䶿぀-ヿ가-힯"
_CJK_RUN_RE = re.compile(r"[" + _CJK_CHAR + r"]{2,}")
_LATIN_PROSE_RE = re.compile(r"[A-Za-z][A-Za-z0-9 ,.'’\-()%/&]{39,}")
_LANG_INLINE_PROTECTED_RE = re.compile(r"`[^`\n]*`|https?://[^\s)>\]}]+", re.I)

# ── Tier-2 泄漏检测模式（EN + ZH；导出供 report_agent 泄漏修复复用）──
LEAKAGE_PATTERNS: List[Tuple[str, re.Pattern]] = [
    # EN
    ("generation_failure_placeholder", re.compile(
        r"Chapter generation failed|章节生成失败|模型反复未能产出合格文本", re.I)),
    ("simulation_agent_label", re.compile(r"\bSimulation Agent\b")),
    ("deduction_reasoning", re.compile(r"Deduction\s*/\s*Reasoning")),
    ("the_simulation", re.compile(r"\bthe simulation(?:['’]s)?\b", re.I)),
    ("simulated_world", re.compile(r"\b(?:the|this|a) simulated world\b", re.I)),
    ("internal_harness_narration", re.compile(
        r"\b(?:the\s+)?harness(?:['’]s)?\b|\bagent clusters?\b|"
        r"\bsimulated environment\b|\bsimulation-derived\b|"
        r"\bworld[- ]state outputs?\b", re.I)),
    ("simulation_surfaced", re.compile(r"simulation (?:consistently |repeatedly )?surfaced", re.I)),
    ("n_actor_simulation", re.compile(r"\b\d+[- ](?:actor|agent|entity) simulation\b", re.I)),
    ("n_actions", re.compile(r"\b\d+\s+actions\b", re.I)),
    ("round_n", re.compile(r"\bround\s+\d+\b", re.I)),
    ("internal_agent_counts", re.compile(
        r"\b\d[\d,]*\s+(?:active\s+)?agents?\b", re.I)),
    ("internal_edge_counts", re.compile(
        r"\b\d[\d,]*\s+(?:directed\s+)?(?:edges|relationships)\b", re.I)),
    ("internal_action_telemetry", re.compile(
        r"\b(?:action-type distribution|ratio of liking to posting|"
        r"posts?\s+(?:vs\.?|versus)\s+\d+\s+likes?|"
        r"\d+\s+likes?\s*(?:,|and)\s*\d+\s+posts?)\b", re.I)),
    ("internal_round_cluster", re.compile(
        r"\b(?:across rounds?|clusters? of agents?|coalition-formation phase)\b", re.I)),
    ("internal_graph_narration", re.compile(
        r"\b(?:the graph (?:contains|places|shows)|causal neighborhood|"
        r"faction map|agent graph|coalition map)\b", re.I)),
    ("internal_graph_process", re.compile(
        r"\b(?:research graph|forecast graph|simulation graph|China subgraph|"
        r"sub-simulation|\d+[- ]edge graph|\d+[- ]agent convergence|"
        r"shared interaction targets|single node moving|"
        r"no node where|forecast graph|remaining Trump-neighborhood dependencies)\b", re.I)),
    ("internal_graph_telemetry", re.compile(
        r"\b(?:post-to-like ratio|combined actions?|network traffic|"
        r"action signatures?|consensus-seeking\s*\+\s*authority-projection|"
        r"consensus-keepers?|authority-projectors?|"
        r"count the action signatures)\b", re.I)),
    ("raw_ontology_relation", re.compile(
        r"\b(?:CAUSES|ENABLES|CONSTRAINS|TRIGGERS|ACCELERATES|INFLUENCES|"
        r"OPPOSES?|SUPPLIES|PARTNERS_WITH|COMPETES_WITH|FUNDED_BY|"
        r"INHERITS_R_AND_D_FROM|COMPOUND_PARITY_WITH)\b")),
    ("bold_graph_relation", re.compile(
        r"\*\*[^*\n]{2,120}\b(?:causes?|enables?|influences?|supplies|"
        r"opposes?)\b[^*\n]{1,120}\*\*", re.I)),
    ("simulated_evidence_data", re.compile(
        r"\bsimulated (?:data|evidence|world)\b", re.I)),
    ("named_proxy_agent", re.compile(
        r"\b(?:NVIDIA|AMD|Intel|TSMC|ASML|OpenAI|Anthropic|Google|Meta|"
        r"Microsoft|Amazon|Trump|Xi)\s+agent\b", re.I)),
    ("action_type_tokens", re.compile(
        r"\b(?:CREATE_POST|CREATE_COMMENT|LIKE_POST|LIKE_COMMENT|REPOST|QUOTE_POST|UNFOLLOW)\b")),
    ("agent_mechanics_en", re.compile(
        r"(?:\b(?:simulation|simulated|agent-based model)\b[^\n.!?]{0,80}"
        r"\bagent (?:discourse|network|behavio(?:r|ur))\b"
        r"|\bagent (?:discourse|network|behavio(?:r|ur))\b[^\n.!?]{0,80}"
        r"\b(?:simulation|simulated|agent-based model)\b"
        r"|\b(?:simulation|simulated|agent-based model)\b[^\n.!?]{0,80}"
        r"\bmost active agents?\b"
        r"|\bmost active agents?\b[^\n.!?]{0,80}"
        r"\b(?:simulation|simulated|agent-based model)\b)", re.I)),
    # These are valid real-world analytical concepts, so only classify them as
    # mechanics when the same sentence explicitly ties them to the simulation.
    ("sim_inference_terms_en", re.compile(
        r"(?:\b(?:simulation|simulated agents?|agent-based model|forecast model)\b"
        r"[^\n.!?]{0,100}\b(?:consensus formation|revealed preferences?)\b"
        r"|\b(?:consensus formation|revealed preferences?)\b[^\n.!?]{0,100}"
        r"\b(?:simulation|simulated agents?|agent-based model|forecast model)\b)", re.I)),
    ("sim_causal_graph", re.compile(
        r"(?:simulation|模拟)[^\n]{0,20}causal graph|causal graph[^\n]{0,20}(?:simulation|模拟)", re.I)),
    ("raw_edge", re.compile(r"--\[[A-Z_]+\]-->")),
    # ZH
    ("sim_agent_zh", re.compile(
        r"模拟代理人|模拟推演|模拟世界|本次模拟|模拟中|模拟的")),
    ("sim_mechanics_zh", re.compile(
        r"次动作|逐轮|轮次|峰值轮次|最活跃\s*Agent|派系图|派系聚类|因果图"
        r"|上帝视角|采访实录|模拟量化")),
    ("sim_inference_terms_zh", re.compile(
        r"(?:(?:模拟|推演|智能体)[^。！？\n]{0,60}(?:共识形成|揭示性偏好|行为信号)"
        r"|(?:共识形成|揭示性偏好|行为信号)[^。！？\n]{0,60}(?:模拟|推演|智能体))")),
]

# 平台行为引文（发帖/点赞/评论机制内容——应删除而非转写）
PLATFORM_BEHAVIOR_RE = re.compile(
    r"发帖|点赞|转发了?|关注了|在\s*(?:推特|Twitter|Reddit)\s*上发"
    r"|\bposted (?:a|the) (?:post|thread)\b|\bliked (?:a|the) post\b"
    r"|\bcommented on (?:a|the) post\b|\bretweet(?:ed)?\b|\brepost(?:ed)?\b", re.I)

# 关系动词映射（EN 名词短语 / ZH 名词）
_REL_EN = {
    "SUPPLIES": "supply", "COMPETES_WITH": "competitive", "DEPENDS_ON": "dependency",
    "REGULATES": "regulatory", "FUNDS": "funding", "CUSTOMER_OF": "customer",
    "PARTNERS_WITH": "partnership", "INVESTS_IN": "investment", "OWNS": "ownership",
    "SANCTIONS": "sanctions", "ACQUIRES": "acquisition", "LICENSES": "licensing",
}
_REL_ZH = {
    "SUPPLIES": "供应", "COMPETES_WITH": "竞争", "DEPENDS_ON": "依赖",
    "REGULATES": "监管", "FUNDS": "注资", "CUSTOMER_OF": "采购",
    "PARTNERS_WITH": "合作", "INVESTS_IN": "投资", "OWNS": "控股",
    "SANCTIONS": "制裁", "ACQUIRES": "并购", "LICENSES": "授权",
}
# 内联箭头替换用的动词形式（'A --[SUPPLIES]--> B' → 'A supplies B' / 'A 供应 B'）
_REL_VERB_EN = {
    "SUPPLIES": "supplies", "COMPETES_WITH": "competes with", "DEPENDS_ON": "depends on",
    "REGULATES": "regulates", "FUNDS": "funds", "CUSTOMER_OF": "is a customer of",
    "PARTNERS_WITH": "partners with", "INVESTS_IN": "invests in", "OWNS": "owns",
    "SANCTIONS": "sanctions", "ACQUIRES": "acquires", "LICENSES": "licenses",
}


def _rel_phrase(rel: str, zh: bool) -> str:
    rel = (rel or "").strip().upper()
    if zh:
        return _REL_ZH.get(rel, rel.replace("_", " ").lower())
    return _REL_EN.get(rel, rel.replace("_", " ").lower())


def _rel_verb(rel: str, zh: bool) -> str:
    rel = (rel or "").strip().upper()
    if zh:
        return _REL_ZH.get(rel, rel.replace("_", " ").lower())
    return _REL_VERB_EN.get(rel, rel.replace("_", " ").lower())


def _is_zh(lang: str) -> bool:
    return not str(lang or "").strip().lower().startswith("en")


# ──────────────────────────────────────────────────────────────
# 修复原语（纯函数；均返回 (new_text, count...)）
# ──────────────────────────────────────────────────────────────

def strip_citation_residue(md: str) -> Tuple[str, int]:
    """删除 [citation:...](url) / [citation:...] 残留记号。"""
    n = 0
    lines = (md or "").split("\n")
    mask = _fence_mask(lines)
    for i, ln in enumerate(lines):
        if mask[i]:
            continue
        new_ln, k = _CITATION_RESIDUE_RE.subn("", ln)
        if k:
            lines[i] = re.sub(r"[ \t]{2,}", " ", new_ln)
            n += k
    return "\n".join(lines), n


def rewrite_edge_dumps(md: str, lang: str = "English") -> Tuple[str, int, int]:
    """括注式边转储 → 自然语言关系括注；孤悬「（依据：」→ 删除。

    返回 (new_md, 转写的括注数, 删除的孤悬引子数)。散文内联的裸边（无括注包裹）
    也一并转写为自然语言。"""
    zh = _is_zh(lang)
    converted = 0
    dangling = 0

    def _render_edges(payload: str) -> str:
        parts: List[str] = []
        # 括注内可能有多条边（；/; 分隔）——先切再逐条解析，尾实体不被空格截断。
        for piece in re.split(r"[；;]", payload):
            m = re.match(r"\s*(.{1,80}?)\s*--\[([A-Z_]+)\]-->\s*(.{1,80}?)\s*$", piece)
            if not m:
                continue
            a, rel, b = m.group(1).strip(), m.group(2), m.group(3).strip()
            if not a or not b:
                continue
            if zh:
                parts.append(f"{a}与{b}的{_rel_phrase(rel, True)}关系")
            else:
                parts.append(f"{a}'s {_rel_phrase(rel, False)} relationship with {b}")
        if not parts:
            return ""
        if zh:
            return "（基于" + "、".join(parts) + "）"
        return "(reflecting " + " and ".join(parts) + ")"

    lines = (md or "").split("\n")
    mask = _fence_mask(lines)
    for i, ln in enumerate(lines):
        if mask[i]:
            continue
        new_ln = ln
        for m in list(_EDGE_DUMP_PAREN_RE.finditer(new_ln)):
            rendered = _render_edges(m.group(1))
            new_ln = new_ln.replace(m.group(0), rendered, 1)
            converted += 1
        # 散文内联裸箭头（未被括注包裹）：只换箭头为关系动词，不猜实体边界。
        new_ln, k_arrow = _EDGE_ARROW_RE.subn(
            lambda m: f" {_rel_verb(m.group(1), zh)} ", new_ln)
        converted += k_arrow
        stripped, k = _DANGLING_EDGE_INTRO_RE.subn("", new_ln)
        if k:
            new_ln = stripped.rstrip()
            dangling += k
        if new_ln != ln:
            lines[i] = new_ln
    return "\n".join(lines), converted, dangling


def rewrite_sim_labels(md: str, lang: str = "English") -> Tuple[str, int]:
    """旧版模拟代理人标签 → 新专家小组转述规范。EN/ZH 标签各自映射到对应语言的新规范。

    标签带冒号（引导引文）→ 新标签保留冒号；句中内联用法（无冒号）→ 从句式转述，
    不生造「…(scenario panel):pushes back」型粘连。"""
    n = 0

    def _en_repl(m: re.Match) -> str:
        name = m.group(1).strip()
        if m.group(2):
            return f"Evidence-based assessment — {name}:"
        return f"the evidence-based assessment for {name}"

    def _zh_repl(m: re.Match) -> str:
        name = m.group(1).strip()
        if m.group(2):
            return f"证据分析——「{name}」："
        return f"关于「{name}」的证据分析"

    def _zh_alt_repl(m: re.Match) -> str:
        name = m.group("name").strip()
        role = str(m.group("role") or "").strip()
        subject = f"「{name}」{role}"
        if m.group("colon"):
            return f"证据分析——{subject}："
        return f"关于{subject}的证据分析"

    def _zh_statement_repl(m: re.Match) -> str:
        name = m.group("name").strip()
        role = str(m.group("role") or "").strip()
        return f"「{name}」{role}的证据分析"

    lines = (md or "").split("\n")
    mask = _fence_mask(lines)
    for i, ln in enumerate(lines):
        if mask[i]:
            continue
        new_ln, k1 = _SIM_LABEL_EN_RE.subn(_en_repl, ln)
        new_ln, k2 = _SIM_LABEL_ZH_RE.subn(_zh_repl, new_ln)
        new_ln, k3 = _SIM_LABEL_ZH_ALT_RE.subn(_zh_alt_repl, new_ln)
        new_ln, k4 = _SIM_LABEL_ZH_STATEMENT_RE.subn(_zh_statement_repl, new_ln)
        new_ln, k5 = _SIM_LABEL_GARBLE_RE.subn("证据分析", new_ln)
        if k1 or k2 or k3 or k4 or k5:
            lines[i] = new_ln
            n += k1 + k2 + k3 + k4 + k5
    return "\n".join(lines), n


def strip_tool_tokens(md: str) -> Tuple[str, int]:
    """删除 [simulation_outcomes] 等原始工具名引用记号。"""
    n = 0
    lines = (md or "").split("\n")
    mask = _fence_mask(lines)
    for i, ln in enumerate(lines):
        if mask[i]:
            continue
        new_ln, k = _TOOL_TOKEN_RE.subn("", ln)
        if k:
            lines[i] = re.sub(r"[ \t]{2,}", " ", new_ln).rstrip()
            n += k
    return "\n".join(lines), n


# 归因线索（孤悬归因行删除的保守门：须像「某人/某视角 说/认为…：」）
_ATTRIBUTION_CUE_RE = re.compile(
    r"视角|表示|认为|指出|强调|反驳|推演|专家|警告|panel|perspective|argu|says|said|notes?d?\b"
    r"|according to|pushes? back|contend|assert|warn|observ|\bagent\b", re.I)


def remove_dangling_attributions(md: str) -> Tuple[str, int]:
    """删除孤悬的引文归因行：以 :/： 结尾、含归因线索、其后（隔空行）不是引用块/列表/表格。

    典型成因：引文接地修复删掉了 blockquote，留下「X pushes back …:」的空引子。"""
    lines = (md or "").split("\n")
    mask = _fence_mask(lines)
    delete: set = set()
    for i, ln in enumerate(lines):
        if mask[i]:
            continue
        s = ln.strip()
        if not s or not s.endswith((":", "：")):
            continue
        if s.startswith(("#", ">", "|", "-", "*", "```", "~~~")) or re.match(r"^\d+[.、)]", s):
            continue
        if not _ATTRIBUTION_CUE_RE.search(s):
            continue
        # 找下一个非空行
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines):
            delete.add(i)          # 文档末尾的孤悬引子
            continue
        nxt = lines[j].strip()
        if nxt.startswith((">", "-", "*", "|", "```", "~~~")) or re.match(r"^\d+[.、)]", nxt):
            continue               # 引子后确有引用块/列表/表格 → 合法引子
        delete.add(i)              # 后面是标题或普通段落 → 引子已孤悬
    if not delete:
        return md, 0
    out: List[str] = [ln for i, ln in enumerate(lines) if i not in delete]
    # 折叠删除后产生的连续空行
    txt = re.sub(r"\n{3,}", "\n\n", "\n".join(out))
    return txt, len(delete)


def strip_pass_narration(md: str) -> Tuple[str, int, int]:
    """括注式 pass 叙述（[Pass 2 working notes] 等）→ 删除；散文提及 → 仅计数。"""
    stripped = 0
    lines = (md or "").split("\n")
    mask = _fence_mask(lines)
    for i, ln in enumerate(lines):
        if mask[i]:
            continue
        new_ln, k = _PASS_BRACKET_RE.subn("", ln)
        if k:
            lines[i] = re.sub(r"[ \t]{2,}", " ", new_ln).rstrip()
            stripped += k
    txt = "\n".join(lines)
    flagged = 0
    mask2 = _fence_mask(lines)
    for i, ln in enumerate(lines):
        if not mask2[i]:
            flagged += len(_PASS_PROSE_RE.findall(ln))
    return txt, stripped, flagged


def normalize_citation_variants(md: str) -> Tuple[str, int]:
    """引用记号变体（【S1】/[S1-a]/[S1 / fact 8]）→ 规整为 [S1]。"""
    n = 0
    lines = (md or "").split("\n")
    mask = _fence_mask(lines)
    for i, ln in enumerate(lines):
        if mask[i]:
            continue
        new_ln = ln
        for pat in _CITE_VARIANT_RES:
            new_ln, k = pat.subn(r"[S\1]", new_ln)
            n += k
        if new_ln != ln:
            lines[i] = new_ln
    return "\n".join(lines), n


def detect_language_contamination(md: str, lang: str) -> Dict[str, Any]:
    """Detect foreign-language report text across structured Markdown.

    Fenced code is excluded. Headings, quotes, tables, and linked prose are included;
    only inline-code and URL destination tokens are masked, so a URL never exempts the
    rest of its line or a human-readable link label.
    """
    zh_target = _is_zh(lang)
    lines = (md or "").split("\n")
    mask = _fence_mask(lines)
    hits = 0
    samples: List[str] = []
    in_references = False
    for i, ln in enumerate(lines):
        if mask[i]:
            continue
        s = ln.strip()
        if not s:
            continue
        if s in ("## References", "## 参考来源"):
            in_references = True
            continue
        if in_references:
            # Original-language publication titles are citation metadata, not a
            # prose-language switch. The finalizer always places References last.
            continue
        scan = _LANG_INLINE_PROTECTED_RE.sub(" ", s)
        if zh_target:
            found = [c for c in _LATIN_PROSE_RE.findall(scan) if c.count(" ") >= 4]
        else:
            found = _CJK_RUN_RE.findall(scan)
        if found:
            hits += 1
            if len(samples) < 5:
                samples.append(s[:90])
    return {"lines": hits, "samples": samples}


def detect_table_cell_truncation(md: str, min_len: int = 160) -> List[str]:
    """表格单元疑似截断：超长且以字母/数字结尾（无句末标点）→ 采样标记（不改写）。"""
    out: List[str] = []
    lines = (md or "").split("\n")
    mask = _fence_mask(lines)
    for i, ln in enumerate(lines):
        if mask[i] or not ln.strip().startswith("|"):
            continue
        if re.match(r"^\s*\|[\s\-:|]+\|\s*$", ln):
            continue                            # 分隔行
        for cell in ln.strip().strip("|").split("|"):
            c = cell.strip()
            if len(c) >= min_len and c and (c[-1].isalnum()):
                out.append(c[-60:])
                if len(out) >= 8:
                    return out
    return out


_CITATION_SUFFIX_RE = re.compile(
    r"(?:\[\s*S\d+[^\]\n]*\]|【\s*S\d+[^】\n]*】)", re.I)
_SENTENCE_CLOSERS = frozenset("\"'”’」』）》】)")


def _split_sentences(text: str) -> List[str]:
    """Split prose without discarding punctuation, citations, or whitespace.

    English sentence terminals retain the historical whitespace/end-of-text
    boundary rule (so decimals and dotted identifiers are not split). Chinese
    terminals are boundaries even when the next sentence has no whitespace.
    A citation immediately following terminal punctuation belongs to the
    preceding sentence, as do closing quotation marks.
    """
    value = str(text or "")
    if not value:
        return []
    parts: List[str] = []
    start = 0
    index = 0
    while index < len(value):
        terminal = value[index]
        if terminal not in "。！？.!?":
            index += 1
            continue
        cursor = index + 1
        while cursor < len(value) and value[cursor] in _SENTENCE_CLOSERS:
            cursor += 1
        # Citations occasionally follow punctuation ("claim.[S2]"); preserve
        # them with the claim instead of leaving an orphan fragment.
        while True:
            citation = _CITATION_SUFFIX_RE.match(value, cursor)
            if citation is None:
                break
            cursor = citation.end()
            while cursor < len(value) and value[cursor] in _SENTENCE_CLOSERS:
                cursor += 1
        is_boundary = (
            terminal in "。！？"
            or cursor >= len(value)
            or value[cursor].isspace()
        )
        if not is_boundary:
            index += 1
            continue
        while cursor < len(value) and value[cursor].isspace():
            cursor += 1
        parts.append(value[start:cursor])
        start = cursor
        index = cursor
    if start < len(value):
        parts.append(value[start:])
    return parts


def _norm_sentence(s: str) -> str:
    t = re.sub(r"[\[【]\s*S[\d?#][^\]】]*[\]】]", "", s)
    t = re.sub(r"[*_`\"'“”‘’「」『』\s]+", "", t)
    return t.lower()


def dedup_duplicate_sentences(md: str, min_chars: int = 60) -> Tuple[str, int]:
    """跨章节逐字重复的长句去重（保留首次出现，删除后续重复）。

    仅作用于散文段落行（跳过标题/引用/列表/表格/围栏），归一化后 >= min_chars 的
    整句重复才判定——短句/套话不动，避免误伤。"""
    seen: set = set()
    removed = 0
    lines = (md or "").split("\n")
    mask = _fence_mask(lines)
    for i, ln in enumerate(lines):
        if mask[i]:
            continue
        s = ln.strip()
        if not s or s.startswith(("#", ">", "|", "-", "*", "!")) or re.match(r"^\d+[.、)]", s):
            continue
        sentences = _split_sentences(ln)
        if len(sentences) <= 0:
            continue
        kept: List[str] = []
        changed = False
        for sent in sentences:
            key = _norm_sentence(sent)
            if len(key) >= min_chars:
                if key in seen:
                    removed += 1
                    changed = True
                    continue
                seen.add(key)
            kept.append(sent)
        if changed:
            lines[i] = "".join(x for x in kept if x.strip()).strip()
    return "\n".join(lines), removed


def check_scenario_probabilities(md: str, spine: Optional[Dict[str, Any]]) -> List[str]:
    """情景概率交叉核对（spine 为唯一真值源）：正文中情景名附近的百分比须与骨架一致（±1pt）。"""
    if not isinstance(spine, dict):
        return []
    issues: List[str] = []
    text = md or ""
    for s in (spine.get("scenarios") or []):
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or "").strip()
        try:
            p = round(float(s.get("probability") or 0.0) * 100)
        except (TypeError, ValueError):
            continue
        if len(name) < 4:
            continue
        idx = text.find(name)
        if idx < 0:
            continue
        window = text[max(0, idx - 18):idx + len(name) + 55]
        pcts = [int(m.group(1)) for m in re.finditer(r"(\d{1,3})\s*%", window)
                if 0 <= int(m.group(1)) <= 100]
        if not pcts or any(abs(pv - p) <= 1 for pv in pcts):
            continue
        near = [pv for pv in pcts if abs(pv - p) <= 60]
        if near:
            pv = min(near, key=lambda x: abs(x - p))
            issues.append(f"scenario '{name[:28]}': prose {pv}% vs spine {p}%")
    return issues[:8]


def leakage_hits(text: str) -> List[str]:
    """返回命中的 Tier-2 泄漏模式名列表（含重复命中，供计数）。"""
    hits: List[str] = []
    lines = (text or "").split("\n")
    mask = _fence_mask(lines)
    visible = "\n".join(
        line for index, line in enumerate(lines) if not mask[index]
    )
    for name, pat in LEAKAGE_PATTERNS:
        hits.extend(name for _ in pat.finditer(visible))
    return hits


def strip_leakage_sentences(text: str, max_scan: int = 400) -> Tuple[str, int]:
    """把散文文本中命中 Tier-2 泄漏模式的**整句**删除（用于 key-points 提炼与重写后兜底）。"""
    if not text:
        return text or "", 0
    sentences = _split_sentences(text)
    kept: List[str] = []
    removed = 0
    for sent in sentences[:max_scan]:
        if any(pat.search(sent) for _, pat in LEAKAGE_PATTERNS):
            removed += 1
            continue
        kept.append(sent)
    kept.extend(sentences[max_scan:])
    return "".join(x for x in kept if x.strip()).strip(), removed


_SIMULATION_OUTCOME_REWRITES: List[Tuple[re.Pattern, str]] = [
    (re.compile(
        r"\bthe simulated world we constructed,\s*populated by\s*"
        r"\d[\d,]*\s+active agents?\s+and\s+\d[\d,]*\s+relationships,\s*treats\b",
        re.I,
    ), "the cross-actor evidence treats"),
    (re.compile(
        r"\bin the simulated world,\s*(\d+)\s+binary forecasts are laid out as\b",
        re.I,
    ), r"The report defines \1 binary forecasts as"),
    (re.compile(r"\b(?:the|this) simulation(?:['’]s)?\s+power map\b", re.I),
     "the actor-power evidence"),
    (re.compile(r"\b(?:the|this) simulated world\b", re.I),
     "the forecast evidence base"),
    (re.compile(r"\b(?:the\s+)?\d+-agent (?:bloc|faction|mega-coalition)\b", re.I),
     "the cross-camp coalition"),
    (re.compile(r"\bCoalition\s+1\b", re.I), "the cross-camp coalition"),
    (re.compile(r"\bagent graph\b", re.I), "stakeholder network"),
    (re.compile(r"\bfaction map\b", re.I), "stakeholder alignment map"),
    (re.compile(r"\bcausal neighborhood\b", re.I), "policy-leverage profile"),
    (re.compile(r"\bthe\s+\d+-actor faction graph\b", re.I),
     "the real-world actor relationship network"),
    (re.compile(r"\bTrump['’]s 11-edge graph\b", re.I),
     "Trump's concentrated policy-leverage structure"),
    (re.compile(r"\bTrump 11-edge policy-leverage profile\b", re.I),
     "Trump's concentrated policy-leverage profile"),
    (re.compile(r"\bthe forecast['’]s China subgraph\b", re.I),
     "the forecast's China evidence"),
    (re.compile(r"\bXi-Centered Subgraph\b", re.I),
     "China's Constraint Network"),
    (re.compile(r"\bChina subgraph\b", re.I), "China evidence"),
    (re.compile(r"\bChina Sub-Simulation\b", re.I), "China Outcome Analysis"),
    (re.compile(r"\bsub-simulation\b", re.I), "scenario analysis"),
    (re.compile(r"\b16-agent convergence\b", re.I), "cross-actor alignment"),
    (re.compile(r"\b(\d+)-agent coalition\b", re.I), r"\1-actor coalition"),
    (re.compile(r"\bshared interaction targets\b", re.I), "shared dependencies"),
    (re.compile(r"\bcluster into a single faction\b", re.I),
     "align within a single coalition"),
    (re.compile(r"\beach node needs the others\b", re.I),
     "each participant depends on the others"),
    (re.compile(r"\bevery member is a node\b", re.I),
     "every member is an actor"),
    (re.compile(r"\bsingle node moving\b", re.I),
     "single actor changing course"),
    (re.compile(
        r"\bthe causal graph has no node where ([^.!?]{1,80}) can intervene\b",
        re.I,
    ), r"the current policy structure gives \1 no direct institutional channel to intervene"),
    (re.compile(
        r"\bno bloc has an agent for ([^.!?]{1,100}) in the forecast graph\b",
        re.I,
    ), r"no bloc gives \1 meaningful institutional representation"),
    (re.compile(r"\bThat is not a bug\.\s*That is the forecast\.\s*", re.I),
     "That omission is itself consequential for the forecast. "),
    (re.compile(r"\bMarkets Know More Than the Graph\b", re.I),
     "Market Signals as a Counterweight"),
    (re.compile(r"\bthe research graph\b", re.I), "the underlying evidence"),
    (re.compile(r"\bthe causal graph\b", re.I), "the causal evidence"),
    (re.compile(r"\bthe forecast graph\b", re.I), "the forecast framework"),
    (re.compile(r"\bthe simulation graph\b", re.I), "the analytical framework"),
    (re.compile(r"\bthe graph\b", re.I), "the evidence"),
    (re.compile(r"\bremaining ([^.!?\n]{0,50}) edges tell\b", re.I),
     r"remaining \1 dependencies clarify"),
    (re.compile(r"\bvia two complementary edges\b", re.I),
     "through two complementary relationships"),
    (re.compile(r"\btwo edges that capture\b", re.I),
     "two relationships that capture"),
    (re.compile(r"\bis the edge that\b", re.I), "is the relationship that"),
    (re.compile(r"\btraceable edges\b", re.I), "traceable relationships"),
    (re.compile(r"\bedges that are inferred\b", re.I), "relationships inferred"),
    (re.compile(r"\bthe original dossier\b", re.I), "the source evidence"),
    (re.compile(r"\bthe research dossier\b", re.I), "the evidence base"),
    (re.compile(r"\bthe dossier['’]s\b", re.I), "the forecast's"),
    (re.compile(r"\bthe dossier\b", re.I), "the evidence base"),
    (re.compile(r"\bDossier\b"), "Forecast"),
    (re.compile(r"\bthe predictive skeleton\b", re.I), "the forecast framework"),
    (re.compile(r"\bthe power map\b", re.I), "the actor-power structure"),
    (re.compile(r"\bthe hyperscaler agent\b", re.I), "the hyperscaler evidence"),
    (re.compile(r"`ENABLES` signal", re.I), "enabling signal"),
    (re.compile(
        r",?\s*restated dozens of times across rounds and across clusters of agents,?",
        re.I,
    ), ""),
    (re.compile(r"\bthe\s+NVIDIA\s+agent\s+reads\b", re.I),
     "NVIDIA's public position indicates"),
    (re.compile(r"\bthe simulated data shows\b", re.I), "the available evidence shows"),
    (re.compile(r"\bthe simulated evidence is\b", re.I), "the available evidence indicates"),
    (re.compile(r"\bsimulation dynamics\b", re.I), "forecast drivers and outcome pathways"),
    (re.compile(r"\bagent dynamics\b", re.I), "stakeholder dynamics"),
    (re.compile(r"\b(?:multi-agent|agent-based|agentic) simulation\b", re.I), "forecast model"),
    (re.compile(
        r"\b(?:the|this) simulation(?:'s)?\s+"
        r"(?:shows?|showed|suggests?|suggested|indicates?|indicated|finds?|found|"
        r"reveals?|revealed|surfaced|produced)\b",
        re.I,
    ), "the evidence indicates"),
    (re.compile(r"\bbased on (?:the|this) simulation\b", re.I),
     "based on the available evidence"),
    (re.compile(r"\bin (?:the|this) simulation\b", re.I), "in the forecast"),
    (re.compile(r"\b(?:the\s+)?faction\s*/\s*cluster analysis shows\b", re.I),
     "The cross-actor evidence indicates"),
    (re.compile(r"模拟动力学|模拟动态|智能体动力学"), "预测驱动因素与结果路径"),
    (re.compile(r"模拟数据集(?:中)?"), "研究证据中"),
    (re.compile(r"模拟数据(?:中)?"), "研究证据"),
    (re.compile(r"模拟证据"), "现有证据"),
    (re.compile(r"模拟图谱"), "证据图谱"),
    (re.compile(r"模拟框架"), "分析框架"),
    (re.compile(r"仿真图"), "证据图谱"),
    # Legacy report prose used this exact internal-method construction. Keep
    # domain terms such as "工业仿真软件" and "碰撞仿真" untouched.
    (re.compile(r"仿真以"), "证据分析以"),
    (re.compile(r"(?:本次|该次|上述)?模拟(?:结果|推演)?(?:显示|表明|揭示|说明)"),
     "现有证据表明"),
    (re.compile(r"基于(?:本次|该次)?模拟(?:结果|推演)?"), "基于现有证据"),
    (re.compile(r"模拟情景中|模拟世界中|本次模拟中"), "预测情景中"),
]


_INTERNAL_TELEMETRY_HEADING_RE = re.compile(
    r"^##\s+(?:Run Telemetry|运行遥测|内部运行指标|运行成本与令牌)(?:\s+.*)?$", re.I)


def strip_internal_telemetry_appendices(md: str) -> Tuple[str, int]:
    """Remove operational cost/token tables from customer-facing Markdown.

    Telemetry remains available as ``telemetry.md`` and JSON artifacts.  This
    guard also cleans legacy reports where an older orchestrator appended it to
    ``full_report.md`` before the default was changed.
    """
    lines = (md or "").split("\n")
    out: List[str] = []
    removed = 0
    index = 0
    while index < len(lines):
        if not _INTERNAL_TELEMETRY_HEADING_RE.match(lines[index].strip()):
            out.append(lines[index])
            index += 1
            continue
        removed += 1
        index += 1
        while index < len(lines) and not re.match(r"^##\s+\S", lines[index].strip()):
            index += 1
        while out and not out[-1].strip():
            out.pop()
        if out:
            out.append("")
    return "\n".join(out), removed


def strip_internal_basis_traces(md: str) -> Tuple[str, int]:
    """Remove parenthesized ``Basis:`` graph/tool traces from customer prose.

    These traces are valuable debug provenance but are neither citations nor
    readable report content.  A tiny balanced-parenthesis scanner handles both
    ASCII and full-width wrappers and preserves all surrounding prose.
    """
    text = md or ""
    text, square_removed = re.subn(
        r"\[\s*Basis\s*:[^\]\n]*\]", "", text, flags=re.I
    )
    starts = re.compile(r"[（(]\s*Basis\s*:", re.I)
    removed = square_removed
    cursor = 0
    out: List[str] = []
    while True:
        match = starts.search(text, cursor)
        if not match:
            out.append(text[cursor:])
            break
        out.append(text[cursor:match.start()])
        depth = 0
        index = match.start()
        end = -1
        while index < len(text):
            char = text[index]
            if char in "(（":
                depth += 1
            elif char in ")）":
                depth -= 1
                if depth <= 0:
                    end = index + 1
                    break
            elif char == "\n" and depth == 1:
                break
            index += 1
        if end < 0:
            # An unterminated internal trace is still junk; remove through the
            # line boundary without consuming the next paragraph.
            newline = text.find("\n", match.start())
            end = len(text) if newline < 0 else newline
        removed += 1
        cursor = end
    cleaned = "".join(out)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+([,.;:，。；：])", r"\1", cleaned)
    return cleaned, removed


def strip_internal_graph_parentheticals(md: str) -> Tuple[str, int]:
    """Remove natural-language graph provenance that is not a reader citation.

    Legacy graph assembly leaked parentheticals such as ``(reflecting X's
    dependency relationship with Y)`` and ``(According to: `raw edge text`)``.
    They are internal trace prose, not evidence attribution.  A balanced scanner
    removes nested variants while preserving ordinary parentheticals and visible
    ``[S<n>]`` markers.
    """
    text = md or ""
    text, marker_repairs = re.subn(
        r"\(\s*According to\s+(\[S\d+\])\s*(?=\n|$)", r" \1", text,
        flags=re.I,
    )
    starts = re.compile(
        r"[（(]\s*(?:According to\s*[:：]|Source\s*[:：]|reflecting\b)", re.I
    )
    removed = marker_repairs
    cursor = 0
    out: List[str] = []
    while True:
        match = starts.search(text, cursor)
        if not match:
            out.append(text[cursor:])
            break
        out.append(text[cursor:match.start()])
        depth = 0
        index = match.start()
        end = -1
        while index < len(text):
            char = text[index]
            if char in "(（":
                depth += 1
            elif char in ")）":
                depth -= 1
                if depth <= 0:
                    end = index + 1
                    break
            elif char == "\n" and depth == 1:
                break
            index += 1
        if end < 0:
            newline = text.find("\n", match.start())
            end = len(text) if newline < 0 else newline
        fragment = text[match.start():end]
        is_according_trace = bool(re.match(
            r"[（(]\s*According to\s*[:：]", fragment, re.I
        ))
        is_relationship_trace = bool(
            re.match(r"[（(]\s*reflecting\b", fragment, re.I)
            and re.search(
                r"\b(?:relationship|edge|node|graph)\b", fragment, re.I
            )
        )
        is_source_trace = bool(
            re.match(r"[（(]\s*Source\s*[:：]", fragment, re.I)
            and re.search(
                r"\b(?:influences?|causes?|enables?|supplies|opposes?|"
                r"relationship|edge|node|graph)\b", fragment, re.I
            )
        )
        if is_according_trace or is_relationship_trace or is_source_trace:
            removed += 1
        else:
            out.append(fragment)
        cursor = end
    cleaned = "".join(out)
    cleaned = re.sub(
        r"\.\s+(?:partners with|opposes|causes|enables|supplies|depends on|"
        r"customer of)\s+[^.!?\n]{0,180}\)\)+\.",
        ".",
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+([,.;:，。；：])", r"\1", cleaned)
    return cleaned, removed


_RELATION_BULLET_RE = re.compile(
    r"^\s*[-*+]\s+.{2,120}\b(?:enables?|causes?|influences?|supplies|"
    r"opposes?|competes with|depends on|funds|partners with)\b.{0,140}$",
    re.I,
)


def strip_internal_relation_bullet_blocks(md: str) -> Tuple[str, int]:
    """Remove uncited runs of three or more graph-relation dump bullets."""
    lines = (md or "").split("\n")
    mask = _fence_mask(lines)
    delete: set[int] = set()
    index = 0
    while index < len(lines):
        if mask[index] or not _RELATION_BULLET_RE.match(lines[index]):
            index += 1
            continue
        start = index
        while (
            index < len(lines)
            and not mask[index]
            and _RELATION_BULLET_RE.match(lines[index])
            and not re.search(r"\[S\d+\]", lines[index], re.I)
        ):
            index += 1
        if index - start >= 3:
            delete.update(range(start, index))
    if not delete:
        return md, 0
    cleaned = "\n".join(line for i, line in enumerate(lines) if i not in delete)
    return re.sub(r"\n{3,}", "\n\n", cleaned), len(delete)


_GENERATION_FAILURE_RE = re.compile(
    r"Chapter generation failed|章节生成失败|模型反复未能产出合格文本|"
    r"claude-cli output contamination by system prompt",
    re.I,
)
_STANDALONE_CITATION_RE = re.compile(
    r"^\s*(?:\[S\d+\]\s*)+[.,;:，。；：]?\s*$", re.I
)


def strip_generation_failure_placeholders(md: str) -> Tuple[str, int]:
    """Remove customer-visible failed-section boilerplate before empty-section pruning."""
    lines = (md or "").split("\n")
    mask = _fence_mask(lines)
    removed = 0
    out: List[str] = []
    for index, line in enumerate(lines):
        if not mask[index] and _GENERATION_FAILURE_RE.search(line):
            removed += 1
            continue
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)), removed


def strip_standalone_citation_lines(md: str) -> Tuple[str, int]:
    """Remove source markers that annotate no claim and render as visual junk."""
    lines = (md or "").split("\n")
    mask = _fence_mask(lines)
    removed = 0
    out: List[str] = []
    for index, line in enumerate(lines):
        if not mask[index] and _STANDALONE_CITATION_RE.match(line):
            removed += 1
            continue
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)), removed


def strip_corrupted_mixed_punctuation_lines(
    md: str, lang: str = "English"
) -> Tuple[str, int]:
    """Drop legacy prose with dense punctuation from the wrong writing system."""
    if _is_zh(lang):
        return md, 0
    lines = (md or "").split("\n")
    mask = _fence_mask(lines)
    removed = 0
    out: List[str] = []
    in_references = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped in {"## References", "## 参考来源"}:
            in_references = True
        if mask[index] or in_references or not stripped:
            out.append(line)
            continue
        punctuation_count = len(re.findall(r"[（），。：；、]", stripped))
        latin_count = len(re.findall(r"[A-Za-z]", stripped))
        if (
            punctuation_count >= 2
            and latin_count >= 20
            and not _CJK_RUN_RE.search(stripped)
        ):
            removed += 1
            continue
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)), removed


def strip_empty_tables(md: str) -> Tuple[str, int]:
    """Remove malformed tables whose first row contains no semantic headers."""
    lines = (md or "").split("\n")
    mask = _fence_mask(lines)
    removed = 0
    out: List[str] = []
    index = 0

    def _semantic_cells(line: str) -> List[str]:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        return [
            re.sub(r"\[S\d+\]", "", cell, flags=re.I).strip()
            for cell in cells
        ]

    while index < len(lines):
        if mask[index] or not lines[index].strip().startswith("|"):
            out.append(lines[index])
            index += 1
            continue
        start = index
        while index < len(lines) and lines[index].strip().startswith("|"):
            index += 1
        block = lines[start:index]
        header_cells = _semantic_cells(block[0]) if block else []
        empty_header = bool(header_cells) and all(
            not cell or re.fullmatch(r"[-—–]+", cell) for cell in header_cells
        )
        if empty_header:
            removed += 1
            continue
        out.extend(block)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)), removed


def strip_empty_sections(md: str) -> Tuple[str, int]:
    """Remove H2+ sections with no prose, table, image, or other reader content."""
    text = md or ""
    total_removed = 0
    for _pass in range(3):
        lines = text.split("\n")
        headings: List[Tuple[int, int]] = []
        in_fence = False
        for index, line in enumerate(lines):
            stripped = line.lstrip()
            if stripped.startswith(("```", "~~~")):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            match = re.match(r"^(#{2,6})\s+\S", line)
            if match:
                headings.append((index, len(match.group(1))))
        delete: set[int] = set()
        for position, (start, level) in enumerate(headings):
            end = len(lines)
            for candidate_start, candidate_level in headings[position + 1:]:
                if candidate_level <= level:
                    end = candidate_start
                    break
            body = lines[start + 1:end]
            substantive = False
            body_fence = False
            for line in body:
                stripped = line.strip()
                if stripped.startswith(("```", "~~~")):
                    body_fence = not body_fence
                    substantive = True
                    continue
                if not stripped or stripped.startswith("<!--"):
                    continue
                if re.match(r"^#{2,6}\s+", stripped):
                    continue
                if _STANDALONE_CITATION_RE.match(stripped):
                    continue
                substantive = True
                break
            if not substantive:
                delete.update(range(start, end))
        if not delete:
            break
        total_removed += len([index for index in delete if re.match(
            r"^#{2,6}\s+", lines[index].strip()
        )])
        text = "\n".join(
            line for index, line in enumerate(lines) if index not in delete
        )
        text = re.sub(r"\n{3,}", "\n\n", text)
    return text, total_removed


def repair_malformed_report_prose(md: str) -> Tuple[str, int]:
    """Repair a small set of deterministic legacy splice artifacts."""
    lines = (md or "").split("\n")
    mask = _fence_mask(lines)
    repaired = 0
    rewrites: List[Tuple[re.Pattern, str]] = [
        (re.compile(r"\bthe available evidence indicates unambiguous\b", re.I),
         "the available evidence is unambiguous"),
        (re.compile(r"(?<=\d{4})(?=(?:complete|confirm|show|indicate|support)\b)", re.I),
         " "),
        (re.compile(r"\bDonald J\.\s+The China side\b"), "The China side"),
        (re.compile(
            r"\.\s+autocracies\)\s+but into chokepoint-dependency camps\.", re.I
        ), "; these dependencies divide actors into chokepoint-based camps rather than ideological blocs."),
        (re.compile(
            r"The 6-3 Supreme Court decision on 20 February 2026 in "
            r"\*Learning Resources v\.\s+(?:The evidence|The graph) records this "
            r"sequence in three separate edges:", re.I,
        ), "The 6-3 Supreme Court decision on 20 February 2026 in "
           "*Learning Resources v. Trump* redirected policy through three linked moves:"),
        (re.compile(r"\s+(?:This is|These are)\s*$", re.I), ""),
    ]
    for index, line in enumerate(lines):
        if mask[index] or not line:
            continue
        updated = line
        for pattern, replacement in rewrites:
            updated, count = pattern.subn(replacement, updated)
            repaired += count
        updated, count = re.subn(
            r"^(\s*(?:>\s*|[-*+]\s+|\d+[.)]\s+)?)the "
            r"(evidence|forecast|analysis|cross-actor)",
            lambda match: match.group(1) + "The " + match.group(2),
            updated,
        )
        repaired += count
        updated, count = re.subn(
            r"^(\s*(?:>\s*|[-*+]\s+|\d+[.)]\s+)?)the analytical perspective",
            lambda match: match.group(1) + "The analytical perspective",
            updated,
            flags=re.I,
        )
        repaired += count
        lines[index] = updated.rstrip()
    return "\n".join(lines), repaired


def scrub_simulation_mechanics(md: str, lang: str = "English") -> Tuple[str, Dict[str, int]]:
    """Remove internal simulation mechanics while retaining forecast outcomes.

    Safe phrases such as "the simulation suggests 62%" are reframed around the
    evidence and keep their numbers/citations. Residual mechanics sentences
    (rounds, action counts, platform behavior, agent-network telemetry) are
    removed. Markdown fences are untouched; tables keep their structure and
    redact only offending cells.
    """
    info = {"rewritten": 0, "sentences_removed": 0, "table_cells_redacted": 0}
    lines = (md or "").split("\n")
    mask = _fence_mask(lines)

    def _rewrite(text: str) -> str:
        out = text
        for pattern, replacement in _SIMULATION_OUTCOME_REWRITES:
            out, count = pattern.subn(replacement, out)
            info["rewritten"] += count
        return out

    def _still_leaks(text: str) -> bool:
        return bool(PLATFORM_BEHAVIOR_RE.search(text) or any(
            pattern.search(text) for _name, pattern in LEAKAGE_PATTERNS
        ))

    for index, line in enumerate(lines):
        if mask[index] or not line.strip():
            continue
        rewritten = _rewrite(line)
        if rewritten.lstrip().startswith("|") and "|" in rewritten:
            cells = rewritten.split("|")
            first_cell = 1 if rewritten.lstrip().startswith("|") else 0
            last_cell = len(cells) - 1 if rewritten.rstrip().endswith("|") else len(cells)
            for cell_index in range(first_cell, last_cell):
                if _still_leaks(cells[cell_index]):
                    cells[cell_index] = " — "
                    info["table_cells_redacted"] += 1
            lines[index] = "|".join(cells)
            continue
        if rewritten.lstrip().startswith("#"):
            # Headings have no sentence punctuation; rewrite known phrases, and
            # replace any remaining mechanics heading with an outcome-first title.
            hashes = re.match(r"^(\s*#{1,6}\s*)", rewritten)
            prefix = hashes.group(1) if hashes else ""
            heading_body = rewritten[len(prefix):].strip() if prefix else rewritten.strip()
            if heading_body.lower() == "forecast drivers and outcome pathways":
                rewritten = prefix + "Forecast Drivers and Outcome Pathways"
            elif _still_leaks(rewritten):
                rewritten = prefix + (
                    "预测驱动因素与结果路径" if _is_zh(lang)
                    else "Forecast Drivers and Outcome Pathways"
                )
                info["rewritten"] += 1
            lines[index] = rewritten
            continue

        prefix_match = re.match(r"^(\s*(?:>\s*|[-*+]\s+|\d+[.)]\s+))", rewritten)
        prefix = prefix_match.group(1) if prefix_match else ""
        body = rewritten[len(prefix):] if prefix else rewritten
        sentences = _split_sentences(body)
        kept: List[str] = []
        for sentence in sentences:
            if _still_leaks(sentence):
                info["sentences_removed"] += 1
                continue
            if sentence.strip():
                # _split_sentences deliberately retains the separator after
                # each sentence. Stripping here glued clean English sentences
                # together (".[S1].Supply") across every backfilled report.
                kept.append(sentence)
        lines[index] = (prefix + "".join(kept).strip()).rstrip() if kept else ""
    return "\n".join(lines), info


_MISSING_SENTENCE_SPACE_RE = re.compile(
    r"(?<=[a-z0-9\]\)\"”’」』])([.!?])(?=[A-Z])")


def repair_missing_sentence_spaces(md: str) -> Tuple[str, int]:
    """Repair legacy ``Sentence.Next`` joins without touching URLs/initialisms.

    The narrow boundary requires a lowercase/digit/closer before the terminal
    and an uppercase-led word after it. This fixes the historical lint bug
    while leaving ``N.V.``, ``U.S.``, decimals, and lowercase domains intact.
    """
    lines = (md or "").split("\n")
    mask = _fence_mask(lines)
    repaired = 0
    for index, line in enumerate(lines):
        if mask[index] or not line:
            continue
        lines[index], count = _MISSING_SENTENCE_SPACE_RE.subn(r"\1 ", line)
        repaired += count
    return "\n".join(lines), repaired


def drop_platform_behavior_quotes(md: str) -> Tuple[str, int]:
    """删除内容为平台行为机制（发帖/点赞/评论/转发）的 blockquote 行——这类引文应删不应转写。"""
    removed = 0
    lines = (md or "").split("\n")
    mask = _fence_mask(lines)
    out: List[str] = []
    for i, ln in enumerate(lines):
        s = ln.strip()
        if (not mask[i]) and s.startswith(">") and PLATFORM_BEHAVIOR_RE.search(s):
            removed += 1
            continue
        out.append(ln)
    return "\n".join(out), removed


# ──────────────────────────────────────────────────────────────
# 入口：lint_report
# ──────────────────────────────────────────────────────────────

def lint_report(md: str, lang: str, mode: str = "final",
                spine: Optional[Dict[str, Any]] = None) -> Tuple[str, Dict[str, Any]]:
    """确定性编辑纪律 lint + 修复。纯函数（无状态、无 LLM、无 IO）。

    Args:
        md: 报告 markdown。
        lang: 目标输出语言（"English"/"Chinese"，其它按 CJK 处理）。
        mode: "final"（成稿）| "research"（研究档案——额外剥离 pass 叙述括注）。
        spine: 可选预测骨架 dict（{"scenarios": [{name, probability}...]}）——传入时做
            情景概率交叉核对（只记数不改写）。

    Returns:
        (cleaned_md, report_dict)：清理后的 markdown + 逐类别命中/动作报告。
    """
    text = md or ""
    rep: Dict[str, Any] = {"mode": mode, "lang": str(lang or "")}

    text, rep["citation_residue"] = strip_citation_residue(text)
    text, rep["edge_dumps"], rep["dangling_edge_intros"] = rewrite_edge_dumps(text, lang)
    text, rep["legacy_sim_labels"] = rewrite_sim_labels(text, lang)
    text, rep["tool_tokens"] = strip_tool_tokens(text)
    if mode == "final":
        text, rep["generation_failure_placeholders"] = strip_generation_failure_placeholders(text)
        text, rep["internal_telemetry_appendices"] = strip_internal_telemetry_appendices(text)
        text, rep["internal_basis_traces"] = strip_internal_basis_traces(text)
        text, rep["internal_graph_parentheticals"] = strip_internal_graph_parentheticals(text)
        text, rep["internal_relation_bullets"] = strip_internal_relation_bullet_blocks(text)
        text, rep["standalone_citation_lines"] = strip_standalone_citation_lines(text)
        text, rep["corrupted_mixed_punctuation_lines"] = (
            strip_corrupted_mixed_punctuation_lines(text, lang)
        )
        text, rep["empty_tables"] = strip_empty_tables(text)
    else:
        rep["generation_failure_placeholders"] = 0
        rep["internal_telemetry_appendices"] = 0
        rep["internal_basis_traces"] = 0
        rep["internal_graph_parentheticals"] = 0
        rep["internal_relation_bullets"] = 0
        rep["standalone_citation_lines"] = 0
        rep["corrupted_mixed_punctuation_lines"] = 0
        rep["empty_tables"] = 0
    text, rep["dangling_attributions"] = remove_dangling_attributions(text)
    text, _pn_stripped, _pn_flagged = strip_pass_narration(text)
    rep["pass_narration"] = {"stripped": _pn_stripped, "flagged": _pn_flagged}
    text, rep["citation_variants"] = normalize_citation_variants(text)
    text, rep["duplicate_sentences_removed"] = dedup_duplicate_sentences(text)
    if mode == "final":
        text, rep["simulation_mechanics"] = scrub_simulation_mechanics(text, lang)
        text, rep["malformed_prose_repairs"] = repair_malformed_report_prose(text)
        text, rep["sentence_spaces_repaired"] = repair_missing_sentence_spaces(text)
        text, rep["empty_sections"] = strip_empty_sections(text)
    else:
        rep["simulation_mechanics"] = {
            "rewritten": 0, "sentences_removed": 0, "table_cells_redacted": 0,
        }
        rep["malformed_prose_repairs"] = 0
        rep["sentence_spaces_repaired"] = 0
        rep["empty_sections"] = 0

    # 检测类（不改写）
    rep["language_contamination"] = detect_language_contamination(text, lang)
    rep["table_cell_truncations"] = detect_table_cell_truncation(text)
    rep["scenario_prob_mismatches"] = check_scenario_probabilities(text, spine)
    rep["leakage_flags"] = len(leakage_hits(text))
    rep["outcome_focus_ok"] = rep["leakage_flags"] == 0
    rep["changed"] = text != (md or "")
    return text, rep
