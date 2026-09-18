"""The real backend entry point must honor the pre-dotenv launcher port."""
import importlib.util
from pathlib import Path
import sys
import types

import pytest


RUN = Path(__file__).resolve().parents[1] / "run.py"


def _entry(monkeypatch, launcher_port):
    if launcher_port is None:
        monkeypatch.delenv("DRF_LAUNCHER_BACKEND_PORT", raising=False)
    else:
        monkeypatch.setenv("DRF_LAUNCHER_BACKEND_PORT", launcher_port)
    monkeypatch.setenv("FLASK_PORT", "5001")
    monkeypatch.setenv("FLASK_HOST", "127.0.0.1")
    calls = {"create": 0, "run": [], "autorun": 0}

    class Server:
        def run(self, **kwargs):
            calls["run"].append(kwargs)

    def create_app():
        calls["create"] += 1
        return Server()

    class AppModule(types.ModuleType):
        def __getattr__(self, name):
            if name == "create_app":
                # Simulate Config import's override=True dotenv behavior.
                monkeypatch.setenv("FLASK_PORT", "6100")
                monkeypatch.setenv("DRF_LAUNCHER_BACKEND_PORT", "6200")
                return create_app
            raise AttributeError(name)

    app = AppModule("app")
    app.__path__ = []
    config = types.ModuleType("app.config")
    config.Config = types.SimpleNamespace(validate=lambda: [], DEBUG=False, APP_API_TOKEN="")
    services = types.ModuleType("app.services")
    services.__path__ = []
    autorun = types.ModuleType("app.services.resolution_autorun")

    def start_autorun():
        calls["autorun"] += 1

    autorun.start = start_autorun
    for name, module in [("app", app), ("app.config", config),
                         ("app.services", services),
                         ("app.services.resolution_autorun", autorun)]:
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(sys, "path", list(sys.path))
    spec = importlib.util.spec_from_file_location("drf_test_launcher_entry", RUN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, calls


@pytest.mark.parametrize("selected", ["1", "5231", "65535"])
def test_launcher_port_survives_config_environment_override(monkeypatch, selected):
    entry, calls = _entry(monkeypatch, selected)
    entry.main()
    assert calls["create"] == 1
    assert calls["run"] == [{"host": "127.0.0.1", "port": int(selected),
                              "debug": False, "threaded": True}]


def test_direct_backend_keeps_post_dotenv_flask_port(monkeypatch):
    entry, calls = _entry(monkeypatch, None)
    entry.main()
    assert calls["run"][0]["port"] == 6100


@pytest.mark.parametrize("selected", ["", "0", "65536", "abc", "1.5", "１２３"])
def test_invalid_launcher_port_stops_before_app_creation(monkeypatch, selected):
    entry, calls = _entry(monkeypatch, selected)
    with pytest.raises(SystemExit) as error:
        entry.main()
    assert error.value.code == 1
    assert calls == {"create": 0, "run": [], "autorun": 0}
