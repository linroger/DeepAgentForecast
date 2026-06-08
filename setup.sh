#!/usr/bin/env bash
#
# setup.sh — DeepResearchForecast quick-start / installer
# ---------------------------------------------------------------------------
# Idempotent, re-runnable setup for the "one prompt -> forecast" engine.
#
#   * Checks prerequisites (node>=18, npm, uv, python3) and WARNS (never
#     hard-fails) when something optional is missing.
#   * Auto-detects the local model provider:
#       - `claude` CLI on PATH -> LLM_PROVIDER=claude-cli, DEERFLOW_MODEL=claude
#       - `codex`  CLI on PATH -> LLM_PROVIDER=codex-cli,  DEERFLOW_MODEL=claude
#       - neither               -> default claude-cli + a warning
#   * Scaffolds .env from .env.example (never overwrites an existing .env) and
#     upserts the detected provider lines safely.
#   * Installs root + frontend + backend dependencies, and the sibling
#     deer-flow research engine venv if that repo is present.
#
# Safe to run multiple times. Optional steps are guarded so a missing piece
# (e.g. no deer-flow checkout) never aborts the whole script.
# ---------------------------------------------------------------------------

# Fail fast on errors, unset variables, and broken pipes — but we deliberately
# guard every OPTIONAL step with `|| true` / explicit `if` blocks so an absent
# optional dependency does not abort the run.
set -euo pipefail

# ---------------------------------------------------------------------------
# Paths & pretty-printing helpers
# ---------------------------------------------------------------------------

# Resolve the directory this script lives in so the script works no matter the
# caller's current working directory. ROOT_DIR is the MiroFish project root.
SCRIPT_SOURCE="${BASH_SOURCE[0]}"
ROOT_DIR="$(cd "$(dirname "$SCRIPT_SOURCE")" && pwd)"

# Colour output only when stdout is an interactive terminal.
if [ -t 1 ]; then
  C_RESET="$(printf '\033[0m')"
  C_BOLD="$(printf '\033[1m')"
  C_GREEN="$(printf '\033[32m')"
  C_YELLOW="$(printf '\033[33m')"
  C_CYAN="$(printf '\033[36m')"
else
  C_RESET="" C_BOLD="" C_GREEN="" C_YELLOW="" C_CYAN=""
fi

# Status markers: ✓ for success, ⚠ for warnings, › for info/steps.
ok()    { printf '%s✓%s %s\n'  "$C_GREEN"  "$C_RESET" "$*"; }
warn()  { printf '%s⚠%s %s\n'  "$C_YELLOW" "$C_RESET" "$*"; }
info()  { printf '%s›%s %s\n'   "$C_CYAN"   "$C_RESET" "$*"; }
step()  { printf '\n%s== %s ==%s\n' "$C_BOLD" "$*" "$C_RESET"; }

# ---------------------------------------------------------------------------
# 1) Banner
# ---------------------------------------------------------------------------
printf '%s' "$C_BOLD"
cat <<'BANNER'
============================================================
   DeepResearchForecast — setup
   one prompt -> research -> simulate -> forecast
============================================================
BANNER
printf '%s\n' "$C_RESET"
info "Project root: $ROOT_DIR"

# ---------------------------------------------------------------------------
# 2) Prerequisite checks (warn, never hard-fail)
# ---------------------------------------------------------------------------
step "Checking prerequisites"

# have <cmd> -> true if the command is on PATH.
have() { command -v "$1" >/dev/null 2>&1; }

# Track whether anything important is missing so we can summarise at the end.
MISSING_PREREQS=0

# --- node (>= 18) ---
if have node; then
  # `node -v` prints e.g. "v20.11.0"; strip the leading 'v' and take the major.
  NODE_VER="$(node -v 2>/dev/null | sed 's/^v//')"
  NODE_MAJOR="${NODE_VER%%.*}"
  if [ -n "$NODE_MAJOR" ] && [ "$NODE_MAJOR" -ge 18 ] 2>/dev/null; then
    ok "node $NODE_VER (>=18)"
  else
    warn "node $NODE_VER found, but >=18 is required. Update Node.js: https://nodejs.org/"
    MISSING_PREREQS=1
  fi
else
  warn "node not found (>=18 required). Install Node.js: https://nodejs.org/"
  MISSING_PREREQS=1
fi

# --- npm ---
if have npm; then
  ok "npm $(npm -v 2>/dev/null)"
else
  warn "npm not found. It ships with Node.js: https://nodejs.org/"
  MISSING_PREREQS=1
fi

# --- uv (Python package/venv manager) ---
if have uv; then
  ok "uv $(uv --version 2>/dev/null | awk '{print $2}')"
else
  warn "uv not found. Install it (Python package manager) with:"
  warn "    curl -LsSf https://astral.sh/uv/install.sh | sh"
  warn "    docs: https://docs.astral.sh/uv/"
  MISSING_PREREQS=1
fi

# --- python3 ---
if have python3; then
  ok "python3 $(python3 --version 2>/dev/null | awk '{print $2}')"
else
  warn "python3 not found. uv can install/manage Python for you (uv python install)."
  warn "    See https://docs.astral.sh/uv/ for details."
  MISSING_PREREQS=1
fi

if [ "$MISSING_PREREQS" -ne 0 ]; then
  warn "Some prerequisites are missing/old. Setup will continue, but install the"
  warn "items above before running the app for the full pipeline to work."
fi

# ---------------------------------------------------------------------------
# 3) Auto-detect the model provider
# ---------------------------------------------------------------------------
step "Detecting model provider"

# DEERFLOW_MODEL must be one of the models DeerFlow's research stage supports
# (claude | minimax). For CLI providers we point DeerFlow at "claude".
LLM_PROVIDER=""
DEERFLOW_MODEL="claude"

if have claude; then
  LLM_PROVIDER="claude-cli"
  DEERFLOW_MODEL="claude"
  ok "Claude Code CLI detected -> LLM_PROVIDER=claude-cli (no API key needed)"
elif have codex; then
  LLM_PROVIDER="codex-cli"
  DEERFLOW_MODEL="claude"
  ok "Codex CLI detected -> LLM_PROVIDER=codex-cli (no API key needed)"
else
  # No local CLI: keep claude-cli as the documented default but make it loud
  # that the user must either install a CLI or configure an API-key provider.
  LLM_PROVIDER="claude-cli"
  DEERFLOW_MODEL="claude"
  warn "No 'claude' or 'codex' CLI found on PATH."
  warn "Defaulting LLM_PROVIDER=claude-cli, but you must EITHER:"
  warn "    • install a CLI:  Claude Code (https://claude.com/claude-code) or Codex, OR"
  warn "    • set an API-key provider in .env (LLM_PROVIDER=openai|kimi|minimax"
  warn "      with LLM_API_KEY / LLM_BASE_URL / LLM_MODEL_NAME)."
fi

# ---------------------------------------------------------------------------
# 4) Scaffold & configure .env  (NEVER overwrite an existing .env)
# ---------------------------------------------------------------------------
step "Configuring .env"

ENV_FILE="$ROOT_DIR/.env"
ENV_EXAMPLE="$ROOT_DIR/.env.example"

if [ -f "$ENV_FILE" ]; then
  ok ".env already exists — leaving it in place (not overwriting)."
elif [ -f "$ENV_EXAMPLE" ]; then
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  ok "Created .env from .env.example"
else
  # Last-resort minimal stub so the upsert below has a file to work on.
  warn ".env.example not found; creating a minimal .env stub."
  cat > "$ENV_FILE" <<'STUB'
LLM_PROVIDER=claude-cli
ZEP_API_KEY=your_zep_api_key_here
STUB
fi

# upsert_env KEY VALUE
# --------------------
# Safely set "KEY=VALUE" in $ENV_FILE:
#   * if an active (non-commented) "KEY=" line exists, replace it in place;
#   * otherwise append the line.
# Uses awk to a temp file (portable across BSD/macOS & GNU sed differences) and
# treats the value as a literal string (no regex/escape surprises).
upsert_env() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp "${ENV_FILE}.XXXXXX")"
  KEY="$key" VALUE="$value" awk '
    BEGIN { key = ENVIRON["KEY"]; value = ENVIRON["VALUE"]; found = 0 }
    {
      # Match an active assignment line for this key (allow leading spaces),
      # ignoring commented-out (#) lines so we never resurrect a comment.
      if ($0 ~ "^[[:space:]]*" key "=") {
        if (!found) { print key "=" value; found = 1 }
        # drop any duplicate active lines for the same key
        next
      }
      print
    }
    END { if (!found) print key "=" value }
  ' "$ENV_FILE" > "$tmp" && mv "$tmp" "$ENV_FILE"
}

upsert_env "LLM_PROVIDER"   "$LLM_PROVIDER"
upsert_env "DEERFLOW_MODEL" "$DEERFLOW_MODEL"
ok "Set LLM_PROVIDER=$LLM_PROVIDER and DEERFLOW_MODEL=$DEERFLOW_MODEL in .env"

# --- Zep API key prompt (required for every run; allow skipping) -------------
# We only prompt if the key is still the placeholder/empty. We never echo the
# secret back and we read silently. If stdin is not a TTY (e.g. CI), we skip.
ZEP_PLACEHOLDER="your_zep_api_key_here"
CURRENT_ZEP="$(grep -E '^[[:space:]]*ZEP_API_KEY=' "$ENV_FILE" | head -n1 | cut -d= -f2- || true)"

if [ -z "$CURRENT_ZEP" ] || [ "$CURRENT_ZEP" = "$ZEP_PLACEHOLDER" ]; then
  if [ -t 0 ]; then
    info "A Zep Cloud API key is required for every run (free tier works):"
    info "    https://app.getzep.com/"
    printf '  Paste your ZEP_API_KEY (or press Enter to skip and edit .env later): '
    # -s: silent (don't echo the secret); -r: raw (keep backslashes).
    read -rs ZEP_INPUT || ZEP_INPUT=""
    printf '\n'
    if [ -n "$ZEP_INPUT" ]; then
      upsert_env "ZEP_API_KEY" "$ZEP_INPUT"
      ok "ZEP_API_KEY saved to .env"
      unset ZEP_INPUT
    else
      warn "Skipped — set ZEP_API_KEY in .env before running (still a placeholder)."
    fi
  else
    warn "ZEP_API_KEY is still a placeholder and stdin is non-interactive."
    warn "Edit .env and set ZEP_API_KEY before running."
  fi
else
  ok "ZEP_API_KEY already configured in .env"
fi

# ---------------------------------------------------------------------------
# 5) Install dependencies
# ---------------------------------------------------------------------------

# --- Root + frontend npm deps ---
step "Installing root + frontend dependencies (npm)"
if have npm; then
  # Prefer the project's convenience script; fall back to explicit installs.
  if npm run | grep -qE '^\s*setup:all' 2>/dev/null; then
    ( cd "$ROOT_DIR" && npm run setup:all )
  else
    ( cd "$ROOT_DIR" && npm install )
    if [ -d "$ROOT_DIR/frontend" ]; then
      ( cd "$ROOT_DIR/frontend" && npm install )
    fi
  fi
  ok "Root + frontend npm dependencies installed"
else
  warn "Skipping npm install — npm is not available."
fi

# --- Backend Python venv via uv ---
step "Installing backend dependencies (uv sync)"
if have uv && [ -d "$ROOT_DIR/backend" ]; then
  ( cd "$ROOT_DIR/backend" && uv sync )
  ok "Backend venv ready (backend/.venv)"
elif [ ! -d "$ROOT_DIR/backend" ]; then
  warn "backend/ directory not found — skipping backend install."
else
  warn "Skipping backend 'uv sync' — uv is not available (see install hint above)."
fi

# --- DeerFlow research engine (sibling ../deer-flow) — OPTIONAL but required
#     for the research stage (stage 1) of a full run ---
step "Setting up DeerFlow research engine (sibling ../deer-flow)"

# Allow an explicit override via DEERFLOW_DIR; otherwise default to the sibling.
DEERFLOW_DIR="${DEERFLOW_DIR:-$(cd "$ROOT_DIR/.." && pwd)/deer-flow}"

if [ -d "$DEERFLOW_DIR" ]; then
  if have uv && [ -d "$DEERFLOW_DIR/backend" ]; then
    info "Found deer-flow at: $DEERFLOW_DIR"
    # Build DeerFlow's isolated venv (Python >=3.12; 3.13 recommended). Guarded
    # so a build failure here does not abort the whole setup.
    if UV_PROJECT_ENVIRONMENT="$DEERFLOW_DIR/backend/.venv" \
         uv sync --project "$DEERFLOW_DIR/backend" --python 3.13; then
      ok "DeerFlow venv ready ($DEERFLOW_DIR/backend/.venv, Python 3.13)"
    else
      warn "DeerFlow 'uv sync' failed. The rest of the setup is unaffected;"
      warn "you can retry with:"
      warn "    UV_PROJECT_ENVIRONMENT=\"$DEERFLOW_DIR/backend/.venv\" \\"
      warn "      uv sync --project \"$DEERFLOW_DIR/backend\" --python 3.13"
    fi
  elif [ ! -d "$DEERFLOW_DIR/backend" ]; then
    warn "deer-flow found but '$DEERFLOW_DIR/backend' is missing — skipping its venv."
  else
    warn "Skipping DeerFlow venv — uv is not available."
  fi
else
  # deer-flow absent: the deep-research stage (stage 1) can't run. Stage 1 is
  # the first stage of a full pipeline, so this repo is REQUIRED for full runs.
  warn "No deer-flow repo found at: $DEERFLOW_DIR"
  warn "Deep research (pipeline stage 1) needs the 'deer-flow' repo as a SIBLING"
  warn "directory of MiroFish-0.1.2. Without it, full 'research -> forecast' runs"
  warn "cannot perform the research stage. Clone deer-flow next to this project,"
  warn "then re-run ./setup.sh (or set DEERFLOW_DIR to its location)."
fi

# ---------------------------------------------------------------------------
# 6) Next steps
# ---------------------------------------------------------------------------
step "Next steps"
cat <<NEXT
  1) Zep API key (required for every run, free tier works):
       • Get one at https://app.getzep.com/
       • Set ZEP_API_KEY=... in: $ENV_FILE

  2) Model provider: currently LLM_PROVIDER=$LLM_PROVIDER (DEERFLOW_MODEL=$DEERFLOW_MODEL).
       • claude-cli / codex-cli use your local CLI — no API key needed.
       • For an API provider, set LLM_PROVIDER=openai|kimi|minimax in .env and add
         LLM_API_KEY / LLM_BASE_URL / LLM_MODEL_NAME. You can also switch the
         provider at runtime from the UI Settings menu (applies to NEW runs).

  3) Start everything (backend :5001 + frontend :3000):
       ${C_BOLD}npm run dev${C_RESET}

  4) Open the dashboard:
       ${C_BOLD}http://localhost:3000/research${C_RESET}
NEXT

printf '\n'
ok "Setup complete."
