"""NEXTSTEPS P3-2: cross-track cast reconciliation (pure function)."""

from app.utils.actors import reconcile_cast


def test_reconcile_noop_when_no_duplicates():
    actors = {"actors": [{"name": "NVIDIA"}, {"name": "AMD"}], "relationships": []}
    out, audit = reconcile_cast(actors)
    assert audit["merged"] == [] and audit["n_after"] == 2
    assert out is actors  # unchanged object on no-op


def test_reconcile_merges_alias_duplicate_and_remaps_relationships():
    actors = {
        "actors": [
            {"name": "NVIDIA", "role": "GPU 龙头", "aliases": ["英伟达"]},
            {"name": "NVIDIA Corp", "stance": "扩张", "influence": "high"},  # same entity (containment)
            {"name": "TSMC", "role": "代工"},
        ],
        "relationships": [
            {"source": "NVIDIA Corp", "target": "TSMC", "type": "DEPENDS_ON"},
        ],
    }
    out, audit = reconcile_cast(actors)
    names = [a["name"] for a in out["actors"]]
    assert len(names) == 2                       # NVIDIA + TSMC (the two NVIDIAs merged)
    assert "TSMC" in names
    merged = [a for a in out["actors"] if a["name"] in ("NVIDIA", "NVIDIA Corp")][0]
    # richer survivor keeps fields from both rows
    assert merged.get("role") == "GPU 龙头" and merged.get("stance") == "扩张"
    assert "NVIDIA Corp" in (merged.get("aliases") or []) and "英伟达" in (merged.get("aliases") or [])
    # relationship endpoint remapped to the canonical survivor name
    assert out["relationships"][0]["source"] == merged["name"]
    assert audit["n_before"] == 3 and audit["n_after"] == 2 and len(audit["merged"]) == 1


def test_reconcile_does_not_overmerge_short_names():
    # "AI" vs "AIA" must NOT merge (short-name guard); distinct entities preserved
    actors = {"actors": [{"name": "AI"}, {"name": "AIA"}, {"name": "EU"}], "relationships": []}
    out, audit = reconcile_cast(actors)
    assert audit["n_after"] == 3 and audit["merged"] == []


def test_reconcile_records_scalar_conflicts():
    actors = {
        "actors": [
            {"name": "X Corp", "role": "买方", "incentives": [{"driver": "a"}]},
            {"name": "X Corporation", "role": "卖方"},  # conflicting role
        ],
        "relationships": [],
    }
    out, audit = reconcile_cast(actors)
    assert audit["n_after"] == 1
    conflicts = audit["merged"][0]["conflicts"]
    assert any(c["field"] == "role" for c in conflicts)
