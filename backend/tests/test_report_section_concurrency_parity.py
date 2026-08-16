"""COST-2: report-section concurrency default + serial/concurrent parity.

The shipped Config default must keep section generation concurrent
(REPORT_SECTION_CONCURRENCY=6: REPORT-1 took 1→3, PAR-3 took 3→6; the measured
serial cost was 12 sections ≈ 24.6 min / 216 LLM calls), and the concurrent
orchestrator (_generate_sections_concurrent) must produce exactly the same set
of sections — same indices, same deterministic content, same failure
degradation — as the serial loop's inline calls.

The default is asserted against the config *source* so a developer's .env /
os.environ cannot mask a shipped-default regression (app.config runs
load_dotenv(override=True) at import time); test_config_optimization_defaults.py
pins the same value via a clean subprocess import.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Config  # noqa: E402
from app.services.report_agent import (  # noqa: E402
    ReportAgent, ReportOutline, ReportSection, SECTION_FAILURE_PLACEHOLDER,
)

_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "config.py"
)


# ─────────────────────────── shipped default stays concurrent ───────────────────────────
def test_shipped_default_section_concurrency_is_concurrent():
    with open(_CONFIG_PATH, encoding="utf-8") as fh:
        src = fh.read()
    match = re.search(
        r"REPORT_SECTION_CONCURRENCY\s*=\s*int\(os\.environ\.get\("
        r"\s*'REPORT_SECTION_CONCURRENCY',\s*'(\d+)'\s*\)",
        src,
    )
    assert match, "REPORT_SECTION_CONCURRENCY default not found in config.py"
    # REPORT-1 (1→3) → PAR-3 (3→6). Regressing to 1 re-serializes the report
    # stage (measured: 12 sections ≈ 24.6 min / 216 calls).
    assert int(match.group(1)) == 6


# ─────────────────────────────────── helpers ────────────────────────────────────
def _outline(titles):
    return ReportOutline(
        title="T", summary="S",
        sections=[ReportSection(title=t, description=f"d-{t}") for t in titles],
    )


def _stub_agent():
    """Agent whose section generator is deterministic per section id (title)."""
    a = ReportAgent.__new__(ReportAgent)

    def _stub(section, outline, previous_sections, progress_callback, section_index):
        if section.title == "BOOM":
            raise RuntimeError("deterministic section failure")
        return f"CONTENT[{section.title}]"

    a._generate_section = _stub
    return a


def _serial_reference(outline):
    """Mirror the production serial loop: inline _generate_section_with_retry
    per section in order, exceptions degraded to the failure placeholder."""
    agent = _stub_agent()
    generated, serial = [], {}
    for i, section in enumerate(outline.sections):
        try:
            content = agent._generate_section_with_retry(
                section=section, outline=outline,
                previous_sections=list(generated),
                progress_callback=None, section_index=i + 1,
            )
        except Exception:  # noqa: BLE001 — same degradation as the serial loop
            content = SECTION_FAILURE_PLACEHOLDER
        serial[i] = content
        generated.append(f"## {section.title}\n\n{content}")
    return serial


# ───────────────────────── concurrency=3 parity with serial ─────────────────────────
def test_concurrency_three_matches_serial_section_set():
    outline = _outline(["正文1", "正文2", "正文3", "正文4", "结论"])
    serial = _serial_reference(outline)
    concurrent = _stub_agent()._generate_sections_concurrent(outline, concurrency=3)

    assert set(concurrent.keys()) == set(serial.keys()) == set(range(5))
    assert concurrent == serial  # identical deterministic content per index


def test_concurrency_three_failure_degrades_exactly_like_serial(monkeypatch):
    # Retry backoff would add 8s+16s sleeps per BOOM; the retry wrapper itself
    # is not under test here (REPORT_SECTION_RETRY_MAX=0 = the no-retry path).
    monkeypatch.setattr(Config, "REPORT_SECTION_RETRY_MAX", 0, raising=False)
    outline = _outline(["正文1", "BOOM", "正文3"])
    serial = _serial_reference(outline)
    concurrent = _stub_agent()._generate_sections_concurrent(outline, concurrency=3)

    assert concurrent == serial
    assert concurrent[1] == SECTION_FAILURE_PLACEHOLDER
    assert concurrent[0] == "CONTENT[正文1]" and concurrent[2] == "CONTENT[正文3]"
