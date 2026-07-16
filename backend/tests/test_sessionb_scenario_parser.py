"""forecast_inputs_from_report_markdown 多情景节稳健性（Session B）。

取证（pipe_0e1b84d2682a grid-storage）：真实报告有多处情景复述——执行摘要「Scenario A
(48%)」、地区节「…for China, US, EU (sum=100%)」、权威节「## Scenarios (…summing to
100%)」内 `### SCN-A — Lithium Dominance: 30%`。旧解析器命中早前一个概率不合计~100% 的
杂乱节即 `return {}`，永不触达后面那个干净分布节 → world_state 种子为空、决策通道不点火。
修复：跳过不构成分布的候选节，继续尝试后续节。
"""
import os, sys
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO, "backend"))
from app.utils import actors as A  # noqa: E402


def test_skips_messy_section_reaches_clean_distribution():
    md = """
## Executive Summary
The base case is Scenario A (48% probability) with diversification counterweights.

### Four MECE scenarios for LFP
**Why A leads.** Scenario A dominates on cost. Scenario B is the trigger case.

## Scenarios (4 mutually exclusive, summing to 100%)

### SCN-A — Lithium Dominance: 30%
High-scale, low-diversification.

### SCN-B — LDES Diversified: 25%
High-scale, high-diversification.

### SCN-C — China-Centric Vertical: 20%
Concentration outcome.

### SCN-D — Stagnation: 25%
Non-technological constraints bind.
"""
    fi = A.forecast_inputs_from_report_markdown(md)
    scs = fi.get("scenarios") or []
    assert len(scs) == 4
    names = [s["name"] for s in scs]
    assert any("Lithium Dominance" in n for n in names)
    seed = A.world_state_seed_from_actors({"forecast_inputs": fi})
    assert len(seed.get("scenarios") or []) == 4
    assert abs(sum(seed["base_rates"].values()) - 1.0) < 0.02


def test_colon_suffix_percent_heading_format():
    md = """## Scenarios (summing to 100%)
### Scenario One: 60%
### Scenario Two: 40%
"""
    fi = A.forecast_inputs_from_report_markdown(md)
    assert len(fi.get("scenarios") or []) == 2


def test_no_valid_distribution_returns_empty():
    md = """## Scenarios
### Scenario A: 10%
### Scenario B: 20%
"""  # sums to 30% -> not a distribution
    assert A.forecast_inputs_from_report_markdown(md) == {}


def test_prefers_canonical_global_section_over_earlier_valid_subsection():
    """The live grid dossier contains several internally valid 100% tables.

    A technology subsection appears first, but the canonical top-level
    ``## Scenarios`` section is the forecast that must seed world-state.  First
    valid wins would silently drive the simulation with the LFP-only branch.
    """
    md = """
### Four MECE scenarios for LFP, 2026–2040
| Scenario | Probability |
|---|---:|
| A. LFP scale | 30% |
| B. LFP saturation | 25% |
| C. LFP fragmentation | 20% |
| D. LFP stagnation | 25% |

## Scenarios (4 mutually exclusive, summing to 100%)
### A. High growth, lithium-dominant: 47%
### B. High growth, diversified: 29%
### C. Low growth, lithium-dominant: 18%
### D. Low growth, LDES leap: 6%
"""
    fi = A.forecast_inputs_from_report_markdown(md)
    scenarios = fi.get("scenarios") or []
    assert [row["probability"] for row in scenarios] == [0.47, 0.29, 0.18, 0.06]
    assert "High growth" in scenarios[0]["name"]
