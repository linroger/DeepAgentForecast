"""Apply small, tracked overlays to DeerFlow's lead-agent factory.

The upstream harness is installed under the gitignored ``deer-flow/`` tree, so a
direct edit there disappears on a clean clone.  Keep narrowly scoped, idempotent
source transformations here and invoke them from both ``setup.sh`` and the runtime
bridge drift guard.

The overlays currently preserve two research-critical contracts:

* ``trim_tokens_to_summarize: null`` means summarize the complete discarded
  segment instead of silently falling back to LangChain's 4K tail.
* ``summarization.model_name: null`` inherits the active run model instead of
  falling back to the first configured provider.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys


LEAD_AGENT_PATH = Path(
    "backend/packages/harness/deerflow/agents/lead_agent/agent.py"
)

_OLD_TRIM_BLOCK = '''    kwargs = {
        "model": model,
        "trigger": trigger,
        "keep": keep,
    }

    if config.trim_tokens_to_summarize is not None:
        kwargs["trim_tokens_to_summarize"] = config.trim_tokens_to_summarize
'''

_NEW_TRIM_BLOCK = '''    kwargs = {
        "model": model,
        "trigger": trigger,
        "keep": keep,
        # ``None`` is an explicit contract: summarize the complete discarded
        # segment. Omitting this kwarg activates LangChain's 4K default and
        # silently drops the beginning of long research histories.
        "trim_tokens_to_summarize": config.trim_tokens_to_summarize,
    }
'''

_OLD_SUMMARY_SIGNATURE = '''def _create_summarization_middleware(*, app_config: AppConfig | None = None) -> DeerFlowSummarizationMiddleware | None:
'''

_NEW_SUMMARY_SIGNATURE = '''def _create_summarization_middleware(
    *,
    app_config: AppConfig | None = None,
    model_name: str | None = None,
) -> DeerFlowSummarizationMiddleware | None:
'''

_OLD_SUMMARY_MODEL_BLOCK = '''    if config.model_name:
        model = create_chat_model(name=config.model_name, thinking_enabled=False, app_config=resolved_app_config, attach_tracing=False)
    else:
        model = create_chat_model(thinking_enabled=False, app_config=resolved_app_config, attach_tracing=False)
'''

_NEW_SUMMARY_MODEL_BLOCK = '''    # An explicit summarization model remains authoritative.  Otherwise inherit
    # the resolved model for this run.  Falling back to the first configured
    # model silently crosses providers in per-run model deployments and can
    # turn context compression into a quota failure or a full request timeout.
    summary_model_name = config.model_name or model_name
    if summary_model_name:
        model = create_chat_model(name=summary_model_name, thinking_enabled=False, app_config=resolved_app_config, attach_tracing=False)
    else:
        model = create_chat_model(thinking_enabled=False, app_config=resolved_app_config, attach_tracing=False)
'''

_OLD_SUMMARY_FACTORY_CALL = '''    summarization_middleware = _create_summarization_middleware(app_config=resolved_app_config)
'''

_NEW_SUMMARY_FACTORY_CALL = '''    summarization_middleware = _create_summarization_middleware(
        app_config=resolved_app_config,
        model_name=model_name,
    )
'''

_TRANSFORMS = (
    (_OLD_TRIM_BLOCK, _NEW_TRIM_BLOCK, "explicit null trim forwarding"),
    (_OLD_SUMMARY_SIGNATURE, _NEW_SUMMARY_SIGNATURE, "runtime model factory parameter"),
    (_OLD_SUMMARY_MODEL_BLOCK, _NEW_SUMMARY_MODEL_BLOCK, "runtime model inheritance"),
    (_OLD_SUMMARY_FACTORY_CALL, _NEW_SUMMARY_FACTORY_CALL, "runtime model call-site forwarding"),
)


def apply(deerflow_root: str | os.PathLike[str]) -> str:
    """Apply the overlay and return ``applied``, ``already_applied``, or ``missing``.

    An unexpected upstream shape raises instead of pretending the safety contract
    landed.  Callers decide whether that should be fatal (tests) or degrade-safe
    (runtime drift guard).
    """

    target = Path(deerflow_root) / LEAD_AGENT_PATH
    if not target.is_file():
        return "missing"
    source = target.read_text(encoding="utf-8")
    updated = source
    changed = False
    for old, new, label in _TRANSFORMS:
        if new in updated:
            continue
        if old not in updated:
            raise RuntimeError(
                "lead-agent overlay context drifted while applying "
                f"{label}; refusing an unsafe edit: {target}"
            )
        updated = updated.replace(old, new, 1)
        changed = True

    if not changed:
        return "already_applied"

    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp.write_text(updated, encoding="utf-8")
        os.replace(tmp, target)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
    return "applied"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: apply_lead_agent_overlays.py <deer-flow-root>")
    status = apply(sys.argv[1])
    print(status)
    if status == "missing":
        raise SystemExit(2)
