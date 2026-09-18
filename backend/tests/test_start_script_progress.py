import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import textwrap
import time

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "watch_pipeline_progress",
    ROOT / "scripts" / "watch_pipeline_progress.py",
)
assert SPEC and SPEC.loader
progress = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(progress)

EXPECTED_BACKEND_BODY = '{"service":"MiroFish Backend","status":"ok"}'
EXPECTED_FRONTEND_BODY = """<!doctype html>
<html>
  <head>
    <meta name="description" content="DeepResearchForecast — test app" />
    <title>DeepResearchForecast · test app</title>
  </head>
  <body><div id="app"></div></body>
</html>
"""


def _write_executable(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    path.chmod(0o755)


def _startup_project(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """Build an isolated start.sh fixture with deterministic HTTP/process probes."""
    root = tmp_path / "project"
    (root / "scripts").mkdir(parents=True)
    (root / "backend" / ".venv" / "bin").mkdir(parents=True)
    (root / "backend" / "uploads" / "pipelines").mkdir(parents=True)
    (root / "frontend").mkdir()
    (root / "logs").mkdir()
    (root / "test-bin").mkdir()

    shutil.copy2(ROOT / "scripts" / "start.sh", root / "scripts" / "start.sh")
    shutil.copy2(
        ROOT / "scripts" / "watch_pipeline_progress.py",
        root / "scripts" / "watch_pipeline_progress.py",
    )
    (root / "backend" / "run.py").write_text("", encoding="utf-8")
    (root / "frontend" / "package.json").write_text("{}\n", encoding="utf-8")

    _write_executable(
        root / "backend" / ".venv" / "bin" / "python",
        f"""
        #!/usr/bin/env bash
        set -u
        joined=" $* "
        if [[ "$joined" == *"watch_pipeline_progress.py"* ]] \
            && [[ "$joined" != *" --once "* ]]; then
          if [ -n "${{FAKE_HEALTH_FAIL_MARKER:-}}" ]; then
            : > "$FAKE_HEALTH_FAIL_MARKER"
          fi
          if [ "${{FAKE_WATCHER_CRASH:-0}}" = "1" ]; then
            exit 23
          fi
        fi
        exec {shlex.quote(sys.executable)} "$@"
        """,
    )

    _write_executable(
        root / "test-bin" / "curl",
        """
        #!/usr/bin/env bash
        set -u
        url="${!#}"
        case "$url" in
          *:"${FAKE_BACKEND_PORT:-5001}"/health)
            body="${FAKE_BACKEND_BODY:-}"
            if [ -n "${FAKE_HEALTH_FAIL_MARKER:-}" ] \
                && [ -f "$FAKE_HEALTH_FAIL_MARKER" ]; then
              body="${FAKE_BACKEND_WRONG_BODY:-not-the-backend}"
            fi
            ;;
          *:3000/)
            body="${FAKE_FRONTEND_BODY:-}"
            ;;
          *:3000/health)
            body="${FAKE_FRONTEND_HEALTH_BODY:-}"
            ;;
          *:3000/__drf/dev-routing)
            if [ "${FAKE_ROUTING_BODY+x}" = "x" ]; then
              body="$FAKE_ROUTING_BODY"
            else
              selected="${FAKE_BACKEND_PORT:-5001}"
              body='{"schema":"drf-dev-routing/v1","service":"DeepResearchForecast Frontend","api_target":"http://127.0.0.1:'"$selected"'","health_target":"http://127.0.0.1:'"$selected"'"}'
            fi
            ;;
          *)
            exit 22
            ;;
        esac
        printf '%s\n' "$body"
        """,
    )

    real_lsof = shutil.which("lsof")
    assert real_lsof
    _write_executable(
        root / "test-bin" / "lsof",
        f"""
        #!/usr/bin/env bash
        set -u
        case " $* " in
          *" -iTCP:5001 "*)
            [ -n "${{FAKE_LISTEN_5001_PID:-}}" ] || exit 1
            printf '%s\n' "$FAKE_LISTEN_5001_PID"
            ;;
          *" -iTCP:3000 "*)
            [ -n "${{FAKE_LISTEN_3000_PID:-}}" ] || exit 1
            printf '%s\n' "$FAKE_LISTEN_3000_PID"
            ;;
          *)
            exec {shlex.quote(real_lsof)} "$@"
            ;;
        esac
        """,
    )

    env = os.environ.copy()
    env.pop("FLASK_PORT", None)
    env.pop("DRF_LAUNCHER_BACKEND_PORT", None)
    env.update(
        {
            "PATH": f"{root / 'test-bin'}:{env['PATH']}",
            "FAKE_BACKEND_BODY": EXPECTED_BACKEND_BODY,
            "FAKE_FRONTEND_BODY": EXPECTED_FRONTEND_BODY,
            "FAKE_FRONTEND_HEALTH_BODY": EXPECTED_BACKEND_BODY,
            "START_MONITOR_INTERVAL_SECONDS": "0.05",
        }
    )
    return root, env


def _run_start(
    root: Path,
    env: dict[str, str],
    *args: str,
    timeout: float = 6,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(root / "scripts" / "start.sh"), *args],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _output(result: subprocess.CompletedProcess[str]) -> str:
    return f"{result.stdout}\n{result.stderr}"


def _state(status="running", **stages):
    return {
        "pipeline_id": "pipe_test",
        "status": status,
        "stages": stages,
    }


def test_progress_events_mark_stage_transitions_and_progress_buckets():
    initial = _state(
        research={"status": "running", "progress": 12, "message": "searching"},
        ontology={"status": "pending", "progress": 0},
    )
    events = progress.progress_events(None, initial, "pipe_test")
    assert events == [
        "[workflow pipe_test] ◆ PIPELINE running",
        "[workflow pipe_test] ▶ RESEARCH 10% — searching",
    ]

    same_bucket = _state(
        research={"status": "running", "progress": 14, "message": "searching"},
        ontology={"status": "pending", "progress": 0},
    )
    assert progress.progress_events(initial, same_bucket, "pipe_test") == []

    next_stage = _state(
        research={"status": "completed", "progress": 100, "message": "dossier ready"},
        ontology={"status": "running", "progress": 5, "message": "extracting"},
    )
    assert progress.progress_events(same_bucket, next_stage, "pipe_test") == [
        "[workflow pipe_test] ✓ RESEARCH 100% — dossier ready",
        "[workflow pipe_test] ▶ ONTOLOGY 5% — extracting",
    ]


def test_progress_events_surface_stage_and_pipeline_failures():
    previous = _state(report={"status": "running", "progress": 80})
    failed = _state(
        status="failed",
        report={"status": "failed", "progress": 80, "error": "quality gate failed"},
    )
    failed["error"] = "report publication rejected"

    assert progress.progress_events(previous, failed, "pipe_test") == [
        "[workflow pipe_test] ✕ REPORT 80% — quality gate failed",
        "[workflow pipe_test] ✕ PIPELINE failed — report publication rejected",
    ]


def test_watcher_ignores_completed_history_then_discovers_new_pipeline(tmp_path):
    old_dir = tmp_path / "pipe_old"
    old_dir.mkdir()
    (old_dir / "pipeline_state.json").write_text(
        '{"pipeline_id":"pipe_old","status":"completed","stages":{}}',
        encoding="utf-8",
    )
    watcher = progress.PipelineProgressWatcher(tmp_path)
    assert watcher.poll(initial=True) == []

    new_dir = tmp_path / "pipe_new"
    new_dir.mkdir()
    (new_dir / "pipeline_state.json").write_text(
        '{"pipeline_id":"pipe_new","status":"running","stages":'
        '{"research":{"status":"running","progress":1}}}',
        encoding="utf-8",
    )
    assert watcher.poll() == [
        "[workflow pipe_new] ◆ PIPELINE running",
        "[workflow pipe_new] ▶ RESEARCH 0%",
    ]


def test_vite_dev_server_uses_strict_port():
    config = (ROOT / "frontend" / "vite.config.js").read_text(encoding="utf-8")
    assert "strictPort: true" in config


@pytest.mark.parametrize("port", [None, "18745"])
@pytest.mark.parametrize("no_open", [False, True])
def test_start_opens_root_once_only_after_ui_and_api_ready(tmp_path, port, no_open):
    root, env = _startup_project(tmp_path)
    browser_log = root / "browser-opens.txt"
    env["FAKE_BROWSER_LOG"] = str(browser_log)
    for name in ("open", "xdg-open"):
        _write_executable(
            root / "test-bin" / name,
            '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$FAKE_BROWSER_LOG"\n',
        )
    if port is not None:
        env["FLASK_PORT"] = port
        env["FAKE_BACKEND_PORT"] = port

    args = ["--detach"] + (["--no-open"] if no_open else [])
    result = _run_start(root, env, *args)

    assert result.returncode == 0, _output(result)
    assert f"http://localhost:{port or '5001'}/health" in result.stdout
    if no_open:
        assert not browser_log.exists()
    else:
        assert browser_log.read_text().splitlines() == ["http://localhost:3000/"]


def test_start_rejects_ui_shell_with_wrong_proxy_backend(tmp_path):
    root, env = _startup_project(tmp_path)
    env["FAKE_FRONTEND_HEALTH_BODY"] = '{"status":"ok","service":"unrelated"}'
    env["FAKE_LISTEN_3000_PID"] = "4343"

    result = _run_start(root, env, "--detach", "--no-open")

    assert result.returncode == 1
    assert "Frontend port :3000 is occupied by a wrong or unhealthy responder" in _output(result)
    assert not (root / ".frontend.pid").exists()


@pytest.mark.parametrize("wrong_route", ["api_target", "health_target", "both"])
def test_start_rejects_same_product_proxy_to_another_backend(tmp_path, wrong_route):
    root, env = _startup_project(tmp_path)
    env["FLASK_PORT"] = env["FAKE_BACKEND_PORT"] = "18745"
    env["FAKE_LISTEN_3000_PID"] = "4343"
    routing = {
        "schema": "drf-dev-routing/v1",
        "service": "DeepResearchForecast Frontend",
        "api_target": "http://127.0.0.1:18745",
        "health_target": "http://127.0.0.1:18745",
    }
    for route in ("api_target", "health_target"):
        if wrong_route in (route, "both"):
            routing[route] = "http://127.0.0.1:5001"
    env["FAKE_ROUTING_BODY"] = json.dumps(routing)
    # Both health endpoints intentionally retain the correct product signature.
    result = _run_start(root, env, "--detach", "--no-open")

    assert result.returncode == 1
    assert "Frontend port :3000 is occupied by a wrong or unhealthy responder" in _output(result)
    assert not (root / ".frontend.pid").exists()


@pytest.mark.parametrize("body", ["", "not-json", "null", "[]", "{}", '{"schema":"other"}',
    '{"schema":"drf-dev-routing/v1","service":"DeepResearchForecast Frontend","api_target":null,"health_target":"http://127.0.0.1:5001"}'])
def test_start_rejects_absent_or_malformed_proxy_identity(tmp_path, body):
    root, env = _startup_project(tmp_path)
    env["FAKE_LISTEN_3000_PID"] = "4343"
    env["FAKE_ROUTING_BODY"] = body

    result = _run_start(root, env, "--detach", "--no-open")

    assert result.returncode == 1
    assert "Frontend port :3000 is occupied by a wrong or unhealthy responder" in _output(result)
    assert not (root / ".frontend.pid").exists()


@pytest.mark.parametrize("port", ["0", "65536", "-1", "5001.5", " 5001", "invalid", "99999999999999999999999999"])
def test_start_rejects_invalid_backend_port_before_starting_services(tmp_path, port):
    root, env = _startup_project(tmp_path)
    env["FLASK_PORT"] = port

    result = _run_start(root, env, "--detach", "--no-open")

    assert result.returncode == 2
    assert "FLASK_PORT must be an integer from 1 through 65535" in _output(result)
    assert not (root / ".backend.pid").exists()
    assert not (root / ".frontend.pid").exists()


@pytest.mark.parametrize("port", ["5001", "18745"])
def test_both_launched_services_receive_the_resolved_port(tmp_path, port):
    root, env = _startup_project(tmp_path)
    capture = root / "service-env.txt"
    env["FAKE_SERVICE_ENV"] = str(capture)
    # Run the actual launch functions with an inert nohup replacement. No app
    # server or socket is created, and no inherited provider configuration is read.
    _write_executable(
        root / "test-bin" / "nohup",
        '#!/usr/bin/env bash\nprintf "%s|%s|%s\\n" "$PWD" "$FLASK_PORT" "$DRF_LAUNCHER_BACKEND_PORT" >> "$FAKE_SERVICE_ENV"\n',
    )
    script = (ROOT / "scripts" / "start.sh").read_text()
    functions = []
    for name in ("_start_backend", "_start_frontend"):
        start = script.index(f"{name}() {{")
        end = script.index("\n}", start) + 2
        functions.append(script[start:end])
    variables = {
        "BACKEND_PORT": port,
        "FRONTEND_PORT": "3000",
        "BACKEND_DIR": str(root / "backend"),
        "FRONTEND_DIR": str(root / "frontend"),
        "BACKEND_LOG": str(root / "logs" / "backend.log"),
        "FRONTEND_LOG": str(root / "logs" / "frontend.log"),
        "BACKEND_PID_FILE": str(root / ".backend.pid"),
        "FRONTEND_PID_FILE": str(root / ".frontend.pid"),
        "PYTHON_BIN": str(root / "backend" / ".venv" / "bin" / "python"),
    }
    lines = [f"{key}={shlex.quote(value)}" for key, value in variables.items()]
    lines += ["_require_command() { return 0; }", *functions, "_start_backend", "_start_frontend"]
    result = subprocess.run(["bash", "-c", "\n".join(lines)], env=env, cwd=root, text=True, capture_output=True, check=False)
    assert result.returncode == 0, _output(result)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if capture.exists() and len(capture.read_text().splitlines()) == 2:
            break
        time.sleep(0.01)
    assert sorted(capture.read_text().splitlines()) == sorted([
        f"{root / 'backend'}|{port}|{port}",
        f"{root / 'frontend'}|{port}|{port}",
    ])


def test_start_rejects_wrong_backend_health_on_an_occupied_port(tmp_path):
    root, env = _startup_project(tmp_path)
    env["FAKE_BACKEND_BODY"] = '{"status":"ok","service":"unrelated"}'
    env["FAKE_LISTEN_5001_PID"] = "4242"

    result = _run_start(root, env, "--detach", "--no-open")

    assert result.returncode == 1
    assert "Backend port :5001 is occupied by a wrong or unhealthy responder" in _output(result)
    assert not (root / ".backend.pid").exists()


def test_start_rejects_generic_frontend_responder_on_an_occupied_port(tmp_path):
    root, env = _startup_project(tmp_path)
    env["FAKE_FRONTEND_BODY"] = "<html><body>some other app</body></html>"
    env["FAKE_LISTEN_3000_PID"] = "4343"

    result = _run_start(root, env, "--detach", "--no-open")

    assert result.returncode == 1
    assert "Backend is already ready on :5001 (external process)." in _output(result)
    assert "Frontend port :3000 is occupied by a wrong or unhealthy responder" in _output(result)
    assert not (root / ".frontend.pid").exists()


def test_stop_refuses_to_signal_an_unrelated_recycled_pid(tmp_path):
    root, env = _startup_project(tmp_path)
    unrelated = subprocess.Popen(["/bin/sleep", "30"], cwd=root)
    try:
        (root / ".backend.pid").write_text(f"{unrelated.pid}\n", encoding="utf-8")

        result = _run_start(root, env, "--stop")

        assert result.returncode == 1
        assert unrelated.poll() is None
        assert f"Refusing to signal backend PID {unrelated.pid}" in _output(result)
        assert not (root / ".backend.pid").exists()
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=3)


def test_attached_mode_restarts_then_reports_a_crashed_progress_watcher(tmp_path):
    root, env = _startup_project(tmp_path)
    env["FAKE_WATCHER_CRASH"] = "1"
    env["START_FOLLOWER_RESTART_LIMIT"] = "1"

    result = _run_start(root, env, "--no-open")

    output = _output(result)
    assert result.returncode == 1
    assert "workflow progress watcher exited with status 23; restarting (1/1)" in output
    assert "workflow progress watcher exited repeatedly; stage streaming is unavailable" in output


def test_attached_mode_monitors_external_service_health(tmp_path):
    root, env = _startup_project(tmp_path)
    marker = tmp_path / "health-fails-after-watcher-starts"
    env["FAKE_HEALTH_FAIL_MARKER"] = str(marker)
    env["FAKE_BACKEND_WRONG_BODY"] = '{"status":"ok","service":"unrelated"}'
    env["START_HEALTH_FAILURE_GRACE_SECONDS"] = "0"

    result = _run_start(root, env, "--no-open")

    output = _output(result)
    assert result.returncode == 1
    assert "Backend is already ready on :5001 (external process)." in output
    assert "Frontend is already ready on :3000 (external process)." in output
    assert "Backend application readiness failed for 0s; exiting attached mode" in output
