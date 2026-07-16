#!/usr/bin/env bash
#
# start.sh — spin up backend + frontend and open the local site.
# ---------------------------------------------------------------------------
# Launches both dev servers DETACHED (nohup + disown), verifies each app's
# readiness signature, opens http://localhost:3000, then follows both logs and durable
# workflow-stage progress in the invoking terminal.
#
# Detached launch matters: a backend started via a foreground/tracked
# background shell call can get reaped when that call's process group is
# torn down (observed repeatedly in this project's run history — see
# handoff.md 2026-07-02/07-03 entries). nohup + disown + redirected I/O
# survives that.
#
#   bash scripts/start.sh            # start both, open browser
#   bash scripts/start.sh --no-open  # start both, skip opening the browser
#   bash scripts/start.sh --detach   # start both and return after readiness
#   bash scripts/start.sh --stop     # stop verified managed services only
#
# Logs: logs/backend.out.log, logs/frontend.out.log (repo-root logs/, created
# if absent). PID files: .backend.pid, .frontend.pid (repo root, gitignored).
# ---------------------------------------------------------------------------
set -uo pipefail

SCRIPT_SOURCE="${BASH_SOURCE[0]}"
ROOT_DIR="$(cd "$(dirname "$SCRIPT_SOURCE")/.." && pwd)"
SCRIPT_SOURCE="$ROOT_DIR/scripts/start.sh"
cd "$ROOT_DIR"

LOG_DIR="$ROOT_DIR/logs"
mkdir -p "$LOG_DIR"
BACKEND_PID_FILE="$ROOT_DIR/.backend.pid"
FRONTEND_PID_FILE="$ROOT_DIR/.frontend.pid"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
PYTHON_BIN="$BACKEND_DIR/.venv/bin/python"
WATCHER_SCRIPT="$ROOT_DIR/scripts/watch_pipeline_progress.py"
PIPELINES_DIR="$BACKEND_DIR/uploads/pipelines"
BACKEND_LOG="$LOG_DIR/backend.out.log"
FRONTEND_LOG="$LOG_DIR/frontend.out.log"
BACKEND_PORT="${FLASK_PORT:-5001}"
FRONTEND_PORT=3000
BACKEND_URL="http://localhost:${BACKEND_PORT}/health"
FRONTEND_URL="http://localhost:${FRONTEND_PORT}/"
BACKEND_TIMEOUT_SECONDS="${START_BACKEND_TIMEOUT_SECONDS:-60}"
FRONTEND_TIMEOUT_SECONDS="${START_FRONTEND_TIMEOUT_SECONDS:-30}"
HEALTH_CURL_TIMEOUT_SECONDS="${START_HEALTH_CURL_TIMEOUT_SECONDS:-2}"
HEALTH_FAILURE_GRACE_SECONDS="${START_HEALTH_FAILURE_GRACE_SECONDS:-8}"
MONITOR_INTERVAL_SECONDS="${START_MONITOR_INTERVAL_SECONDS:-1}"
FOLLOWER_RESTART_LIMIT="${START_FOLLOWER_RESTART_LIMIT:-1}"

NO_OPEN=0
STOP=0
FOLLOW=1
for arg in "$@"; do
  case "$arg" in
    --no-open) NO_OPEN=1 ;;
    --detach) FOLLOW=0 ;;
    --stop) STOP=1 ;;
    --help|-h)
      sed -n '12,21p' "$SCRIPT_SOURCE"
      exit 0
      ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done

_pid_alive() {
  local pid="$1"
  case "$pid" in
    ''|*[!0-9]*) return 1 ;;
  esac
  [ "$pid" -gt 1 ] && kill -0 "$pid" 2>/dev/null
}

_require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "ERROR: required command '$1' is unavailable." >&2
    return 1
  fi
}

_is_nonnegative_integer() {
  case "$1" in
    ''|*[!0-9]*) return 1 ;;
  esac
}

_is_positive_integer() {
  _is_nonnegative_integer "$1" && [ "$1" -gt 0 ]
}

_read_pidfile() {
  local pid=""
  [ -f "$1" ] || return 1
  IFS= read -r pid < "$1" || true
  printf '%s' "$pid"
}

_canonical_dir() {
  [ -d "$1" ] || return 1
  (cd "$1" 2>/dev/null && pwd -P)
}

_pid_cwd() {
  local pid="$1"
  if [ -L "/proc/$pid/cwd" ] && command -v readlink >/dev/null 2>&1; then
    readlink "/proc/$pid/cwd" 2>/dev/null
    return
  fi
  lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1
}

_pid_command() {
  ps -ww -o command= -p "$1" 2>/dev/null | sed -e 's/^[[:space:]]*//'
}

_command_matches_role() {
  local role="$1" command="$2"
  case "$role" in
    backend)
      case "$command" in
        *python*run.py*) return 0 ;;
      esac
      ;;
    frontend)
      case "$command" in
        *npm*run\ dev*|*node_modules/.bin/vite*|*node*/vite*|*node*vite.js*) return 0 ;;
      esac
      ;;
  esac
  return 1
}

_pid_identity_matches() {
  local pid="$1" role="$2" expected_cwd="$3"
  local actual_cwd command canonical_actual canonical_expected
  _pid_alive "$pid" || return 1
  actual_cwd="$(_pid_cwd "$pid")" || return 1
  canonical_actual="$(_canonical_dir "$actual_cwd")" || return 1
  canonical_expected="$(_canonical_dir "$expected_cwd")" || return 1
  [ "$canonical_actual" = "$canonical_expected" ] || return 1
  command="$(_pid_command "$pid")" || return 1
  _command_matches_role "$role" "$command"
}

_port_owner_pids() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null \
    | awk '/^[0-9]+$/ && !seen[$0]++'
}

_port_has_listener() {
  local owners
  owners="$(_port_owner_pids "$1" || true)"
  [ -n "$owners" ]
}

_pid_descends_from() {
  local ancestor="$1" current="$2" parent steps=0
  while _pid_alive "$current" && [ "$steps" -lt 64 ]; do
    [ "$current" = "$ancestor" ] && return 0
    parent="$(ps -o ppid= -p "$current" 2>/dev/null)" || return 1
    parent="${parent//[[:space:]]/}"
    case "$parent" in
      ''|*[!0-9]*|0|1) return 1 ;;
    esac
    [ "$parent" = "$current" ] && return 1
    current="$parent"
    steps=$((steps + 1))
  done
  return 1
}

_pid_directly_owns_port() {
  local pid="$1" port="$2" owner owners
  owners="$(_port_owner_pids "$port" || true)"
  while IFS= read -r owner; do
    [ "$owner" = "$pid" ] && return 0
  done <<< "$owners"
  return 1
}

_pid_tree_owns_port() {
  local pid="$1" port="$2" owner owners
  owners="$(_port_owner_pids "$port" || true)"
  while IFS= read -r owner; do
    [ -n "$owner" ] || continue
    _pid_descends_from "$pid" "$owner" && return 0
  done <<< "$owners"
  return 1
}

_service_pid_verified() {
  local pid="$1" role="$2" expected_cwd="$3" port="$4"
  _pid_identity_matches "$pid" "$role" "$expected_cwd" \
    && _pid_tree_owns_port "$pid" "$port"
}

_stop_from_pidfile() {
  local pidfile="$1" label="$2" role="$3" expected_cwd="$4" port="$5"
  local pid owner owners verified_owners="" still_owned=0
  [ -f "$pidfile" ] || return 0
  pid="$(_read_pidfile "$pidfile" || true)"

  if ! _pid_alive "$pid"; then
    echo "Removing stale $label PID file${pid:+ (pid $pid is not running)}."
    rm -f "$pidfile"
    return 0
  fi
  if ! _pid_identity_matches "$pid" "$role" "$expected_cwd"; then
    echo "ERROR: Refusing to signal $label PID $pid: command/cwd do not match this application; removing the untrusted PID file." >&2
    rm -f "$pidfile"
    return 1
  fi
  if ! _pid_tree_owns_port "$pid" "$port"; then
    echo "ERROR: Refusing to signal $label PID $pid: its verified command/cwd do not own listener :$port; PID file retained for inspection." >&2
    return 1
  fi

  owners="$(_port_owner_pids "$port" || true)"
  while IFS= read -r owner; do
    [ -n "$owner" ] || continue
    if _pid_descends_from "$pid" "$owner" \
        && _pid_identity_matches "$owner" "$role" "$expected_cwd"; then
      verified_owners="${verified_owners}${owner}"$'\n'
    fi
  done <<< "$owners"

  # Re-run every identity/ownership check immediately before sending a signal.
  if ! _service_pid_verified "$pid" "$role" "$expected_cwd" "$port"; then
    echo "ERROR: Refusing to signal $label PID $pid: process identity changed during verification." >&2
    return 1
  fi
  echo "Stopping verified $label (pid $pid, listener :$port)…"
  kill "$pid" 2>/dev/null || true
  while IFS= read -r owner; do
    [ -n "$owner" ] && [ "$owner" != "$pid" ] || continue
    if _pid_identity_matches "$owner" "$role" "$expected_cwd" \
        && _pid_directly_owns_port "$owner" "$port"; then
      kill "$owner" 2>/dev/null || true
    fi
  done <<< "$verified_owners"

  local stop_attempt=0
  while [ "$stop_attempt" -lt 20 ]; do
    still_owned=0
    if _service_pid_verified "$pid" "$role" "$expected_cwd" "$port"; then
      still_owned=1
    fi
    while IFS= read -r owner; do
      [ -n "$owner" ] || continue
      if _pid_identity_matches "$owner" "$role" "$expected_cwd" \
          && _pid_directly_owns_port "$owner" "$port"; then
        still_owned=1
      fi
    done <<< "$verified_owners"
    [ "$still_owned" -eq 0 ] && break
    sleep 0.25
    stop_attempt=$((stop_attempt + 1))
  done

  # Force only processes whose identity and port ownership still match.
  if _service_pid_verified "$pid" "$role" "$expected_cwd" "$port"; then
    kill -9 "$pid" 2>/dev/null || true
  fi
  while IFS= read -r owner; do
    [ -n "$owner" ] && [ "$owner" != "$pid" ] || continue
    if _pid_identity_matches "$owner" "$role" "$expected_cwd" \
        && _pid_directly_owns_port "$owner" "$port"; then
      kill -9 "$owner" 2>/dev/null || true
    fi
  done <<< "$verified_owners"

  sleep 0.05
  still_owned=0
  if _service_pid_verified "$pid" "$role" "$expected_cwd" "$port"; then
    still_owned=1
  fi
  while IFS= read -r owner; do
    [ -n "$owner" ] || continue
    if _pid_identity_matches "$owner" "$role" "$expected_cwd" \
        && _pid_directly_owns_port "$owner" "$port"; then
      still_owned=1
    fi
  done <<< "$verified_owners"
  if [ "$still_owned" -ne 0 ]; then
    echo "ERROR: Verified $label process still owns :$port after TERM/KILL; PID file retained." >&2
    return 1
  fi

  rm -f "$pidfile"
  return 0
}

if [ "$STOP" -eq 1 ]; then
  _require_command lsof || exit 1
  _require_command ps || exit 1
  STOP_FAILED=0
  _stop_from_pidfile "$BACKEND_PID_FILE" "backend" "backend" "$BACKEND_DIR" "$BACKEND_PORT" || STOP_FAILED=1
  _stop_from_pidfile "$FRONTEND_PID_FILE" "frontend" "frontend" "$FRONTEND_DIR" "$FRONTEND_PORT" || STOP_FAILED=1
  if [ "$STOP_FAILED" -ne 0 ]; then
    echo "Stop incomplete: one or more PID files were unsafe to signal." >&2
    exit 1
  fi
  echo "Stopped verified managed services."
  exit 0
fi

_backend_ready() {
  local body
  body="$(curl -fsS --max-time "$HEALTH_CURL_TIMEOUT_SECONDS" "$BACKEND_URL" 2>/dev/null)" || return 1
  "$PYTHON_BIN" -c '
import json
import sys

try:
    payload = json.load(sys.stdin)
except (json.JSONDecodeError, UnicodeDecodeError):
    raise SystemExit(1)
raise SystemExit(
    0
    if isinstance(payload, dict)
    and payload.get("status") == "ok"
    and payload.get("service") == "MiroFish Backend"
    else 1
)
' <<< "$body" >/dev/null 2>&1
}

_frontend_ready() {
  local body
  body="$(curl -fsS --max-time "$HEALTH_CURL_TIMEOUT_SECONDS" "$FRONTEND_URL" 2>/dev/null)" || return 1
  [[ "$body" == *'<title>DeepAgentForecast'* \
    && "$body" == *'name="description" content="DeepAgentForecast'* \
    && "$body" == *'id="app"'* ]]
}

_wait_ready() {
  local readiness_fn="$1" label="$2" timeout_s="$3" managed="$4"
  local pidfile="$5" role="$6" expected_cwd="$7" waited=0 pid
  while [ "$waited" -lt "$timeout_s" ]; do
    "$readiness_fn" && return 0
    if [ "$managed" -eq 1 ]; then
      pid="$(_read_pidfile "$pidfile" || true)"
      if ! _pid_alive "$pid"; then
        echo "ERROR: $label process exited before application readiness; inspect its log." >&2
        return 1
      fi
      if ! _pid_identity_matches "$pid" "$role" "$expected_cwd"; then
        echo "ERROR: $label PID $pid changed command/cwd before readiness; refusing to trust it." >&2
        return 1
      fi
    fi
    sleep 1
    waited=$((waited + 1))
  done
  echo "ERROR: $label did not present the expected application signature within ${timeout_s}s." >&2
  return 1
}

_start_backend() {
  _require_command nohup || return 1
  echo "Starting backend on :$BACKEND_PORT …"
  (
    cd "$BACKEND_DIR" || exit 1
    nohup "$PYTHON_BIN" run.py > "$BACKEND_LOG" 2>&1 < /dev/null &
    printf '%s\n' "$!" > "$BACKEND_PID_FILE"
    disown
  )
}

_start_frontend() {
  _require_command nohup || return 1
  _require_command npm || return 1
  echo "Starting frontend on :$FRONTEND_PORT …"
  (
    cd "$FRONTEND_DIR" || exit 1
    nohup npm run dev > "$FRONTEND_LOG" 2>&1 < /dev/null &
    printf '%s\n' "$!" > "$FRONTEND_PID_FILE"
    disown
  )
}

BACKEND_MANAGED=0
FRONTEND_MANAGED=0
BACKEND_STARTED=0
FRONTEND_STARTED=0

_prepare_backend() {
  local pid
  pid="$(_read_pidfile "$BACKEND_PID_FILE" || true)"
  if _pid_alive "$pid"; then
    if _service_pid_verified "$pid" "backend" "$BACKEND_DIR" "$BACKEND_PORT"; then
      if _backend_ready; then
        echo "Backend already running and application-ready (verified pid $pid)."
        BACKEND_MANAGED=1
        return 0
      fi
      echo "Backend pid $pid owns :$BACKEND_PORT but fails its JSON signature; replacing the verified process."
      _stop_from_pidfile "$BACKEND_PID_FILE" "backend" "backend" "$BACKEND_DIR" "$BACKEND_PORT" || return 1
    elif _pid_identity_matches "$pid" "backend" "$BACKEND_DIR"; then
      echo "ERROR: Backend PID $pid matches command/cwd but does not own :$BACKEND_PORT; refusing to kill it or launch a duplicate." >&2
      return 1
    else
      echo "Ignoring stale/recycled backend PID $pid; command/cwd/port ownership did not verify, so it will not be signalled." >&2
      rm -f "$BACKEND_PID_FILE"
    fi
  else
    [ -f "$BACKEND_PID_FILE" ] && echo "Removing dead/invalid backend PID file."
    rm -f "$BACKEND_PID_FILE"
  fi

  if _backend_ready; then
    echo "Backend is already ready on :$BACKEND_PORT (external process)."
    return 0
  fi
  if _port_has_listener "$BACKEND_PORT"; then
    echo "ERROR: Backend port :$BACKEND_PORT is occupied by a wrong or unhealthy responder; refusing to launch over it." >&2
    return 1
  fi
  _start_backend || return 1
  BACKEND_MANAGED=1
  BACKEND_STARTED=1
}

_prepare_frontend() {
  local pid
  pid="$(_read_pidfile "$FRONTEND_PID_FILE" || true)"
  if _pid_alive "$pid"; then
    if _service_pid_verified "$pid" "frontend" "$FRONTEND_DIR" "$FRONTEND_PORT"; then
      if _frontend_ready; then
        echo "Frontend already running and application-ready (verified pid $pid)."
        FRONTEND_MANAGED=1
        return 0
      fi
      echo "Frontend pid $pid owns :$FRONTEND_PORT but fails its app signature; replacing the verified process."
      _stop_from_pidfile "$FRONTEND_PID_FILE" "frontend" "frontend" "$FRONTEND_DIR" "$FRONTEND_PORT" || return 1
    elif _pid_identity_matches "$pid" "frontend" "$FRONTEND_DIR"; then
      echo "ERROR: Frontend PID $pid matches command/cwd but does not own :$FRONTEND_PORT; refusing to kill it or launch a duplicate." >&2
      return 1
    else
      echo "Ignoring stale/recycled frontend PID $pid; command/cwd/port ownership did not verify, so it will not be signalled." >&2
      rm -f "$FRONTEND_PID_FILE"
    fi
  else
    [ -f "$FRONTEND_PID_FILE" ] && echo "Removing dead/invalid frontend PID file."
    rm -f "$FRONTEND_PID_FILE"
  fi

  if _frontend_ready; then
    echo "Frontend is already ready on :$FRONTEND_PORT (external process)."
    return 0
  fi
  if _port_has_listener "$FRONTEND_PORT"; then
    echo "ERROR: Frontend port :$FRONTEND_PORT is occupied by a wrong or unhealthy responder; refusing to launch over it." >&2
    return 1
  fi
  _start_frontend || return 1
  FRONTEND_MANAGED=1
  FRONTEND_STARTED=1
}

_rollback_started_services() {
  if [ "$FRONTEND_STARTED" -eq 1 ]; then
    _stop_from_pidfile "$FRONTEND_PID_FILE" "frontend" "frontend" "$FRONTEND_DIR" "$FRONTEND_PORT" || true
  fi
  if [ "$BACKEND_STARTED" -eq 1 ]; then
    _stop_from_pidfile "$BACKEND_PID_FILE" "backend" "backend" "$BACKEND_DIR" "$BACKEND_PORT" || true
  fi
}

for required in curl lsof ps; do
  _require_command "$required" || exit 1
done
if [ ! -x "$PYTHON_BIN" ]; then
  echo "ERROR: backend Python is unavailable at $PYTHON_BIN; run setup first." >&2
  exit 1
fi
if ! _is_positive_integer "$BACKEND_TIMEOUT_SECONDS" \
    || ! _is_positive_integer "$FRONTEND_TIMEOUT_SECONDS" \
    || ! _is_positive_integer "$HEALTH_CURL_TIMEOUT_SECONDS" \
    || ! _is_nonnegative_integer "$HEALTH_FAILURE_GRACE_SECONDS" \
    || ! _is_nonnegative_integer "$FOLLOWER_RESTART_LIMIT"; then
  echo "ERROR: startup/curl timeouts must be positive integers; grace/restart settings must be non-negative integers." >&2
  exit 2
fi
if ! _is_positive_integer "$BACKEND_PORT" || [ "$BACKEND_PORT" -gt 65535 ]; then
  echo "ERROR: FLASK_PORT must be an integer from 1 through 65535." >&2
  exit 2
fi
if ! "$PYTHON_BIN" -c 'import math, sys; value = float(sys.argv[1]); raise SystemExit(0 if math.isfinite(value) and value > 0 else 1)' \
    "$MONITOR_INTERVAL_SECONDS"; then
  echo "ERROR: START_MONITOR_INTERVAL_SECONDS must be a positive finite number." >&2
  exit 2
fi

if [ "$FOLLOW" -eq 1 ]; then
  _require_command tail || exit 1
  if [ ! -r "$WATCHER_SCRIPT" ]; then
    echo "ERROR: workflow progress watcher is unavailable at $WATCHER_SCRIPT." >&2
    exit 1
  fi
  if ! "$PYTHON_BIN" "$WATCHER_SCRIPT" --pipelines-dir "$PIPELINES_DIR" --once >/dev/null 2>&1; then
    echo "ERROR: workflow progress watcher preflight failed; no services were started." >&2
    exit 1
  fi
  : >> "$BACKEND_LOG"
  : >> "$FRONTEND_LOG"
fi

# --- Backend ---------------------------------------------------------------
_prepare_backend || exit 1

# --- Frontend ----------------------------------------------------------------
if ! _prepare_frontend; then
  _rollback_started_services
  exit 1
fi

echo "Waiting for services to come up…"
STARTUP_FAILED=0
_wait_ready _backend_ready "Backend" "$BACKEND_TIMEOUT_SECONDS" "$BACKEND_MANAGED" \
  "$BACKEND_PID_FILE" "backend" "$BACKEND_DIR" || STARTUP_FAILED=1
_wait_ready _frontend_ready "Frontend" "$FRONTEND_TIMEOUT_SECONDS" "$FRONTEND_MANAGED" \
  "$FRONTEND_PID_FILE" "frontend" "$FRONTEND_DIR" || STARTUP_FAILED=1

if [ "$STARTUP_FAILED" -eq 0 ] && [ "$BACKEND_MANAGED" -eq 1 ]; then
  backend_pid="$(_read_pidfile "$BACKEND_PID_FILE" || true)"
  if ! _service_pid_verified "$backend_pid" "backend" "$BACKEND_DIR" "$BACKEND_PORT"; then
    echo "ERROR: Backend became ready, but its PID no longer owns the expected listener." >&2
    STARTUP_FAILED=1
  fi
fi
if [ "$STARTUP_FAILED" -eq 0 ] && [ "$FRONTEND_MANAGED" -eq 1 ]; then
  frontend_pid="$(_read_pidfile "$FRONTEND_PID_FILE" || true)"
  if ! _service_pid_verified "$frontend_pid" "frontend" "$FRONTEND_DIR" "$FRONTEND_PORT"; then
    echo "ERROR: Frontend became ready, but its PID no longer owns the expected listener." >&2
    STARTUP_FAILED=1
  fi
fi

if [ "$STARTUP_FAILED" -ne 0 ]; then
  echo "Startup failed. Recent service output:" >&2
  tail -n 60 "$BACKEND_LOG" "$FRONTEND_LOG" 2>/dev/null >&2 || true
  _rollback_started_services
  exit 1
fi

echo ""
echo "Backend:  $BACKEND_URL   (log: logs/backend.out.log,  pid file: .backend.pid)"
echo "Frontend: $FRONTEND_URL  (log: logs/frontend.out.log, pid file: .frontend.pid)"
echo "Stop both with: bash scripts/start.sh --stop"

if [ "$NO_OPEN" -eq 0 ]; then
  case "$(uname -s)" in
    Darwin) open "$FRONTEND_URL" 2>/dev/null ;;
    Linux) xdg-open "$FRONTEND_URL" 2>/dev/null || true ;;
    *) echo "Open $FRONTEND_URL manually." ;;
  esac
fi

if [ "$FOLLOW" -eq 0 ]; then
  echo "Detached mode: services are ready and logs remain under logs/."
  exit 0
fi

echo ""
echo "Streaming service logs and workflow stages (Ctrl-C stops streaming; services stay up)."
echo "Stage marks: ▶ running  ✓ completed  ✕ failed  ↻ resuming  ■ cancelled"

FOLLOW_INTERRUPTED=0
LOG_FOLLOWER_PID=""
WATCHER_PID=""
LOG_FOLLOWER_RESTARTS=0
WATCHER_RESTARTS=0

_child_job_running() {
  local target="$1" job_pid
  while IFS= read -r job_pid; do
    [ "$job_pid" = "$target" ] && return 0
  done < <(jobs -pr)
  return 1
}

_start_log_follower() {
  tail -n 80 -F "$BACKEND_LOG" "$FRONTEND_LOG" 2>/dev/null &
  LOG_FOLLOWER_PID="$!"
}

_start_progress_watcher() {
  "$PYTHON_BIN" "$WATCHER_SCRIPT" --pipelines-dir "$PIPELINES_DIR" &
  WATCHER_PID="$!"
}

_cleanup_followers() {
  if [ -n "$LOG_FOLLOWER_PID" ] && _child_job_running "$LOG_FOLLOWER_PID"; then
    kill "$LOG_FOLLOWER_PID" 2>/dev/null || true
  fi
  if [ -n "$WATCHER_PID" ] && _child_job_running "$WATCHER_PID"; then
    kill "$WATCHER_PID" 2>/dev/null || true
  fi
  [ -n "$LOG_FOLLOWER_PID" ] && wait "$LOG_FOLLOWER_PID" 2>/dev/null || true
  [ -n "$WATCHER_PID" ] && wait "$WATCHER_PID" 2>/dev/null || true
  if [ "$FOLLOW_INTERRUPTED" -eq 1 ]; then
    echo ""
    echo "Streaming stopped; backend and frontend remain running. Use 'npm stop' to stop them."
  fi
}
_interrupt_follow() {
  FOLLOW_INTERRUPTED=1
  exit 130
}
trap _interrupt_follow INT TERM
trap _cleanup_followers EXIT

_start_log_follower
_start_progress_watcher

BACKEND_UNHEALTHY_SINCE=""
FRONTEND_UNHEALTHY_SINCE=""

while true; do
  if ! _child_job_running "$LOG_FOLLOWER_PID"; then
    follower_status=0
    wait "$LOG_FOLLOWER_PID" || follower_status=$?
    if [ "$LOG_FOLLOWER_RESTARTS" -lt "$FOLLOWER_RESTART_LIMIT" ]; then
      LOG_FOLLOWER_RESTARTS=$((LOG_FOLLOWER_RESTARTS + 1))
      echo "[stream] WARN: service log follower exited with status $follower_status; restarting ($LOG_FOLLOWER_RESTARTS/$FOLLOWER_RESTART_LIMIT)." >&2
      _start_log_follower
    else
      echo "[stream] ✕ service log follower exited repeatedly; live service logs are unavailable." >&2
      exit 1
    fi
  fi
  if ! _child_job_running "$WATCHER_PID"; then
    watcher_status=0
    wait "$WATCHER_PID" || watcher_status=$?
    if [ "$WATCHER_RESTARTS" -lt "$FOLLOWER_RESTART_LIMIT" ]; then
      WATCHER_RESTARTS=$((WATCHER_RESTARTS + 1))
      echo "[stream] WARN: workflow progress watcher exited with status $watcher_status; restarting ($WATCHER_RESTARTS/$FOLLOWER_RESTART_LIMIT)." >&2
      _start_progress_watcher
    else
      echo "[stream] ✕ workflow progress watcher exited repeatedly; stage streaming is unavailable." >&2
      exit 1
    fi
  fi

  if [ "$BACKEND_MANAGED" -eq 1 ]; then
    backend_pid="$(_read_pidfile "$BACKEND_PID_FILE" || true)"
    if ! _service_pid_verified "$backend_pid" "backend" "$BACKEND_DIR" "$BACKEND_PORT"; then
      echo "[service] ✕ BACKEND PID identity/listener ownership changed; refusing to trust it. Inspect logs/backend.out.log." >&2
      exit 1
    fi
  fi
  if [ "$FRONTEND_MANAGED" -eq 1 ]; then
    frontend_pid="$(_read_pidfile "$FRONTEND_PID_FILE" || true)"
    if ! _service_pid_verified "$frontend_pid" "frontend" "$FRONTEND_DIR" "$FRONTEND_PORT"; then
      echo "[service] ✕ FRONTEND PID identity/listener ownership changed; refusing to trust it. Inspect logs/frontend.out.log." >&2
      exit 1
    fi
  fi

  now="$(date +%s)"
  if _backend_ready; then
    if [ -n "$BACKEND_UNHEALTHY_SINCE" ]; then
      echo "[service] ✓ Backend application readiness recovered."
    fi
    BACKEND_UNHEALTHY_SINCE=""
  else
    if [ -z "$BACKEND_UNHEALTHY_SINCE" ]; then
      BACKEND_UNHEALTHY_SINCE="$now"
      echo "[service] WARN: Backend failed its JSON readiness signature; allowing ${HEALTH_FAILURE_GRACE_SECONDS}s grace." >&2
    fi
    backend_unhealthy_for=$((now - BACKEND_UNHEALTHY_SINCE))
    if [ "$backend_unhealthy_for" -ge "$HEALTH_FAILURE_GRACE_SECONDS" ]; then
      echo "[service] ✕ Backend application readiness failed for ${backend_unhealthy_for}s; exiting attached mode (service is hung, unreachable, or the wrong app)." >&2
      exit 1
    fi
  fi

  if _frontend_ready; then
    if [ -n "$FRONTEND_UNHEALTHY_SINCE" ]; then
      echo "[service] ✓ Frontend application readiness recovered."
    fi
    FRONTEND_UNHEALTHY_SINCE=""
  else
    if [ -z "$FRONTEND_UNHEALTHY_SINCE" ]; then
      FRONTEND_UNHEALTHY_SINCE="$now"
      echo "[service] WARN: Frontend failed its DeepAgentForecast HTML signature; allowing ${HEALTH_FAILURE_GRACE_SECONDS}s grace." >&2
    fi
    frontend_unhealthy_for=$((now - FRONTEND_UNHEALTHY_SINCE))
    if [ "$frontend_unhealthy_for" -ge "$HEALTH_FAILURE_GRACE_SECONDS" ]; then
      echo "[service] ✕ Frontend application readiness failed for ${frontend_unhealthy_for}s; exiting attached mode (service is hung, unreachable, or the wrong app)." >&2
      exit 1
    fi
  fi

  sleep "$MONITOR_INTERVAL_SECONDS"
done
