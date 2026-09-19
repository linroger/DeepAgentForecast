"""Pure mechanical publication checks for NEW agentic research runs only.

This module is standalone (stdlib only) so both the deployed bridge and the
backend's importlib helper can replay exactly the same policy. It does not read
environment variables, import the legacy judge, retrieve sources, or repair text.

Integration contract:
* Select this policy using the parent's pinned engine/policy for a new run, never
  by accepting a policy flag supplied by a report or an arbitrary receipt.
* Pass the exact, ordered citation source list used by synthesis: [S1] denotes
  its first row. Do not filter, sort, deduplicate or promote search snippets here.
* Run the parent's global actor audit on the final report, pass it explicitly as
  actor_audit, and independently verify audit provenance/requiredness on replay.
  scenario_frame is the authoritative list of {name, weight} objects, or None/[]
  when no frame was supplied. This gate never invents scenarios.
* After final assembly, persist make_receipt's result alongside report.md and
  sources.json, sealing all three in the research artifact manifest. Validate
  the receipt against those exact artifacts before admission to the next stage.
* For the new policy, store LLM verdicts in advisory (or a separately sealed
  legacy-named research_report_judge.json artifact). Do not call report_passes or
  the legacy scorecard-schema/threshold check on this receipt. Keep all existing
  legacy judge artifact, prose-prefix and manifest checks on the legacy branch.

Receipt hashes establish content identity, not authenticity. A parent must bind
the receipt and its inputs to trusted run state; someone able to replace and
rehash the entire receipt can change optional inputs. validate_receipt recomputes
all mechanical checks and never trusts a stored passed bit. It does not prove
factual accuracy, source retrieval, semantic citation support, or actor provenance.
"""
from __future__ import annotations

import hashlib
import importlib.util
from functools import lru_cache
from pathlib import Path
import json
import math
import re
from urllib.parse import urlsplit


SCHEMA = "research-quality/v1"
POLICY = "mechanical-with-advisory/v1"
GATE_VERSION = "research-quality-gate/v1"
_RESULT_KEYS = frozenset({
    "schema", "policy", "passed", "errors", "warnings", "report_sha256",
    "report_chars", "sources_sha256",
})
_RECEIPT_KEYS = _RESULT_KEYS | {"gate_version", "inputs", "advisory", "receipt_sha256"}
_INPUT_KEYS = {"min_chars", "actor_audit", "scenario_frame"}
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+\S.*$")
_CITATION = re.compile(r"\[S([1-9][0-9]*)\]")
_CITATION_LIKE = re.compile(r"\[S(?=[0-9\s#?+\-\]]|$)[^\]\n]*(?:\]|$)", re.MULTILINE)
_REFERENCE_LABEL = re.compile(r"\[([^\]\n]*)\]")
_URL = re.compile(r"https?://[^\s<>\[\]]+")
_ERROR_LINE = re.compile(
    r"^(?:#{1,6}\s+|[-*>]\s*)*(?:"
    r"The configured LLM provider\b|LLM request failed\b|"
    r"research tool budget exhausted\b|Traceback \(most recent call last\)|"
    r"ERROR\s*:|Error code:\s*[45]\d\d\b|SUBAGENT_OUTCOME:\s*BLOCKED\b|"
    r"insufficient_quota\b|unprocessable_entity\b|new_sensitive\b|"
    r"rate limit(?:\s*$| exceeded\b| reached\b|:)|"
    r"content filter(?:\s*$|ed\b|:))",
    re.IGNORECASE,
)
_PLACEHOLDER_LINE = re.compile(
    r"^(?:#{1,6}\s+|[-*>]\s*)*(?:"
    r"(?:TODO|TBD|PLACEHOLDER)(?:\s*:|\s*$)|"
    r"\[(?:(?:report|content|output)\s+)?(?:truncated|incomplete|omitted)\]|"
    r"(?:report generation failed|to be continued|insert (?:report|content) here)\b|"
    r"(?:报告未完成|报告生成失败|待补充|未完待续)(?:[。:：]|$))",
    re.IGNORECASE,
)
_NUMBER = r"[+\-]?(?:\d+(?:\.\d*)?|\.\d+)"
_PERCENT = re.compile(rf"^({_NUMBER})\s*%$")
_SCENARIO_HEADERS = {"scenario", "scenario name", "情景", "场景"}
_WEIGHT_HEADERS = {"weight", "probability", "likelihood", "weight (%)", "probability (%)", "权重", "概率"}


def _canonical(value) -> str:
    """Strict, lossless JSON: no NaN, key coercion, tuples or custom objects."""
    def check(item):
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list:
            for child in item:
                check(child)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                check(child)
            return
        raise ValueError("Expected strict JSON values and string object keys")

    try:
        check(value)
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False)
        encoded.encode("utf-8")
        return encoded
    except (TypeError, ValueError, RecursionError, OverflowError, UnicodeError) as exc:
        raise ValueError("Value is not canonical UTF-8 JSON") from exc


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _visible_prose(report: str, errors: list[str]) -> str:
    """Mask fenced/inline code and comments; preserve paragraph boundaries."""
    output = []
    fence = None
    for line in report.splitlines():
        match = _FENCE.match(line)
        if fence is not None:
            if (match and match[1][0] == fence[0] and len(match[1]) >= len(fence)
                    and not match[2].strip()):
                fence = None
            output.append("")
        elif match:
            fence = match[1]
            output.append("")
        else:
            output.append(line)
    if fence is not None:
        errors.append("report_unclosed_code_fence")
    text = "\n".join(output)
    text = re.sub(r"(`+)(?!`)(.*?)\1(?!`)", "", text, flags=re.DOTALL)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    if "<!--" in text:
        errors.append("report_unclosed_comment")
        text = text.split("<!--", 1)[0]
    return text


def _check_structure(prose: str, errors: list[str]) -> None:
    lines = [line.strip() for line in prose.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if _ERROR_LINE.match(line):
            errors.append("report_error_placeholder")
        if _PLACEHOLDER_LINE.match(line):
            errors.append("report_unfinished_placeholder")
        heading = _HEADING.match(line)
        if heading:
            following = _HEADING.match(lines[index + 1]) if index + 1 < len(lines) else None
            if index + 1 == len(lines) or (following and len(following[1]) <= len(heading[1])):
                errors.append("report_empty_section")
    if lines and re.fullmatch(r"(?:[-*+]|\d+[.)])\s*", lines[-1]):
        errors.append("report_unfinished_list")
    raw_lines = prose.splitlines()
    for index in range(len(raw_lines) - 1):
        if "|" not in raw_lines[index]:
            continue
        cells = _table_cells(raw_lines[index])
        separator = _table_cells(raw_lines[index + 1])
        if not separator or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator):
            continue
        if len(cells) != len(separator):
            errors.append("report_table_columns_inconsistent")
        row_count = 0
        for line in raw_lines[index + 2:]:
            if "|" not in line:
                break
            row_count += 1
            if len(_table_cells(line)) != len(cells):
                errors.append("report_table_columns_inconsistent")
        if not row_count:
            errors.append("report_empty_table")


def _valid_url(value) -> bool:
    if type(value) is not str or not value or re.search(r"[\s<>\x00-\x1f\x7f\\]", value):
        return False
    try:
        parsed = urlsplit(value)
        return bool(
            parsed.scheme in {"http", "https"} and parsed.hostname
            and parsed.username is None and parsed.password is None
            and (parsed.port is None or 0 < parsed.port <= 65535)
        )
    except ValueError:
        return False


def _link_target(text: str, start: int = 0) -> str | None:
    """Read an adjacent Markdown link without copying the remaining report."""
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "\n":
            break
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                match = re.fullmatch(
                    r'''(?:<([^<>]*)>|(\S+?))(?:[ \t]+(?:"[^"\n]*"|'[^'\n]*'))?''',
                    text[start + 1:index],
                )
                return (match[1] or match[2]) if match else None
    return None


def _reference_urls(line: str) -> list[str]:
    urls = []
    for url in _URL.findall(line):
        url = url.rstrip(".,;:!?）。，；")
        # Remove Markdown's closing parenthesis, keeping balanced URL ones.
        while url.endswith(")") and url.count(")") > url.count("("):
            url = url[:-1]
        urls.append(url)
    return urls


def _reference_label(label: str) -> str:
    return " ".join(label.split()).casefold()


def _check_sources(sources, prose: str, errors: list[str], warnings: list[str]) -> None:
    if type(sources) is not list:
        errors.append("sources_not_list")
        return
    urls = {}
    for index, source in enumerate(sources, 1):
        url = source.get("url") if type(source) is dict else source
        if not _valid_url(url):
            errors.append(f"source_url_invalid:S{index}")
        else:
            urls[str(index)] = url
        if type(source) is dict:
            if "n" in source and (type(source["n"]) is not int or source["n"] != index):
                errors.append(f"source_index_mismatch:S{index}")
            if source.get("ok") is False:
                errors.append(f"source_unavailable:S{index}")
    for match in _CITATION_LIKE.finditer(prose):
        if not _CITATION.fullmatch(match[0]):
            errors.append("citation_marker_malformed")
    citations = list(_CITATION.finditer(prose))
    if not citations:
        warnings.append("no_source_citations")
    if not sources:
        warnings.append("no_declared_sources")
    references = {}
    for definition in re.finditer(
        r"^ {0,3}\[([^\]\n]+)\]:\s*(?:<([^>\n]+)>|(\S+))", prose, flags=re.MULTILINE,
    ):
        label, target = _reference_label(definition[1]), definition[2] or definition[3]
        if label in references and references[label] != target:
            references[label] = None  # Conflicting bindings cannot prove resolution.
        else:
            references[label] = target
    for match in citations:
        if match[1] not in urls:
            errors.append(f"citation_unresolved:S{match[1]}")
        # Markdown [S1](url) explicitly binds this marker to a URL.
        end = match.end()
        if prose.startswith("(", end):
            target = _link_target(prose, end)
            if not _valid_url(target) or target != urls.get(match[1]):
                errors.append(f"citation_url_mismatch:S{match[1]}")
        elif prose.startswith("[", end):
            reference = _REFERENCE_LABEL.match(prose, end)
            label = _reference_label(reference[1] or f"S{match[1]}") if reference else ""
            target = references.get(label)
            if not _valid_url(target) or target != urls.get(match[1]):
                errors.append(f"citation_url_mismatch:S{match[1]}")
        elif f"s{match[1]}" in references:
            if references[f"s{match[1]}"] != urls.get(match[1]):
                errors.append(f"citation_url_mismatch:S{match[1]}")
    # Check explicit source declarations, e.g. '- [S1] title — https://...'.
    # Do not infer a citation's target from URLs in unrelated body sentences.
    in_references = False
    for line in prose.splitlines():
        heading = _HEADING.match(line.strip())
        if heading:
            in_references = bool(re.fullmatch(
                r"#{1,6}\s+(?:references|sources|参考文献|参考来源|来源)\s*#*",
                line.strip(), flags=re.IGNORECASE,
            ))
            continue
        marker = re.match(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)?\[S([1-9][0-9]*)\]", line)
        if not marker:
            continue
        links = _reference_urls(line)
        if in_references and not links:
            errors.append(f"reference_url_missing:S{marker[1]}")
        if links and urls.get(marker[1]) not in links:
            errors.append(f"citation_url_mismatch:S{marker[1]}")


def _check_actor_audit(actor_audit, errors: list[str], warnings: list[str]) -> None:
    if actor_audit is None:
        warnings.append("actor_audit_not_supplied")
        return
    if type(actor_audit) is not dict or type(actor_audit.get("required")) is not bool:
        errors.append("actor_audit_invalid")
        return
    if actor_audit["required"]:
        if actor_audit.get("complete") is not True:
            errors.append("required_actor_audit_incomplete")
        if actor_audit.get("errors"):
            errors.append("required_actor_audit_has_errors")
    if "errors" in actor_audit and (
        type(actor_audit["errors"]) is not list
        or any(type(error) is not str for error in actor_audit["errors"])
    ):
        errors.append("actor_audit_errors_invalid")


def _table_cells(line: str) -> list[str]:
    return [cell.strip().strip("*").strip()
            for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def _scenario_restatements(prose: str, names: dict[str, float]):
    """Yield only explicit name-to-percent assignments or labeled table cells.

    Nearby financial percentages, unlabeled table columns, numeric prose,
    ranges, and arbitrary semantic restatements are deliberately not inferred.
    """
    lines = prose.splitlines()
    for line in lines:
        clean = line.replace("**", "").strip()
        for name in names:
            expression = re.compile(
                rf"(?<!\w){re.escape(name)}(?!\w)(?:\s+scenario)?\s*"
                rf"(?:(?:probability|weight|likelihood)\s*[:=]\s*|[:=]\s*|\(\s*)"
                rf"({_NUMBER})\s*%", re.IGNORECASE,
            )
            for match in expression.finditer(clean):
                yield name, float(match[1])
    for index in range(len(lines) - 2):
        if "|" not in lines[index]:
            continue
        header = [cell.casefold() for cell in _table_cells(lines[index])]
        separator = _table_cells(lines[index + 1])
        if not separator or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator):
            continue
        name_column = next((i for i, cell in enumerate(header) if cell in _SCENARIO_HEADERS), None)
        weight_column = next((i for i, cell in enumerate(header) if cell in _WEIGHT_HEADERS), None)
        if name_column is None or weight_column is None:
            continue
        for line in lines[index + 2:]:
            if "|" not in line:
                break
            cells = _table_cells(line)
            if len(cells) != len(header):
                break
            name = cells[name_column].casefold()
            canonical = all(re.fullmatch(r"sc[1-4]", key) for key in names)
            if canonical:
                stable = re.match(r"^(sc[1-4])(?:\s*$|\s*[—–:-])", name)
                name = stable[1] if stable else "__unbound__"
            percent = _PERCENT.fullmatch(cells[weight_column])
            if name in names and percent:
                yield name, float(percent[1])
            elif canonical:
                yield name, None


def _check_scenarios(frame, prose: str, errors: list[str], warnings: list[str]) -> None:
    if frame is None or (type(frame) is list and not frame):
        warnings.append("scenario_frame_not_supplied")
        return
    if type(frame) is not list:
        errors.append("scenario_frame_not_list")
        return
    names = {}
    for index, row in enumerate(frame, 1):
        if type(row) is not dict:
            errors.append(f"scenario_invalid:{index}")
            continue
        name, weight = row.get("name"), row.get("weight")
        if type(name) is not str or not name.strip():
            errors.append(f"scenario_name_invalid:{index}")
            continue
        name = name.strip().casefold()
        if name in names:
            errors.append(f"scenario_name_duplicate:{index}")
        if (type(weight) not in (int, float) or not 0 <= weight <= 100
                or not math.isfinite(weight)):
            errors.append(f"scenario_weight_invalid:{index}")
            continue
        names[name] = float(weight)
    if len(names) != len(frame):
        return
    if not math.isclose(math.fsum(names.values()), 100.0, rel_tol=0.0, abs_tol=1e-6):
        errors.append("scenario_weights_total_not_100")
    seen = set()
    canonical = all(re.fullmatch(r"sc[1-4]", key) for key in names)
    for name, weight in _scenario_restatements(prose, names):
        if name not in names:
            errors.append("scenario_probability_table_has_unbound_identity")
            continue
        seen.add(name)
        if weight is None:
            errors.append(f"scenario_probability_unparseable:{name}")
        elif not math.isclose(weight, names[name], rel_tol=0.0, abs_tol=1e-6):
            errors.append(f"scenario_restatement_mismatch:{name}")
    if canonical and seen != set(names):
        errors.append("canonical_scenario_frame_not_fully_represented")
    if canonical:
        lines = prose.splitlines()
        for index in range(len(lines) - 2):
            header = [cell.casefold() for cell in _table_cells(lines[index])]
            separator = _table_cells(lines[index + 1])
            if "|" not in lines[index] or not separator or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator):
                continue
            name_col = next((i for i, cell in enumerate(header) if cell in _SCENARIO_HEADERS), None)
            weight_col = next((i for i, cell in enumerate(header) if cell in _WEIGHT_HEADERS), None)
            if name_col is None or weight_col is None:
                continue
            ids = []
            cursor = index + 2
            while cursor < len(lines) and "|" in lines[cursor]:
                cells = _table_cells(lines[cursor])
                if len(cells) != len(header):
                    break
                match = re.match(r"^(sc[1-4])(?:\s*$|\s*[—–:-])", cells[name_col].casefold())
                ids.append(match[1] if match else "__unbound__")
                cursor += 1
            if len(ids) != len(names) or set(ids) != set(names):
                errors.append("canonical_scenario_table_ids_invalid")


def evaluate_report(report: str, sources: list, *, actor_audit: dict | None = None,
                    scenario_frame: list | None = None, min_chars=400) -> dict:
    """Return deterministic defects and limitations, with exact artifact hashes.

    min_chars counts visible prose characters after masking code/comments; the
    returned report_chars and report_sha256 always bind the unmodified string.
    Invalid/non-JSON inputs fail with a null hash where identity cannot be made.
    """
    errors: list[str] = []
    warnings = ["semantic_accuracy_not_checked", "source_retrieval_not_verified"]
    report_hash = source_hash = None
    if type(report) is not str:
        errors.append("report_not_string")
        prose = ""
    else:
        try:
            report_hash = _sha256(report)
        except UnicodeError:
            errors.append("report_not_utf8")
        prose = _visible_prose(report, errors)
    try:
        source_hash = _sha256(_canonical(sources))
    except ValueError:
        errors.append("sources_not_strict_json")
    for label, value in (("actor_audit", actor_audit), ("scenario_frame", scenario_frame)):
        try:
            _canonical(value)
        except ValueError:
            errors.append(f"{label}_not_strict_json")
    if not prose.strip():
        errors.append("report_empty")
    if type(min_chars) is not int or min_chars < 1:
        errors.append("min_chars_invalid")
    elif len(prose.strip()) < min_chars:
        errors.append("report_too_short")
    _check_structure(prose, errors)
    _check_sources(sources, prose, errors, warnings)
    _check_actor_audit(actor_audit, errors, warnings)
    _check_scenarios(scenario_frame, prose, errors, warnings)
    return {
        "schema": SCHEMA, "policy": POLICY, "passed": not errors,
        "errors": list(dict.fromkeys(errors)), "warnings": list(dict.fromkeys(warnings)),
        "report_sha256": report_hash, "report_chars": len(report) if type(report) is str else 0,
        "sources_sha256": source_hash,
    }


@lru_cache(maxsize=1)
def _scenario_module():
    spec = importlib.util.spec_from_file_location("_quality_scenarios", Path(__file__).with_name("research_scenarios.py"))
    if spec is None or spec.loader is None:
        raise ValueError("scenario validation module unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical_frame_input(frame):
    return (isinstance(frame, list) and bool(frame)
            and all(isinstance(row, dict) and isinstance(row.get("name"), str)
                    and re.fullmatch(r"SC[1-4]", row["name"], re.I) for row in frame))


def make_receipt(report: str, sources: list, advisory=None, *, actor_audit: dict | None = None,
                 scenario_frame: list | None = None, min_chars=400, scenario_contract=None) -> dict:
    """Snapshot the complete gate result and replay inputs, separate from advice.

    Defective reports yield failed receipts. Non-JSON or non-UTF-8 inputs raise
    ValueError because an exact, durable JSON identity cannot represent them.
    """
    if type(report) is not str:
        raise ValueError("report must be a UTF-8 string")
    try:
        _sha256(report)
    except UnicodeError as exc:
        raise ValueError("report must be a UTF-8 string") from exc
    _canonical(sources)
    inputs = json.loads(_canonical({
        "min_chars": min_chars, "actor_audit": actor_audit, "scenario_frame": scenario_frame,
    }))
    receipt = evaluate_report(report, sources, **inputs)
    receipt.update(gate_version=GATE_VERSION, inputs=inputs,
                   advisory=json.loads(_canonical(advisory)))
    if scenario_contract is not None:
        contract = _scenario_module().parse_frame(scenario_contract)
        if _scenario_module().probability_frame(contract) != inputs["scenario_frame"]:
            raise ValueError("scenario contract does not match probability frame")
        receipt["scenario_contract"] = contract
    elif _canonical_frame_input(scenario_frame):
        raise ValueError("canonical scenario contract is required")
    receipt["receipt_sha256"] = _sha256(_canonical(receipt))
    return receipt


def validate_receipt(receipt, report: str, sources: list) -> list[str]:
    """Return violations; [] means a strictly bound receipt whose replay passes.

    Unknown versions and fields fail closed. This verifies content and recorded
    optional checks, not their producer authority or the run's required policy.
    """
    if type(receipt) is not dict or set(receipt) not in (_RECEIPT_KEYS, _RECEIPT_KEYS | {"scenario_contract"}):
        return ["receipt_schema_invalid"]
    try:
        _canonical(receipt)
    except ValueError:
        return ["receipt_not_strict_json"]
    errors = []
    for key, expected in (("schema", SCHEMA), ("policy", POLICY), ("gate_version", GATE_VERSION)):
        if receipt[key] != expected:
            errors.append(f"receipt_{key}_unsupported")
    inputs = receipt["inputs"]
    if type(inputs) is not dict or set(inputs) != _INPUT_KEYS:
        return errors + ["receipt_inputs_invalid"]
    if "scenario_contract" in receipt:
        try:
            contract = _scenario_module().parse_frame(receipt["scenario_contract"])
            if contract != receipt["scenario_contract"] or _scenario_module().probability_frame(contract) != inputs["scenario_frame"]:
                errors.append("receipt_scenario_contract_mismatch")
        except (ValueError, TypeError):
            errors.append("receipt_scenario_contract_invalid")
    elif _canonical_frame_input(inputs.get("scenario_frame")):
        errors.append("receipt_scenario_contract_missing")
    expected_hash = _sha256(_canonical({key: value for key, value in receipt.items() if key != "receipt_sha256"}))
    if receipt["receipt_sha256"] != expected_hash:
        errors.append("receipt_hash_mismatch")
    actual = evaluate_report(report, sources, **inputs)
    for key in sorted(_RESULT_KEYS):
        # Canonical comparison distinguishes booleans from integers (True != 1).
        if _canonical(receipt[key]) != _canonical(actual[key]):
            errors.append(f"receipt_{key}_mismatch")
    errors.extend(actual["errors"])
    return list(dict.fromkeys(errors))
