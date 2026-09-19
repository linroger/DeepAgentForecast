"""Offline, provider-free contract tests for the new agentic publication policy."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


MODULE = Path(__file__).resolve().parents[2] / "deerflow_bridge/research_quality.py"


@pytest.fixture(scope="module")
def quality():
    # Match backend loading and a deployed bridge with no backend on sys.path.
    spec = importlib.util.spec_from_file_location("isolated_research_quality", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sources():
    return [
        {"url": "https://example.org/release", "title": "发布", "source_origin": "fetched",
         "receipt_id": "fetch-1"},
        {"url": "https://example.org/search", "source_origin": "search_snippet",
         "excerpt": "Search evidence only", "receipt_id": "search-2"},
    ]


@pytest.fixture
def report():
    return (
        "# Research report\n\n## Findings\n\n"
        + "The release describes an observed change, with uncertainty about its persistence [S1]. " * 7
        + "\n\n## Limits\n\nThe search result is a discovery lead, not fetched evidence [S2].\n"
        + "\n## References\n\n- [S1] Release — https://example.org/release\n"
        + "- [S2] Search — https://example.org/search\n"
    )


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def resign(receipt):
    receipt["receipt_sha256"] = hashlib.sha256(canonical({
        key: value for key, value in receipt.items() if key != "receipt_sha256"
    }).encode("utf-8")).hexdigest()


@pytest.mark.parametrize("advisory", [None, {"status": "unavailable"},
    {"verdict": "FAIL", "scores": {"all": 5}},
    {"verdict": "FAIL", "scores": {"all": 0}},
    {"verdict": "PASS", "scores": {"all": 0}}])
def test_advisory_never_vetoes(quality, report, sources, advisory):
    receipt = quality.make_receipt(report, sources, advisory=advisory)
    assert receipt["schema"] == "research-quality/v1"
    assert receipt["policy"] == "mechanical-with-advisory/v1"
    assert receipt["passed"] is True
    assert receipt["advisory"] == advisory
    assert quality.validate_receipt(receipt, report, sources) == []


def test_exact_identity_determinism_and_no_source_promotion(quality, report, sources):
    original = copy.deepcopy(sources)
    result = quality.evaluate_report(report, sources)
    assert result["passed"] is True
    assert result["report_sha256"] == hashlib.sha256(report.encode("utf-8")).hexdigest()
    assert result["report_chars"] == len(report)
    assert result["sources_sha256"] == hashlib.sha256(canonical(sources).encode("utf-8")).hexdigest()
    assert sources == original
    assert "search_snippet" in canonical(sources)
    reordered_keys = [{k: row[k] for k in reversed(row)} for row in sources]
    assert quality.make_receipt(report, sources) == quality.make_receipt(report, reordered_keys)
    assert result["sources_sha256"] != quality.evaluate_report(report, sources[::-1])["sources_sha256"]


@pytest.mark.parametrize("suffix", [" [S3]", " [S0]", " [S01]", " [S1", " [S-1]",
    "\n\n## Unfinished", "\n\n- ", "\n\n```python\nunclosed",
    "\n\n<!-- unclosed comment", "\n\nTODO: finish the report",
    "\n\n[report truncated]", "\n\nLLM request failed: provider unavailable",
    "\n\nThe configured LLM provider rejected this request"])
def test_mechanical_defects_fail(quality, report, sources, suffix):
    result = quality.evaluate_report(report + suffix, sources)
    assert result["passed"] is False
    assert result["errors"]


@pytest.mark.parametrize("bad_report", ["", " \n\t", "too short", "# Heading\n" * 60,
    "```text\n" + "[S999] " * 100 + "\n```"])
def test_empty_or_structurally_incomplete_reports_fail(quality, sources, bad_report):
    assert quality.evaluate_report(bad_report, sources)["passed"] is False


def test_code_and_comments_do_not_create_citations_or_placeholders(quality, report, sources):
    text = report + (
        "\n```text\n[S999] [S-1] ERROR: example\n```\n"
        "\n~~~text\n[S88]\n~~~\n"
        "\nThe syntax example `[S999]` is literal text.\n"
        "<!-- actor marker: [S1000] -->\n"
    )
    assert quality.evaluate_report(text, sources)["passed"] is True
    assert quality.evaluate_report(text + "Actual claim [S999].", sources)["passed"] is False


@pytest.mark.parametrize("url", [None, "", "example.org/a", "file:///tmp/source", "https://",
    "https://example.org/a b", "https://user:password@example.org/a", "https://example.org:bad/a"])
def test_declared_source_must_have_usable_url(quality, report, sources, url):
    sources[0]["url"] = url
    assert quality.evaluate_report(report, sources)["passed"] is False


def test_citation_link_and_reference_url_must_match_declared_source(quality, report, sources):
    assert quality.evaluate_report(report.replace("[S1].", "[S1](https://evil.example/wrong)."), sources)["passed"] is False
    assert quality.evaluate_report(report.replace("https://example.org/release", "https://evil.example/wrong"), sources)["passed"] is False
    assert quality.evaluate_report(report.replace("[S1].", "[S1](https://example.org/release)."), sources)["passed"] is True


def test_zero_scenarios_are_not_invented_or_rejected(quality, report, sources):
    for frame in (None, []):
        result = quality.evaluate_report(report + "\nRevenue could grow 30% or 60%.", sources, scenario_frame=frame)
        assert result["passed"] is True
        receipt = quality.make_receipt(report, sources, scenario_frame=frame)
        assert receipt["inputs"]["scenario_frame"] == frame
        assert quality.validate_receipt(receipt, report, sources) == []


@pytest.mark.parametrize("weights", [[0, 100], [40, 60], [33.333333333, 66.666666667]])
def test_scenario_weights_valid(quality, report, sources, weights):
    frame = [{"name": name, "weight": weight} for name, weight in zip(("Base", "Upside"), weights, strict=True)]
    text = report + f"\nBase: {weights[0]}%\nUpside: {weights[1]}%\n"
    receipt = quality.make_receipt(text, sources, scenario_frame=frame)
    assert receipt["passed"] is True
    assert quality.validate_receipt(receipt, text, sources) == []


@pytest.mark.parametrize("weights", [[-1, 101], [40, 50], [True, 99], ["40", 60],
    [float("nan"), 100], [float("inf"), 0], [10**500, 0]])
def test_invalid_scenario_weights_fail(quality, report, sources, weights):
    frame = [{"name": name, "weight": weight} for name, weight in zip(("Base", "Upside"), weights, strict=True)]
    assert quality.evaluate_report(report, sources, scenario_frame=frame)["passed"] is False


@pytest.mark.parametrize("statement", ["Base: 60%", "Base scenario (60%)", "Base probability: 60%",
    "| Scenario | Weight |\n|---|---|\n| Base | 60% |\n| Upside | 40% |"])
def test_parseable_scenario_restatement_must_match(quality, report, sources, statement):
    frame = [{"name": "Base", "weight": 40}, {"name": "Upside", "weight": 60}]
    assert quality.evaluate_report(report + "\n" + statement, sources, scenario_frame=frame)["passed"] is False


def test_scenario_nearby_business_percentages_are_not_probability_claims(quality, report, sources):
    frame = [{"name": "Base", "weight": 40}, {"name": "Upside", "weight": 60}]
    text = report + "\nUnder Base, revenue grows 7%.\n| Scenario | Revenue growth |\n|---|---|\n| Base | 7% |\n"
    assert quality.evaluate_report(text, sources, scenario_frame=frame)["passed"] is True


@pytest.mark.parametrize("complete", [False, None, 1, "true"])
def test_required_global_actor_audit_is_mandatory_regardless_of_advice(quality, report, sources, complete):
    audit = {"required": True, "complete": complete, "errors": ["actor_missing"]}
    receipt = quality.make_receipt(report, sources, actor_audit=audit, advisory={"verdict": "PASS", "score": 5})
    assert receipt["passed"] is False
    assert quality.validate_receipt(receipt, report, sources)


def test_actor_audit_is_snapshotted_for_replay(quality, report, sources):
    audit = {"required": True, "complete": True, "errors": [], "report_input_sha256": "parent-verified"}
    frame = [{"name": "Base", "weight": 100}]
    advice = {"verdict": "FAIL"}
    receipt = quality.make_receipt(report, sources, actor_audit=audit, scenario_frame=frame, advisory=advice)
    assert quality.validate_receipt(receipt, report, sources) == []
    audit["complete"] = False
    frame[0]["weight"] = 10
    advice["verdict"] = "PASS"
    assert receipt["inputs"]["actor_audit"]["complete"] is True
    assert receipt["inputs"]["scenario_frame"][0]["weight"] == 100
    assert receipt["advisory"]["verdict"] == "FAIL"


@pytest.mark.parametrize("key,value", [("passed", True), ("errors", []), ("schema", "bogus"),
    ("policy", "mechanical-with-advisory/v2"), ("gate_version", "future"),
    ("report_chars", 1), ("report_sha256", "0" * 64), ("sources_sha256", "0" * 64)])
def test_replay_rejects_forged_receipt_even_with_recomputed_hash(quality, report, sources, key, value):
    text = report + "\nBad citation [S999]."
    receipt = quality.make_receipt(text, sources)
    receipt[key] = value
    resign(receipt)
    assert quality.validate_receipt(receipt, text, sources)


def test_receipt_tamper_schema_and_binding(quality, report, sources):
    receipt = quality.make_receipt(report, sources)
    assert quality.validate_receipt(receipt, report + "\n", sources)
    changed = copy.deepcopy(sources)
    changed[1]["source_origin"] = "fetched"
    assert quality.validate_receipt(receipt, report, changed)
    for key in receipt:
        broken = copy.deepcopy(receipt)
        del broken[key]
        assert quality.validate_receipt(broken, report, sources), key
    receipt["advisory"] = {"verdict": "PASS"}
    assert quality.validate_receipt(receipt, report, sources)
    receipt = quality.make_receipt(report, sources)
    receipt["passed"] = 1  # True == 1 must not relax the schema.
    resign(receipt)
    assert quality.validate_receipt(receipt, report, sources)
    receipt = quality.make_receipt(report, sources)
    receipt["unknown"] = "ignored bypass"
    resign(receipt)
    assert quality.validate_receipt(receipt, report, sources)


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), {1: "numeric key"}, (1, 2), {1, 2}])
def test_strict_json_sources_and_receipts(quality, report, sources, bad_value):
    sources[0]["metadata"] = bad_value
    assert quality.evaluate_report(report, sources)["passed"] is False
    with pytest.raises(ValueError):
        quality.make_receipt(report, sources)
    assert quality.validate_receipt({"schema": "research-quality/v1"}, report, sources)


@pytest.mark.parametrize("threshold", [0, -1, True, 1.5, "400"])
def test_invalid_gate_configuration_fails(quality, report, sources, threshold):
    assert quality.evaluate_report(report, sources, min_chars=threshold)["passed"] is False


def test_threshold_is_replayed_and_result_cannot_claim_semantic_verification(quality, report, sources):
    receipt = quality.make_receipt(report, sources, min_chars=len(report) + 1)
    assert receipt["passed"] is False
    assert quality.validate_receipt(receipt, report, sources)
    result = quality.evaluate_report(report, sources)
    assert "semantic_accuracy_not_checked" in result["warnings"]


@pytest.mark.parametrize("target", ["javascript:alert(1)", "file:///tmp/source", "#unbound", "", "https://"])
def test_non_http_markdown_citation_targets_fail(quality, report, sources, target):
    text = report.replace("[S1].", f"[S1]({target}).")
    assert quality.evaluate_report(text, sources)["passed"] is False


@pytest.mark.parametrize("ending", ["| Name | Weight |\n|---|---|",
    "| Name | Weight |\n|---|---|\n| Base | 40% | Extra |"])
def test_unfinished_tables_fail(quality, report, sources, ending):
    assert quality.evaluate_report(report + "\n" + ending, sources)["passed"] is False


def test_valid_markdown_table_with_escaped_pipe(quality, report, sources):
    ending = "| Name | Value |\n|---|---|\n| Base \\| Upside | 40% |\n"
    assert quality.evaluate_report(report + "\n" + ending, sources)["passed"] is True


def test_parenthesized_source_url_and_markdown_title(quality, report, sources):
    sources[0]["url"] = "https://example.org/release_(final)"
    report = report.replace("https://example.org/release", sources[0]["url"])
    report = report.replace("[S1].", f'[S1](<{sources[0]["url"]}> "Release").')
    assert quality.evaluate_report(report, sources)["passed"] is True


@pytest.mark.parametrize("placeholder", ["rate limit", "content filter", "content filter: rejected"])
def test_operational_error_placeholders_fail(quality, report, sources, placeholder):
    assert quality.evaluate_report(report + "\n" + placeholder, sources)["passed"] is False


def test_receipt_replays_actor_and_scenario_failures_even_after_rehash(quality, report, sources):
    receipt = quality.make_receipt(report, sources, actor_audit={"required": True, "complete": True},
                                   scenario_frame=[{"name": "Base", "weight": 100}])
    receipt["inputs"]["actor_audit"]["complete"] = False
    resign(receipt)
    assert "required_actor_audit_incomplete" in quality.validate_receipt(receipt, report, sources)
    receipt["inputs"]["actor_audit"]["complete"] = True
    receipt["inputs"]["scenario_frame"][0]["weight"] = 0
    resign(receipt)
    assert "scenario_weights_total_not_100" in quality.validate_receipt(receipt, report, sources)


@pytest.mark.parametrize("field,value", [("actor_audit", {"required": "true", "complete": True}),
    ("actor_audit", {"required": True, "complete": True, "errors": ["missing"]}),
    ("scenario_frame", [{"name": "Base", "weight": 50}, {"name": "base", "weight": 50}]),
    ("scenario_frame", {"scenarios": []})])
def test_malformed_optional_inputs_fail_closed(quality, report, sources, field, value):
    assert quality.evaluate_report(report, sources, **{field: value})["passed"] is False


@pytest.mark.parametrize("link,definition", [
    ("[S1][release]", "[release]: https://evil.example/incorrect"),
    ("[S1][unknown]", ""),
    ("[S1][]", "[S1]: https://evil.example/incorrect"),
])
def test_reference_style_citation_links_cannot_override_declared_url(quality, report, sources, link, definition):
    text = report.replace("[S1].", link + ".") + "\n" + definition
    assert quality.evaluate_report(text, sources)["passed"] is False


def test_reference_style_citation_links_resolve_and_receipt_roundtrips(quality, report, sources):
    text = report.replace("[S1].", "[S1][release].") + "\n[release]: <https://example.org/release>\n"
    receipt = json.loads(canonical(quality.make_receipt(text, sources)))
    assert receipt["passed"] is True
    assert quality.validate_receipt(receipt, text, sources) == []


@pytest.mark.parametrize("bad_report", [None, 123, [], {"report": "ignored"}, "\ud800"])
def test_invalid_report_input_fails_without_crashing(quality, sources, bad_report):
    assert quality.evaluate_report(bad_report, sources)["passed"] is False
    with pytest.raises(ValueError):
        quality.make_receipt(bad_report, sources)


def test_cyclic_or_non_json_receipt_fails_closed(quality, report, sources):
    receipt = quality.make_receipt(report, sources)
    receipt["advisory"] = receipt
    assert quality.validate_receipt(receipt, report, sources) == ["receipt_not_strict_json"]


def test_citation_source_order_is_explicit_and_never_renumbered(quality, report, sources):
    sources[0]["n"] = 2
    sources[1]["n"] = 1
    assert quality.evaluate_report(report, sources)["passed"] is False
    assert sources[0]["n"] == 2
