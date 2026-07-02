"""DRF-2 scaffold offline tests: skills / config / extensions / market tool.

验证 drf2/（REDESIGN.md 的新系统脚手架）中「技能 + 配置」子树的正确性：

1. drf2/skills/**/SKILL.md 用 **真实的** deer-flow 2.0 解析器/校验器
   （sys.modules 手术式注入，绕过 deerflow.skills.__init__ 对 langchain 的重依赖；
   deer-flow-2.0.0 源码树缺失或依赖不可用时干净地 skip）。
2. drf2/config/config.yaml：YAML 合法、必备块齐全、$ENV 占位符、子代理与技能/
   工具白名单互相一致（漂移守卫）。
3. drf2/config/extensions_config.json：两台引擎的 stdio 注册 + 7 技能启用。
4. drf2/config/market_tools.py：Oddpool 快照纯逻辑（过滤/去重/排序/降级），
   全程 mock fetch，离线可跑。
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import re
import sys
import types as pytypes
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DRF2 = REPO_ROOT / "drf2"
SKILLS_ROOT = DRF2 / "skills"
CONFIG_YAML = DRF2 / "config" / "config.yaml"
EXTENSIONS_JSON = DRF2 / "config" / "extensions_config.json"
HARNESS_ROOT = REPO_ROOT / "deer-flow-2.0.0" / "backend" / "packages" / "harness" / "deerflow"

EXPECTED_SKILLS = {
    "deep-research",
    "actor-ontology-research",
    "ontology-generation",
    "kg-construction",
    "simulation-design",
    "forecast-report",
    "prediction-markets",
}

# 引擎 MCP 工具名（与 drf2/engines/*/server.py 实际注册的名字一致；技能 allowed-tools /
# 子代理 tools 白名单都据此校验。TestEngineToolNameDrift 再把这两个集合与引擎源码
# 提取出的真名对表 —— 引擎改名会先让测试红，再同步技能与配置）。
KG_MCP_TOOLS = {
    "kg_add_episode", "kg_search", "kg_get_entities", "kg_get_edges",
    "kg_causal_paths", "kg_n_hop_subgraph", "kg_trace_cascade", "kg_centrality_priors",
}
SIM_MCP_TOOLS = {"sim_start", "sim_status", "sim_results", "sim_stop", "sim_interview_agents"}
HARNESS_BUILTIN_TOOLS = {"tool_search"}  # tool_search.enabled=true 时由 harness 注入


def _skill_dirs() -> list[Path]:
    if not SKILLS_ROOT.is_dir():
        return []
    return sorted(p.parent for p in SKILLS_ROOT.glob("**/SKILL.md"))


# ---------------------------------------------------------------------------
# 真实 deer-flow 2.0 模块的手术式导入。
# deerflow.skills.__init__ / deerflow.models 会连锁 import langchain（backend venv
# 没有），因此预注册带 __path__ 的空壳包对象、只加载需要的叶子模块，模块级测试
# 结束后清掉全部 deerflow* 注入，绝不污染其余 584 项测试。
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def deerflow_shell():
    if not (HARNESS_ROOT / "skills" / "parser.py").is_file():
        pytest.skip("deer-flow-2.0.0 vendored source not present — cannot import the real modules")
    if "deerflow" in sys.modules:  # 环境里已有真 deerflow（不太可能）→ 直接用
        yield
        return
    for name, path in (("deerflow", HARNESS_ROOT),
                       ("deerflow.skills", HARNESS_ROOT / "skills"),
                       ("deerflow.config", HARNESS_ROOT / "config")):
        shell = pytypes.ModuleType(name)
        shell.__path__ = [str(path)]
        sys.modules[name] = shell
    try:
        yield
    finally:
        for name in [n for n in sys.modules if n == "deerflow" or n.startswith("deerflow.")]:
            sys.modules.pop(name, None)


@pytest.fixture(scope="module")
def deerflow_skill_modules(deerflow_shell):
    try:
        parser = importlib.import_module("deerflow.skills.parser")
        validation = importlib.import_module("deerflow.skills.validation")
        skill_types = importlib.import_module("deerflow.skills.types")
    except ImportError as exc:  # 依赖缺失 → skip 而非 fail
        pytest.skip(f"real deer-flow parser unimportable in this venv: {exc}")
    return parser, validation, skill_types


@pytest.fixture(scope="module")
def deerflow_config_modules(deerflow_shell):
    try:
        app_config = importlib.import_module("deerflow.config.app_config")
        extensions_config = importlib.import_module("deerflow.config.extensions_config")
    except ImportError as exc:  # pydantic/dotenv 等缺失 → skip 而非 fail
        pytest.skip(f"real deer-flow config schema unimportable in this venv: {exc}")
    return app_config, extensions_config


class TestSkillsParseWithRealParser:
    def test_seven_skills_present(self):
        names = {d.name for d in _skill_dirs()}
        assert names == EXPECTED_SKILLS, f"skill dirs mismatch: {names ^ EXPECTED_SKILLS}"

    def test_every_skill_passes_real_validator(self, deerflow_skill_modules):
        _, validation, _ = deerflow_skill_modules
        for skill_dir in _skill_dirs():
            ok, message, name = validation._validate_skill_frontmatter(skill_dir)
            assert ok, f"{skill_dir.name}: {message}"
            assert name == skill_dir.name, (
                f"frontmatter name {name!r} must equal directory name {skill_dir.name!r}")

    def test_every_skill_parses_with_real_parser(self, deerflow_skill_modules):
        parser, _, skill_types = deerflow_skill_modules
        for skill_dir in _skill_dirs():
            skill = parser.parse_skill_file(
                skill_dir / "SKILL.md", skill_types.SkillCategory.CUSTOM)
            assert skill is not None, f"{skill_dir.name}: parse_skill_file returned None"
            assert skill.name == skill_dir.name
            assert skill.description.strip()
            # allowed-tools 是本管线的强制约束：每个技能都要显式声明工具面
            assert skill.allowed_tools, f"{skill_dir.name}: allowed-tools missing/empty"

    def test_skills_live_under_custom_category(self):
        # harness loader 只扫 <skills.path>/{public,custom}/ —— 布局守卫
        for skill_dir in _skill_dirs():
            rel = skill_dir.relative_to(SKILLS_ROOT)
            assert rel.parts[0] == "custom", (
                f"{skill_dir.name} must live under drf2/skills/custom/ (got {rel})")

    def test_allowed_tools_reference_known_tools(self, deerflow_skill_modules):
        parser, _, skill_types = deerflow_skill_modules
        cfg = yaml.safe_load(CONFIG_YAML.read_text(encoding="utf-8"))
        config_tools = {t["name"] for t in cfg.get("tools", [])}
        known = config_tools | KG_MCP_TOOLS | SIM_MCP_TOOLS | HARNESS_BUILTIN_TOOLS
        for skill_dir in _skill_dirs():
            skill = parser.parse_skill_file(
                skill_dir / "SKILL.md", skill_types.SkillCategory.CUSTOM)
            unknown = set(skill.allowed_tools or []) - known
            assert not unknown, f"{skill_dir.name}: unknown allowed-tools {sorted(unknown)}"


class TestConfigYaml:
    @pytest.fixture(scope="class")
    def cfg(self):
        assert CONFIG_YAML.is_file(), "drf2/config/config.yaml missing"
        return yaml.safe_load(CONFIG_YAML.read_text(encoding="utf-8"))

    def test_required_blocks(self, cfg):
        for key in ("config_version", "models", "tools", "tool_groups",
                    "tool_search", "sandbox", "subagents", "skills", "database"):
            assert key in cfg, f"config.yaml missing required block {key!r}"
        assert isinstance(cfg["config_version"], int) and cfg["config_version"] >= 14

    def test_models_claude_and_minimax(self, cfg):
        by_name = {m["name"]: m for m in cfg["models"]}
        assert "claude-sonnet" in by_name and "minimax-m3" in by_name
        claude = by_name["claude-sonnet"]
        assert claude["use"] == "deerflow.models.claude_provider:ClaudeChatModel"
        mm = by_name["minimax-m3"]
        assert mm["use"] == "deerflow.models.patched_minimax:PatchedChatMiniMax"
        assert mm["model"] == "MiniMax-M3"
        # $ENV 占位符（绝不硬编码 key）
        assert mm["api_key"] == "$MINIMAX_API_KEY"
        assert "api_key" not in claude or str(claude.get("api_key", "")).startswith("$")
        # MiniMax 契约：temperature ∈ (0.0, 1.0]
        assert 0.0 < float(mm["temperature"]) <= 1.0
        # thinking 预算须小于 max_tokens（Anthropic API 硬约束）
        budget = claude["when_thinking_enabled"]["thinking"]["budget_tokens"]
        assert 1024 <= int(budget) < int(claude["max_tokens"])

    def test_no_hardcoded_secrets(self):
        text = CONFIG_YAML.read_text(encoding="utf-8")
        for marker in ("sk-", "xoxb-", "tp-", "AKIA"):
            for line in text.splitlines():
                if "api_key" in line.lower() and marker in line:
                    pytest.fail(f"possible hardcoded secret in config.yaml: {line.strip()}")

    def test_custom_agents_wiring(self, cfg):
        agents = cfg["subagents"]["custom_agents"]
        assert set(agents) == {"researcher", "ontology-builder", "sim-configurer", "forecaster"}
        # 每阶段模型路由 + 提升的超时（REDESIGN：research/forecast 2700，sim-config 900）
        assert agents["researcher"]["model"] == "claude-sonnet"
        assert agents["forecaster"]["model"] == "claude-sonnet"
        assert agents["ontology-builder"]["model"] == "minimax-m3"
        assert agents["sim-configurer"]["model"] == "minimax-m3"
        assert agents["researcher"]["timeout_seconds"] == 2700
        assert agents["forecaster"]["timeout_seconds"] == 2700
        assert agents["sim-configurer"]["timeout_seconds"] == 900
        model_names = {m["name"] for m in cfg["models"]}
        for name, agent in agents.items():
            assert agent["description"].strip() and agent["system_prompt"].strip()
            assert agent["model"] in model_names, f"{name}: unknown model {agent['model']}"

    def test_agent_skill_whitelists_reference_real_skills(self, cfg):
        skill_names = {d.name for d in _skill_dirs()}
        for name, agent in cfg["subagents"]["custom_agents"].items():
            skills = agent.get("skills")
            assert skills, f"{name}: skills whitelist must be non-empty"
            missing = set(skills) - skill_names
            assert not missing, f"{name}: whitelisted skills not on disk: {sorted(missing)}"

    def test_agent_tool_whitelists_reference_known_tools(self, cfg):
        config_tools = {t["name"] for t in cfg["tools"]}
        known = config_tools | KG_MCP_TOOLS | SIM_MCP_TOOLS | HARNESS_BUILTIN_TOOLS
        for name, agent in cfg["subagents"]["custom_agents"].items():
            unknown = set(agent.get("tools") or []) - known
            assert not unknown, f"{name}: unknown tools {sorted(unknown)}"

    def test_prediction_market_tool_registered(self, cfg):
        tools = {t["name"]: t for t in cfg["tools"]}
        assert "prediction_market_search" in tools
        assert tools["prediction_market_search"]["use"] == (
            "drf2.config.market_tools:prediction_market_search_tool")

    def test_skills_path_points_at_drf2_skills(self, cfg):
        path = Path(cfg["skills"]["path"])
        assert path == SKILLS_ROOT, f"skills.path {path} != {SKILLS_ROOT}"
        # loader 扫 {public,custom} —— custom 必须存在且非空
        assert (path / "custom").is_dir() and any((path / "custom").iterdir())

    def test_tool_search_enabled_for_deferred_mcp(self, cfg):
        assert cfg["tool_search"]["enabled"] is True


class TestRealSchemaValidation:
    """用 harness 自己的 pydantic 模型整体校验两份配置（最强的 schema 契约测试）。"""

    def test_config_yaml_loads_via_real_appconfig(self, deerflow_config_modules):
        app_config, _ = deerflow_config_modules
        cfg = app_config.AppConfig.from_file(str(CONFIG_YAML))
        assert [m.name for m in cfg.models] == ["claude-sonnet", "minimax-m3"]
        assert set(cfg.subagents.custom_agents) == {
            "researcher", "ontology-builder", "sim-configurer", "forecaster"}
        assert cfg.subagents.custom_agents["researcher"].timeout_seconds == 2700
        assert cfg.subagents.custom_agents["sim-configurer"].timeout_seconds == 900
        assert cfg.tool_search.enabled is True
        assert Path(cfg.skills.path) == SKILLS_ROOT

    def test_extensions_json_loads_via_real_schema(self, deerflow_config_modules):
        _, extensions_config = deerflow_config_modules
        ext = extensions_config.ExtensionsConfig.from_file(str(EXTENSIONS_JSON))
        enabled = ext.get_enabled_mcp_servers()
        assert set(enabled) == {"drf2-kg", "drf2-simulation"}
        for server in enabled.values():
            assert server.type == "stdio"
        for name in EXPECTED_SKILLS:
            assert ext.is_skill_enabled(name, "custom"), f"skill {name} not enabled"


class TestExtensionsConfig:
    @pytest.fixture(scope="class")
    def ext(self):
        assert EXTENSIONS_JSON.is_file(), "drf2/config/extensions_config.json missing"
        return json.loads(EXTENSIONS_JSON.read_text(encoding="utf-8"))

    def test_two_engine_servers_registered(self, ext):
        servers = ext["mcpServers"]
        assert set(servers) == {"drf2-kg", "drf2-simulation"}
        for name, server in servers.items():
            assert server["enabled"] is True
            assert server["type"] == "stdio"
            assert server["command"], f"{name}: empty command"
            assert server["args"][0] == "-m", f"{name}: expected python -m entrypoint"
            # $ENV 占位符注入（不落明文 key）
            for key, value in server.get("env", {}).items():
                if key.endswith("_KEY"):
                    assert str(value).startswith("$"), f"{name}: {key} must be $ENV placeholder"
        assert servers["drf2-kg"]["args"][1] == "drf2.engines.kg.server"
        assert servers["drf2-simulation"]["args"][1] == "drf2.engines.simulation.server"

    def test_engine_entry_modules_exist(self, ext):
        # extensions_config 引用的 python -m 入口必须真实存在于 drf2/engines/
        for server in ext["mcpServers"].values():
            module = server["args"][1]
            path = REPO_ROOT / (module.replace(".", "/") + ".py")
            assert path.is_file(), f"entry module {module} not found at {path}"

    def test_all_seven_skills_enabled(self, ext):
        skills = ext["skills"]
        assert set(skills) == EXPECTED_SKILLS
        assert all(entry.get("enabled") is True for entry in skills.values())


class TestEngineToolNameDrift:
    """把测试内的工具名集合与引擎源码里实际注册的名字对表（漂移守卫）。"""

    def test_kg_tool_names_match_engine_source(self):
        src = REPO_ROOT / "drf2" / "engines" / "kg" / "server.py"
        if not src.is_file():
            pytest.skip("KG engine source not present yet")
        # server.py 的工具注册表以裸字符串常量声明工具名（整串恰为 kg_xxx）
        extracted = set(re.findall(r'"(kg_[a-z_]+)"', src.read_text(encoding="utf-8")))
        assert extracted == KG_MCP_TOOLS, (
            f"KG engine tool names drifted: engine={sorted(extracted)} "
            f"tests/config/skills expect={sorted(KG_MCP_TOOLS)}")

    def test_sim_tool_names_match_engine_source(self):
        src = REPO_ROOT / "drf2" / "engines" / "simulation" / "server.py"
        if not src.is_file():
            pytest.skip("simulation engine source not present yet")
        extracted = set(re.findall(r"async def (sim_[a-z_]+)\(",
                                   src.read_text(encoding="utf-8")))
        assert extracted == SIM_MCP_TOOLS, (
            f"simulation engine tool names drifted: engine={sorted(extracted)} "
            f"tests/config/skills expect={sorted(SIM_MCP_TOOLS)}")


# ---------------------------------------------------------------------------
# market_tools 纯逻辑（无网络、无 langchain 依赖）
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def market_tools():
    spec = importlib.util.spec_from_file_location(
        "drf2_market_tools_under_test", DRF2 / "config" / "market_tools.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _raw_market(**over):
    base = {
        "market_id": "m1", "exchange": "Kalshi", "question": "Will X happen by 2027?",
        "status": "active", "last_yes_price": "0.2100",
        "volume": "1000", "liquidity": "50", "event_title": "X",
    }
    base.update(over)
    return base


class TestMarketToolsLogic:
    def test_normalize_good_market(self, market_tools):
        norm = market_tools.normalize_market(_raw_market(), matched_query="x")
        assert norm == {
            "market_id": "m1", "exchange": "kalshi",
            "question": "Will X happen by 2027?", "implied_yes_prob": 0.21,
            "volume": 1000.0, "liquidity": 50.0, "event_title": "X",
            "matched_query": "x",
        }

    @pytest.mark.parametrize("over", [
        {"status": "settled"},                      # 已结算
        {"settled_at": "2026-01-01"},               # 已结算（时间戳）
        {"last_yes_price": None},                   # 无价
        {"last_yes_price": "1.5"},                  # 价格越界
        {"volume": "10"},                           # 低量（< 200）
        {"liquidity": "0"},                         # 零流动性
        {"market_id": ""},                          # 无 id
    ])
    def test_normalize_rejects_bad_markets(self, market_tools, over):
        assert market_tools.normalize_market(_raw_market(**over), matched_query="x") is None

    def test_snapshot_dedupes_sorts_and_limits(self, market_tools):
        def fetch(q, limit):
            if q == "tariff":
                return [_raw_market(market_id="a", volume="500"),
                        _raw_market(market_id="b", volume="9000")]
            return [_raw_market(market_id="a", volume="500"),   # 跨 query 重复 → 去重
                    _raw_market(market_id="c", volume="3000")]
        out = market_tools.snapshot_for_queries(["tariff", "chips"], fetch=fetch)
        assert [m["market_id"] for m in out] == ["b", "c", "a"]  # volume 降序
        assert out[2]["matched_query"] == "tariff"               # 首个命中的 query 保留
        capped = market_tools.snapshot_for_queries(["tariff", "chips"], fetch=fetch, max_total=2)
        assert [m["market_id"] for m in capped] == ["b", "c"]

    def test_snapshot_survives_query_failure(self, market_tools):
        def fetch(q, limit):
            if q == "boom":
                raise RuntimeError("network down")
            return [_raw_market(market_id="ok")]
        out = market_tools.snapshot_for_queries(["boom", "fine"], fetch=fetch)
        assert [m["market_id"] for m in out] == ["ok"]  # 单 query 失败不阻断整体

    def test_impl_without_key_degrades_with_note(self, market_tools, monkeypatch):
        monkeypatch.delenv("ODDPOOL_API_KEY", raising=False)
        payload = json.loads(market_tools.prediction_market_search_impl("tariff, chips"))
        assert payload["markets"] == []
        assert "ODDPOOL_API_KEY" in payload["note"]

    def test_impl_with_key_uses_snapshot(self, market_tools, monkeypatch):
        monkeypatch.setenv("ODDPOOL_API_KEY", "test-key")
        monkeypatch.setattr(
            market_tools, "snapshot_for_queries",
            lambda qlist, **kw: [{"market_id": "m9", "implied_yes_prob": 0.4}])
        payload = json.loads(market_tools.prediction_market_search_impl("tariff\nchips"))
        assert payload["queries"] == ["tariff", "chips"]
        assert payload["markets"][0]["market_id"] == "m9"
        assert "calibration anchors" in payload["note"]

    def test_impl_empty_queries(self, market_tools):
        payload = json.loads(market_tools.prediction_market_search_impl("  \n , "))
        assert payload["markets"] == [] and "no queries" in payload["note"]

    def test_module_importable_without_langchain(self, market_tools):
        # backend venv 无 langchain：模块必须仍可导入，tool 变量为 None（harness 环境才非 None）
        assert hasattr(market_tools, "prediction_market_search_tool")
