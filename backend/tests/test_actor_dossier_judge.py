"""NEXTSTEPS P3-1: actor-dossier AI-judge pure helpers (offline).

deerflow_research.py imports the deerflow harness lazily, so its pure judge helpers
(dossier_passes / build_judge_prompt) test fine under backend/.venv. The judge→refine
loop itself needs a live model and is exercised end-to-end.
"""

import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "deerflow_bridge"))

import deerflow_research as dr  # noqa: E402


def test_dossier_passes_all_high():
    sc = {"scores": {k: 5 for k in dr._JUDGE_DIMS}, "verdict": "PASS"}
    assert dr.dossier_passes(sc) is True


def test_dossier_passes_fails_on_low_critical_dim():
    sc = {"scores": {**{k: 4 for k in dr._JUDGE_DIMS}, "cast_correctness": 3}}
    assert dr.dossier_passes(sc) is False        # a critical dim < 4


def test_dossier_passes_fails_on_any_dim_below_3():
    sc = {"scores": {**{k: 5 for k in dr._JUDGE_DIMS}, "history_evolution": 2}}
    assert dr.dossier_passes(sc) is False        # any dim < 3


def test_dossier_passes_allows_noncritical_at_3():
    sc = {"scores": {**{k: 5 for k in dr._JUDGE_DIMS}, "history_evolution": 3, "salience_ranking": 3}}
    assert dr.dossier_passes(sc) is True          # criticals >=4, none <3, mean >=4


def test_dossier_passes_degrades_without_scorecard():
    assert dr.dossier_passes(None) is True
    assert dr.dossier_passes({}) is True                      # no scores, no FAIL → don't block
    assert dr.dossier_passes({"verdict": "FAIL"}) is False    # explicit FAIL, no scores


def test_build_judge_prompt_mentions_dims_and_question():
    p = dr.build_judge_prompt("谁会赢 2027", None)
    assert "谁会赢 2027" in p and "cast_correctness" in p and "PASS" in p
