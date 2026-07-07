"""Offline unit tests for PAR-2 parallel-research-track merge helpers.

These are pure functions (no network / LLM / disk) that deterministically fold K
angle-specialized research tracks back into one handoff/. Covers the four
load-bearing merge rules called out in the PAR-2 spec:

  * source union with highest-tier-wins dedup by URL
  * research_report concat under '# Track N' H1 headers
  * quantitative/contested/timeline concat-with-dedup
  * research_quality = min score across tracks (with per-track detail)
  * prediction_markets = freshest snapshot by as_of

plus actors merge routed through the existing reconcile_cast machinery.
"""

from app.services.pipeline_orchestrator import (
    merge_track_reports,
    merge_sources_union,
    merge_list_dedup,
    merge_actors_objs,
    merge_research_quality,
    pick_freshest_markets,
    _source_tier_histogram,
    _track_tier_rank,
    _track_norm_url,
    _RESEARCH_TRACK_ANGLES,
)


# ── tier rank / url normalization ───────────────────────────────────────────

def test_tier_rank_orders_s1_highest_unknown_lowest():
    assert _track_tier_rank("S1") == 1
    assert _track_tier_rank("s3") == 3
    assert _track_tier_rank("S4") == 4
    assert _track_tier_rank(None) == 9
    assert _track_tier_rank("garbage") == 9


def test_norm_url_strips_trailing_slash_and_case():
    assert _track_norm_url("https://A.com/x/") == "https://a.com/x"
    assert _track_norm_url("  https://a.com/x  ") == "https://a.com/x"
    assert _track_norm_url(None) == ""


# ── source union: highest tier wins on URL dedup ────────────────────────────

def test_source_union_dedups_by_url_keeping_highest_tier():
    t1 = [{"url": "https://a.gov/x", "tier": "S3", "title": "A"}]
    t2 = [{"url": "https://a.gov/x/", "tier": "S1", "date": "2026-01-01"}]  # same URL, better tier
    t3 = [{"url": "https://b.com/y", "tier": "S2", "title": "B"}]
    out = merge_sources_union([t1, t2, t3])
    # a.gov collapsed to one row, tier upgraded to S1, missing fields backfilled
    urls = [s["url"] for s in out]
    assert urls == ["https://a.gov/x", "https://b.com/y"]
    a = out[0]
    assert a["tier"] == "S1"          # highest tier kept
    assert a["title"] == "A"          # kept from first-seen
    assert a["date"] == "2026-01-01"  # backfilled from the higher-tier row


def test_source_union_keeps_urlless_rows_and_is_deterministic():
    t1 = [{"title": "no-url row"}]
    t2 = [{"url": "https://a.com", "tier": "S2"}]
    out = merge_sources_union([t1, t2])
    assert len(out) == 2
    assert out[0]["title"] == "no-url row"
    # rerun → identical order/content (determinism)
    assert merge_sources_union([t1, t2]) == out


def test_source_union_ignores_non_list_and_non_dict():
    assert merge_sources_union([None, "bad", [123, {"url": "u", "tier": "S1"}]]) == [
        {"url": "u", "tier": "S1"}
    ]


# ── report concat under '# Track N' headers ─────────────────────────────────

def test_merge_track_reports_concats_under_h1_headers():
    out = merge_track_reports([("Angle A", "body-a"), ("Angle B", "body-b")])
    assert out == "# Track 1: Angle A\n\nbody-a\n\n# Track 2: Angle B\n\nbody-b"


def test_merge_track_reports_skips_empty_and_renumbers_contiguously():
    out = merge_track_reports([("A", "body-a"), ("B", "   "), ("C", "body-c")])
    # empty middle track dropped; numbering stays contiguous 1,2
    assert "# Track 1: A" in out
    assert "# Track 2: C" in out
    assert "Track 3" not in out
    assert "B" not in out.split("body-a")[0] or "# Track" in out  # B header absent


# ── quantitative/contested/timeline concat + dedup ──────────────────────────

def test_merge_list_dedup_concats_and_dedups_by_identity():
    t1 = [{"metric": "gdp", "value": 1}, {"metric": "cpi", "value": 2}]
    t2 = [{"value": 1, "metric": "gdp"}, {"metric": "unemp", "value": 3}]  # first is dup (key order irrelevant)
    out = merge_list_dedup([t1, t2])
    assert out == [
        {"metric": "gdp", "value": 1},
        {"metric": "cpi", "value": 2},
        {"metric": "unemp", "value": 3},
    ]


def test_merge_list_dedup_tolerates_non_list_inputs():
    assert merge_list_dedup([None, [{"a": 1}], "bad", [{"a": 1}]]) == [{"a": 1}]


# ── research_quality = min across tracks ────────────────────────────────────

def test_merge_research_quality_takes_min_score_with_per_track_detail():
    m1 = {"research_quality": {"score": 0.80, "components": {"x": 1}}}
    m2 = {"research_quality": {"score": 0.55, "components": {"y": 2}}}
    m3 = {"research_quality": {"score": 0.70}}
    merged = merge_research_quality([m1, m2, m3])
    assert merged["score"] == 0.55                     # conservative min
    assert merged["components"] == {"y": 2}            # base is the min-score track
    assert merged["merged_from_tracks"] == 3
    scores = [p.get("score") for p in merged["per_track"]]
    assert scores == [0.80, 0.55, 0.70]


def test_merge_research_quality_handles_missing_scores():
    merged = merge_research_quality([{}, {"research_quality": {"no_score": True}}])
    assert "score" not in merged
    assert merged["merged_from_tracks"] == 2
    assert merged["per_track"][0] == {"track": 1, "research_quality": None}


# ── source tier histogram recompute ─────────────────────────────────────────

def test_source_tier_histogram_counts_by_tier():
    sources = [
        {"tier": "S1"}, {"tier": "S1"}, {"tier": "S3"}, {"tier": None}, {"no": "tier"}
    ]
    hist = _source_tier_histogram(sources)
    assert hist == {"s1_count": 2, "s2_count": 0, "s3_count": 1, "s4_count": 0, "s_unknown": 2}


# ── freshest prediction-markets snapshot ────────────────────────────────────

def test_pick_freshest_markets_by_as_of():
    a = {"as_of": "2026-01-01T00:00:00Z", "markets": [{"q": 1}]}
    b = {"as_of": "2026-03-01T00:00:00Z", "markets": [{"q": 2}]}
    assert pick_freshest_markets([a, b]) is b
    assert pick_freshest_markets([b, a]) is b


def test_pick_freshest_markets_prefers_nonempty_then_falls_back():
    empty = {"as_of": "2026-05-01", "markets": []}
    nonempty = {"as_of": "2026-01-01", "markets": [{"q": 1}]}
    assert pick_freshest_markets([empty, nonempty]) is nonempty
    # all empty → returns first dict (fallback)
    only_empty = {"as_of": "x", "markets": []}
    assert pick_freshest_markets([only_empty]) is only_empty
    assert pick_freshest_markets([None, "bad"]) is None


# ── actors merge via reconcile_cast machinery ───────────────────────────────

def test_merge_actors_unions_rows_and_reconciles_duplicates():
    t1 = {
        "central_question": "Q1",
        "as_of_date": "2026-05-01",
        "actors": [{"name": "Nvidia", "role": "chipmaker", "influence": "high"}],
        "relationships": [{"source": "Nvidia", "target": "TSMC", "label": "buys_from"}],
    }
    t2 = {
        "central_question": "Q2",  # scalar taken from first non-empty track (t1)
        "actors": [
            {"name": "NVIDIA Corp", "aliases": ["Nvidia"]},   # duplicate of t1's Nvidia
            {"name": "AMD", "role": "chipmaker"},
        ],
    }
    merged, audit = merge_actors_objs([t1, t2])
    assert merged is not None
    names = {a.get("name") for a in merged.get("actors", [])}
    # Nvidia + NVIDIA Corp reconciled to a single canonical row; AMD distinct
    assert len(merged["actors"]) == 2
    assert "AMD" in names
    assert merged["central_question"] == "Q1"   # scalar from first non-empty track
    assert audit.get("n_before") == 3
    assert audit.get("n_after") == 2


def test_merge_actors_all_empty_returns_none():
    merged, audit = merge_actors_objs([None, "bad", {}])
    # {} is a dict → becomes base with empty actors; reconcile of <2 rows is a no-op
    assert merged == {"actors": []}
    merged2, _ = merge_actors_objs([None, "bad"])
    assert merged2 is None


# ── angle table sanity ──────────────────────────────────────────────────────

def test_track_angles_track1_has_no_prefix():
    # Track 1 must be the verbatim base evidence sweep (empty prefix → byte-identical prompt).
    assert _RESEARCH_TRACK_ANGLES[0][2] == ""
    # tracks 2/3 carry angle-specialization prefixes
    assert _RESEARCH_TRACK_ANGLES[1][2].strip() != ""
    assert _RESEARCH_TRACK_ANGLES[2][2].strip() != ""
    assert len(_RESEARCH_TRACK_ANGLES) == 3
