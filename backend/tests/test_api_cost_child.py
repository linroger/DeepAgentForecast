"""Actual detached child attempts persist their price despite parent rate drift."""

import json
import sqlite3

import pytest

from app.config import Config
from app.utils import telemetry as tel
from app.utils.api_cost import quote_cost
from app.utils.usage_ledger import UsageLedger
from test_simulation_shared_usage import launch as launch, _child, _environment


@pytest.mark.parametrize("scenario", ["retry", "crash"])
def test_child_price_receipt_survives_retry_or_crash_and_recovery(launch, monkeypatch, scenario):
    env = _environment(launch)
    env["LLM_COST_PER_MTOK"] = '{"openai":[1,2]}'
    process = _child(launch, scenario, env=env)
    assert process.returncode == (23 if scenario == "crash" else 0), process.stderr + process.stdout
    with sqlite3.connect(launch.ledger) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(row) for row in conn.execute("SELECT * FROM usage_operations WHERE source='llm_api_attempt'")]
    assert len(rows) == (1 if scenario == "crash" else 3)
    for row in rows:
        metadata = json.loads(row["metadata_json"])
        assert metadata["cost_quote"]["input_usd_per_1k"] == 0.001
        assert metadata["cost_quote"]["output_usd_per_1k"] == 0.002
        assert row["operation_id"].startswith(launch.context["launch_token"] + ":")
    monkeypatch.setattr(Config, "LLM_COST_PER_MTOK", '{"openai":[20,40]}')
    tel.LLMMeter.reset(launch.state.pipeline_id)
    ledger = UsageLedger(str(launch.ledger))
    if scenario == "crash":
        row = rows[0]
        assert row["status"] == "in_flight"
        metadata = json.loads(row["metadata_json"])
        metadata.update(usage_class="known", usage_source="known")
        ledger.record_snapshot(run_id=launch.state.pipeline_id, attempt_id=launch.context["attempt_id"],
            source="llm_api_attempt", operation_id=row["operation_id"], metadata=metadata,
            counters={"calls": 1, "prompt_tokens": 1000, "completion_tokens": 1000,
                      "cost_usd": quote_cost(metadata["cost_quote"], 1000, 1000)}, status="completed")
        assert ledger.snapshot(launch.state.pipeline_id)["total"]["cost_usd"] == 0.003
    else:
        total = ledger.snapshot(launch.state.pipeline_id)["total"]
        assert total["total_tokens"] == 180
        assert total["cost_usd"] == 0.00021
