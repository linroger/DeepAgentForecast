#!/usr/bin/env bash
#
# doctor.sh — DeepAgentForecast environment health check
# ---------------------------------------------------------------------------
# Verifies, in seconds, everything a full "one prompt -> forecast" run needs:
# tool versions, both Python venvs, the DeerFlow checkout + bridge overlay,
# and the .env credentials for the providers you have selected.
#
#   npm run doctor        (or: bash scripts/doctor.sh)
#
# Exit code: 0 = ready to run; 1 = at least one blocking problem found.
# ---------------------------------------------------------------------------
set -u

SCRIPT_SOURCE="${BASH_SOURCE[0]}"
ROOT_DIR="$(cd "$(dirname "$SCRIPT_SOURCE")/.." && pwd)"

if [ -t 1 ]; then
  C_RESET="$(printf '\033[0m')"; C_GREEN="$(printf '\033[32m')"
  C_YELLOW="$(printf '\033[33m')"; C_RED="$(printf '\033[31m')"; C_BOLD="$(printf '\033[1m')"
else
  C_RESET="" C_GREEN="" C_YELLOW="" C_RED="" C_BOLD=""
fi

FAILURES=0
WARNINGS=0
ok()   { printf '%s✓%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn() { printf '%s⚠%s %s\n' "$C_YELLOW" "$C_RESET" "$*"; WARNINGS=$((WARNINGS+1)); }
bad()  { printf '%s✗%s %s\n' "$C_RED" "$C_RESET" "$*"; FAILURES=$((FAILURES+1)); }
sect() { printf '\n%s== %s ==%s\n' "$C_BOLD" "$*" "$C_RESET"; }
have() { command -v "$1" >/dev/null 2>&1; }

# read KEY from .env (first active assignment; empty if absent)
envval() {
  [ -f "$ROOT_DIR/.env" ] || { echo ""; return; }
  grep -E "^[[:space:]]*$1=" "$ROOT_DIR/.env" | head -n1 | cut -d= -f2- | tr -d '"' || true
}

sect "Tooling"
if have node; then
  NODE_VER="$(node -v 2>/dev/null | sed 's/^v//')"
  NODE_MAJOR="${NODE_VER%%.*}"; NODE_MINOR="$(printf '%s' "$NODE_VER" | cut -d. -f2)"
  if [ "$NODE_MAJOR" -ge 22 ] 2>/dev/null || { [ "$NODE_MAJOR" -eq 20 ] && [ "${NODE_MINOR:-0}" -ge 19 ]; } || [ "$NODE_MAJOR" -eq 21 ]; then
    ok "node $NODE_VER"
  else
    bad "node $NODE_VER — the frontend (vite 7) needs >=20.19. https://nodejs.org/"
  fi
else
  bad "node not found (>=20.19 required)"
fi
have uv  && ok "uv $(uv --version 2>/dev/null | awk '{print $2}')" || bad "uv not found — curl -LsSf https://astral.sh/uv/install.sh | sh"
have git && ok "git $(git --version 2>/dev/null | awk '{print $3}')" || warn "git not found (needed to auto-download deer-flow)"

sect "Backend venv (MiroFish)"
BE_PY="$ROOT_DIR/backend/.venv/bin/python"
if [ -x "$BE_PY" ]; then
  BE_VER="$("$BE_PY" --version 2>/dev/null | awk '{print $2}')"
  case "$BE_VER" in
    3.11.*|3.12.*) ok "backend/.venv on Python $BE_VER" ;;
    "") bad "backend/.venv python is broken — rebuild: ( cd backend && uv sync --python 3.12 )" ;;
    *) warn "backend/.venv on Python $BE_VER — the simulation stack targets 3.11–3.12; if imports below fail, rebuild: ( cd backend && uv sync --python 3.12 )" ;;
  esac
  if "$BE_PY" -c "import camel, oasis, graphiti_core, flask" >/dev/null 2>&1; then
    ok "backend imports OK (camel / oasis / graphiti_core / flask)"
  else
    bad "backend deps missing — run: ( cd backend && uv sync --python 3.12 )"
  fi
else
  bad "backend venv missing — run: ( cd backend && uv sync --python 3.12 )  (or ./setup.sh)"
fi

sect "DeerFlow research engine"
DEERFLOW_DIR="${DEERFLOW_DIR:-$(envval DEERFLOW_DIR)}"
DEERFLOW_DIR="${DEERFLOW_DIR:-$ROOT_DIR/deer-flow}"
if [ -d "$DEERFLOW_DIR/backend" ]; then
  ok "deer-flow checkout: $DEERFLOW_DIR"
  [ -f "$DEERFLOW_DIR/deerflow_research.py" ] && ok "bridge entry point installed (deerflow_research.py)" \
    || bad "bridge overlay missing — re-run ./setup.sh (installs deerflow_research.py + patches)"
  [ -f "$DEERFLOW_DIR/config.yaml" ] && ok "deer-flow config.yaml present" \
    || bad "deer-flow/config.yaml missing — re-run ./setup.sh"
  DF_PY="${DEERFLOW_PYTHON:-$(envval DEERFLOW_PYTHON)}"
  DF_PY="${DF_PY:-$DEERFLOW_DIR/backend/.venv/bin/python}"
  if [ -x "$DF_PY" ]; then
    DF_VER="$("$DF_PY" --version 2>/dev/null | awk '{print $2}')"
    if "$DF_PY" -c "import deerflow, langgraph" >/dev/null 2>&1; then
      ok "deer-flow venv OK (Python $DF_VER, deerflow importable)"
    else
      bad "deer-flow venv incomplete — UV_PROJECT_ENVIRONMENT=\"$DEERFLOW_DIR/backend/.venv\" uv sync --project \"$DEERFLOW_DIR/backend\" --python 3.12"
    fi
  else
    bad "deer-flow venv missing ($DF_PY) — re-run ./setup.sh"
  fi
else
  bad "deer-flow not found at $DEERFLOW_DIR — run ./setup.sh (auto-clones it) or set DEERFLOW_DIR"
fi

sect "Configuration (.env)"
if [ -f "$ROOT_DIR/.env" ]; then
  ok ".env exists"
else
  bad ".env missing — cp .env.example .env (or run ./setup.sh)"
fi

# Knowledge graph runs locally (Graphiti). No API key needed; just verify a local
# backend is importable (embedded FalkorDB via falkordblite, or kuzu).
GRAPH_BACKEND="$(envval GRAPH_BACKEND)"; GRAPH_BACKEND="${GRAPH_BACKEND:-auto}"
if [ -n "${BE_PY:-}" ] && [ -x "$BE_PY" ]; then
  if "$BE_PY" -c "import importlib.util,sys; sys.exit(0 if (importlib.util.find_spec('redislite.async_falkordb_client') or importlib.util.find_spec('kuzu')) else 1)" >/dev/null 2>&1; then
    ok "local knowledge graph backend available (GRAPH_BACKEND=$GRAPH_BACKEND, no API key needed)"
  else
    bad "no local graph backend installed — run: ( cd backend && uv sync --python 3.12 )  (installs falkordblite)"
  fi
fi

PROVIDER="$(envval LLM_PROVIDER)"; PROVIDER="${PROVIDER:-claude-cli}"
case "$PROVIDER" in
  claude-cli)
    if have claude; then ok "LLM_PROVIDER=claude-cli and \`claude\` CLI found"
    else bad "LLM_PROVIDER=claude-cli but \`claude\` CLI not on PATH — install Claude Code or switch provider"; fi ;;
  codex-cli)
    if have codex; then ok "LLM_PROVIDER=codex-cli and \`codex\` CLI found"
    else bad "LLM_PROVIDER=codex-cli but \`codex\` CLI not on PATH"; fi ;;
  openai|kimi|minimax|deepseek|qwen|glm)
    KEY="$(envval LLM_API_KEY)"
    case "$KEY" in
      ""|your_api_key|your_api_key_here) bad "LLM_PROVIDER=$PROVIDER needs LLM_API_KEY in .env" ;;
      *) ok "LLM_PROVIDER=$PROVIDER with LLM_API_KEY set" ;;
    esac ;;
  *) warn "Unknown LLM_PROVIDER '$PROVIDER' (expected claude-cli/codex-cli/openai/kimi/minimax/deepseek/qwen/glm)" ;;
esac

DF_MODEL="$(envval DEERFLOW_MODEL)"; DF_MODEL="${DF_MODEL:-claude}"
df_key_check() { # $1 model  $2 env var
  if [ "$DF_MODEL" = "$1" ]; then
    V="$(envval "$2")"
    if [ -n "$V" ]; then ok "DEERFLOW_MODEL=$1 with $2 set"
    else bad "DEERFLOW_MODEL=$1 needs $2 in .env"; fi
  fi
}
case "$DF_MODEL" in
  claude)
    if [ -f "$HOME/.claude/.credentials.json" ] || have claude; then
      ok "DEERFLOW_MODEL=claude (Claude Code OAuth credentials available)"
    else
      bad "DEERFLOW_MODEL=claude needs a Claude Code login — install the \`claude\` CLI and sign in once"
    fi ;;
  codex)
    if [ -f "$HOME/.codex/auth.json" ] || have codex; then ok "DEERFLOW_MODEL=codex (Codex credentials available)"
    else bad "DEERFLOW_MODEL=codex needs \`codex\` login (~/.codex/auth.json)"; fi ;;
  minimax)  df_key_check minimax  MINIMAX_API_KEY ;;
  deepseek) df_key_check deepseek DEEPSEEK_API_KEY ;;
  qwen)     df_key_check qwen     DASHSCOPE_API_KEY ;;
  glm)      df_key_check glm      ZHIPUAI_API_KEY ;;
  kimi)     df_key_check kimi     KIMI_API_KEY ;;
  *) warn "Unknown DEERFLOW_MODEL '$DF_MODEL' (expected claude/minimax/deepseek/qwen/glm/codex/kimi)" ;;
esac

printf '\n'
if [ "$FAILURES" -eq 0 ]; then
  ok "All checks passed${WARNINGS:+ ($WARNINGS warning(s))} — start with: ${C_BOLD}npm run dev${C_RESET} → http://localhost:3000/research"
  exit 0
else
  bad "$FAILURES blocking problem(s) found (and $WARNINGS warning(s)). Fix the ✗ items above, then re-run: npm run doctor"
  exit 1
fi
