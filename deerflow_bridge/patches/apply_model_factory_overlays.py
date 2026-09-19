"""Apply narrow, idempotent overlays to DeerFlow's model factory.

``context_window_tokens`` is local budgeting metadata consumed by the research
bridge.  DeerFlow's permissive ``ModelConfig`` otherwise forwards every unknown
key to the provider constructor; LangChain then places this one in
``model_kwargs`` and sends it to OpenAI-compatible APIs, which reject it.

GLM-5.3 always reasons. Normalize the actual model ID's request after native
thinking settings are resolved and reuse the existing reasoning replay adapter.
Contracts: https://docs.z.ai/guides/llm/glm-5.3 and
https://docs.z.ai/guides/capabilities/thinking-mode.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys


MODEL_FACTORY_PATH = Path(
    "backend/packages/harness/deerflow/models/factory.py")

_ORIGINAL_EXCLUDE_TAIL = '''            "supports_vision",
        },
    )
'''

_METADATA_SAFE_EXCLUDE_TAIL = '''            "supports_vision",
            # Research-bridge budgeting metadata; never a provider/API kwarg.
            "context_window_tokens",
        },
    )
'''

_MODEL_LOOKUP = "    model_class = resolve_class(model_config.use, BaseChatModel)\n"
_GLM_MODEL_LOOKUP = '''    # Match the physical model, not a configurable display/provider alias.
    is_glm_53 = str(kwargs.get("model", model_config.model)).strip().lower() == "glm-5.3"
''' + _MODEL_LOOKUP

_REASONING_GATE = "    if not model_config.supports_reasoning_effort:\n"
_GLM_REASONING_GATE = "    if not model_config.supports_reasoning_effort and not is_glm_53:\n"

_MODEL_CONSTRUCTION = "    model_instance = model_class(**kwargs, **model_settings_from_config)\n"
_GLM_MODEL_CONSTRUCTION = '''    if is_glm_53:
        # GLM-5.3 rejects disabled thinking. Legacy tool-free/low-latency intent
        # uses enabled + low, while research keeps a valid explicit effort or max.
        extra_body = _deep_merge_dicts(model_settings_from_config.get("extra_body"), kwargs.get("extra_body") or {})
        explicit_effort = kwargs.get("reasoning_effort")
        if explicit_effort is None:
            explicit_effort = extra_body.get("reasoning_effort")
        if explicit_effort is None:
            explicit_effort = model_settings_from_config.get("reasoning_effort")
        model_settings_from_config = {**model_settings_from_config, **kwargs}
        kwargs = {}
        extra_body.pop("reasoning_effort", None)
        extra_body["thinking"] = _deep_merge_dicts(extra_body.get("thinking"), {"type": "enabled"})
        model_settings_from_config["extra_body"] = extra_body
        model_settings_from_config["reasoning_effort"] = (
            explicit_effort if thinking_enabled and explicit_effort in ("low", "high", "max")
            else "max" if thinking_enabled else "low"
        )
        # Keep the caller's physical output cap exactly; no implicit reserve here.
        # The existing adapter captures streamed/nonstreamed reasoning and replays
        # it byte-for-byte with tool results, as required by GLM's tool protocol.
        if model_config.use in ("langchain_openai:ChatOpenAI", "deerflow.models.patched_deepseek:PatchedChatDeepSeek"):
            from deerflow.models.patched_deepseek import PatchedChatDeepSeek

            model_class = PatchedChatDeepSeek
            # ChatDeepSeek owns api_base separately from BaseChatOpenAI's alias.
            # Translate explicitly so switching adapter cannot switch endpoints.
            base_url = model_settings_from_config.pop("base_url", None)
            openai_api_base = model_settings_from_config.pop("openai_api_base", None)
            if base_url is not None or openai_api_base is not None:
                model_settings_from_config["api_base"] = base_url if base_url is not None else openai_api_base

''' + _MODEL_CONSTRUCTION


def _replace_once(source: str, before: str, after: str, target: Path) -> str:
    if source.count(after) == 1:
        return source
    if source.count(before) != 1 or after in source:
        raise RuntimeError(
            f"model-factory overlay context drifted; refusing an unsafe edit: {target}"
        )
    return source.replace(before, after, 1)


def apply(deerflow_root: str | os.PathLike[str]) -> str:
    """Return ``applied``, ``already_applied``, or ``missing``."""
    target = Path(deerflow_root) / MODEL_FACTORY_PATH
    if not target.is_file():
        return "missing"
    source = target.read_text(encoding="utf-8")
    updated = source
    for before, after in (
        (_ORIGINAL_EXCLUDE_TAIL, _METADATA_SAFE_EXCLUDE_TAIL),
        (_MODEL_LOOKUP, _GLM_MODEL_LOOKUP),
        (_REASONING_GATE, _GLM_REASONING_GATE),
        (_MODEL_CONSTRUCTION, _GLM_MODEL_CONSTRUCTION),
    ):
        updated = _replace_once(updated, before, after, target)
    if updated == source:
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
        raise SystemExit(
            "usage: apply_model_factory_overlays.py <deer-flow-root>")
    status = apply(sys.argv[1])
    print(status)
    if status == "missing":
        raise SystemExit(2)
