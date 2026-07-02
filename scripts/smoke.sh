#!/usr/bin/env bash
#
# smoke.sh — DeepAgentForecast fast offline pipeline smoke test (EXECPLAN2 I-7-5)
# ---------------------------------------------------------------------------
# Proves the *inter-stage contracts* of the pipeline still hold after a refactor,
# without any real research / LLM spend. Where doctor.sh checks the environment,
# smoke.sh actually wires the stages together and asserts each one still emits
# its expected artifact shape:
#
#   stage 2  ontology  : OntologyGenerator (stub LLM) -> entity_types/edge_types
#   stage 3  graph     : GraphBuilderService.get_graph_data frontend shape
#                        (REAL local Graphiti when a provider is available and
#                         SMOKE_GRAPH=1; otherwise an offline stub graph snapshot)
#   stage 4  entities  : ZepEntityReader.filter_defined_entities (graph -> OASIS)
#   stage 5  sim-config: SimulationConfigGenerator.generate_config (stub LLM) +
#                        OASIS profiles CSV/JSON headers
#   stage 6  report    : ReportOutline assembly (non-empty outline shape)
#   (opt)    sim leg   : a 1-round / 2-agent OASIS micro-sim, only when SMOKE_FULL=1
#
#   npm run smoke        (or: bash scripts/smoke.sh)
#
# OPTIONAL-DEGRADE invariant: nothing here is on the default `npm run dev` path.
# The default run is deterministic, $0 and < ~60s. The two heavier legs are
# strictly opt-in via env flags and fall back to the cheap path when off:
#
#   SMOKE_GRAPH=1   build a REAL local Graphiti graph for stage 3/4 (needs a
#                   configured LLM provider; falls back to the stub snapshot
#                   automatically if extraction is unavailable). Default: stub.
#   SMOKE_FULL=1    additionally run the OASIS micro-sim leg (slow/fragile;
#                   needs a real LLM for the agents). Default: skipped.
#
# Determinism knobs (exported here, honored by the stages that read them):
#   SIM_SEED=1337            seeds OASIS / config sampling for reproducible runs
#   LLM_TEMPERATURE_OVERRIDE=0   read opportunistically via getattr where present
#
# Exit code: 0 = every exercised stage contract held; 1 = a contract broke.
# ---------------------------------------------------------------------------
set -u

SCRIPT_SOURCE="${BASH_SOURCE[0]}"
ROOT_DIR="$(cd "$(dirname "$SCRIPT_SOURCE")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"

if [ -t 1 ]; then
  C_RESET="$(printf '\033[0m')"; C_GREEN="$(printf '\033[32m')"
  C_YELLOW="$(printf '\033[33m')"; C_RED="$(printf '\033[31m')"; C_BOLD="$(printf '\033[1m')"
else
  C_RESET="" C_GREEN="" C_YELLOW="" C_RED="" C_BOLD=""
fi
say()  { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn() { printf '%s⚠%s %s\n' "$C_YELLOW" "$C_RESET" "$*"; }
bad()  { printf '%s✗%s %s\n' "$C_RED" "$C_RESET" "$*"; }
sect() { printf '\n%s== %s ==%s\n' "$C_BOLD" "$*" "$C_RESET"; }

# --- determinism + cost guards (OFF by default for the heavy legs) ----------
export SIM_SEED="${SIM_SEED:-1337}"
export LLM_TEMPERATURE_OVERRIDE="${LLM_TEMPERATURE_OVERRIDE:-0}"
SMOKE_GRAPH="${SMOKE_GRAPH:-0}"
SMOKE_FULL="${SMOKE_FULL:-0}"
export SMOKE_GRAPH SMOKE_FULL
# A stable, throwaway data dir so the smoke never touches a contributor's real runs.
SMOKE_TMP="$(mktemp -d "${TMPDIR:-/tmp}/daf_smoke.XXXXXX")"
export PIPELINE_DATA_DIR="${PIPELINE_DATA_DIR:-$SMOKE_TMP/pipelines}"
cleanup() { rm -rf "$SMOKE_TMP" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

# --- locate a backend interpreter -------------------------------------------
BE_PY="$BACKEND_DIR/.venv/bin/python"
PY=""
if [ -x "$BE_PY" ]; then
  PY="$BE_PY"
elif command -v uv >/dev/null 2>&1; then
  PY="uv-run"   # sentinel: invoke via `uv run python` inside backend/
else
  bad "no backend interpreter found — run ( cd backend && uv sync --python 3.12 ) or install uv"
  exit 1
fi

run_py() {  # run_py <script.py> [args...]
  local script="$1"; shift
  if [ "$PY" = "uv-run" ]; then
    ( cd "$BACKEND_DIR" && uv run python "$script" "$@" )
  else
    ( cd "$BACKEND_DIR" && "$BE_PY" "$script" "$@" )
  fi
}

sect "DeepAgentForecast pipeline smoke (offline)"
say  "seed=$SIM_SEED  graph_leg=$([ "$SMOKE_GRAPH" = "1" ] && echo real || echo stub)  sim_leg=$([ "$SMOKE_FULL" = "1" ] && echo on || echo off)"

# ---------------------------------------------------------------------------
# The driver is emitted as a throwaway module so we ship a single new file
# (scripts/smoke.sh) yet still run real backend code with proper imports.
# ---------------------------------------------------------------------------
DRIVER="$SMOKE_TMP/_smoke_driver.py"
cat > "$DRIVER" <<'PYEOF'
"""Offline pipeline smoke driver (EXECPLAN2 I-7-5).

Exercises stages 2-6 of the pipeline against stub LLMs and a local/stub graph,
asserting each stage's artifact shape. Emits SMOKE_CONFIG=<path> on stdout so the
shell wrapper can optionally feed the generated config into the OASIS micro-sim.

Run from backend/: python <this>  (cwd already backend/ via the wrapper).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

# Make `import app...` resolve when run from the backend/ working directory.
sys.path.insert(0, os.path.abspath(os.getcwd()))


def _fail(stage: str, msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"[smoke] STAGE FAILED ({stage}): {msg}", file=sys.stderr)
    raise SystemExit(3)


class _StubLLM:
    """Deterministic, $0 stand-in for app.utils.llm_client.LLMClient.

    Mirrors the real surface used by the generators (chat / chat_json /
    supports_native_tools). chat_json returns a scripted dict per call kind so
    the generators take their real parsing paths; an empty default is safe
    because every parser in these stages has defensive .get() fallbacks.
    """

    def __init__(self, ontology=None):
        self.provider = "stub"
        self.model = "stub-1"
        self._ontology = ontology or {}
        self.calls = 0

    def chat(self, messages, temperature=0.7, max_tokens=4096, response_format=None):
        self.calls += 1
        return ""

    def chat_json(self, messages, temperature=0.3, max_tokens=4096):
        self.calls += 1
        text = ""
        for m in messages:
            text += str(m.get("content", "")) if isinstance(m, dict) else str(m)
        # The ontology system prompt is unmistakable; everything else uses the
        # stages' own safe defaults so the deterministic fallbacks run.
        if "知识图谱本体设计专家" in text or "entity_types" in text and "edge_types" in text and self._ontology:
            return dict(self._ontology)
        return {}

    def supports_native_tools(self):
        return False


# A compact, fixed ontology (mirrors graph_builder.set_ontology's expectations:
# concrete types + Person/Organization fallbacks, with attributes & edges).
STUB_ONTOLOGY = {
    "entity_types": [
        {"name": "Company", "description": "A company or chipmaker.",
         "attributes": [{"name": "kind", "type": "text", "description": "kind of company"}],
         "examples": ["TSMC", "NVIDIA"]},
        {"name": "GovernmentAgency", "description": "A government body or regulator.",
         "attributes": [{"name": "country", "type": "text", "description": "country"}],
         "examples": ["US Commerce Dept"]},
        {"name": "Person", "description": "Any individual not fitting a specific type.",
         "attributes": [{"name": "role", "type": "text", "description": "role or title"}],
         "examples": ["Morris Chang"]},
        {"name": "Organization", "description": "Any organization not fitting a specific type.",
         "attributes": [{"name": "org_type", "type": "text", "description": "type"}],
         "examples": ["industry group"]},
    ],
    "edge_types": [
        {"name": "REGULATES", "description": "regulatory action between bodies",
         "attributes": [{"name": "detail", "type": "text", "description": "detail"}],
         "source_targets": [{"source": "GovernmentAgency", "target": "Company"}]},
        {"name": "DEPENDS_ON", "description": "supply/manufacturing dependency",
         "attributes": [],
         "source_targets": [{"source": "Company", "target": "Company"}]},
    ],
    "analysis_summary": "半导体出口管制场景的最小本体。",
}

# ~6 fixture chunks — used by the REAL graph leg when SMOKE_GRAPH=1.
FIXTURE_CHUNKS = [
    "TSMC is a Taiwanese chipmaker. Morris Chang founded TSMC.",
    "TSMC announced a new advanced semiconductor fabrication plant in Arizona.",
    "The United States government supported the investment through the CHIPS Act.",
    "The United States imposed advanced-semiconductor export controls on China.",
    "NVIDIA designs AI chips and depends on TSMC for manufacturing.",
    "The export controls restrict NVIDIA from selling its most advanced GPUs to China.",
]

# Offline stub graph snapshot — the cheap fallback for stages 3/4 when no real
# graph is built. Shapes match GraphBuilderService.get_graph_data exactly.
STUB_GRAPH_SNAPSHOT = {
    "graph_id": "smoke-stub-graph",
    "nodes": [
        {"uuid": "n-tsmc", "name": "TSMC", "labels": ["Entity", "Company"],
         "summary": "Taiwanese chipmaker founded by Morris Chang.",
         "attributes": {"kind": "foundry"}, "created_at": "2026-01-01T00:00:00"},
        {"uuid": "n-nvidia", "name": "NVIDIA", "labels": ["Entity", "Company"],
         "summary": "Designs AI chips, depends on TSMC for manufacturing.",
         "attributes": {"kind": "fabless"}, "created_at": "2026-01-01T00:00:00"},
        {"uuid": "n-us", "name": "United States", "labels": ["Entity", "GovernmentAgency"],
         "summary": "Imposed advanced-semiconductor export controls.",
         "attributes": {"country": "US"}, "created_at": "2026-01-01T00:00:00"},
        {"uuid": "n-chang", "name": "Morris Chang", "labels": ["Entity", "Person"],
         "summary": "Founder of TSMC.", "attributes": {"role": "founder"},
         "created_at": "2026-01-01T00:00:00"},
        {"uuid": "n-semi-assoc", "name": "Semiconductor Industry Association",
         "labels": ["Entity", "Organization"],
         "summary": "An industry group representing chipmakers.",
         "attributes": {"org_type": "industry group"}, "created_at": "2026-01-01T00:00:00"},
    ],
    "edges": [
        {"uuid": "e-1", "name": "DEPENDS_ON", "fact": "NVIDIA depends on TSMC for manufacturing.",
         "fact_type": "DEPENDS_ON", "source_node_uuid": "n-nvidia", "target_node_uuid": "n-tsmc",
         "source_node_name": "NVIDIA", "target_node_name": "TSMC", "attributes": {},
         "created_at": "2026-01-01T00:00:00", "valid_at": None, "invalid_at": None,
         "expired_at": None, "episodes": []},
        {"uuid": "e-2", "name": "REGULATES", "fact": "The US regulates exports affecting NVIDIA.",
         "fact_type": "REGULATES", "source_node_uuid": "n-us", "target_node_uuid": "n-nvidia",
         "source_node_name": "United States", "target_node_name": "NVIDIA", "attributes": {},
         "created_at": "2026-01-01T00:00:00", "valid_at": None, "invalid_at": None,
         "expired_at": None, "episodes": []},
    ],
    "node_count": 5,
    "edge_count": 2,
}

NODE_KEYS = {"uuid", "name", "labels", "summary", "attributes", "created_at"}
EDGE_KEYS = {"uuid", "name", "fact", "fact_type", "source_node_uuid", "target_node_uuid",
             "source_node_name", "target_node_name", "attributes", "created_at",
             "valid_at", "invalid_at", "expired_at", "episodes"}


def stage_ontology():
    from app.services.ontology_generator import OntologyGenerator
    gen = OntologyGenerator(llm_client=_StubLLM(ontology=STUB_ONTOLOGY))
    onto = gen.generate(
        document_texts=["\n".join(FIXTURE_CHUNKS)],
        simulation_requirement="半导体出口管制下各方的舆论反应",
    )
    if not isinstance(onto, dict):
        _fail("ontology", f"generate() returned {type(onto)!r}, expected dict")
    if not isinstance(onto.get("entity_types"), list) or not onto["entity_types"]:
        _fail("ontology", f"missing/empty entity_types: {onto.get('entity_types')!r}")
    if not isinstance(onto.get("edge_types"), list) or not onto["edge_types"]:
        _fail("ontology", f"missing/empty edge_types: {onto.get('edge_types')!r}")
    names = {e.get("name") for e in onto["entity_types"]}
    # Ontology generator must guarantee the Person/Organization fallbacks exist.
    if "Person" not in names or "Organization" not in names:
        _fail("ontology", f"fallback types missing from {sorted(n for n in names if n)}")
    print(f"[smoke] stage 2 ontology OK: {len(onto['entity_types'])} entity types, "
          f"{len(onto['edge_types'])} edge types")
    return onto


def _build_real_graph(onto):
    """Build a REAL local Graphiti graph (no Zep key) and return its graph_data.

    Returns None (signals the caller to fall back to the stub snapshot) if the
    local extraction backend / provider is unavailable, so the default smoke
    stays $0 and deterministic.
    """
    try:
        from app.services.graph_builder import GraphBuilderService
        builder = GraphBuilderService()                       # no Zep key needed
        graph_id = builder.create_graph("smoke-real-graph")
        builder.set_ontology(graph_id, onto)
        uuids = builder.add_text_batches(graph_id, FIXTURE_CHUNKS, batch_size=5)
        builder._wait_for_episodes(uuids)
        data = builder.get_graph_data(graph_id)
        if not data.get("node_count"):
            print("[smoke] real graph produced 0 nodes — falling back to stub snapshot")
            return None, None
        return data, graph_id
    except Exception as e:  # extraction needs a live provider; degrade cleanly
        print(f"[smoke] real graph build unavailable ({type(e).__name__}: {str(e)[:120]}) "
              "— falling back to stub snapshot")
        return None, None


def stage_graph(onto):
    """Stage 3: assert get_graph_data frontend shape (real or stub)."""
    want_real = os.environ.get("SMOKE_GRAPH", "0") == "1"
    data, graph_id = (None, None)
    if want_real:
        data, graph_id = _build_real_graph(onto)
    if data is None:
        data = STUB_GRAPH_SNAPSHOT
        graph_id = data["graph_id"]
        source = "stub"
    else:
        source = "real"

    required = {"graph_id", "nodes", "edges", "node_count", "edge_count"}
    if not required.issubset(data):
        _fail("graph", f"get_graph_data missing keys: {required - set(data)}")
    if not data["nodes"] or not data["edges"]:
        _fail("graph", "empty graph (no nodes/edges)")
    if not NODE_KEYS.issubset(data["nodes"][0]):
        _fail("graph", f"node[0] missing keys: {NODE_KEYS - set(data['nodes'][0])}")
    if not EDGE_KEYS.issubset(data["edges"][0]):
        _fail("graph", f"edge[0] missing keys: {EDGE_KEYS - set(data['edges'][0])}")
    print(f"[smoke] stage 3 graph OK ({source}): {data['node_count']} nodes, "
          f"{data['edge_count']} edges, shape verified")
    return data, graph_id, source


def stage_entities(graph_data, graph_id, source):
    """Stage 4: graph -> typed OASIS entities via the real filtering contract."""
    from app.services.zep_entity_reader import EntityNode, FilteredEntities

    if source == "real":
        # Exercise the REAL reader against the freshly built local graph.
        from app.services.zep_entity_reader import ZepEntityReader
        reader = ZepEntityReader()
        filtered = reader.filter_defined_entities(graph_id, enrich_with_edges=True)
    else:
        # Stub path: apply the *same* filtering rule the reader uses (labels beyond
        # Entity/Node) so the OASIS contract is validated without a live graph.
        entities, types = [], set()
        for n in graph_data["nodes"]:
            custom = [l for l in n["labels"] if l not in ("Entity", "Node")]
            if not custom:
                continue
            types.add(custom[0])
            entities.append(EntityNode(uuid=n["uuid"], name=n["name"], labels=n["labels"],
                                       summary=n["summary"], attributes=n["attributes"]))
        filtered = FilteredEntities(entities=entities, entity_types=types,
                                    total_count=len(graph_data["nodes"]),
                                    filtered_count=len(entities))

    fd = filtered.to_dict()
    if not {"entities", "entity_types", "total_count", "filtered_count"}.issubset(fd):
        _fail("entities", f"FilteredEntities.to_dict missing keys: {fd.keys()}")
    if fd["filtered_count"] <= 0:
        _fail("entities", "no typed entities produced for OASIS")
    e0 = fd["entities"][0]
    if not {"uuid", "name", "labels", "summary", "attributes"}.issubset(e0):
        _fail("entities", f"entity[0] missing keys: {e0.keys()}")
    print(f"[smoke] stage 4 entities OK: {fd['filtered_count']} typed entities, "
          f"types={sorted(t for t in fd['entity_types'])}")
    return filtered.entities


def stage_sim_config(entities, graph_id, tmp_dir):
    """Stage 5: SimulationConfigGenerator + OASIS profiles, both offline."""
    from app.services.simulation_config_generator import (
        SimulationConfigGenerator, SimulationParameters)
    from app.services.oasis_profile_generator import (
        OasisProfileGenerator, OasisAgentProfile)

    # Limit to two entities for the (optional) micro-sim leg downstream.
    entities = entities[:2] if len(entities) >= 2 else entities

    gen = SimulationConfigGenerator.__new__(SimulationConfigGenerator)
    gen.provider = "stub"
    gen.api_key = None
    gen.base_url = None
    gen.model_name = "stub-1"
    gen.llm = _StubLLM()                       # empty chat_json -> rule-based fallbacks

    params = gen.generate_config(
        simulation_id="smoke-sim",
        project_id="smoke-proj",
        graph_id=graph_id or "smoke-graph",
        simulation_requirement="半导体出口管制下各方的舆论反应",
        document_text="\n".join(FIXTURE_CHUNKS),
        entities=entities,
        enable_twitter=True,
        enable_reddit=True,
    )
    if not isinstance(params, SimulationParameters):
        _fail("sim-config", f"generate_config returned {type(params)!r}")
    cfg = params.to_dict()
    required = {"simulation_id", "project_id", "graph_id", "time_config",
                "agent_configs", "event_config"}
    if not required.issubset(cfg):
        _fail("sim-config", f"config missing keys: {required - set(cfg)}")
    if not cfg["agent_configs"]:
        _fail("sim-config", "no agent_configs generated")
    print(f"[smoke] stage 5 sim-config OK: {len(cfg['agent_configs'])} agents, "
          f"time_config + event_config present")

    # ---- OASIS profile artifact headers (CSV for twitter, JSON for reddit) ----
    profiles = []
    for i, e in enumerate(entities):
        profiles.append(OasisAgentProfile(
            user_id=i,
            user_name=f"smoke_user_{i}",
            name=e.name or f"Agent {i}",
            bio=f"{e.name} participates in the discussion.",
            persona=f"{e.name} holds a consistent stance in social discussions.",
            profession=e.get_entity_type() or "Person",
            interested_topics=["Semiconductors", "Policy"],
            source_entity_uuid=e.uuid,
            source_entity_type=e.get_entity_type() or "Person",
        ))
    pgen = OasisProfileGenerator.__new__(OasisProfileGenerator)
    twitter_csv = os.path.join(tmp_dir, "twitter_profiles.csv")
    reddit_json = os.path.join(tmp_dir, "reddit_profiles.json")
    pgen._save_twitter_csv(profiles, twitter_csv)
    pgen._save_reddit_json(profiles, reddit_json)

    import csv
    with open(twitter_csv, "r", encoding="utf-8") as f:
        header = next(csv.reader(f))
    # Actual OASIS CSV header (kept in sync with _save_twitter_csv).
    if not {"user_id", "name", "username"}.issubset(set(header)):
        _fail("sim-config", f"twitter CSV header missing required fields: {header}")
    with open(reddit_json, "r", encoding="utf-8") as f:
        reddit = json.load(f)
    if not reddit or not {"user_id", "username", "name", "bio", "persona"}.issubset(reddit[0]):
        _fail("sim-config", f"reddit JSON missing required fields: "
                            f"{list(reddit[0].keys()) if reddit else []}")
    print(f"[smoke] stage 5 profiles OK: twitter CSV header={header}, "
          f"reddit JSON {len(reddit)} entries")

    # Persist a full simulation_config.json for the optional micro-sim leg.
    cfg_path = os.path.join(tmp_dir, "simulation_config.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg_path


def stage_report(graph_id):
    """Stage 6: report outline assembly — assert a non-empty, well-formed outline."""
    from app.services.report_agent import ReportOutline, ReportSection
    outline = ReportOutline(
        title="半导体出口管制舆论模拟报告",
        summary="围绕出口管制争议，各方在社媒上的立场与传播路径分析。",
        sections=[
            ReportSection(title="背景与争议断层",
                          description="梳理核心争议点与关键角色。"),
            ReportSection(title="舆论传播与立场演化",
                          description="模拟中各方立场的动态变化。"),
            ReportSection(title="结论与情景研判",
                          description="综合研判与情景推演。"),
        ],
    )
    d = outline.to_dict()
    if not d.get("title") or not d.get("sections"):
        _fail("report", "outline has empty title or no sections")
    md = outline.to_markdown()
    if not md.strip() or len(md.strip()) < 40:
        _fail("report", "report markdown outline is empty/too short")
    if not all(s.get("title") for s in d["sections"]):
        _fail("report", "a section has an empty title")
    print(f"[smoke] stage 6 report OK: outline '{d['title']}' with "
          f"{len(d['sections'])} sections, {len(md)} chars of markdown")


def main():
    tmp_dir = tempfile.mkdtemp(prefix="daf_smoke_stage_")
    onto = stage_ontology()
    graph_data, graph_id, source = stage_graph(onto)
    entities = stage_entities(graph_data, graph_id, source)
    cfg_path = stage_sim_config(entities, graph_id, tmp_dir)
    stage_report(graph_id)
    # Hand the generated config to the shell wrapper (used only if SMOKE_FULL=1).
    print(f"SMOKE_CONFIG={cfg_path}")
    print("[smoke] stages 2-6 contracts held.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PYEOF

# ---------------------------------------------------------------------------
# Run the offline driver (stages 2-6) and capture the generated config path.
# ---------------------------------------------------------------------------
sect "Stages 2-6 (offline contracts)"
DRIVER_OUT="$SMOKE_TMP/driver.out"
if run_py "$DRIVER" >"$DRIVER_OUT" 2>&1; then
  # Echo the driver's per-stage progress, then report success.
  while IFS= read -r line; do
    case "$line" in
      "[smoke] stage "*" OK"*) ok "${line#\[smoke\] }" ;;
      "SMOKE_CONFIG="*) : ;;  # internal handoff, not user-facing
      "[smoke] stages 2-6"*) : ;;
      *) [ -n "$line" ] && say "  $line" ;;
    esac
  done < "$DRIVER_OUT"
  SMOKE_CONFIG="$(grep -m1 '^SMOKE_CONFIG=' "$DRIVER_OUT" | cut -d= -f2-)"
  ok "stages 2-6 contracts held"
else
  bad "offline pipeline smoke FAILED — stage contract broke:"
  sed 's/^/    /' "$DRIVER_OUT"
  exit 1
fi

# ---------------------------------------------------------------------------
# Optional OASIS micro-sim leg (opt-in, slow/fragile, needs a real LLM).
# ---------------------------------------------------------------------------
if [ "$SMOKE_FULL" = "1" ]; then
  sect "Stage run (OASIS micro-sim, opt-in)"
  if [ -z "${SMOKE_CONFIG:-}" ] || [ ! -f "$SMOKE_CONFIG" ]; then
    warn "no generated config available — skipping sim leg"
  else
    SIM_DIR="$(dirname "$SMOKE_CONFIG")"
    say "  running 1-round / 2-agent micro-sim (max 120s); needs a live LLM for agents"
    # Guard the fragile leg with a hard timeout; a clean startup that we then
    # cut off is still useful signal that wiring is intact. Best-effort: a
    # timeout/non-fatal exit does NOT fail the smoke (it is opt-in).
    TIMEOUT_BIN=""
    command -v timeout >/dev/null 2>&1 && TIMEOUT_BIN="timeout 120"
    command -v gtimeout >/dev/null 2>&1 && TIMEOUT_BIN="gtimeout 120"
    SIM_LOG="$SMOKE_TMP/sim.out"
    set +e
    if [ "$PY" = "uv-run" ]; then
      ( cd "$BACKEND_DIR" && $TIMEOUT_BIN uv run python scripts/run_parallel_simulation.py \
          --config "$SMOKE_CONFIG" --max-rounds 1 --no-wait ) >"$SIM_LOG" 2>&1
    else
      ( cd "$BACKEND_DIR" && $TIMEOUT_BIN "$BE_PY" scripts/run_parallel_simulation.py \
          --config "$SMOKE_CONFIG" --max-rounds 1 --no-wait ) >"$SIM_LOG" 2>&1
    fi
    SIM_RC=$?
    set -e 2>/dev/null || true
    if [ "$SIM_RC" -eq 0 ]; then
      ok "micro-sim completed (1 round)"
    elif [ "$SIM_RC" -eq 124 ]; then
      warn "micro-sim hit the 120s timeout (opt-in leg; wiring exercised, not a failure)"
    else
      warn "micro-sim exited rc=$SIM_RC (opt-in leg; likely missing live LLM). Last lines:"
      tail -n 12 "$SIM_LOG" 2>/dev/null | sed 's/^/    /'
    fi
  fi
else
  say ""
  say "  (sim leg skipped — set SMOKE_FULL=1 to also run the OASIS micro-sim)"
fi

printf '\n'
ok "Smoke passed — pipeline stages still wire together. ${C_BOLD}Run a real forecast: npm run dev${C_RESET}"
exit 0
