#!/usr/bin/env python3
"""Foglamp WP0A on-timeout process diagnostics helper.

Given --pid (default: this process), prints a JSON document:
  {pid, cmdline, children: [recursive {pid, cmdline, children}], pythonFaultStacks}

Children come from parsing `ps -o pid=,ppid=,command= -ax` (macOS/Linux, no
psutil). The helper never signals or kills anything unless --signal is passed
explicitly; even then it only sends SIGUSR1, which is harmless unless the
target cooperatively registered a faulthandler for it.

`--install-faulthandler-snippet` prints the two-line snippet a test harness
should add so SIGUSR1 dumps Python stacks to the target's stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys

FAULTHANDLER_SNIPPET = "import faulthandler, signal\nfaulthandler.register(signal.SIGUSR1)"
NO_HANDLER_REASON = "no cooperative fault handler; use faulthandler in target"


def _read_process_table() -> dict[int, tuple[int, str]]:
    """Return {pid: (ppid, command)} from ps output."""
    result = subprocess.run(
        ["ps", "-o", "pid=,ppid=,command=", "-ax"],
        capture_output=True,
        text=True,
        check=False,
    )
    table: dict[int, tuple[int, str]] = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        command = parts[2] if len(parts) == 3 else ""
        table[pid] = (ppid, command)
    return table


def _children_of(pid: int, table: dict[int, tuple[int, str]]) -> list[dict[str, object]]:
    child_pids = sorted(child for child, (ppid, _cmd) in table.items() if ppid == pid)
    return [
        {
            "children": _children_of(child, table),
            "cmdline": table[child][1],
            "pid": child,
        }
        for child in child_pids
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="process_diagnostics", description=__doc__)
    parser.add_argument("--pid", type=int, default=None, help="target pid (default: this process)")
    parser.add_argument(
        "--signal",
        action="store_true",
        help="send SIGUSR1 to the target so a registered faulthandler dumps Python stacks",
    )
    parser.add_argument(
        "--install-faulthandler-snippet",
        action="store_true",
        help="print the snippet a test harness should add, then exit",
    )
    args = parser.parse_args(argv)

    if args.install_faulthandler_snippet:
        print(FAULTHANDLER_SNIPPET)
        return 0

    pid = args.pid if args.pid is not None else os.getpid()
    table = _read_process_table()
    if pid not in table:
        json.dump({"error": f"pid {pid} not found in process table"}, sys.stdout, ensure_ascii=False, sort_keys=True, indent=2)
        sys.stdout.write("\n")
        return 2

    python_fault_stacks: object
    if args.signal:
        try:
            os.kill(pid, signal.SIGUSR1)
            python_fault_stacks = {
                "note": "SIGUSR1 sent; stacks appear on target stderr only if faulthandler is registered",
                "signalSent": "SIGUSR1",
            }
        except (ProcessLookupError, PermissionError) as exc:
            python_fault_stacks = {"error": str(exc), "signalSent": None}
    else:
        python_fault_stacks = None

    document: dict[str, object] = {
        "children": _children_of(pid, table),
        "cmdline": table[pid][1],
        "pid": pid,
        "pythonFaultStacks": python_fault_stacks,
    }
    if python_fault_stacks is None:
        document["pythonFaultStacksReason"] = NO_HANDLER_REASON

    json.dump(document, sys.stdout, ensure_ascii=False, sort_keys=True, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
