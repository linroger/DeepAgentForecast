"""VIZ-1: deterministic report visualizer — Mermaid renderers, matplotlib PNG family,
manifest correctness, malformed-input safety, and matplotlib-missing fallback.

以真实工件的最小内联样本驱动（形状取自 report_90f1f75991f2/forecast.json 与
pipe_aa0fb94abe92/handoff 的 timeline.json / actors.json / world_state_trajectory）。
全部离线、无 LLM、无网络。"""

import json
import os

import pytest

from app.services import report_visualizer as rv
from app.services.report_visualizer import ReportVisualizer


@pytest.fixture(autouse=True)
def _no_png_export(monkeypatch):
    """WAVE9：本文件不测 kaleido PNG 导出（每次导出要起一次 Chromium，全套会慢数分钟）。
    统一关掉，让 build_all 走「HTML + matplotlib 回退 PNG」路径；kaleido 专项测试见
    test_wave9_visualizer.py。"""
    monkeypatch.setattr(ReportVisualizer, "_png_export_ok", lambda self: False)


# ─────────────────────────────────────────────────────────────────────────────
# 最小真实工件样本（键名与真实落盘一致）
# ─────────────────────────────────────────────────────────────────────────────
TIMELINE = [
    {"date": "2025-01-20", "event": "Trump inaugurated for second non-consecutive term"},
    {"date": "2026-02-28", "event": "US/Israel military strikes Iran — start of 2026 Iran war"},
    {"date": "2026-06-17", "event": "Trump signs Iran ceasefire; 60-day interim begins"},
]

ACTORS = {
    "relationships": [
        {"source": "Donald Trump", "target": "Hakeem Jeffries", "type": "OPPOSES",
         "sign": "rival", "strength": "high", "polarity": -0.95},
        {"source": "DCCC", "target": "House Democrats", "type": "SUPPORTS",
         "valence": "supportive", "strength": "high", "polarity": 0.8},
    ],
    "actors": [{"name": "Donald Trump"}, {"name": "Hakeem Jeffries"}],
}

FORECAST = {
    "headline": "Democrats net 1-3 Senate seats",
    "scenarios": [
        {"name": "Modest Democratic wave", "probability": 0.4609, "p_low": 0.40, "p_high": 0.52},
        {"name": "Clean Democratic wave", "probability": 0.2412},
        {"name": "Status quo hold", "probability": 0.184},
        {"name": "Republican overperformance", "probability": 0.114},
    ],
    "binary_forecasts": [
        {"id": "F1", "statement": "Dems win House", "probability": 0.82},  # no anchor
        {"id": "F5", "statement": "Dems win OH Senate", "probability": 0.45,
         "market_anchor": {"market_id": "SENATEOHS-26-D", "implied_yes_prob": 0.52,
                           "divergence": -0.07}},
    ],
}

WORLD_STATE = {
    "trajectory": [
        {"round": 0, "shares": {"Modest wave": 0.5, "Clean wave": 0.25, "Hold": 0.25}},
        {"round": 1, "shares": {"Modest wave": 0.55, "Clean wave": 0.28, "Hold": 0.17}},
        {"round": 2, "shares": {"Modest wave": 0.6, "Clean wave": 0.3, "Hold": 0.1}},
    ],
    "converged_at": 2,
}

COMPARISON = {
    "dimensions": [
        {"name": "总动作量", "baseline": 100, "scenario": 120, "delta": "+20", "verdict": "更高"},
        {"name": "执行轮数", "baseline": 5, "scenario": 6, "delta": "+1", "verdict": "更长"},
        {"name": "峰值轮次", "baseline": "round 3 (12)", "scenario": "round 2 (18)",
         "delta": "-1 轮", "verdict": "更早"},  # 数值可从字符串抽取
    ],
}

CALIBRATION = {
    "bins": [
        {"range": [0.0, 0.2], "mean_predicted": 0.12, "observed": 0.10, "count": 5},
        {"range": [0.4, 0.6], "mean_predicted": 0.55, "observed": 0.58, "count": 8},
        {"range": [0.8, 1.0], "mean_predicted": 0.90, "observed": 0.85, "count": 4},
    ],
}


# ============================ (A) Mermaid renderers ============================

def test_mermaid_timeline_shape():
    out = ReportVisualizer.render_mermaid_timeline(TIMELINE)
    assert out.startswith("```mermaid")
    assert "timeline" in out
    lines = out.splitlines()
    assert lines[1] == "timeline"
    # 每个事件一行 "<date> : <event>"，按日期升序
    assert "2025-01-20 : Trump inaugurated" in out
    assert "2026-06-17 : Trump signs Iran ceasefire; 60-day interim begins" in out
    # 升序：inaugurated 行必须在 ceasefire 行之前
    assert out.index("2025-01-20") < out.index("2026-06-17")
    assert out.rstrip().endswith("```")


def test_mermaid_timeline_dict_wrapper_and_zh_keys():
    wrapped = {"timeline": [{"日期": "2026-01-01", "事件": "中文事件"}]}
    out = ReportVisualizer.render_mermaid_timeline(wrapped)
    assert "2026-01-01 : 中文事件" in out


def test_mermaid_causal_string_form():
    out = ReportVisualizer.render_mermaid_causal(
        ["Trump approval --[CAUSES,-,strong]--> GOP seats"])
    assert out.startswith("```mermaid")
    assert "flowchart LR" in out
    # 签名边标签：CAUSES −/strong
    assert 'CAUSES −/strong' in out
    assert 'Trump approval' in out and 'GOP seats' in out


def test_mermaid_causal_structured_and_multihop():
    out = ReportVisualizer.render_mermaid_causal([
        {"source": "A", "target": "B", "type": "ENABLES", "sign": "+", "strength": "moderate"},
        {"path": ["X", "Y", "Z"], "relation": "CAUSES", "sign": "-"},
    ])
    assert "ENABLES +/moderate" in out
    # 多跳 path 拆成两条边 X->Y, Y->Z
    assert out.count("CAUSES −") == 2


def test_mermaid_actor_network_shape():
    out = ReportVisualizer.render_mermaid_actor_network(ACTORS)
    assert out.startswith("```mermaid")
    assert "graph TD" in out
    assert "Donald Trump" in out and "Hakeem Jeffries" in out
    assert "OPPOSES −" in out       # rival → −
    assert "SUPPORTS +" in out      # supportive → +


def test_mermaid_coalition_subgraphs():
    out = ReportVisualizer.render_mermaid_coalition(
        {"clusters": [{"label": "Dem bloc", "members": ["Jeffries", "Schumer"]},
                      ["Trump", "Vance"]]})
    assert "flowchart TB" in out
    assert 'subgraph c1["Dem bloc (2)"]' in out
    assert 'subgraph c2["Faction 2 (2)"]' in out
    assert out.count("subgraph") == 2


def test_mermaid_deterministic():
    # 相同输入 → 逐字节相同输出
    a = ReportVisualizer.render_mermaid_actor_network(ACTORS)
    b = ReportVisualizer.render_mermaid_actor_network(ACTORS)
    assert a == b


def test_mermaid_label_sanitization():
    # 引号/竖线/换行等破坏语法的字符被规整
    dirty = [{"date": "2026-01-01", "event": 'He said "yes" | no\nmaybe'}]
    out = ReportVisualizer.render_mermaid_timeline(dirty)
    assert '"yes"' not in out.split("title")[-1]  # 内部双引号被替换成单引号
    assert "|" not in out.split("timeline", 1)[1]
    assert "\n" in out  # 结构换行还在
    # 事件文本内无裸换行破坏 period : event 结构
    ev_line = [l for l in out.splitlines() if "2026-01-01" in l][0]
    assert "maybe" in ev_line


# ============================ malformed-input safety ============================

@pytest.mark.parametrize("renderer", [
    ReportVisualizer.render_mermaid_timeline,
    ReportVisualizer.render_mermaid_causal,
    ReportVisualizer.render_mermaid_coalition,
    ReportVisualizer.render_mermaid_actor_network,
])
@pytest.mark.parametrize("bad", [None, "", [], {}, 42, "not a dict",
                                 [None, 1, "x"], {"weird": True}, [{"no": "keys"}]])
def test_mermaid_malformed_returns_empty(renderer, bad):
    # 任何畸形输入 → ''，绝不抛异常
    assert renderer(bad) == ""


def test_causal_unparseable_string_skipped():
    assert ReportVisualizer.render_mermaid_causal(["no arrow here"]) == ""
    # 一好一坏 → 只保留好的
    out = ReportVisualizer.render_mermaid_causal(
        ["garbage", "A --[CAUSES]--> B"])
    assert "A" in out and "B" in out and "CAUSES" in out


# ============================ (B) matplotlib PNG family ============================

@pytest.mark.skipif(not rv.MATPLOTLIB_AVAILABLE, reason="matplotlib not installed")
def test_png_builders_write_files(tmp_path):
    charts = str(tmp_path / "charts")
    viz = ReportVisualizer()
    p1 = viz.build_scenario_bars(FORECAST, charts)
    p2 = viz.build_model_vs_market(FORECAST, charts)
    p3 = viz.build_worldstate_area(WORLD_STATE, charts)
    p4 = viz.build_comparison_bars(COMPARISON, charts)
    p5 = viz.build_calibration_curve(CALIBRATION, charts)
    for rel in (p1, p2, p3, p4, p5):
        assert rel is not None
        assert rel.startswith("charts/") and rel.endswith(".png")
        assert os.path.exists(str(tmp_path / rel))
        # PNG 魔数校验
        with open(str(tmp_path / rel), "rb") as f:
            assert f.read(8) == b"\x89PNG\r\n\x1a\n"


@pytest.mark.skipif(not rv.MATPLOTLIB_AVAILABLE, reason="matplotlib not installed")
def test_png_builders_none_on_empty(tmp_path):
    charts = str(tmp_path / "charts")
    viz = ReportVisualizer()
    assert viz.build_scenario_bars({"scenarios": []}, charts) is None
    assert viz.build_model_vs_market({"binary_forecasts": []}, charts) is None
    # 全部无 market_anchor → None
    assert viz.build_model_vs_market(
        {"binary_forecasts": [{"id": "F1", "probability": 0.5}]}, charts) is None
    # 单个时间点 → 面积图无意义 → None
    assert viz.build_worldstate_area(
        {"trajectory": [{"round": 0, "shares": {"A": 1.0}}]}, charts) is None
    assert viz.build_comparison_bars({"dimensions": []}, charts) is None
    assert viz.build_calibration_curve({"bins": []}, charts) is None


# ============================ build_all + manifest ============================

def _full_artifacts():
    return {
        "forecast": FORECAST,
        "timeline": TIMELINE,
        "actors": ACTORS,
        "causal_paths": ["Trump approval --[CAUSES,-,strong]--> GOP seats"],
        "coalition": {"clusters": [{"label": "Dem bloc", "members": ["Jeffries", "Schumer"]}]},
        "world_state_trajectory": WORLD_STATE,
        "comparison": COMPARISON,
        "calibration": CALIBRATION,
    }


def test_build_all_manifest_and_persist(tmp_path):
    viz = ReportVisualizer()
    manifest = viz.build_all("report_x", str(tmp_path), _full_artifacts())
    assert isinstance(manifest, list) and manifest
    # WAVE9 每项字段齐全（含 id/title；png_path 可选）
    for e in manifest:
        assert {"id", "path", "type", "title", "caption", "source",
                "placement_hint"} <= set(e.keys())
        assert e["type"] in ("png", "html")
        # 相对路径落在 charts/ 且文件真实存在
        assert e["path"].startswith("charts/")
        assert os.path.exists(str(tmp_path / e["path"]))
        if e.get("png_path"):
            assert os.path.exists(str(tmp_path / e["png_path"]))
    # WAVE9：build_all 不再产出 mermaid（plotly 泳道/网络图取代）
    assert not any(e["type"] == "mermaid" for e in manifest)
    # manifest 落盘为 schema v2 dict 且 items 可回读
    mpath = tmp_path / "viz_manifest.json"
    assert mpath.exists()
    reloaded = json.loads(mpath.read_text(encoding="utf-8"))
    assert reloaded["schema_version"] == 2
    assert reloaded["items"] == manifest
    assert isinstance(reloaded["skipped"], list)
    for s in reloaded["skipped"]:
        assert set(s.keys()) == {"builder", "reason"}


@pytest.mark.skipif(not rv.MATPLOTLIB_AVAILABLE, reason="matplotlib not installed")
def test_build_all_includes_png_when_matplotlib(tmp_path):
    # PNG 导出关闭（autouse fixture）→ matplotlib 回退族补 PNG：核心图挂 png_path，
    # comparison/calibration 作为独立 png 项。
    viz = ReportVisualizer()
    manifest = viz.build_all("report_png", str(tmp_path), _full_artifacts())
    by_id = {e["id"]: e for e in manifest}
    if rv.PLOTLY_AVAILABLE:
        assert by_id["scenario_probabilities"]["type"] == "html"
        assert by_id["scenario_probabilities"]["png_path"].endswith(".png")
        assert os.path.exists(str(tmp_path / by_id["scenario_probabilities"]["png_path"]))
    pngs = [e for e in manifest if e["type"] == "png"]
    hints = {e["placement_hint"] for e in pngs}
    assert {"comparison", "calibration"} <= hints


def test_build_all_no_renderers_reports_skips(tmp_path, monkeypatch):
    # 模拟两个可选绘图依赖（matplotlib + plotly）都缺失：无图可产，但 skipped 必须完整记录
    # （WAVE9：不再有沉默跳过；旧 mermaid 回退已随 mermaid 族退出 build_all）
    monkeypatch.setattr(rv, "MATPLOTLIB_AVAILABLE", False)
    monkeypatch.setattr(rv, "PLOTLY_AVAILABLE", False)
    viz = ReportVisualizer()
    manifest = viz.build_all("report_nomat", str(tmp_path), _full_artifacts())
    assert manifest == []
    reloaded = json.loads((tmp_path / "viz_manifest.json").read_text(encoding="utf-8"))
    assert reloaded["items"] == []
    reasons = {s["builder"]: s["reason"] for s in reloaded["skipped"]}
    assert reasons["scenario_probabilities"] == "plotly_unavailable_or_disabled"
    assert reasons["matplotlib_family"] == "matplotlib_unavailable_or_disabled"


def test_build_all_master_switch_off(tmp_path, monkeypatch):
    # REPORT_VISUALIZER=False → 返回 [] 且不落盘
    monkeypatch.setattr(rv, "_cfg",
                        lambda name, default: False if name == "REPORT_VISUALIZER" else default)
    viz = ReportVisualizer()
    manifest = viz.build_all("report_off", str(tmp_path), _full_artifacts())
    assert manifest == []
    assert not (tmp_path / "viz_manifest.json").exists()


def test_build_all_empty_artifacts(tmp_path):
    # 无任何工件 → 空 items，仍安全落盘清单（skipped 记录全部 no_input）
    viz = ReportVisualizer()
    manifest = viz.build_all("report_empty", str(tmp_path), {})
    assert manifest == []
    assert (tmp_path / "viz_manifest.json").exists()
    reloaded = json.loads((tmp_path / "viz_manifest.json").read_text(encoding="utf-8"))
    assert reloaded["items"] == []
    if rv.PLOTLY_AVAILABLE:
        assert any(s["reason"] == "no_input" for s in reloaded["skipped"])


def test_build_all_none_artifacts_safe(tmp_path):
    viz = ReportVisualizer()
    assert viz.build_all("report_none", str(tmp_path), None) == []


def test_max_nodes_cap(monkeypatch):
    # 节点上限：超出即截断（确定性保留首现节点）
    monkeypatch.setattr(rv, "_cfg",
                        lambda name, default: 2 if name == "REPORT_VIZ_MAX_NODES" else default)
    rels = [{"source": f"S{i}", "target": f"T{i}", "type": "OPPOSES"} for i in range(10)]
    out = ReportVisualizer.render_mermaid_actor_network({"relationships": rels})
    # 首条边即用满 2 个节点，后续新节点被拒 → 只剩 1 条边
    assert out.count("-->") == 1


def test_comparison_numeric_extraction(tmp_path):
    # comparison 维度里字符串型 baseline/scenario（'round 3 (12)'）应能抽出数字
    if not rv.MATPLOTLIB_AVAILABLE:
        pytest.skip("matplotlib not installed")
    viz = ReportVisualizer()
    charts = str(tmp_path / "charts")
    # 只含字符串数值的维度也可解析
    comp = {"dimensions": [{"name": "峰值", "baseline": "round 3 (12)", "scenario": "round 2 (18)"}]}
    rel = viz.build_comparison_bars(comp, charts)
    assert rel is not None and os.path.exists(str(tmp_path / rel))


# ============================ (B2) PM-6 market price-history ============================
# 合成价格历史：{market_id: [{t,p}]}（t=unix 秒，p∈[0,1]）。默认 market_id 命中 FORECAST 的 F5 锚点。

def _synthetic_price_history(market_id="SENATEOHS-26-D", n=6, base=0.45):
    t0 = 1_750_000_000  # 2025-ish unix 秒
    return {market_id: [{"t": t0 + i * 86400, "p": round(base + 0.012 * i, 4)} for i in range(n)]}


def _assert_png(path):
    assert os.path.exists(path)
    with open(path, "rb") as f:
        assert f.read(8) == b"\x89PNG\r\n\x1a\n"


@pytest.mark.skipif(not rv.MATPLOTLIB_AVAILABLE, reason="matplotlib not installed")
def test_price_history_writes_figure(tmp_path):
    viz = ReportVisualizer()
    charts = str(tmp_path / "charts")
    ph = _synthetic_price_history()
    # FORECAST: F1 无 market_anchor、F5 有 SENATEOHS-26-D → 恰 1 个命中锚点 → 1 张图
    paths = viz.render_market_price_history(ph, FORECAST["binary_forecasts"], charts)
    assert len(paths) == 1
    rel = paths[0]
    assert rel.startswith("charts/market_price_history_") and rel.endswith(".png")
    _assert_png(str(tmp_path / rel))


@pytest.mark.skipif(not rv.MATPLOTLIB_AVAILABLE, reason="matplotlib not installed")
def test_price_history_grouping_two_per_figure(tmp_path):
    # >6 锚点 → 每图最多 2 子图 → ceil(7/2)=4 张图（分组降低图数量）
    viz = ReportVisualizer()
    charts = str(tmp_path / "charts")
    anchors = [{"id": f"F{i}", "probability": 0.5,
                "market_anchor": {"market_id": f"M{i}", "implied_yes_prob": 0.5,
                                  "divergence": 0.0}} for i in range(7)]
    ph = {f"M{i}": [{"t": 1_750_000_000 + k * 86400, "p": 0.4 + 0.02 * k}
                    for k in range(4)] for i in range(7)}
    paths = viz.render_market_price_history(ph, anchors, charts)
    assert len(paths) == 4
    for rel in paths:
        _assert_png(str(tmp_path / rel))


@pytest.mark.skipif(not rv.MATPLOTLIB_AVAILABLE, reason="matplotlib not installed")
def test_price_history_one_per_figure_when_leq6(tmp_path):
    # ≤6 锚点 → 一锚一图（不分组）
    viz = ReportVisualizer()
    charts = str(tmp_path / "charts")
    anchors = [{"id": f"F{i}", "probability": 0.5,
                "market_anchor": {"market_id": f"M{i}", "implied_yes_prob": 0.5}}
               for i in range(3)]
    ph = {f"M{i}": [{"t": 1_750_000_000 + k * 86400, "p": 0.5}
                    for k in range(3)] for i in range(3)}
    paths = viz.render_market_price_history(ph, anchors, charts)
    assert len(paths) == 3


@pytest.mark.skipif(not rv.MATPLOTLIB_AVAILABLE, reason="matplotlib not installed")
def test_price_history_missing_input_safety(tmp_path):
    viz = ReportVisualizer()
    charts = str(tmp_path / "charts")
    bf = FORECAST["binary_forecasts"]
    # 各种缺失/畸形 price_history → [] 且不抛
    assert viz.render_market_price_history(None, bf, charts) == []
    assert viz.render_market_price_history({}, bf, charts) == []
    assert viz.render_market_price_history("nope", bf, charts) == []
    # 缺失/畸形 anchors → []
    assert viz.render_market_price_history(_synthetic_price_history(), None, charts) == []
    assert viz.render_market_price_history(_synthetic_price_history(), [], charts) == []
    assert viz.render_market_price_history(_synthetic_price_history(), "x", charts) == []
    # market_id 不命中 price_history → []
    assert viz.render_market_price_history(
        {"OTHER": [{"t": 1, "p": 0.5}, {"t": 2, "p": 0.6}]}, bf, charts) == []
    # 命中但历史点 <2 → 该锚点跳过 → []
    assert viz.render_market_price_history(
        {"SENATEOHS-26-D": [{"t": 1, "p": 0.5}]}, bf, charts) == []


def test_price_history_matplotlib_missing_returns_empty(tmp_path, monkeypatch):
    # matplotlib 缺失 → 直接 []（不触碰绘图）
    monkeypatch.setattr(rv, "MATPLOTLIB_AVAILABLE", False)
    viz = ReportVisualizer()
    charts = str(tmp_path / "charts")
    assert viz.render_market_price_history(
        _synthetic_price_history(), FORECAST["binary_forecasts"], charts) == []


@pytest.mark.skipif(not rv.MATPLOTLIB_AVAILABLE, reason="matplotlib not installed")
def test_build_all_price_history_from_artifact(tmp_path):
    # market_price_history 作为内联 artifact → build_all 生成图并登记 manifest（placement_hint=binary_forecasts）
    viz = ReportVisualizer()
    arts = dict(_full_artifacts())
    arts["market_price_history"] = _synthetic_price_history()
    manifest = viz.build_all("report_ph", str(tmp_path), arts)
    ph_items = [e for e in manifest if e["source"] == "market_price_history"]
    assert len(ph_items) == 1
    e = ph_items[0]
    assert e["placement_hint"] == "binary_forecasts"
    assert e["path"].startswith("charts/market_price_history_")
    # plotly 可用 → HTML 主图 + matplotlib 回退 PNG 挂 png_path；否则纯 PNG 项
    if rv.PLOTLY_AVAILABLE:
        assert e["type"] == "html"
        assert e["png_path"].startswith("charts/market_price_history_")
        _assert_png(str(tmp_path / e["png_path"]))
    else:
        assert e["type"] == "png"
        _assert_png(str(tmp_path / e["path"]))
    # manifest 落盘可回读
    reloaded = json.loads((tmp_path / "viz_manifest.json").read_text(encoding="utf-8"))
    assert reloaded["items"] == manifest


@pytest.mark.skipif(not rv.MATPLOTLIB_AVAILABLE, reason="matplotlib not installed")
def test_build_all_price_history_from_report_dir_file(tmp_path):
    # 无内联 artifact，但 reports/{id}/market_price_history.json 存在 → build_all 自动读取
    (tmp_path / "market_price_history.json").write_text(
        json.dumps(_synthetic_price_history()), encoding="utf-8")
    viz = ReportVisualizer()
    manifest = viz.build_all("report_phfile", str(tmp_path), _full_artifacts())
    assert any(e["source"] == "market_price_history" for e in manifest)


@pytest.mark.skipif(not rv.MATPLOTLIB_AVAILABLE, reason="matplotlib not installed")
def test_build_all_price_history_from_handoff_dir_file(tmp_path):
    # handoff_dir 指向的 handoff/market_price_history.json 也被采纳
    hd = tmp_path / "handoff"
    hd.mkdir()
    (hd / "market_price_history.json").write_text(
        json.dumps(_synthetic_price_history()), encoding="utf-8")
    viz = ReportVisualizer()
    arts = dict(_full_artifacts())
    arts["handoff_dir"] = str(hd)
    manifest = viz.build_all("report_phhd", str(tmp_path), arts)
    assert any(e["source"] == "market_price_history" for e in manifest)


def test_build_all_price_history_absent_skipped(tmp_path):
    # 无任何 price_history 来源 → 无 market_price_history 项（静默跳过，不影响其余图）
    viz = ReportVisualizer()
    manifest = viz.build_all("report_noph", str(tmp_path), _full_artifacts())
    assert not any(e.get("source") == "market_price_history" for e in manifest)


@pytest.mark.skipif(not rv.MATPLOTLIB_AVAILABLE, reason="matplotlib not installed")
def test_build_all_price_history_knob_off(tmp_path, monkeypatch):
    # REPORT_VIZ_PRICE_HISTORY=False → 整族跳过（其余图仍生成）
    monkeypatch.setattr(
        rv, "_cfg",
        lambda name, default: False if name == "REPORT_VIZ_PRICE_HISTORY" else default)
    viz = ReportVisualizer()
    arts = dict(_full_artifacts())
    arts["market_price_history"] = _synthetic_price_history()
    manifest = viz.build_all("report_phoff", str(tmp_path), arts)
    assert not any(e.get("source") == "market_price_history" for e in manifest)
    # 其余 PNG（如 scenario）仍在 → 证明只是该族被关
    assert any(e["type"] == "png" for e in manifest)


def test_price_history_deterministic_and_dedup(tmp_path):
    # 相同锚点 market_id 去重（保留首现）；同输入两次规整完全一致
    if not rv.MATPLOTLIB_AVAILABLE:
        pytest.skip("matplotlib not installed")
    ph = _synthetic_price_history()
    anchors = [
        {"id": "F5a", "probability": 0.45,
         "market_anchor": {"market_id": "SENATEOHS-26-D", "implied_yes_prob": 0.52}},
        {"id": "F5b", "probability": 0.30,  # 同 market_id → 去重丢弃
         "market_anchor": {"market_id": "SENATEOHS-26-D", "implied_yes_prob": 0.40}},
    ]
    a = rv._normalize_price_anchors(anchors, ph)
    b = rv._normalize_price_anchors(anchors, ph)
    assert len(a) == 1 and a[0]["label"] == "F5a"
    assert [x["market_id"] for x in a] == [x["market_id"] for x in b]


# ============================ (C) ITEM-16 plotly 交互式 HTML 族 ============================

def _assert_offline_html(path):
    """自包含 HTML 断言：文件存在、含 <html>、且整份 plotly.js 已内联（离线可开）。

    离线判据用「文件体量」而非扫描 URL 子串：include_plotlyjs='inline' 会把 ~3.5MB 的 plotly.js
    整体嵌入文档，故 HTML 必然 >1MB；若是 CDN/directory 模式则只会外链一个 <script src=...> 且
    文档仅几 KB。（plotly.js 捆绑源码内含 mapbox/topojson 等 URL 字符串常量——仅地理图 trace 才
    用到，我们只画 bar/scatter/stackplot，运行期零网络——故不能据 URL 子串判定「外链」。）"""
    assert os.path.exists(path)
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    assert "<html" in html.lower()
    assert "Plotly" in html            # plotly 运行时存在
    # >1MB ⟺ 整份 plotly.js 已内联（CDN 模式的 HTML 仅数 KB）→ 真正离线自包含
    assert len(html) > 1_000_000


@pytest.mark.skipif(not rv.PLOTLY_AVAILABLE, reason="plotly not installed")
def test_html_builders_write_self_contained_files(tmp_path):
    charts = str(tmp_path / "charts")
    viz = ReportVisualizer()
    h1 = viz.build_scenario_bars_html(FORECAST, charts)
    h2 = viz.build_model_vs_market_html(FORECAST, charts)
    h3 = viz.build_worldstate_area_html(WORLD_STATE, charts)
    for rel in (h1, h2, h3):
        assert rel is not None
        assert rel.startswith("charts/") and rel.endswith(".html")
        _assert_offline_html(str(tmp_path / rel))


@pytest.mark.skipif(not rv.PLOTLY_AVAILABLE, reason="plotly not installed")
def test_html_builders_none_on_empty(tmp_path):
    charts = str(tmp_path / "charts")
    viz = ReportVisualizer()
    assert viz.build_scenario_bars_html({"scenarios": []}, charts) is None
    assert viz.build_model_vs_market_html({"binary_forecasts": []}, charts) is None
    # 无 market_anchor → None
    assert viz.build_model_vs_market_html(
        {"binary_forecasts": [{"id": "F1", "probability": 0.5}]}, charts) is None
    # 单个时间点 → 面积图无意义 → None
    assert viz.build_worldstate_area_html(
        {"trajectory": [{"round": 0, "shares": {"A": 1.0}}]}, charts) is None


@pytest.mark.skipif(not rv.PLOTLY_AVAILABLE, reason="plotly not installed")
def test_html_price_history_writes_files(tmp_path):
    viz = ReportVisualizer()
    charts = str(tmp_path / "charts")
    ph = _synthetic_price_history()
    # 恰 1 个命中锚点（F5 → SENATEOHS-26-D）→ 1 张交互式 HTML
    paths = viz.render_market_price_history_html(ph, FORECAST["binary_forecasts"], charts)
    assert len(paths) == 1
    rel = paths[0]
    assert rel.startswith("charts/market_price_history_") and rel.endswith(".html")
    _assert_offline_html(str(tmp_path / rel))


@pytest.mark.skipif(not rv.PLOTLY_AVAILABLE, reason="plotly not installed")
def test_html_price_history_missing_input_safety(tmp_path):
    viz = ReportVisualizer()
    charts = str(tmp_path / "charts")
    bf = FORECAST["binary_forecasts"]
    assert viz.render_market_price_history_html(None, bf, charts) == []
    assert viz.render_market_price_history_html({}, bf, charts) == []
    assert viz.render_market_price_history_html(_synthetic_price_history(), None, charts) == []
    # market_id 不命中 → []
    assert viz.render_market_price_history_html(
        {"OTHER": [{"t": 1, "p": 0.5}, {"t": 2, "p": 0.6}]}, bf, charts) == []


def test_html_builders_plotly_missing_returns_none(tmp_path, monkeypatch):
    # plotly 缺失 → 全部构建器返回 None/[]（不触碰 plotly）
    monkeypatch.setattr(rv, "PLOTLY_AVAILABLE", False)
    viz = ReportVisualizer()
    charts = str(tmp_path / "charts")
    assert viz.build_scenario_bars_html(FORECAST, charts) is None
    assert viz.build_model_vs_market_html(FORECAST, charts) is None
    assert viz.build_worldstate_area_html(WORLD_STATE, charts) is None
    assert viz.render_market_price_history_html(
        _synthetic_price_history(), FORECAST["binary_forecasts"], charts) == []


@pytest.mark.skipif(not rv.PLOTLY_AVAILABLE, reason="plotly not installed")
def test_build_all_includes_html_when_plotly(tmp_path):
    viz = ReportVisualizer()
    arts = dict(_full_artifacts())
    arts["market_price_history"] = _synthetic_price_history()
    manifest = viz.build_all("report_html", str(tmp_path), arts)
    htmls = [e for e in manifest if e["type"] == "html"]
    # WAVE9：至少 scenario + binary dotplot + model-vs-market + timeline + actor network +
    # actor bubble + worldstate + 1 市场价格历史
    assert len(htmls) >= 6
    hints = {e["placement_hint"] for e in htmls}
    assert {"scenarios", "binary_forecasts", "simulation", "timeline", "actors"} <= hints
    for e in htmls:
        assert e["path"].startswith("charts/") and e["path"].endswith(".html")
        _assert_offline_html(str(tmp_path / e["path"]))


@pytest.mark.skipif(not rv.PLOTLY_AVAILABLE, reason="plotly not installed")
def test_build_all_interactive_knob_off(tmp_path, monkeypatch):
    # REPORT_VIZ_INTERACTIVE=False → 无 html 项（PNG/Mermaid 仍生成）
    monkeypatch.setattr(
        rv, "_cfg",
        lambda name, default: False if name == "REPORT_VIZ_INTERACTIVE" else default)
    viz = ReportVisualizer()
    manifest = viz.build_all("report_nohtml", str(tmp_path), _full_artifacts())
    assert not any(e["type"] == "html" for e in manifest)
    assert manifest  # 其余族仍在


def test_build_all_plotly_missing_no_html(tmp_path, monkeypatch):
    # plotly 缺失 → build_all 不含任何 html 项（degrade-safe），其余族不受影响
    monkeypatch.setattr(rv, "PLOTLY_AVAILABLE", False)
    viz = ReportVisualizer()
    manifest = viz.build_all("report_noplotly", str(tmp_path), _full_artifacts())
    assert not any(e["type"] == "html" for e in manifest)
    assert manifest
