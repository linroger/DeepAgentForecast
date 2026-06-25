"""NEXTSTEPS P2-4: forecast ledger (append / read / calibration / due) — offline."""

import json
import os

from app.services.forecast_ledger import (
    _year_end,
    append_forecast,
    calibration_summary,
    due_for_resolution,
    read_ledger,
)


def test_append_and_read(tmp_path):
    d = str(tmp_path)
    fc = {"horizon": "2030", "confidence": "high",
          "scenarios": [{"name": "A", "probability": 0.6, "resolution_criteria": "x"},
                        {"name": "B", "probability": 0.4}]}
    e = append_forecast(fc, report_id="r1", created_at="2026-01-01T00:00:00", d=d)
    assert e["report_id"] == "r1" and e["resolution_date"] == "2030-12-31"
    led = read_ledger(d)
    assert len(led) == 1 and led[0]["horizon"] == "2030" and led[0]["resolved"] is False


def test_append_skips_no_scenarios(tmp_path):
    assert append_forecast({"scenarios": []}, report_id="r", d=str(tmp_path)) is None
    assert append_forecast(None, report_id="r", d=str(tmp_path)) is None


def test_calibration_summary_over_resolved(tmp_path):
    d = str(tmp_path)
    append_forecast({"horizon": "2027", "scenarios": [{"name": "A", "probability": 0.7},
                                                      {"name": "B", "probability": 0.3}]},
                    report_id="r1", d=d)
    append_forecast({"horizon": "2027", "scenarios": [{"name": "A", "probability": 0.4},
                                                      {"name": "B", "probability": 0.6}]},
                    report_id="r2", d=d)
    led = read_ledger(d)
    led[0]["resolved"], led[0]["outcome"] = True, "A"
    led[1]["resolved"], led[1]["outcome"] = True, "B"
    with open(os.path.join(d, "ledger.jsonl"), "w", encoding="utf-8") as fh:
        for e in led:
            fh.write(json.dumps(e) + "\n")
    cs = calibration_summary(d)
    assert cs["n_resolved"] == 2 and cs["mean_brier"] is not None


def test_calibration_empty_when_unresolved(tmp_path):
    append_forecast({"scenarios": [{"name": "A", "probability": 1.0}]}, report_id="r", d=str(tmp_path))
    cs = calibration_summary(str(tmp_path))
    assert cs["n_resolved"] == 0 and cs["mean_brier"] is None


def test_due_for_resolution(tmp_path):
    d = str(tmp_path)
    append_forecast({"horizon": "2025", "scenarios": [{"name": "A", "probability": 1.0}]},
                    report_id="past", d=d)
    append_forecast({"horizon": "2099", "scenarios": [{"name": "A", "probability": 1.0}]},
                    report_id="future", d=d)
    due = due_for_resolution("2026-06-01", d=d)
    assert len(due) == 1 and due[0]["report_id"] == "past"


def test_year_end_helper():
    assert _year_end("2030") == "2030-12-31"
    assert _year_end("到2027年底") == "2027-12-31"
    assert _year_end(None) is None
