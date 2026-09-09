"""Recorded price coverage is distinct from usage resolution and reservations."""

import json
from pathlib import Path
import sqlite3
import subprocess
from threading import Event, Thread

import pytest

from app.utils import telemetry as tel
from app.utils.api_cost import quote_cost
from app.utils.usage_ledger import (
    UsageLedger, UsageLedgerBudgetExceeded, UsageLedgerStorageError,
    _COST_INEXACT_SQL, _COST_PRICE_CLASS_SQL,
)
from test_api_cost_ledger import quote


RUN = "pipe-cost-coverage"
INDEX = "usage_operations_cost_coverage"


@pytest.fixture
def ledger(tmp_path):
    tel.LLMMeter.reset(RUN)
    value = UsageLedger(str(tmp_path / "usage.sqlite3"))
    value.initialize(RUN)
    yield value
    tel.LLMMeter.reset(RUN)


@pytest.fixture(scope="module")
def older_ledger():
    source = subprocess.check_output(["git", "show", "a000e29:backend/app/utils/usage_ledger.py"],
                                     cwd=Path(__file__).resolve().parents[2], text=True)
    namespace = {"__name__": "app.utils._pre_coverage_ledger", "__package__": "app.utils"}
    exec(compile(source, "<a000e29-usage-ledger>", "exec"), namespace)
    return namespace["UsageLedger"]


def observe(ledger, identity, *, source="llm_api_attempt", price="priced", status="completed",
            usage="known", counters=None, attempt="first", **kwargs):
    meta = {"stage": "run", "provider": "openai", "model": "fixture", "usage_class": usage,
            "billing_basis": "api", "cost_estimated": True, "fallback": False}
    counts = {"calls": 0 if status == "in_flight" else 1, "prompt_tokens": 0, "completion_tokens": 0,
              "cached": 0, **(counters or {})}
    if source == "llm_api_attempt" and price != "unquoted":
        selected = quote()
        if price == "unpriced":
            selected = quote(source="unpriced", rate_key=None, input_usd_per_1k=None, output_usd_per_1k=None)
        elif price == "zero":
            selected = quote(input_usd_per_1k=0, output_usd_per_1k=0)
        meta["cost_quote"] = selected
        counts["cost_usd"] = quote_cost(selected, counts["prompt_tokens"], counts["completion_tokens"])
    return ledger.record_snapshot(run_id=RUN, attempt_id=attempt, source=source, operation_id=identity,
                                  metadata=meta, counters=counts, status=status, **kwargs)


def state(ledger):
    return ledger.snapshot(RUN)["cost_coverage"]


def test_empty_state_is_empty_recorded_scope_without_complete_usage(ledger):
    assert state(ledger) == {"schema": "cost-coverage-state/v1", "coverage": "recorded_operations",
        "priced_api_operations": 0, "unpriced_api_operations": 0, "unquoted_api_operations": 0,
        "inexact_api_operations": 0, "non_api_operations": 0, "cache_only_operations": 0,
        "legacy_baseline_present": False, "legacy_baseline_ambiguous": False,
        "price_coverage_complete": True, "usage_complete": False}


def test_price_partition_and_usage_gap_are_independent_and_run_wide(ledger):
    observe(ledger, "pending", status="in_flight")
    observe(ledger, "known")
    observe(ledger, "estimate", usage="estimated")
    observe(ledger, "http-error", status="unknown", usage="known")
    observe(ledger, "malformed", status="accounting_error", usage="unknown")
    observe(ledger, "unpriced", price="unpriced")
    observe(ledger, "missing", price="unquoted")
    observe(ledger, "research", source="research_process")
    observe(ledger, "cache", source="llm_call", counters={"cached": 1})
    result = state(ledger)
    assert (result["priced_api_operations"], result["unpriced_api_operations"],
            result["unquoted_api_operations"], result["inexact_api_operations"]) == (5, 1, 1, 3)
    assert result["non_api_operations"] == result["cache_only_operations"] == 1
    assert result["price_coverage_complete"] is False
    assert ledger.snapshot(RUN, attempt_id="new-attempt")["cost_coverage"] == result


@pytest.mark.parametrize("gap", ["unpriced", "unquoted", "research_process"])
def test_price_gap_denial_precedes_marker_and_token_policy_mutation(ledger, gap):
    observe(ledger, "history", price=gap if gap != "research_process" else "priced",
            source=gap if gap == "research_process" else "llm_api_attempt")
    plan = {"token_limit": 100, "prompt_tokens_estimate": 10,
            "completion_tokens_limit": 10, "estimator": "utf8-json-quarter/v1"}
    with pytest.raises(UsageLedgerBudgetExceeded, match="price coverage"):
        observe(ledger, "rejected", status="in_flight", require_cost_coverage=True, token_reservation=plan)
    with sqlite3.connect(ledger.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM usage_operations").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM usage_reservations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM usage_token_policy").fetchone()[0] == 0


@pytest.mark.parametrize("status,usage", [("in_flight", "unknown"), ("unknown", "unknown"),
                                         ("completed", "estimated"), ("unknown", "known")])
def test_priced_uncertainty_does_not_serialize_or_stop_price_only_admission(ledger, status, usage):
    observe(ledger, "prior", status=status, usage=usage)
    observe(ledger, "next", status="in_flight", require_cost_coverage=True)
    assert state(ledger)["price_coverage_complete"] is True
    assert state(ledger)["priced_api_operations"] == 2
    assert state(ledger)["inexact_api_operations"] == (1 if usage == "known" else 2)


def test_known_terminal_error_and_explicit_zero_are_priced(ledger):
    observe(ledger, "zero", price="zero", status="unknown", counters={"prompt_tokens": 1000})
    assert state(ledger)["priced_api_operations"] == 1
    assert state(ledger)["inexact_api_operations"] == 0
    assert ledger.snapshot(RUN)["total"]["cost_usd"] == 0
    observe(ledger, "next", status="in_flight", require_cost_coverage=True)


@pytest.mark.parametrize("change", [{}, {"cached": 0}, {"calls": 2}, {"prompt_tokens": 1},
                                    {"completion_tokens": 1}, {"cache_read_tokens": 1},
                                    {"cache_write_tokens": 1}, {"uncached_tokens": 1}, {"cost_usd": 0.1}])
def test_local_cache_exemption_requires_no_token_or_cost_observation(ledger, change):
    observe(ledger, "cache", source="llm_call", counters={"cached": 1, **change})
    assert state(ledger)["cache_only_operations"] == (0 if change else 1)
    assert state(ledger)["non_api_operations"] == (1 if change else 0)


@pytest.mark.parametrize("source,status,usage", [("simulation_process", "completed", "known"),
                                                ("llm_call", "unknown", "known"),
                                                ("llm_call", "completed", "estimated")])
def test_cache_flags_cannot_hide_another_source_or_uncertain_usage(ledger, source, status, usage):
    observe(ledger, "cache", source=source, status=status, usage=usage, counters={"cached": 1})
    assert state(ledger)["non_api_operations"] == 1


def test_zero_opaque_baseline_still_lacks_price_provenance(tmp_path):
    ledger = UsageLedger(str(tmp_path / "legacy.sqlite3"))
    ledger.initialize(RUN, {"total": {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0}})
    assert state(ledger)["legacy_baseline_present"] is True
    assert state(ledger)["price_coverage_complete"] is False
    with pytest.raises(UsageLedgerBudgetExceeded):
        observe(ledger, "next", status="in_flight", require_cost_coverage=True)


@pytest.mark.parametrize("options", [{"status": "completed"}, {"price": "unpriced"},
                                     {"price": "unquoted"}, {"counters": {"calls": 1}},
                                     {"require_cost_coverage": 1}])
def test_invalid_coverage_request_is_a_recoverable_policy_stop(ledger, options):
    args = {"status": "in_flight", "require_cost_coverage": True, **options}
    with pytest.raises(UsageLedgerBudgetExceeded):
        observe(ledger, "bad", **args)
    assert state(ledger)["priced_api_operations"] == 0


def test_old_writer_index_maintenance_and_readonly_no_index_fallback(ledger, older_ledger):
    observe(older_ledger(str(ledger.path)), "old", price="unquoted", counters={"prompt_tokens": 1000})
    indexed = state(ledger)
    assert indexed["unquoted_api_operations"] == 1
    with sqlite3.connect(ledger.path) as conn:
        conn.execute(f"DROP INDEX {INDEX}")
    assert UsageLedger(str(ledger.path)).snapshot(RUN)["cost_coverage"] == indexed
    with pytest.raises(UsageLedgerBudgetExceeded):
        observe(ledger, "next", status="in_flight", require_cost_coverage=True)
    with sqlite3.connect(ledger.path) as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (INDEX,)).fetchone() is None


def test_grouping_and_admission_use_the_shared_expression_index(ledger):
    observe(ledger, "one")
    with sqlite3.connect(ledger.path) as conn:
        grouped = conn.execute(f"EXPLAIN QUERY PLAN SELECT ({_COST_PRICE_CLASS_SQL}), ({_COST_INEXACT_SQL}),"
            f"COUNT(*) FROM usage_operations WHERE run_id=? GROUP BY ({_COST_PRICE_CLASS_SQL}),"
            f"({_COST_INEXACT_SQL})", (RUN,)).fetchall()
        admission = conn.execute(f"EXPLAIN QUERY PLAN SELECT 1 FROM usage_operations WHERE run_id=? "
            f"AND ({_COST_PRICE_CLASS_SQL}) IN ('api_unpriced','api_unquoted','non_api') LIMIT 1", (RUN,)).fetchall()
    assert all(INDEX in " ".join(str(row) for row in plan) for plan in (grouped, admission))


@pytest.mark.parametrize("change", [{"extra": 1}, {"input_usd_per_1k": None},
                                    {"input_usd_per_1k": True}, {"rate_key": "wrong"},
                                    {"provider": "different"}, {"model": "different"},
                                    {"schema": "unknown"}, {"source": "unknown"}])
def test_malformed_quote_cannot_establish_price_coverage(ledger, change):
    observe(ledger, "bad")
    with sqlite3.connect(ledger.path) as conn:
        conn.execute("DROP TRIGGER usage_preserve_api_cost_quote")
        metadata = json.loads(conn.execute("SELECT metadata_json FROM usage_operations").fetchone()[0])
        metadata["cost_quote"].update(change)
        conn.execute("UPDATE usage_operations SET metadata_json=?", (json.dumps(metadata),))
    ledger.initialize(RUN)
    assert state(ledger)["unquoted_api_operations"] == 1
    with pytest.raises(UsageLedgerBudgetExceeded):
        observe(ledger, "next", status="in_flight", require_cost_coverage=True)


def test_gap_committed_before_marker_transaction_is_seen(ledger, monkeypatch):
    ready, inserted = Event(), Event()
    original = ledger._connect

    def connect(*, write, create=False):
        conn = original(write=write, create=create)
        if write and not create:
            ready.set()
            assert inserted.wait(5)
        return conn

    errors = []

    def request():
        try:
            observe(ledger, "next", status="in_flight", require_cost_coverage=True)
        except Exception as exc:
            errors.append(exc)

    monkeypatch.setattr(ledger, "_connect", connect)
    worker = Thread(target=request)
    worker.start()
    assert ready.wait(5)
    observe(UsageLedger(str(ledger.path)), "late-gap", price="unpriced")
    inserted.set()
    worker.join(5)
    assert not worker.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], UsageLedgerBudgetExceeded)
    assert state(ledger)["priced_api_operations"] == 0


def test_facade_denial_is_not_sticky_and_coverage_survives_flush_and_restart(ledger, tmp_path):
    observe(ledger, "old", price="unpriced")
    tel.LLMMeter.attach_durable_run(RUN, str(ledger.path), attempt_id="first")
    kwargs = {"run_id": RUN, "stage": "run", "calls": 0, "status": "in_flight", "cost_quote": quote()}
    with pytest.raises(tel.BudgetExceeded):
        tel.LLMMeter.record_snapshot("llm_api_attempt", "rejected", "openai", "fixture", 0, 0, 0,
                                    require_cost_coverage=True, **kwargs)
    # Restoring the caller's disabled dollar guard needs no storage reset.
    tel.LLMMeter.record_snapshot("llm_api_attempt", "allowed", "openai", "fixture", 0, 0, 0, **kwargs)
    expected = state(ledger)
    assert tel.LLMMeter.status_snapshot(RUN)["cost_coverage"] == expected
    output = tmp_path / "run.json"
    tel.LLMMeter.write_run_telemetry(str(output), RUN, {"cost_coverage": "stale"})
    assert json.loads(output.read_text())["cost_coverage"] == expected
    tel.LLMMeter.reset(RUN)
    assert UsageLedger(str(ledger.path)).snapshot(RUN)["cost_coverage"] == expected


def test_missing_storage_is_not_empty_price_coverage(tmp_path):
    path = tmp_path / "missing.sqlite3"
    with pytest.raises(UsageLedgerStorageError):
        UsageLedger(str(path)).snapshot(RUN)
    assert not path.exists()


@pytest.mark.parametrize("provider", ["OpenAI", "\tOpenAI\t", "\nOpenAI\n", "ÜPROV", "\u00a0OpenAI\u00a0", "\0OpenAI"])
@pytest.mark.parametrize("invalid", [False, True])
def test_provider_normalization_matches_quote_validator(ledger, provider, invalid):
    selected = quote(provider=provider, rate_key=provider.strip().lower() + ":fixture")
    metadata = {"stage": "run", "provider": provider, "model": "fixture", "cost_quote": selected,
                "usage_class": "known", "billing_basis": "api", "cost_estimated": True}
    ledger.record_snapshot(run_id=RUN, attempt_id="first", source="llm_api_attempt", operation_id="unicode",
                           metadata=metadata, counters={"calls": 1}, status="unknown")
    if invalid:
        with sqlite3.connect(ledger.path) as conn:
            conn.execute("DROP TRIGGER usage_preserve_api_cost_quote")
            metadata["cost_quote"]["rate_key"] = "wrong"
            conn.execute("UPDATE usage_operations SET metadata_json=?", (json.dumps(metadata),))
        ledger.initialize(RUN)
    observed = state(ledger)
    assert observed["priced_api_operations"] == (0 if invalid else 1)
    assert observed["unquoted_api_operations"] == (1 if invalid else 0)
    assert observed["inexact_api_operations"] == 0
    if invalid:
        with pytest.raises(UsageLedgerBudgetExceeded):
            observe(ledger, "next", status="in_flight", require_cost_coverage=True)
    else:
        observe(ledger, "next", status="in_flight", require_cost_coverage=True)


def test_binding_disappearing_during_validation_cannot_bypass_durable_admission(ledger, monkeypatch):
    from app.utils import api_cost
    tel.LLMMeter.attach_durable_run(RUN, str(ledger.path), attempt_id="first")
    original = api_cost.validate_cost_quote
    reset = False

    def validate_then_reset(*args, **kwargs):
        nonlocal reset
        result = original(*args, **kwargs)
        if not reset:
            tel.LLMMeter.reset(RUN)
            reset = True
        return result

    monkeypatch.setattr(api_cost, "validate_cost_quote", validate_then_reset)
    with pytest.raises(tel.BudgetExceeded, match="binding"):
        tel.LLMMeter.record_snapshot("llm_api_attempt", "one", "openai", "fixture", 0, 0, 0,
                                    calls=0, status="in_flight", run_id=RUN, cost_quote=quote(),
                                    require_cost_coverage=True)
    assert not tel.LLMMeter.is_durable_run(RUN)
    assert tel.LLMMeter.snapshot(RUN)["total"]["calls"] == 0
    assert state(ledger)["priced_api_operations"] == 0
    assert RUN not in tel.LLMMeter._runs


def test_unbound_default_stays_in_memory_without_price_gate(ledger):
    tel.LLMMeter.record_snapshot("llm_api_attempt", "one", "openai", "fixture", 10, 1, 0,
                                run_id=RUN, cost_quote=quote())
    assert tel.LLMMeter.snapshot(RUN)["total"]["total_tokens"] == 11
    assert "cost_coverage" not in tel.LLMMeter.snapshot(RUN)
    assert state(ledger)["priced_api_operations"] == 0


def test_embedded_nul_model_keeps_python_label_validation(ledger):
    selected = quote(model="\0fixture", rate_key="openai:\0fixture")
    ledger.record_snapshot(run_id=RUN, attempt_id="first", source="llm_api_attempt", operation_id="nul-model",
        metadata={"stage": "run", "provider": "openai", "model": "\0fixture", "cost_quote": selected,
                  "usage_class": "known", "billing_basis": "api", "cost_estimated": True},
        counters={"calls": 1})
    assert state(ledger)["priced_api_operations"] == 1


@pytest.mark.parametrize("field", ["prompt_tokens", "completion_tokens", "total_tokens", "cache_read_tokens",
                                   "cache_write_tokens", "uncached_tokens", "cost_usd"])
def test_boolean_false_cannot_establish_zero_cache_counters(ledger, field):
    observe(ledger, "cache", source="llm_call", counters={"cached": 1})
    with sqlite3.connect(ledger.path) as conn:
        counters = json.loads(conn.execute("SELECT counter_json FROM usage_operations").fetchone()[0])
        counters[field] = False
        conn.execute("UPDATE usage_operations SET counter_json=?", (json.dumps(counters),))
    assert state(ledger)["cache_only_operations"] == 0
    assert state(ledger)["non_api_operations"] == 1
