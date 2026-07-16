"""Session-B 技能瘦身 + 市场停查规则的卫生守卫。

背景（2026-07 循环取证）：actor-ontology SKILL.md 曾膨胀到 ~30.6KB（≈7.7K tokens），
在一次 87 分钟的 turn 里随**每个** provider 请求重复发送，且 ≈40% 内容是同一规则的
第 2~4 次复述；同时 deep-research 的 core loop 把 prediction_market_search 写成每阶段
必查，导致零覆盖问题上单次运行浪费 93 次工具调用 + 41 次传输失败重试。本文件守住
瘦身后的四个不变式：

1. actor-ontology SKILL.md 保持 < 20,000 字节，承重小节（triage / 边契约 / 输出契约）
   与合法 YAML frontmatter 完整；
2. 任何 bridge 技能不得再用「deep-research §N」式悬空引用（deep-research 核心早已无
   编号小节，引用必须落到真实标题或 references/ 文件名上）；
3. 记者/媒体降级规则（outlet-as-actor 的唯一判据句）在 actor-ontology 里恰好出现一次
   （home section 保留，其余位置只允许按标题名引用，不得复述）；
4. deep-research 开局一次全量市场扫 + 零覆盖后停查 + 传输失败不同阶段内重试的限定词
   落盘，且 prediction-markets 侧有配对的 no_market_coverage 停查规则。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPO_ROOT / "deerflow_bridge" / "skills"
ACTOR_SKILL = SKILLS_ROOT / "actor-ontology-research" / "SKILL.md"
DEEP_SKILL = SKILLS_ROOT / "deep-research" / "SKILL.md"
MARKET_SKILL = SKILLS_ROOT / "prediction-markets" / "SKILL.md"
SCORING_RUBRIC = SKILLS_ROOT / "actor-ontology-research" / "references" / "scoring-rubric.md"

# actor-ontology 的承重标题：triage（§2）、边契约（§4）、输出契约（§9）+ 运行时角色契约
# 与判分小节。断言用「标题核」前缀，容忍括注尾巴措辞微调（与 drf2 漂移守卫的归一化一致）。
LOAD_BEARING_HEADINGS = (
    "## 2. Who counts as a key actor",
    "### 2.1 The three-way separation",
    "### 3.1 Runtime role-contract handoff",
    "## 4. The relationship network",
    "## 8. The judge rubric",
    "## 9. Output contract",
)

# 「§ 引用指向 deep-research」的两类形状：
#   deep-research 名字后 ≤25 字符内出现 §（如 "per `deep-research` §10"、"`deep-research` §12"）；
#   "(§7 there)" 式的 there-回指。
DANGLING_REF_PATTERNS = (
    re.compile(r"deep-research\S*[^\n]{0,25}§"),
    re.compile(r"§\s*\d+(?:\.\d+)?\s+there\b"),
)

OUTLET_RULE_PHRASE = "actor only if it is itself a principal or amplifier"


def _frontmatter(text: str) -> dict:
    match = re.match(r"\A---\n(.*?)\n---\n", text, flags=re.DOTALL)
    assert match, "SKILL.md must open with a YAML frontmatter block"
    data = yaml.safe_load(match.group(1))
    assert isinstance(data, dict), "frontmatter must parse to a mapping"
    return data


class TestActorOntologyCompressed:
    def test_under_size_budget_with_load_bearing_headings(self):
        raw = ACTOR_SKILL.read_bytes()
        assert len(raw) < 20_000, (
            f"actor-ontology SKILL.md is {len(raw)}B — the compressed budget is <20,000B; "
            f"new normative content belongs in a lazy references/ file, not the resident body")
        text = raw.decode("utf-8")
        for heading in LOAD_BEARING_HEADINGS:
            assert any(line.startswith(heading) for line in text.splitlines()), (
                f"load-bearing heading {heading!r} missing — compression must not drop sections")

    def test_frontmatter_intact(self):
        data = _frontmatter(ACTOR_SKILL.read_text(encoding="utf-8"))
        assert data.get("name") == "actor-ontology-research"
        description = str(data.get("description", ""))
        assert "deep-research" in description and "AI-judge" in description, (
            "frontmatter description lost its inheritance/judge-gate declaration")

    def test_outlet_demotion_rule_appears_exactly_once(self):
        # home section 是 §2.1 的 reporter/outlet rule；其余位置只能按标题名引用。
        text = ACTOR_SKILL.read_text(encoding="utf-8")
        count = text.count(OUTLET_RULE_PHRASE)
        assert count == 1, (
            f"outlet/reporter demotion rule must appear exactly once (found {count}) — "
            f"restating it re-inflates the skill; reference the home section by name instead")

    def test_scoring_rubric_reference_exists(self):
        assert SCORING_RUBRIC.is_file(), "references/scoring-rubric.md missing"
        body = SCORING_RUBRIC.read_text(encoding="utf-8")
        assert body.strip(), "scoring-rubric.md must not be empty"
        assert "5/5" in body and "≤2" in body, "rubric anchors (5/5 vs ≤2) missing"
        # SKILL.md 必须留一行指针，判官阶段才知道去加载它
        assert "references/scoring-rubric.md" in ACTOR_SKILL.read_text(encoding="utf-8")


class TestNoDanglingDeepResearchRefs:
    def test_no_section_number_refs_point_at_deep_research(self):
        offenders: list[str] = []
        for skill_md in sorted(SKILLS_ROOT.glob("*/SKILL.md")):
            for lineno, line in enumerate(
                    skill_md.read_text(encoding="utf-8").splitlines(), start=1):
                for pattern in DANGLING_REF_PATTERNS:
                    if pattern.search(line):
                        offenders.append(f"{skill_md.parent.name}/SKILL.md:{lineno}: {line.strip()}")
        assert not offenders, (
            "dangling deep-research §-references (its core has no numbered sections; "
            "point at a real heading or references/ file instead):\n" + "\n".join(offenders))


class TestMarketNoCoverageStopRule:
    def test_deep_research_core_loop_qualifier(self):
        text = DEEP_SKILL.read_text(encoding="utf-8")
        assert "once, in the opening pass" in text, "opening-pass-only market sweep missing"
        assert "zero topically relevant markets" in text, "no-coverage skip qualifier missing"
        assert "no_market_coverage" in text, "no_market_coverage record instruction missing"
        assert "must not be retried within the same phase" in text, (
            "transport-failure no-retry rule missing")

    def test_prediction_markets_matching_stop_rule(self):
        text = MARKET_SKILL.read_text(encoding="utf-8")
        assert "no_market_coverage" in text, "matching stop rule missing in prediction-markets"
        assert "stop querying markets" in text
        assert "never loop retries" in text.lower(), "transport_failure retry-loop ban missing"
