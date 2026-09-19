"""Model-aware research defaults; explicit settings and saved identities win.

GLM-5.3 capability: https://docs.z.ai/guides/llm/glm-5.3 (1M context,
128K output, reasoning always enabled). The exact 1,048,576 / 131,072 limits
are also documented by Alibaba Cloud Model Studio. Working views deliberately
use only part of capacity; full evidence remains in the archive.
"""
from __future__ import annotations

import os
import math
import re

GLM_CONTEXT = 1_048_576
GLM_MAX_OUTPUT = 131_072
_CONTEXT_GLM = {
    'context_window_tokens': GLM_CONTEXT,
    'working_tokens': 262_144,
    'reserved_output_tokens': 65_536,
    'prompt_overhead_tokens': 32_768,
    'safety_margin_tokens': 32_768,
    'retrieval_tokens': 32_768,
    'paragraph_tokens': 4_096,
}
_EXECUTION_BASE = {
    'task_steps': 12, 'max_followups': 5, 'discovery_rounds': 3,
    'phase_deadline_s': 2700, 'call_timeout_s': 600,
    'prompt_budget_tokens': 4_000_000, 'compaction_keep_tokens': 16_000,
    'synthesis_outline_context_chars': 120_000,
    'synthesis_section_context_chars': 60_000,
    'synthesis_total_routed_context_chars': 600_000,
    'review_timeout_s': 120, 'repair_timeout_s': 120,
    'reasoning_reserve_tokens': 0, 'synthesis_max_context_chars': 0,
}
_EXECUTION_GLM = {
    **_EXECUTION_BASE, 'task_steps': 24, 'max_followups': 8,
    'phase_deadline_s': 3600, 'prompt_budget_tokens': 12_000_000,
    'compaction_keep_tokens': 65_536,
    'synthesis_outline_context_chars': 262_144,
    'synthesis_section_context_chars': 196_608,
    'synthesis_total_routed_context_chars': 2_097_152,
    'review_timeout_s': 180, 'repair_timeout_s': 240,
    'reasoning_reserve_tokens': 4096,
}
_ZERO_ALLOWED = {'max_followups', 'discovery_rounds', 'prompt_budget_tokens', 'reasoning_reserve_tokens', 'synthesis_max_context_chars'}

EXECUTION_ENV_ALIASES = {
    "SYNTHESIS_OUTLINE_CONTEXT_CHARS": "synthesis_outline_context_chars",
    "SYNTHESIS_SECTION_CONTEXT_CHARS": "synthesis_section_context_chars",
    "SYNTHESIS_TOTAL_ROUTED_CONTEXT_CHARS": "synthesis_total_routed_context_chars",
    "SYNTHESIS_MAX_CONTEXT_CHARS": "synthesis_max_context_chars",
}


def is_glm53(model_name, model_id=None):
    """An actual model ID overrides a configurable alias, including `glm`."""
    name = str(model_id if model_id is not None else model_name or '').strip().casefold()
    return name in {'glm-5.3', 'zai-org/glm-5.3', 'z-ai/glm-5.3'} or (model_id is None and name == 'glm')


def profile_name(model_name=None, model_id=None):
    return 'glm-5.3-1m/v1' if is_glm53(model_name, model_id) else 'conservative-128k/v1'


def context_defaults(model_name=None, model_id=None):
    return dict(_CONTEXT_GLM) if is_glm53(model_name, model_id) else {}


def execution_defaults(model_name=None, model_id=None):
    return dict(_EXECUTION_GLM if is_glm53(model_name, model_id) else _EXECUTION_BASE)


def execution_policy(model_name=None, model_id=None):
    settings = execution_defaults(model_name, model_id)
    for field in settings:
        env_name = 'RESEARCH_AGENTIC_' + field.upper()
        raw = os.environ.get(env_name)
        if raw is None:
            continue
        if field.endswith('_s'):
            value = float(raw)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f'{env_name} must be finite and positive')
            settings[field] = value
            continue
        if not re.fullmatch(r'[0-9]+', raw) or int(raw) < (0 if field in _ZERO_ALLOWED else 1):
            raise ValueError(f'{env_name} must be a valid nonnegative integer' if field in _ZERO_ALLOWED else f'{env_name} must be a positive integer')
        settings[field] = int(raw)
    for env_name, field in EXECUTION_ENV_ALIASES.items():
        raw = os.environ.get(env_name)
        if raw is None:
            continue
        if not re.fullmatch(r'[0-9]+', raw) or int(raw) < (0 if field in _ZERO_ALLOWED else 1):
            raise ValueError(f'{env_name} must be a valid context budget')
        modern = 'RESEARCH_AGENTIC_' + field.upper()
        if modern in os.environ and settings[field] != int(raw):
            raise ValueError(f'{env_name} conflicts with {modern}')
        settings[field] = int(raw)
    return settings


def workspace_execution_policy(workspace):
    identity = workspace.identity
    saved = identity.get('execution_policy')
    if saved is None:
        # Existing workspaces retain the behavior with which their tasks ran.
        return execution_policy()
    if type(saved) is not dict or set(saved) != set(_EXECUTION_BASE):
        raise ValueError('invalid saved research execution policy')
    for key, value in saved.items():
        if key.endswith('_s'):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError('invalid saved research deadline')
            continue
        if type(value) is not int or value < (0 if key in _ZERO_ALLOWED else 1):
            raise ValueError('invalid saved research execution budget')
    return dict(saved)


def select_engine(explicit=None, *, resume=False, saved_engine=None):
    """Fresh launches are agentic; unpinned legacy recovery remains hybrid."""
    if explicit is not None and str(explicit).strip():
        value = str(explicit).strip().lower()
        if value not in {'agentic', 'hybrid', 'linear'}:
            raise ValueError('unsupported research engine')
        return value
    if resume:
        if saved_engine is not None and not isinstance(saved_engine, str):
            raise ValueError('invalid saved research engine')
        if saved_engine in {'agentic', 'agentic-phases/v1'}:
            return 'agentic'
        if saved_engine in {'linear', 'linear-v2'}:
            return 'linear'
        return 'hybrid'
    return 'agentic'
