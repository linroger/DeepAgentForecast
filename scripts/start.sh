#!/usr/bin/env bash
#
# start.sh — spin up backend + frontend and open the local site.
# ---------------------------------------------------------------------------
# Launches both dev servers DETACHED (nohup + disown), polls each port until
# healthy, then opens http://localhost:3000 in the default browser.
#
# Detached launch matters: a backend started via a foreground/tracked
# background shell call can get reaped when that call's process group is
# torn down (observed repeatedly in this project's run history — see
# handoff.md 2026-07-02/07-03 entries). nohup + disown + redirected I/O
# survives that.
#
#   bash scripts/start.sh            # start both, open browser
#   bash scripts/start.sh --no-open  # start both, skip opening the browser
#   bash scripts/start.sh --stop     # stop both (by pid file)
#
# Logs: logs/backend.out.log, logs/frontend.out.log (repo-root logs/, created
# if absent). PID files: .backend.pid, .frontend.pid (repo root, gitignored).
# ---------------------------------------------------------------------------
set -u

SCRIPT_SOURCE="${BASH_SOURCE[0]}"
ROOT_DIR="$(cd "$(dirname "$SCRIPT_SOURCE")/.." && pwd)"
cd "$ROOT_DIR"

LOG_DIR="$ROOT_DIR/logs"
mkdir -p "$LOG_DIR"
BACKEND_PID_FILE="$ROOT_DIR/.backend.pid"
FRONTEND_PID_FILE="$ROOT_DIR/.frontend.pid"
BACKEND_PORT="${FLASK_PORT:-5001}"
FRONTEND_PORT=3000
BACKEND_URL="http://localhost:${BACKEND_PORT}/health"
FRONTEND_URL="http://localhost:${FRONTEND_PORT}/"

NO_OPEN=0
STOP=0
for arg in "$@"; do
  case "$arg" in
    --no-open) NO_OPEN=1 ;;
    --stop) STOP=1 ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done

_pid_alive() {
  local pid="$1"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

_stop_from_pidfile() {
  local pidfile="$1" label="$2"
  if [ -f "$pidfile" ]; then
    local pid
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if _pid_alive "$pid"; then
      echo "Stopping $label (pid $pid)…"
      kill "$pid" 2>/dev/null
      for _ in $(seq 1 10); do
        _pid_alive "$pid" || break
        sleep 0.5
      done
      _pid_alive "$pid" && kill -9 "$pid" 2>/dev/null
    fi
    rm -f "$pidfile"
  fi
}

if [ "$STOP" -eq 1 ]; then
  _stop_from_pidfile "$BACKEND_PID_FILE" "backend"
  _stop_from_pidfile "$FRONTEND_PID_FILE" "frontend"
  echo "Stopped."
  exit 0
fi

_wait_http() {
  local url="$1" label="$2" timeout_s="$3"
  local waited=0
  while [ "$waited" -lt "$timeout_s" ]; do
    if curl -fsS -o /dev/null --max-time 2 "$url" 2>/dev/null; then
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  echo "WARN: $label did not answer $url within ${timeout_s}s (it may still be starting — check logs)." >&2
  return 1
}

# --- Backend ---------------------------------------------------------------
if [ -f "$BACKEND_PID_FILE" ] && _pid_alive "$(cat "$BACKEND_PID_FILE" 2>/dev/null)"; then
  echo "Backend already running (pid $(cat "$BACKEND_PID_FILE"))."
else
  echo "Starting backend on :$BACKEND_PORT …"
  (
    cd "$ROOT_DIR/backend"
    nohup .venv/bin/python run.py > "$LOG_DIR/backend.out.log" 2>&1 < /dev/null &
    echo $! > "$BACKEND_PID_FILE"
    disown
  )
fi

# --- Frontend ----------------------------------------------------------------
if [ -f "$FRONTEND_PID_FILE" ] && _pid_alive "$(cat "$FRONTEND_PID_FILE" 2>/dev/null)"; then
  echo "Frontend already running (pid $(cat "$FRONTEND_PID_FILE"))."
else
  echo "Starting frontend on :$FRONTEND_PORT …"
  (
    cd "$ROOT_DIR/frontend"
    nohup npm run dev > "$LOG_DIR/frontend.out.log" 2>&1 < /dev/null &
    echo $! > "$FRONTEND_PID_FILE"
    disown
  )
fi

echo "Waiting for services to come up…"
_wait_http "$BACKEND_URL" "backend" 60
_wait_http "$FRONTEND_URL" "frontend" 30

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
