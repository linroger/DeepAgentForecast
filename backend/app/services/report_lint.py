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
]

# CJK 字符类（与 report_agent._CJK_CHAR 同源）
_CJK_CHAR = r"一-鿿㐀-䶿぀-ヿ가-힯"
_CJK_RUN_RE = re.compile(r"[" + _CJK_CHAR + r"]{2,}")
_LATIN_PROSE_RE = re.compile(r"[A-Za-z][A-Za-z0-9 ,.'’\-()%/&]{39,}")

# ── Tier-2 泄漏检测模式（EN + ZH；导出供 report_agent 泄漏修复复用）──
LEAKAGE_PATTERNS: List[Tuple[str, re.Pattern]] = [
    # EN
    ("simulation_agent_label", re.compile(r"\bSimulation Agent\b")),
    ("deduction_reasoning", re.compile(r"Deduction\s*/\s*Reasoning")),
    ("the_simulation", re.compile(r"\b[Tt]he simulation(?:'s)?\b")),
    ("simulation_surfaced", re.compile(r"simulation (?:consistently |repeatedly )?surfaced", re.I)),
    ("n_actor_simulation", re.compile(r"\b\d+[- ](?:actor|agent|entity) simulation\b", re.I)),
    ("n_actions", re.compile(r"\b\d+\s+actions\b", re.I)),
    ("round_n", re.compile(r"\bround\s+\d+\b", re.I)),
    ("action_type_tokens", re.compile(
        r"\b(?:CREATE_POST|CREATE_COMMENT|LIKE_POST|LIKE_COMMENT|REPOST|QUOTE_POST|UNFOLLOW)\b")),
    ("agent_mechanics_en", re.compile(
        r"\bagent (?:discourse|network|behavio(?:r|ur))\b|\bmost active agents?\b"
        r"|\bconsensus formation\b|\brevealed preference\b", re.I)),
    ("sim_causal_graph", re.compile(
        r"(?:simulation|模拟)[^\n]{0,20}causal graph|causal graph[^\n]{0,20}(?:simulation|模拟)", re.I)),
    ("raw_edge", re.compile(r"--\[[A-Z_]+\]-->")),
    # ZH
    ("sim_agent_zh", re.compile(r"模拟代理人|模拟推演|模拟世界|本次模拟|模拟中|模拟的|智能体")),
    ("sim_mechanics_zh", re.compile(
        r"次动作|逐轮|轮次|峰值轮次|最活跃\s*Agent|派系图|派系聚类|共识形成|因果图"
        r"|揭示性偏好|行为信号|上帝视角|采访实录|模拟量化")),
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
            return f"Analytical perspective — {name} (scenario panel):"
        return f"the analytical perspective of {name} (scenario panel)"

    def _zh_repl(m: re.Match) -> str:
        name = m.group(1).strip()
        if m.group(2):
            return f"情景推演专家视角——「{name}」："
        return f"「{name}」的情景推演专家视角"

    lines = (md or "").split("\n")
    mask = _fence_mask(lines)
    for i, ln in enumerate(lines):
        if mask[i]:
            continue
        new_ln, k1 = _SIM_LABEL_EN_RE.subn(_en_repl, ln)
        new_ln, k2 = _SIM_LABEL_ZH_RE.subn(_zh_repl, new_ln)
        new_ln, k3 = _SIM_LABEL_GARBLE_RE.subn("情景推演专家视角", new_ln)
        if k1 or k2 or k3:
            lines[i] = new_ln
            n += k1 + k2 + k3
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
    r"|according to|pushes? back|contend|assert|warn|observ", re.I)


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
    """跨语言污染行检测（不改写）：英文稿中的 CJK 行 / 中文稿中的长英文散文行。"""
    zh_target = _is_zh(lang)
    lines = (md or "").split("\n")
    mask = _fence_mask(lines)
    hits = 0
    samples: List[str] = []
    for i, ln in enumerate(lines):
        if mask[i]:
            continue
        s = ln.strip()
        if not s or s.startswith(("#", "|")) or "http" in s:
            continue
        if zh_target:
            found = [c for c in _LATIN_PROSE_RE.findall(s) if c.count(" ") >= 4]
        else:
            found = _CJK_RUN_RE.findall(s)
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


_SENT_SPLIT_RE = re.compile(r"(?<=[。！？.!?])\s+")


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
        sentences = _SENT_SPLIT_RE.split(ln)
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
            lines[i] = " ".join(x for x in kept if x.strip()).strip()
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
    for name, pat in LEAKAGE_PATTERNS:
        hits.extend(name for _ in pat.finditer(text or ""))
    return hits


def strip_leakage_sentences(text: str, max_scan: int = 400) -> Tuple[str, int]:
    """把散文文本中命中 Tier-2 泄漏模式的**整句**删除（用于 key-points 提炼与重写后兜底）。"""
    if not text:
        return text or "", 0
    sentences = _SENT_SPLIT_RE.split(text)
    kept: List[str] = []
    removed = 0
    for sent in sentences[:max_scan]:
        if any(pat.search(sent) for _, pat in LEAKAGE_PATTERNS):
            removed += 1
            continue
        kept.append(sent)
    kept.extend(sentences[max_scan:])
    return " ".join(x for x in kept if x.strip()).strip(), removed


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
    text, rep["dangling_attributions"] = remove_dangling_attributions(text)
    text, _pn_stripped, _pn_flagged = strip_pass_narration(text)
    rep["pass_narration"] = {"stripped": _pn_stripped, "flagged": _pn_flagged}
    text, rep["citation_variants"] = normalize_citation_variants(text)
    text, rep["duplicate_sentences_removed"] = dedup_duplicate_sentences(text)

    # 检测类（不改写）
    rep["language_contamination"] = detect_language_contamination(text, lang)
    rep["table_cell_truncations"] = detect_table_cell_truncation(text)
    rep["scenario_prob_mismatches"] = check_scenario_probabilities(text, spine)
    rep["leakage_flags"] = len(leakage_hits(text))
    rep["changed"] = text != (md or "")
    return text, rep
