#!/usr/bin/env python3
"""Validate that every env var Config reads is documented in .env.example
(EXECPLAN2 I-8-5).

Drift here is a real DX hazard: a knob that Config honours but `.env.example`
never mentions is undiscoverable; a documented knob nothing reads is dead. This
script is wired into `doctor.sh` and CI (`--strict` exits non-zero on drift).

Usage:
    python backend/scripts/check_env_drift.py [--strict] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG = os.path.join(ROOT, "backend", "app", "config.py")
ENV_EXAMPLE = os.path.join(ROOT, ".env.example")

# Env vars that are read elsewhere (Flask/runtime/deerflow) and intentionally not
# all surfaced in .env.example, or are infra-level — exclude from "missing" drift.
_IGNORE = {
    "FLASK_HOST", "FLASK_PORT", "FLASK_DEBUG", "WERKZEUG_RUN_MAIN",
    "PYTHONUNBUFFERED", "PYTHONUTF8", "PYTHONIOENCODING", "TMPDIR", "LOG_LEVEL",
    # ZEP_API_KEY is an internal Graphiti-shim compat sentinel ('local-graphiti'),
    # not a user-facing knob — intentionally absent from .env.example.
    "ZEP_API_KEY",
}

_ENV_READ_RE = re.compile(r"os\.environ(?:\.get\(\s*['\"]([A-Z0-9_]+)['\"]|\[\s*['\"]([A-Z0-9_]+)['\"]\s*\])")
_ENV_DOC_RE = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]+)=", re.M)


# Per-provider dynamic knobs (e.g. LLM_KIMI_DISABLE_THINKING) — documented as a
# pattern (LLM_<PROVIDER>_DISABLE_THINKING) rather than one line per provider.
_IGNORE_PATTERNS = [re.compile(r"^LLM_[A-Z0-9]+_DISABLE_THINKING$")]


def config_env_vars() -> set:
    text = open(CONFIG, encoding="utf-8").read()
    out = set()
    for m in _ENV_READ_RE.finditer(text):
        out.add(m.group(1) or m.group(2))
    out -= _IGNORE
    return {v for v in out if not any(p.match(v) for p in _IGNORE_PATTERNS)}


def documented_env_vars() -> set:
    if not os.path.exists(ENV_EXAMPLE):
        return set()
    text = open(ENV_EXAMPLE, encoding="utf-8").read()
    return set(_ENV_DOC_RE.findall(text))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="exit 1 if any Config var is undocumented")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    read = config_env_vars()
    documented = documented_env_vars()
    missing = sorted(read - documented)          # read by Config, not in .env.example
    undocumented_ok = sorted(documented - read)  # in .env.example, not read by Config (informational)

    if args.json:
        print(json.dumps({"missing_from_env_example": missing,
                          "documented_but_unread": undocumented_ok}, indent=2))
    else:
        if missing:
            print("⚠️  Config reads these env vars but .env.example does not document them:")
            for k in missing:
                print(f"    - {k}")
        else:
            print("✓ every Config env var is documented in .env.example")
        if undocumented_ok:
            print("ℹ️  documented in .env.example but not read by Config (may be for subprocesses):")
            print("    " + ", ".join(undocumented_ok))

    return 1 if (args.strict and missing) else 0


if __name__ == "__main__":
    sys.exit(main())
