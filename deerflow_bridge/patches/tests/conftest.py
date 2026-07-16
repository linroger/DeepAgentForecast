"""Isolate DeerFlow's known import cycle for overlay regression tests."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "deer-flow" / "backend" / "packages" / "harness"))
sys.path.insert(0, str(ROOT / "deer-flow" / "backend"))

# Match DeerFlow's own tests/conftest.py boundary. Individual executor tests
# temporarily remove this shim and load the real module with its dependencies
# isolated, avoiding the production package's circular eager imports.
executor_mock = MagicMock()
executor_mock.SubagentExecutor = MagicMock
executor_mock.SubagentResult = MagicMock
executor_mock.SubagentStatus = MagicMock
executor_mock.MAX_CONCURRENT_SUBAGENTS = 3
executor_mock.get_background_task_result = MagicMock()
sys.modules["deerflow.subagents.executor"] = executor_mock
