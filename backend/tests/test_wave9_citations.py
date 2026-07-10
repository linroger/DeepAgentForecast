"""WAVE10 无缝引用（seamless citations）离线测试——单一语法 / 最终化 / 修复 / 对账 / PDF。

无 LLM / 无网络：与其余报告测试同一套 __new__ + 属性注入 harness；ReportManager.REPORTS_DIR
重定向进 tmp。覆盖：

  * actors.sources_index_unified —— 单一 [S<n>] 语法、层级降级为注记、相关性排序截取、
    编号锚定原始位置；sources_index_tiered_map 与渲染同序；
  * ReportAgent._build_sources_index —— 返回 (文本, 记号→来源映射)，旋钮关闭回退旧位置索引；
  * forecast_extractor.validate_citation_markers —— 首现顺序 / 计数 / 悬空 / 未引用 / 围栏跳过；
  * audit_citation_grounding(index_map=…) —— resolved_* 独立指标，缺省输出不变；
  * _finalize_citations —— References/参考来源附录（只列被引用来源）、围栏不动、URL 有效性
    守卫、citations.json 工件、幂等重跑；
  * _run_repair_passes 悬空维度 —— 保留验证（登记进索引）/ 重映射 / 删除；
  * 双语引用对账 —— 漂移块带精确记号清单重译一次；仍漂移 → warning + citation_drift；
  * ReportManager._rewrite_citations_for_pdf —— 首次出现转 pandoc 脚注、围栏/附录/不可解析
    记号不动、脚注定义用短域名链接。
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Config  # noqa: E402
from app.services.report_agent import ReportAgent, ReportManager  # noqa: E402
from app.services.forecast_extractor import (  # noqa: E402
    audit_citation_grounding, validate_citation_markers,
)
from app.utils import actors as actors_mod  # noqa: E402


# ─────────────────────────────── helpers ────────────────────────────────
def _agent(**over):
    a = ReportAgent.__new__(ReportAgent)
    a.sources = []
    a.research_report = ""
    a.situation_brief = ""
    a._background_block = ""
    a._outline_summary = ""
    a.output_language = "English"
    for k, v in over.items():
        setattr(a, k, v)
    return a


@pytest.fixture
def reports_tmp(tmp_path, monkeypatch):
    d = tmp_path / "reports"
    d.mkdir()
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(d))
    return str(d)


_SOURCES = [
    {"title": "www.mckinsey", "url": "https://www.mckinsey", "tier": "S3"},   # S1 截断 URL
    {"title": "Reuters chip export report", "url": "https://www.reuters.com/tech/chips-2027",
     "tier": "S2", "date": "2027-01-15"},                                     # S2
    {"title": "BIS export control notice", "url": "https://bis.gov/notice/42",
     "tier": "S1", "date": "2026-11-01"},                                     # S3
    {"title": "Some blog take", "url": "https://blog.example.com/post/1"},    # S4 无层级
    "not-a-dict",                                                             # S5 非法行（保位）
    {"title": "Analyst tail note", "url": "https://note.example.com/a", "tier": "S4"},  # S6
]


# ───────────────────── actors.sources_index_unified ─────────────────────
def test_sources_index_unified_grammar_ranking_and_positions():
    research = "According to https://www.reuters.com/tech/chips-2027 the flows shifted."
    text, tag_map = actors_mod.sources_index_unified(_SOURCES, research, max_sources=3)
    # 单一语法：索引头声明裸 [S12] 形状；不再出现 [S1-a] 语法。
    assert "[S12]" in text.split("\n")[0]
    assert "-a]" not in text
    # 相关性排序截取：被研究报告引用的 S2 必入选；S1 层级的 S3 必入选。
    assert "S2" in tag_map and "S3" in tag_map
    assert len(tag_map) == 3
    # 编号锚定原始位置：非法行占位（S5 不存在，S6 编号不漂移到 S5）。
    assert "S5" not in tag_map
    # 层级降级为标题后注记（渲染含「S1·」注记而非记号语法）。
    assert "S1·" in text
    # 记号映射与渲染一致：每个入选记号都出现在文本里。
    for tag in tag_map:
        assert f"[{tag}]" in text


def test_sources_index_unified_empty():
    assert actors_mod.sources_index_unified(None) == ("", {})
    assert actors_mod.sources_index_unified([]) == ("", {})


def test_sources_index_tiered_map_matches_render():
    text = actors_mod.sources_index_tiered(_SOURCES)
    tag_map = actors_mod.sources_index_tiered_map(_SOURCES)
    for tag in tag_map:
        assert f"[{tag}]" in text


# ───────────────────── ReportAgent._build_sources_index ──────────────────
def test_build_sources_index_returns_text_and_map():
    a = _agent(sources=list(_SOURCES), research_report="")
    text, tag_map = a._build_sources_index()
    assert text and isinstance(tag_map, dict) and tag_map
    assert all(t.startswith("S") for t in tag_map)


def test_build_sources_index_legacy_fallback(monkeypatch):
    monkeypatch.setattr(Config, "REPORT_CITATION_SINGLE_GRAMMAR", False, raising=False)
    monkeypatch.setattr(Config, "RESEARCH_EVIDENCE_GRADING", False, raising=False)
    a = _agent(sources=list(_SOURCES), research_report="")
    text, tag_map = a._build_sources_index()
    assert "【可引用来源（正文用 [S1]/[S2] 形式标注）】" in text
    assert "S1" in tag_map and "S2" in tag_map
    # 旧位置索引同样跳过非法行但保位（S5 缺、S6 在）。
    assert "S5" not in tag_map and "S6" in tag_map


def test_build_sources_index_no_sources():
    a = _agent(sources=[])
    assert a._build_sources_index() == ("", {})


# ───────────────────── validate_citation_markers ────────────────────────
_MD_MARKERS = """# T

Growth hit 42% [S1] and later [S1] again.

Second claim 17% [S2] plus legacy 【S3】 variant.

```mermaid
graph TD; A-->B; %% [S99] inside fence must not count
```

Hallucinated tail [S246].
"""


def test_validate_citation_markers_order_counts_dangling_uncited():
    imap = {"S1": {}, "S2": {}, "S3": {}, "S4": {}}
    v = validate_citation_markers(_MD_MARKERS, imap)
    assert v["order"] == ["S1", "S2", "S3", "S246"]
    assert v["counts"]["S1"] == 2 and v["total_markers"] == 5
    assert v["dangling"] == ["S246"]
    assert v["uncited"] == ["S4"]
    assert "S99" not in v["counts"]          # 围栏内不计


def test_validate_citation_markers_no_map():
    v = validate_citation_markers("Line [S7].", None)
    assert v["order"] == ["S7"] and v["dangling"] == [] and v["uncited"] == []


# ───────────────────── audit resolved metrics ────────────────────────────
def test_audit_citation_grounding_resolved_metrics_are_separate():
    md = "Adoption 42% [S1].\nDecline 17% [S246].\n"
    base = audit_citation_grounding(md)
    assert "resolved_coverage" not in base            # 缺省输出与历史一致
    assert base["coverage"] == 1.0
    strict = audit_citation_grounding(md, index_map={"S1": {}})
    assert strict["coverage"] == 1.0                  # 发布门口径不动
    assert strict["resolved_cited"] == 1
    assert strict["resolved_coverage"] == 0.5


# ───────────────────── citation finalizer ────────────────────────────────
_MD_FINAL = """# Report Title

> Summary line.

## Findings

Growth hit 42% [S2] and controls tightened [S3]. Weak take [S1].

```mermaid
graph TD; A-->B; %% [S2] literal in fence
```

## Outlook

Repeat citation [S2] and a dangling one [S99].
"""


def test_finalize_citations_appends_references_and_writes_json(reports_tmp):
    rid = "report_cite_fin"
    ReportManager._ensure_report_folder(rid)
    a = _agent(sources=list(_SOURCES), output_language="English")
    a._citation_index = {"S1": _SOURCES[0], "S2": _SOURCES[1], "S3": _SOURCES[2]}

    class _Rep:
        markdown_content = _MD_FINAL

    rep = _Rep()
    a._finalize_citations(rid, rep)
    md = rep.markdown_content
    # References 附录追加在文末，只列被引用来源，按首现顺序编号。
    assert "## References" in md
    refs = md.split("## References", 1)[1]
    assert refs.index("[S2]") < refs.index("[S3]") < refs.index("[S1]")
    # 正文内联记号不可变；围栏内容原样。
    assert md.count("[S2]") >= 3 and "%% [S2] literal in fence" in md
    # 悬空 [S99] 不入附录（保留在正文）。
    assert "[S99]" in md.split("## References")[0]
    assert "[S99]" not in refs
    # URL 守卫：S2 是可点击链接，S1（截断 mckinsey）不渲染为链接。
    assert "[https://www.reuters.com/tech/chips-2027](https://www.reuters.com/tech/chips-2027)" in refs
    assert "](https://www.mckinsey)" not in refs
    assert "`https://www.mckinsey`" in refs
    # citations.json 工件：markers + unresolved。
    cpath = os.path.join(reports_tmp, rid, "citations.json")
    assert os.path.exists(cpath)
    with open(cpath, encoding="utf-8") as f:
        data = json.load(f)
    tags = [m["tag"] for m in data["markers"]]
    assert tags == ["S2", "S3", "S1"]
    by_tag = {m["tag"]: m for m in data["markers"]}
    assert by_tag["S1"]["url_valid"] is False and by_tag["S2"]["url_valid"] is True
    assert by_tag["S2"]["display"] == 1 and by_tag["S2"]["count"] == 2
    assert data["unresolved"] == [{"tag": "S99", "count": 1}]
    # full_report.md 已回写。
    with open(os.path.join(reports_tmp, rid, "full_report.md"), encoding="utf-8") as f:
        assert "## References" in f.read()
    # 幂等重跑：附录不重复。
    a._finalize_citations(rid, rep)
    assert rep.markdown_content.count("## References") == 1


def test_finalize_citations_zh_heading_and_noop_without_citations(reports_tmp):
    rid = "report_cite_zh"
    ReportManager._ensure_report_folder(rid)
    a = _agent(sources=list(_SOURCES), output_language="Chinese")
    a._citation_index = {"S2": _SOURCES[1]}

    class _Rep:
        markdown_content = "# 标题\n\n## 章节\n\n增长 42% [S2]。\n"

    rep = _Rep()
    a._finalize_citations(rid, rep)
    assert "## 参考来源" in rep.markdown_content
    # 无引用成稿：不追加附录、成稿不动。
    class _Rep2:
        markdown_content = "# 标题\n\n## 章节\n\n没有引用的正文。\n"

    rep2 = _Rep2()
    ReportManager._ensure_report_folder("report_cite_zh2")
    a._finalize_citations("report_cite_zh2", rep2)
    assert "参考来源" not in rep2.markdown_content


# ───────────────────── dangling repair in the repair chain ───────────────
def test_run_repair_passes_dangling_keep_and_strip(reports_tmp):
    s1 = {"title": "Alpha dossier", "content": "alpha metric 37% detail"}
    s2 = {"title": "Beta dossier", "content": "beta metric 55% detail"}
    a = _agent(sources=[s1, s2])
    a._citation_index = {"S1": s1}          # 注入索引只含 S1 → [S2] 假悬空、[S77] 真悬空
    md = ("# T\n\n"
          "Line one 37% [S1].\n\n"
          "Line two 55% [S2].\n\n"
          "Line three 99% [S77].\n")
    forecast = {"citation_audit": {"coverage": 1.0, "quantitative_claims": 3}, "quality": {}}
    new_md = a._run_repair_passes("rid_dangle", forecast, md, report=None)
    # [S2] 数字锚定命中全量列表第 2 条 → 保留并登记进索引。
    assert "[S2]" in new_md and "S2" in a._citation_index
    # [S77] 无锚点 → 删除。
    assert "[S77]" not in new_md
    rep = forecast["quality"]["repair"]
    dg = next(p for p in rep["passes"] if p["dimension"] == "citation_dangling")
    assert dg["kept_verified"] == 1 and dg["stripped"] == 1
    assert rep["before"]["dangling_citations"] == 2
    assert rep["after"]["dangling_citations"] == 0


def test_run_repair_passes_dangling_remap():
    s1 = {"title": "Alpha dossier", "content": "alpha metric 37% detail"}
    s2 = {"title": "Beta dossier", "content": "beta metric 55% detail"}
    a = _agent(sources=[s1, s2])
    a._citation_index = {"S1": s1}
    md = "Line 55% [S246].\n"
    forecast = {"citation_audit": {"coverage": 1.0, "quantitative_claims": 1}, "quality": {}}
    new_md = a._run_repair_passes("rid_remap", forecast, md, report=None)
    # 编号超全量列表 → 数字锚定重映射到命中来源 [S2]。
    assert "[S2]" in new_md and "[S246]" not in new_md
    dg = next(p for p in forecast["quality"]["repair"]["passes"]
              if p["dimension"] == "citation_dangling")
    assert dg["remapped"] == 1


def test_run_repair_passes_dangling_disabled(monkeypatch):
    monkeypatch.setattr(Config, "REPORT_CITATION_REPAIR", False, raising=False)
    a = _agent(sources=[{"title": "x", "content": "37%"}])
    a._citation_index = {"S1": a.sources[0]}
    md = "Line 99% [S77].\n"
    forecast = {"citation_audit": {"coverage": 1.0, "quantitative_claims": 1}, "quality": {}}
    out = a._run_repair_passes("rid_off", forecast, md, report=None)
    assert out == md and "repair" not in forecast["quality"]


# ───────────────────── bilingual citation parity ─────────────────────────
_EN_CITED = """# Outlook Report

> Summary.

## Findings

The Outlook base case is 42% [S1] with confirmation [S1] and a floor [S2].

## Tail

The Outlook tail sits at 21%.
"""


class _CiteLLM:
    """首译丢一个 [S1]；重试（系统提示词含记号清单）时 drop_always 决定是否补齐。"""

    def __init__(self, drop_always=False):
        self.model = "fake"
        self.provider = "fake"
        self.calls = []
        self.drop_always = drop_always

    def chat(self, messages=None, temperature=0.1, max_tokens=4096, tier="strong", **kw):
        self.calls.append(messages)
        sys_p = (messages or [{}])[0].get("content", "")
        user = (messages or [{}])[-1].get("content", "")
        out = user.replace("Outlook", "展望")
        is_retry = "CITATION TOKEN INVENTORY" in sys_p
        if self.drop_always or not is_retry:
            out = out.replace(" [S1]", "", 1)
        return out


def test_bilingual_parity_retry_restores_markers(reports_tmp, monkeypatch):
    monkeypatch.setattr(Config, "REPORT_TRANSLATION_CONCURRENCY", 1, raising=False)
    rid = "report_parity_ok"
    ReportManager._ensure_report_folder(rid)
    from app.services.report_agent import Report, ReportStatus
    report = Report(report_id=rid, simulation_id="s", graph_id="g",
                    simulation_requirement="req", status=ReportStatus.COMPLETED,
                    markdown_content=_EN_CITED)
    llm = _CiteLLM(drop_always=False)
    a = _agent(llm=llm, output_language="English")
    a._generate_bilingual_report(rid, report)
    zh_path = os.path.join(reports_tmp, rid, "full_report.zh.md")
    assert os.path.exists(zh_path)
    with open(zh_path, encoding="utf-8") as f:
        out = f.read()
    assert out.count("[S1]") == 2 and out.count("[S2]") == 1   # 重译补齐
    assert any("CITATION TOKEN INVENTORY" in (c or [{}])[0].get("content", "")
               for c in llm.calls)                              # 确实带清单重试过
    entry = report.translations[0]
    assert entry["translation_quality"] == "ok"
    assert "citation_drift" not in entry


def test_bilingual_parity_persistent_drift_warns(reports_tmp, monkeypatch):
    monkeypatch.setattr(Config, "REPORT_TRANSLATION_CONCURRENCY", 1, raising=False)
    rid = "report_parity_drift"
    ReportManager._ensure_report_folder(rid)
    from app.services.report_agent import Report, ReportStatus
    report = Report(report_id=rid, simulation_id="s", graph_id="g",
                    simulation_requirement="req", status=ReportStatus.COMPLETED,
                    markdown_content=_EN_CITED)
    a = _agent(llm=_CiteLLM(drop_always=True), output_language="English")
    a._generate_bilingual_report(rid, report)
    entry = report.translations[0]
    assert entry["translation_quality"] == "warning"
    assert entry["citation_drift"]
    assert entry["citation_drift"][0]["diff"]["S1"] == {"src": 2, "dst": 1}


def test_bilingual_parity_disabled_keeps_first_pass(reports_tmp, monkeypatch):
    monkeypatch.setattr(Config, "REPORT_TRANSLATION_CONCURRENCY", 1, raising=False)
    monkeypatch.setattr(Config, "REPORT_TRANSLATION_CITATION_PARITY", False, raising=False)
    rid = "report_parity_off"
    ReportManager._ensure_report_folder(rid)
    from app.services.report_agent import Report, ReportStatus
    report = Report(report_id=rid, simulation_id="s", graph_id="g",
                    simulation_requirement="req", status=ReportStatus.COMPLETED,
                    markdown_content=_EN_CITED)
    llm = _CiteLLM(drop_always=False)
    a = _agent(llm=llm, output_language="English")
    a._generate_bilingual_report(rid, report)
    # 关闭对账：不重试（无 INVENTORY 调用）、丢标记不告警（历史行为）。
    assert not any("CITATION TOKEN INVENTORY" in (c or [{}])[0].get("content", "")
                   for c in llm.calls)
    assert report.translations[0]["translation_quality"] == "ok"


# ───────────────────── PDF footnote rewrite ───────────────────────────────
_CITATIONS_MAP = {
    "S1": {"tag": "S1", "title": "Reuters chip export report", "domain": "reuters.com",
           "date": "2027-01-15", "url": "https://www.reuters.com/tech/chips-2027",
           "url_valid": True},
    "S3": {"tag": "S3", "title": "www.mckinsey", "domain": "www.mckinsey",
           "date": "", "url": "https://www.mckinsey", "url_valid": False},
}

_MD_PDF = """# T

## Body

First use 42% [S1] then repeat [S1] and unresolvable [S9] and bad-url [S3].

```python
x = "[S1] literal stays"
```

## References

1. [S1] Reuters chip export report — reuters.com
"""


def test_rewrite_citations_for_pdf_footnotes_fence_and_refs_safe():
    out = ReportManager._rewrite_citations_for_pdf(_MD_PDF, _CITATIONS_MAP)
    body = out.split("## References", 1)[0]
    # 首次出现 → 脚注引用；重复出现保留字面记号（正文 1 处 + 围栏内 1 处字面）。
    assert "[^s1]" in body and "then repeat [S1]" in body
    assert body.count("[S1]") == 2
    # 不可解析 [S9] 原样。
    assert "[S9]" in body
    # 围栏内字面内容不动。
    assert 'x = "[S1] literal stays"' in out
    # References 附录里的条目标签不被改写。
    assert "1. [S1] Reuters chip export report" in out
    # 脚注定义追加在文末：有效 URL 用短域名链接；无效 URL 无链接。
    assert "[^s1]: Reuters chip export report — reuters.com, 2027-01-15 " \
           "[reuters.com](https://www.reuters.com/tech/chips-2027)" in out
    assert "[^s3]: www.mckinsey" in out
    assert "](https://www.mckinsey)" not in out


def test_rewrite_citations_for_pdf_empty_map_is_noop():
    assert ReportManager._rewrite_citations_for_pdf(_MD_PDF, {}) == _MD_PDF


def test_load_citations_map_missing_and_valid(tmp_path):
    assert ReportManager._load_citations_map(str(tmp_path)) == {}
    payload = {"markers": [{"tag": "S1", "title": "t"}]}
    with open(tmp_path / "citations.json", "w", encoding="utf-8") as f:
        json.dump(payload, f)
    m = ReportManager._load_citations_map(str(tmp_path))
    assert m["S1"]["title"] == "t"


def test_export_pdf_applies_citation_rewrite_only_to_pandoc(reports_tmp, monkeypatch):
    rid = "report_pdf_cite"
    ReportManager._ensure_report_folder(rid)
    folder = os.path.join(reports_tmp, rid)
    with open(os.path.join(folder, "full_report.md"), "w", encoding="utf-8") as f:
        f.write("# T\n\n## Body\n\nClaim 42% [S1].\n")
    with open(os.path.join(folder, "citations.json"), "w", encoding="utf-8") as f:
        json.dump({"markers": [_CITATIONS_MAP["S1"]]}, f)

    seen = {}

    def _fake_pandoc(cls, report_id, md, folder_, pdf_path):
        seen["md"] = md
        with open(pdf_path, "wb") as fh:
            fh.write(b"%PDF-1.4 fake")
        return True

    monkeypatch.setattr(Config, "REPORT_PDF_EXPORT", True, raising=False)
    monkeypatch.setattr(ReportManager, "_export_pdf_pandoc", classmethod(_fake_pandoc))
    out = ReportManager.export_pdf(rid)
    assert out and seen["md"].count("[^s1]") == 2      # 正文引用 + 文末定义
    assert "[^s1]:" in seen["md"]
