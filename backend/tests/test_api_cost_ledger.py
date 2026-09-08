"""Captured API prices remain immutable through ledger and facade replay."""

import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

from app.config import Config
from app.utils import telemetry as tel
from app.utils.usage_ledger import UsageLedger, UsageLedgerConflict, UsageLedgerStorageError


RUN = "pipe-api-cost-quote"
GUARD = "usage_preserve_api_cost_quote"
INSERT_GUARD = "usage_validate_api_cost_quote_insert"


def quote(**changes):
    return {"schema": "api-cost-quote/v1", "provider": "openai", "model": "fixture",
            "source": "configured_model", "rate_key": "openai:fixture",
            "input_usd_per_1k": 0.001, "output_usd_per_1k": 0.002, **changes}


def observe(ledger, operation="one", *, inputs=0, outputs=0, status="in_flight",
            cost_quote=None, quoted=True, attempt="first", metadata=None, cost=None):
    selected = quote() if cost_quote is None else cost_quote
    meta = {"stage": "run", "provider": "openai", "model": "fixture", "usage_class": "known",
            "billing_basis": "api", "cost_estimated": True, "fallback": False}
    if quoted:
        meta["cost_quote"] = selected
    meta.update(metadata or {})
    expected = inputs / 1000 * 0.001 + outputs / 1000 * 0.002
    return ledger.record_snapshot(
        run_id=RUN, attempt_id=attempt, source="llm_api_attempt", operation_id=operation,
        metadata=meta, counters={"prompt_tokens": inputs, "completion_tokens": outputs,
                                 "calls": 0 if status == "in_flight" else 1,
                                 "cost_usd": expected if cost is None else cost}, status=status)


def operation(ledger, identity="one"):
    with sqlite3.connect(ledger.path) as conn:
        row = conn.execute("SELECT metadata_json,counter_json FROM usage_operations "
                           "WHERE run_id=? AND operation_id=?", (RUN, identity)).fetchone()
    return json.loads(row[0]), json.loads(row[1])


@pytest.fixture
def ledger(tmp_path):
    tel.LLMMeter.reset(RUN)
    result = UsageLedger(str(tmp_path / "usage.sqlite3"))
    result.initialize(RUN)
    yield result
    tel.LLMMeter.reset(RUN)


@pytest.fixture(scope="module")
def older_ledger():
    """Exercise the actual pre-quote writer against the new shared database."""
    source = subprocess.check_output(
        ["git", "show", "186af1a:backend/app/utils/usage_ledger.py"],
        cwd=Path(__file__).resolve().parents[2], text=True)
    namespace = {"__name__": "app.utils._pre_quote_ledger", "__package__": "app.utils"}
    exec(compile(source, "<186af1a-usage-ledger>", "exec"), namespace)
    return namespace["UsageLedger"]


def test_quote_survives_restart_replay_and_growth_without_repricing(ledger, monkeypatch):
    observe(ledger)
    observe(ledger, inputs=1000, outputs=100, status="completed")
    monkeypatch.setattr(Config, "LLM_COST_PER_MTOK", '{"openai":[99,99]}')
    restarted = UsageLedger(str(ledger.path))
    assert not any(observe(restarted, inputs=1000, outputs=100, status="completed", attempt="second").values())
    delta = observe(restarted, inputs=1500, outputs=100, status="completed", attempt="second")
    assert delta["total_tokens"] == 500
    assert delta["cost_usd"] == pytest.approx(0.0005)
    assert operation(restarted)[0]["cost_quote"] == quote()
    snapshot = restarted.snapshot(RUN)
    assert snapshot["total"]["cost_usd"] == 0.0017
    assert snapshot["cost_estimated"] is True
    assert restarted.budget_totals(RUN, include_cost=True)["cost_usd"] == snapshot["total"]["cost_usd"]


@pytest.mark.parametrize("change", ["replace", "remove", "add"])
def test_quote_presence_and_value_cannot_change_on_replay(ledger, change):
    observe(ledger, quoted=change != "add")
    before = operation(ledger)
    with pytest.raises(UsageLedgerConflict):
        observe(ledger, cost_quote=quote(source="configured_provider", rate_key="openai"),
                quoted=change != "remove", inputs=1000, status="completed")
    assert operation(ledger) == before


@pytest.mark.parametrize("metadata,cost_quote,cost", [
    ({}, quote(provider="different"), 0),
    ({}, quote(model="different"), 0),
    ({"cost_estimated": False}, quote(), 0),
    ({"cost_estimated": 1}, quote(), 0),
    ({}, quote(), 1),
    ({}, quote(input_usd_per_1k=True), 0),
])
def test_invalid_quote_attribution_or_cost_cannot_create_operation(ledger, metadata, cost_quote, cost):
    with pytest.raises(ValueError):
        observe(ledger, metadata=metadata, cost_quote=cost_quote, cost=cost)
    assert ledger.snapshot(RUN)["total"]["calls"] == 0
    with sqlite3.connect(ledger.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM usage_operations").fetchone()[0] == 0


def test_crossed_cumulative_snapshots_price_merged_highwater(ledger):
    observe(ledger)
    observe(ledger, inputs=1000, status="completed")
    delta = observe(ledger, outputs=1000, status="completed")
    assert delta["prompt_tokens"] == 0 and delta["completion_tokens"] == 1000
    assert delta["cost_usd"] == pytest.approx(0.002)
    stored = operation(ledger)[1]
    assert stored["total_tokens"] == 2000 and stored["cost_usd"] == 0.003
    assert not any(observe(ledger, inputs=1000, status="completed").values())


@pytest.mark.parametrize("change", ["remove", "replace", "add"])
def test_guard_blocks_older_writer_metadata_replacement(ledger, change):
    observe(ledger, quoted=change != "add")
    metadata, counters = operation(ledger)
    original = dict(metadata)
    if change == "remove":
        metadata.pop("cost_quote")
    else:
        metadata["cost_quote"] = quote(source="configured_provider", rate_key="openai")
    with sqlite3.connect(ledger.path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="quote"):
            conn.execute("UPDATE usage_operations SET metadata_json=?,status='completed' WHERE run_id=?",
                         (json.dumps(metadata, sort_keys=True), RUN))
    assert operation(ledger) == (original, counters)


@pytest.mark.parametrize("settlement", [False, True])
@pytest.mark.parametrize("guard", [GUARD, INSERT_GUARD])
def test_missing_guard_stops_quoted_write_without_schema_repair(ledger, settlement, guard):
    if settlement:
        observe(ledger)
    with sqlite3.connect(ledger.path) as conn:
        conn.execute(f"DROP TRIGGER {guard}")
    with pytest.raises(UsageLedgerStorageError, match="guard"):
        observe(ledger, inputs=1000 if settlement else 0,
                status="completed" if settlement else "in_flight")
    with sqlite3.connect(ledger.path) as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (guard,)).fetchone() is None
    assert ledger.snapshot(RUN)["total"]["total_tokens"] == 0


def test_old_storage_and_unquoted_observations_remain_compatible(ledger):
    with sqlite3.connect(ledger.path) as conn:
        conn.execute(f"DROP TRIGGER {GUARD}")
    observe(ledger, quoted=False)
    observe(ledger, quoted=False, inputs=1000, status="completed", cost=0.007)
    assert ledger.snapshot(RUN)["total"]["cost_usd"] == 0.007
    assert ledger.budget_totals(RUN, include_cost=True)["cost_usd"] == 0.007
    with sqlite3.connect(ledger.path) as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (GUARD,)).fetchone() is None


@pytest.mark.parametrize("durable", [False, True])
def test_facade_quote_is_copied_and_cost_stays_estimated_without_cache_discount(ledger, durable, monkeypatch):
    if durable:
        tel.LLMMeter.attach_durable_run(RUN, str(ledger.path), attempt_id="first")
    selected = quote()
    tel.LLMMeter.record_snapshot("llm_api_attempt", "one", "openai", "fixture", 1000, 1000, 1,
                                run_id=RUN, cost_quote=selected, usage_source="known",
                                cache_read_tokens=600, cache_write_tokens=100, uncached_tokens=None)
    selected["input_usd_per_1k"] = 999
    monkeypatch.setattr(Config, "LLM_COST_PER_MTOK", '{"openai":[99,99]}')
    delta = tel.LLMMeter.record_snapshot("llm_api_attempt", "one", "openai", "fixture", 1000, 1000, 1,
                                       run_id=RUN, cost_quote=quote(), usage_source="known",
                                       cache_read_tokens=600, cache_write_tokens=100, uncached_tokens=None)
    assert not any(delta.values())
    snapshot = tel.LLMMeter.snapshot(RUN)
    assert snapshot["total"]["cost_usd"] == 0.003 and snapshot["cost_estimated"] is True
    if durable:
        assert snapshot["total"]["cache_read_tokens"] == 600
        assert snapshot["total"]["cache_write_tokens"] == 100
        assert snapshot["cache_partition_known"] is False
        assert operation(ledger)[0]["cost_quote"] == quote()


def test_in_memory_crossed_snapshots_and_quote_identity(ledger):
    for inputs, outputs in ((1000, 0), (0, 1000)):
        tel.LLMMeter.record_snapshot("llm_api_attempt", "one", "openai", "fixture", inputs, outputs, 0,
                                    run_id=RUN, cost_quote=quote())
    assert tel.LLMMeter.snapshot(RUN)["total"]["cost_usd"] == 0.003
    with pytest.raises(UsageLedgerConflict):
        tel.LLMMeter.record_snapshot("llm_api_attempt", "one", "openai", "fixture", 1000, 1000, 0,
                                    run_id=RUN)


@pytest.mark.parametrize("invalid", ["explicit_cost", "non_api"])
def test_quote_facade_rejects_ambiguous_cost_or_non_api_source(ledger, invalid):
    kwargs = {"cost_usd": 0} if invalid == "explicit_cost" else {}
    with pytest.raises(ValueError):
        tel.LLMMeter.record_snapshot("llm_api_attempt" if invalid == "explicit_cost" else "llm_call",
                                    "one", "openai", "fixture", 0, 0, 0,
                                    run_id=RUN, cost_quote=quote(), **kwargs)


def test_unquoted_in_memory_cost_estimated_behavior_is_unchanged(ledger):
    tel.LLMMeter.record_snapshot("legacy", "one", "openai", "fixture", 1000, 100, 0,
                                run_id=RUN, cost_usd=0.1)
    assert tel.LLMMeter.snapshot(RUN)["cost_estimated"] is False


def test_actual_older_writer_cannot_reprice_a_copied_quote(ledger, older_ledger):
    observe(ledger)
    before = operation(ledger)
    with pytest.raises(RuntimeError, match="Cannot commit"):
        observe(older_ledger(str(ledger.path)), inputs=1000, outputs=1000, status="completed", cost=0.099)
    assert operation(ledger) == before
    assert ledger.snapshot(RUN)["total"]["cost_usd"] == 0
    # An older writer with the original quote and correct arithmetic still fits
    # the contract. Its terminal observation can be replayed without credit.
    observe(older_ledger(str(ledger.path)), inputs=1000, outputs=1000, status="completed", cost=0.003)
    assert not any(observe(ledger, inputs=1000, outputs=1000, status="completed").values())


def test_preexisting_bad_quoted_cost_is_not_corrected_with_negative_delta(ledger, older_ledger):
    observe(ledger)
    with sqlite3.connect(ledger.path) as conn:
        conn.execute(f"DROP TRIGGER {GUARD}")
        conn.execute(f"DROP TRIGGER {INSERT_GUARD}")
    observe(older_ledger(str(ledger.path)), inputs=1000, outputs=1000, status="completed", cost=0.099)
    ledger.initialize(RUN)
    with pytest.raises(UsageLedgerStorageError):
        observe(ledger, inputs=1000, outputs=1000, status="completed")
    assert operation(ledger)[1]["cost_usd"] == 0.099
    with sqlite3.connect(ledger.path) as conn:
        assert [row[0] for row in conn.execute("SELECT cost_usd FROM usage_deltas")] == [0.099]


@pytest.mark.parametrize("unknown", [False, True])
def test_zero_and_unknown_quotes_do_not_escape_formula_validation(ledger, older_ledger, unknown):
    selected = quote(input_usd_per_1k=0.0, output_usd_per_1k=0.0)
    if unknown:
        selected = quote(source="unpriced", rate_key=None, input_usd_per_1k=None, output_usd_per_1k=None)
    observe(ledger, cost_quote=selected)
    with pytest.raises(RuntimeError, match="Cannot commit"):
        observe(older_ledger(str(ledger.path)), inputs=1000, status="completed", cost_quote=selected, cost=0.001)
    observe(older_ledger(str(ledger.path)), inputs=1000, status="completed", cost_quote=selected, cost=0)
    assert not any(observe(ledger, inputs=1000, status="completed", cost_quote=selected, cost=0).values())
    assert ledger.snapshot(RUN)["total"]["cost_usd"] == 0
    assert ledger.snapshot(RUN)["cost_estimated"] is True


@pytest.mark.parametrize("update", [False, True])
@pytest.mark.parametrize("invalid", ["price", "estimated", "attribution", "unknown_rate", "null_rate", "nonfinite"])
def test_database_guards_validate_quoted_inserts_and_updates(ledger, update, invalid):
    observe(ledger)
    metadata, counters = operation(ledger)
    if invalid == "price":
        counters["cost_usd"] = 1
    elif invalid == "estimated":
        metadata["cost_estimated"] = 1
    elif invalid == "attribution":
        metadata["provider"] = "different"
    elif invalid == "unknown_rate":
        metadata["cost_quote"]["source"] = "unpriced"
    elif invalid == "null_rate":
        metadata["cost_quote"]["input_usd_per_1k"] = None
    else:
        counters["cost_usd"] = float("nan")
    with sqlite3.connect(ledger.path) as conn:
        with pytest.raises(sqlite3.Error):
            if update:
                conn.execute("UPDATE usage_operations SET metadata_json=?,counter_json=? WHERE run_id=?",
                             (json.dumps(metadata), json.dumps(counters), RUN))
            else:
                conn.execute("INSERT INTO usage_operations SELECT run_id,source,'forged',owner_attempt_id,"
                             "?,?,status,snapshot_version FROM usage_operations WHERE run_id=?",
                             (json.dumps(metadata), json.dumps(counters), RUN))
    assert ledger.snapshot(RUN)["total"]["cost_usd"] == 0


def test_quoted_costs_retain_grouped_rounding_threshold(ledger):
    for index, (stage, amount) in enumerate((("a", 0.1), ("b", 0.1), ("a", 0.3), ("b", 0.0000005))):
        observe(ledger, str(index), inputs=1000, status="completed", cost=amount,
                cost_quote=quote(input_usd_per_1k=amount), metadata={"stage": stage})
    assert ledger.snapshot(RUN)["total"]["cost_usd"] == 0.500001
    assert ledger.budget_totals(RUN, include_cost=True)["cost_usd"] == 0.500001
