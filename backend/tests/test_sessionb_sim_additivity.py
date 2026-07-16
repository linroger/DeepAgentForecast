"""SESSION-B 黄金测试：让多智能体**模拟真正贡献 forecast**的端到端接线。

取证（pipe_bef6879b2e94 / sim_05ab2bdebbd2）：handoff/actors.json 缺 forecast_inputs 键 →
world_state_seed_from_actors 返回 {} → 决策通道从未点火、无 world_state_trajectory.json →
18 轮模拟对任何 forecast 数字零贡献。本文件按四条覆盖钉死修复：

(a) 研究报告情景节 → forecast_inputs 回环：解析结果注入 actors 后 world_state 种子非空；
(b) prepare-path 兜底注入：actors 缺 forecast_inputs 但报告有情景节时，编排器就地补齐；
(c) 诚实且**加性**的 sim 溯源：world-state 块注入时 'world-state outcome shares' 不降级、
    且决策通道收敛份额被记成显式可审计的 forecast.sim_adjustment；块缺席时保持今日降级行为；
(d) 节奏分层非退化：够大的全 principal 阵容被收紧出 sampled 层，产生可分辨的逐轮名册差异。

Offline：零 LLM / 零网络（LLM 抽取用 conftest.FakeLLMClient）。
"""

import math

from app.services.forecast_extractor import (
    extract_binary_forecasts,
    reconcile_forecast_contract,
    world_state_outcome_from_signal_pack,
)
from app.services.pipeline_orchestrator import (
    _forecast_inputs_missing,
    inject_forecast_inputs_from_report,
)
from app.services.simulation_config_generator import (
    AgentActivityConfig,
    SimulationConfigGenerator,
)
from app.utils.actors import (
    forecast_inputs_from_report_markdown,
    world_state_seed_from_actors,
)
from tests.conftest import FakeLLMClient

# 研究报告情景节样例（EN 标题式，2-6 个带概率情景、中点求和≈1）。
_REPORT_MD = """\
# Deep Research Report

## Mutually Exclusive Scenarios

Weighted to sum to 100% for the 2030 horizon.

### Scenario A: Structured Scale (50% probability)

**Description**: Commercial scale in structured settings.

### Scenario B: General-Purpose Acceleration (30% probability)

**Description**: Broad task generalization.

### Scenario C: Stall (20% probability)

**Description**: Persistent reliability gaps cap deployment.
"""

# 世界态结果分布块（逐字节对齐 report_agent._world_state_block 渲染格式）。
_WORLD_STATE_BLOCK = (
    "【预测结果分布 P(outcome)（内部分析先验；须与外部证据交叉验证）】\n"
    "· Structured Scale: 62%\n· General-Purpose Acceleration: 30%\n· Stall: 8%\n"
    "稳定性诊断：已趋稳"
)
_COALITION_BLOCK = (
    "## 派系/联盟图（按共享互动对象聚类，确定性）\n- 派系 1（3 人）: 甲、乙、丙"
)


def _provenance_config(monkeypatch):
    """确定性单轮抽取（contrarian/ensemble 关；sim 敏感开 → 注入 signal_pack）。"""
    from app.config import Config
    monkeypatch.setattr(Config, "FORECAST_BINARY_CONTRARIAN", False, raising=False)
    monkeypatch.setattr(Config, "FORECAST_SIM_SENSITIVITY", True, raising=False)
    monkeypatch.setattr(Config, "FORECAST_ENSEMBLE_MODELS", "", raising=False)


# ------------------------------------------------------------------ (a) 报告→种子回环
def test_report_scenarios_roundtrip_into_actors_and_seed_nonempty():
    parsed = forecast_inputs_from_report_markdown(_REPORT_MD)
    assert parsed and len(parsed["scenarios"]) == 3
    # 注入 actors 后，世界态种子（决策通道的点火条件）必须非空。
    actors = {"actors": [], "forecast_inputs": parsed}
    seed = world_state_seed_from_actors(actors)
    assert seed.get("scenarios") and len(seed["scenarios"]) == 3
    names = seed["scenarios"]
    assert any("Structured Scale" in nm for nm in names)
    assert any("General-Purpose Acceleration" in nm for nm in names)
    assert any("Stall" in nm for nm in names)
    # 基率来自情景概率（点估计），非均匀先验；三点估计求和≈1。
    assert seed.get("uniform_prior") is False
    assert abs(sum(seed["base_rates"].values()) - 1.0) < 1e-6
    _scale_key = next(nm for nm in names if "Structured Scale" in nm)
    assert abs(seed["base_rates"][_scale_key] - 0.50) < 1e-6

    # 空/无情景节 → {}（degrade-safe，绝不播失真种子）。
    assert forecast_inputs_from_report_markdown("no scenarios here") == {}
    assert world_state_seed_from_actors({"actors": []}) == {}


# ------------------------------------------------------------------ (b) prepare 兜底注入
def test_inject_forecast_inputs_populates_missing_actors():
    # actors 缺 forecast_inputs：应就地注入，且注入后种子非空。
    actors = {"actors": [{"name": "X"}], "as_of_date": "2026-01-01"}
    assert _forecast_inputs_missing(actors) is True
    fi = inject_forecast_inputs_from_report(actors, _REPORT_MD)
    assert fi is not None and len(fi["scenarios"]) == 3
    assert actors["forecast_inputs"] is fi              # 就地写入同一对象
    assert world_state_seed_from_actors(actors).get("scenarios")
    assert _forecast_inputs_missing(actors) is False

    # 已有非空 forecast_inputs.scenarios → 视为已具备种子，no-op（不覆盖）。
    seeded = {"forecast_inputs": {"scenarios": [{"name": "Keep", "probability": 1.0}]}}
    assert _forecast_inputs_missing(seeded) is False
    assert inject_forecast_inputs_from_report(seeded, _REPORT_MD) is None
    assert seeded["forecast_inputs"]["scenarios"][0]["name"] == "Keep"

    # Name-only shells are not weighted distributions. They must not suppress
    # the report-derived probabilities and silently force uniform priors.
    shell = {
        "forecast_inputs": {
            "scenarios": [{"name": "Base"}, {"name": "Upside"}],
        }
    }
    assert _forecast_inputs_missing(shell) is True
    shell_fi = inject_forecast_inputs_from_report(shell, _REPORT_MD)
    assert shell_fi is not None and len(shell_fi["scenarios"]) == 3
    shell_seed = world_state_seed_from_actors(shell)
    assert shell_seed["uniform_prior"] is False
    assert abs(sum(shell_seed["base_rates"].values()) - 1.0) < 1e-6

    # 报告无情景节 → 不注入（返回 None，actors 不变）。
    empty = {"actors": []}
    assert inject_forecast_inputs_from_report(empty, "nothing to parse") is None
    assert "forecast_inputs" not in empty

    # 非 dict actors → 无处注入（degrade-safe）。
    assert inject_forecast_inputs_from_report(None, _REPORT_MD) is None
    assert _forecast_inputs_missing(None) is False


# ------------------------------------------------------------------ (c) 诚实+加性 sim 溯源
def test_world_state_outcome_parses_from_signal_pack():
    out = world_state_outcome_from_signal_pack(_WORLD_STATE_BLOCK)
    assert out and out["source"] == "world-state outcome shares"
    assert out["converged"] is True
    shares = out["scenario_shares"]
    assert set(shares) == {"Structured Scale", "General-Purpose Acceleration", "Stall"}
    assert abs(sum(shares.values()) - 1.0) < 1e-6          # 归一
    assert shares["Structured Scale"] > shares["General-Purpose Acceleration"]
    # 无世界态块 → None（不虚构）。
    assert world_state_outcome_from_signal_pack(_COALITION_BLOCK) is None
    assert world_state_outcome_from_signal_pack("") is None


def test_reconcile_records_sim_adjustment_when_outcome_present():
    forecast = {
        "scenarios": [
            {"name": "Structured Scale", "probability": 0.50},
            {"name": "General-Purpose Acceleration", "probability": 0.30},
            {"name": "Stall", "probability": 0.20},
        ],
        "binary_forecasts": [],
    }
    ws = {"scenario_shares": {"Structured Scale": 0.62, "General-Purpose Acceleration": 0.30,
                             "Stall": 0.08}, "converged": True, "converged_at": 5}
    reconcile_forecast_contract(forecast, world_state_outcome=ws)
    adj = forecast.get("sim_adjustment")
    assert adj is not None
    assert adj["source"] == "world-state outcome shares"
    assert adj["converged_at"] == 5 and adj["converged"] is True
    # delta = sim 份额 - 研究先验。
    assert adj["delta_vs_research_prior"]["Structured Scale"] == 0.12
    assert adj["delta_vs_research_prior"]["General-Purpose Acceleration"] == 0.0
    assert adj["delta_vs_research_prior"]["Stall"] == -0.12
    assert forecast["proposition_consistency"]["sim_adjustment"] is adj


def test_reconcile_records_no_sim_adjustment_when_outcome_absent():
    forecast = {
        "scenarios": [{"name": "A", "probability": 0.6}, {"name": "B", "probability": 0.4}],
        "binary_forecasts": [],
    }
    diag = reconcile_forecast_contract(forecast)          # trajectory 缺席
    assert "sim_adjustment" not in forecast               # 绝不虚构 sim 贡献
    assert diag["sim_adjustment"] is None


def test_world_state_label_kept_and_sim_adjustment_end_to_end(monkeypatch):
    """world-state 块真实注入 → 'world-state outcome shares' 不降级、收敛份额挂进
    binary_quality → reconcile 记成 forecast.sim_adjustment（复现 report_agent 调用序）。"""
    _provenance_config(monkeypatch)
    fake = FakeLLMClient(json_responses=[{"binary_forecasts": [
        {"id": "F1", "statement": "Structured scale dominates by 2030", "probability": 0.62,
         "resolution_criteria": "share > 60% by 2030", "theme": "t1", "horizon_year": 2030,
         "source": "world-state outcome shares"},
        {"id": "F2", "statement": "Stall outcome by 2030", "probability": 0.08,
         "resolution_criteria": "share < 10% by 2030", "theme": "t2", "horizon_year": 2030},
    ]}])
    out = extract_binary_forecasts("dossier", fake, min_count=2, language="English",
                                   signal_pack=_WORLD_STATE_BLOCK)
    # 标签保留、零降级。
    assert out["binary_forecasts"][0]["source"] == "world-state outcome shares"
    assert out["binary_quality"]["provenance_downgrades"] == 0
    # 收敛份额被挂到 binary_quality（供 reconcile 拾取）。
    ws_out = out["binary_quality"]["world_state_outcome"]
    assert ws_out["scenario_shares"]["Structured Scale"] > 0.5

    # 复现 report_agent 调用序：binary_quality 先挂到 forecast，再 reconcile。
    forecast = {
        "scenarios": [
            {"name": "Structured Scale", "probability": 0.50},
            {"name": "General-Purpose Acceleration", "probability": 0.30},
            {"name": "Stall", "probability": 0.20},
        ],
        "binary_forecasts": out["binary_forecasts"],
        "binary_quality": out["binary_quality"],
    }
    reconcile_forecast_contract(forecast)                 # 无显式参数 → 自动从 binary_quality 拾取
    assert forecast.get("sim_adjustment") is not None
    assert forecast["sim_adjustment"]["scenario_shares"]["Structured Scale"] > 0.5


def test_no_sim_adjustment_when_block_absent_and_label_downgraded(monkeypatch):
    """world-state 块未注入（只有 coalition 块）→ 同名 source 降级、无 sim_adjustment。"""
    _provenance_config(monkeypatch)
    fake = FakeLLMClient(json_responses=[{"binary_forecasts": [
        {"id": "F1", "statement": "Alpha exceeds 10% by 2030", "probability": 0.7,
         "resolution_criteria": "metric > 10% by 2030", "theme": "t1", "horizon_year": 2030,
         "source": "world-state outcome shares"},
        {"id": "F2", "statement": "Beta below 500 by 2030", "probability": 0.3,
         "resolution_criteria": "metric < 500 by 2030", "theme": "t2", "horizon_year": 2030},
    ]}])
    out = extract_binary_forecasts("dossier", fake, min_count=2, language="English",
                                   signal_pack=_COALITION_BLOCK)
    assert out["binary_forecasts"][0]["source"] == "research-prior"       # 降级
    assert out["binary_forecasts"][0]["source_claimed"] == "world-state outcome shares"
    assert out["binary_quality"]["provenance_downgrades"] == 1
    assert "world_state_outcome" not in out["binary_quality"]             # 块缺席 → 不挂
    forecast = {
        "scenarios": [{"name": "A", "probability": 0.6}, {"name": "B", "probability": 0.4}],
        "binary_forecasts": out["binary_forecasts"],
        "binary_quality": out["binary_quality"],
    }
    reconcile_forecast_contract(forecast)
    assert "sim_adjustment" not in forecast


# ------------------------------------------------------------------ (d) 节奏分层非退化
def _gen():
    return SimulationConfigGenerator.__new__(SimulationConfigGenerator)


def _agents(specs, entity_type="Organization"):
    return [
        AgentActivityConfig(agent_id=i, entity_uuid=f"u{i}", entity_name=n,
                            entity_type=entity_type, influence_weight=w)
        for (i, n, w) in specs
    ]


def test_cadence_tiers_non_flat_when_whole_cast_would_be_principal():
    """取证复现：12 名非受众 actor 全部 ≥0.6 → 旧实现全判 principal（每轮名册恒等）。
    收紧后应产生 principal + sampled 两层，principal 取头部影响力（可分辨的逐轮差异）。"""
    g = _gen()
    # 12 个降序、互异且均 ≥0.6 的影响力。
    specs = [(i, f"A{i}", round(1.0 - i * 0.03, 3)) for i in range(12)]
    agents = _agents(specs)
    n = g._assign_cadence_tiers(agents)
    cadences = {c.cadence for c in agents}
    assert cadences == {"principal", "sampled"}                # 非退化：两层并存
    expected_principals = max(g.PRINCIPAL_CADENCE_GRADE_MIN,
                              math.ceil(12 * g.PRINCIPAL_CADENCE_FRACTION))
    assert n == expected_principals == 6
    principal_ids = {c.agent_id for c in agents if c.cadence == "principal"}
    assert principal_ids == set(range(6))                      # 头部 6 名（影响力最高）
    assert all(c.cadence == "sampled" for c in agents if c.agent_id >= 6)


def test_cadence_tiers_preserve_top20_when_sampled_layer_exists():
    """回归钉：合格阵容大到已自带 sampled 层（23 合格取 20、余 3 采样）时，不收紧——
    保持旧「前 20」语义，避免误伤本就非退化的大阵容。"""
    g = _gen()
    agents = _agents([(i, f"A{i}", 3.0) for i in range(19)]
                     + [(19, "B19", 0.7), (20, "B20", 0.7), (21, "B21", 0.7), (22, "B22", 0.7)]
                     + [(23, "C23", 0.5)])                     # 24 名非受众，1 名 <0.6
    n = g._assign_cadence_tiers(agents)
    assert n == 20                                             # 满额，未被收紧
    by_id = {c.agent_id: c.cadence for c in agents}
    assert all(by_id[i] == "principal" for i in range(20))
    assert by_id[20] == by_id[21] == by_id[22] == by_id[23] == "sampled"


def test_cadence_tiers_small_cast_stays_all_principal():
    """小阵容（≤ GRADE_MIN）不收紧：采样本就无法区分，保持全 principal（旧行为）。"""
    g = _gen()
    agents = _agents([(0, "A", 0.9), (1, "B", 0.8), (2, "C", 0.7)])   # 3 名，全 ≥0.6
    n = g._assign_cadence_tiers(agents)
    assert n == 3 and all(c.cadence == "principal" for c in agents)
