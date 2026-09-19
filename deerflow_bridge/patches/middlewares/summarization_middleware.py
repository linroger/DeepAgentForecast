"""Summarization middleware extensions for DeerFlow."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import Collection
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, Protocol, override, runtime_checkable

from langchain.agents import AgentState
from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, RemoveMessage, ToolMessage, get_buffer_string, messages_to_dict
from langgraph.config import get_config
from langgraph.constants import TAG_NOSTREAM
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime

from deerflow.agents.middlewares.dynamic_context_middleware import is_dynamic_context_reminder
from deerflow.agents.middlewares.tool_call_metadata import clone_ai_message_with_tool_calls
from research_compaction import ResearchCompactionError, record_compaction, stop_after_compaction_failure

logger = logging.getLogger(__name__)

# Research-stage token lever 2 (inter-phase thread compaction): A/B override for
# LangChain's ``trim_tokens_to_summarize`` — how much of the discarded history
# segment the summarizer itself gets to read. Unset (the default) preserves
# today's behavior EXACTLY: the configured value flows through untouched
# (config.yaml ships ``null`` → None → summarize the complete discarded
# segment). Set to a positive integer to cap the summarizer's input at that
# many tokens; set to "none"/"null"/"full" to force full-segment summarization
# regardless of config. Invalid values are logged and ignored — never guessed.
TRIM_TOKENS_ENV = "RESEARCH_TRIM_TOKENS_TO_SUMMARIZE"
_TRIM_NONE_SENTINELS = frozenset({"none", "null", "full"})


def _resolve_trim_tokens_override() -> tuple[bool, int | None]:
    """Parse TRIM_TOKENS_ENV into ``(has_override, value)``.

    ``(False, None)`` means "no override — leave the configured value alone",
    which is also the fail-safe result for any unparseable input.
    """
    raw = os.environ.get(TRIM_TOKENS_ENV)
    if raw is None:
        return False, None
    text = raw.strip().lower()
    if not text:
        return False, None
    if text in _TRIM_NONE_SENTINELS:
        return True, None
    try:
        value = int(text)
    except ValueError:
        logger.warning(
            "%s=%r is neither a positive integer nor one of %s — ignoring the override",
            TRIM_TOKENS_ENV,
            raw,
            sorted(_TRIM_NONE_SENTINELS),
        )
        return False, None
    if value < 1:
        logger.warning(
            "%s=%r must be >= 1 (or a null sentinel) — ignoring the override",
            TRIM_TOKENS_ENV,
            raw,
        )
        return False, None
    return True, value


@dataclass(frozen=True)
class SummarizationEvent:
    """Context emitted before conversation history is summarized away."""

    messages_to_summarize: tuple[AnyMessage, ...]
    preserved_messages: tuple[AnyMessage, ...]
    thread_id: str | None
    agent_name: str | None
    runtime: Runtime


@runtime_checkable
class BeforeSummarizationHook(Protocol):
    """Hook invoked before summarization removes messages from state."""

    def __call__(self, event: SummarizationEvent) -> None: ...


def _resolve_thread_id(runtime: Runtime) -> str | None:
    """Resolve the current thread ID from runtime context or LangGraph config."""
    thread_id = runtime.context.get("thread_id") if runtime.context else None
    if thread_id is None:
        try:
            config_data = get_config()
        except RuntimeError:
            return None
        thread_id = config_data.get("configurable", {}).get("thread_id")
    return thread_id


def _resolve_agent_name(runtime: Runtime) -> str | None:
    """Resolve the current agent name from runtime context or LangGraph config."""
    agent_name = runtime.context.get("agent_name") if runtime.context else None
    if agent_name is None:
        try:
            config_data = get_config()
        except RuntimeError:
            return None
        agent_name = config_data.get("configurable", {}).get("agent_name")
    return agent_name


def _tool_call_path(tool_call: dict[str, Any]) -> str | None:
    """Best-effort extraction of a file path argument from a read_file-like tool call."""
    args = tool_call.get("args") or {}
    if not isinstance(args, dict):
        return None
    for key in ("path", "file_path", "filepath"):
        value = args.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _clone_ai_message(
    message: AIMessage,
    tool_calls: list[dict[str, Any]],
    *,
    content: Any | None = None,
) -> AIMessage:
    """Clone an AIMessage while replacing its tool_calls list and optional content."""
    return clone_ai_message_with_tool_calls(message, tool_calls, content=content)


@dataclass
class _SkillBundle:
    """Skill-related tool calls and tool results associated with one AIMessage."""

    ai_index: int
    skill_tool_indices: tuple[int, ...]
    skill_tool_call_ids: frozenset[str]
    skill_tool_tokens: int
    skill_key: str


class DeerFlowSummarizationMiddleware(SummarizationMiddleware):
    """Summarization middleware with pre-compression hook dispatch and skill rescue."""

    def __init__(
        self,
        *args,
        skills_container_path: str | None = None,
        skill_file_read_tool_names: Collection[str] | None = None,
        before_summarization: list[BeforeSummarizationHook] | None = None,
        preserve_recent_skill_count: int = 5,
        preserve_recent_skill_tokens: int = 25_000,
        preserve_recent_skill_tokens_per_skill: int = 5_000,
        **kwargs,
    ) -> None:
        # Lever-2 env knob. ``trim_tokens_to_summarize`` is keyword-only on the
        # LangChain parent, so every construction path (the lead-agent factory is
        # the sole entry point today) routes it through ``kwargs`` — overriding
        # here covers them all. No env → kwargs pass through byte-for-byte.
        has_trim_override, trim_override = _resolve_trim_tokens_override()
        if has_trim_override:
            configured = kwargs.get("trim_tokens_to_summarize", "<parent default>")
            kwargs["trim_tokens_to_summarize"] = trim_override
            logger.info(
                "%s override active: trim_tokens_to_summarize %s -> %s",
                TRIM_TOKENS_ENV,
                configured,
                trim_override,
            )
        super().__init__(*args, **kwargs)
        self._skills_container_path = skills_container_path or "/mnt/skills"
        self._skill_file_read_tool_names = frozenset(skill_file_read_tool_names or {"read_file", "read", "view", "cat"})
        self._before_summarization_hooks = before_summarization or []
        self._preserve_recent_skill_count = max(0, preserve_recent_skill_count)
        self._preserve_recent_skill_tokens = max(0, preserve_recent_skill_tokens)
        self._preserve_recent_skill_tokens_per_skill = max(0, preserve_recent_skill_tokens_per_skill)
        # The summary LLM call runs inside a LangGraph middleware hook, so its token
        # stream would otherwise be captured by the messages-tuple stream callback and
        # broadcast to the frontend as a phantom AI message. Tag a dedicated model copy
        # with TAG_NOSTREAM so the streaming handler skips it.
        # Keep self.model untagged so the parent's profile / ls_params inspection still works.
        #
        # Preserve any tags already bound on the model (e.g. "middleware:summarize" set in
        # lead_agent/agent.py for RunJournal attribution): RunnableBinding.with_config does a
        # shallow merge that would otherwise overwrite the existing tags list entirely.
        existing_tags = list((getattr(self.model, "config", None) or {}).get("tags") or [])
        merged_tags = [*existing_tags, TAG_NOSTREAM] if TAG_NOSTREAM not in existing_tags else existing_tags
        self._summary_model = self.model.with_config(tags=merged_tags)

    @override
    def _create_summary(self, messages_to_summarize: list[AnyMessage]) -> str:
        return self._summarize_with(messages_to_summarize)

    @override
    async def _acreate_summary(self, messages_to_summarize: list[AnyMessage]) -> str:
        return await self._asummarize_with(messages_to_summarize)

    def _summary_admission(self, prompt, *, asynchronous=False):
        """Keep standalone legacy summarizers independent of new helper exports."""
        if os.environ.get("RESEARCH_ENGINE", "").strip().lower() != "agentic":
            return nullcontext(None)
        try:
            if asynchronous:
                from deerflow.agents.middlewares.model_concurrency_middleware import async_provider_model_admission as admission
            else:
                from deerflow.agents.middlewares.model_concurrency_middleware import provider_model_admission as admission
        except ImportError:
            error = ResearchCompactionError("checkpoint_unavailable")
            stop_after_compaction_failure(error)
            raise error from None
        bound_kwargs = getattr(self._summary_model, "kwargs", None)
        tools = bound_kwargs.get("tools") if isinstance(bound_kwargs, dict) else None
        return admission(prompt, tools)

    def _summarize_with(self, messages_to_summarize: list[AnyMessage]) -> str:
        """Mirror the parent ``_create_summary`` but invoke the nostream-tagged model.

        We do not swap ``self.model`` at the instance level: the agent/middleware is
        cached and reused across concurrent runs, so a temporary swap would leak the
        ``RunnableBinding`` to other coroutines during ``await`` and break parent logic
        that inspects the raw model (``profile`` / ``_get_ls_params``).
        """
        prompt = self._required_summary_prompt(messages_to_summarize)
        try:
            from deerflow.agents.middlewares.model_concurrency_middleware import (
                provider_model_lease,
            )

            with provider_model_lease():
                with self._summary_admission(prompt) as ticket:
                    response = self._summary_model.invoke(
                        prompt,
                        config={"metadata": {"lc_source": "summarization"}},
                    )
                    if ticket is not None:
                        ticket.settle(response)
        except ResearchCompactionError:
            raise
        except Exception:
            # Provider diagnostics can contain request details. They are never
            # valid replacement evidence and must not enter a summary message.
            raise ResearchCompactionError("summary_model_failed") from None
        return self._validated_summary_response(response)

    async def _asummarize_with(self, messages_to_summarize: list[AnyMessage]) -> str:
        """Async counterpart of :meth:`_summarize_with` using the nostream model."""
        prompt = self._required_summary_prompt(messages_to_summarize)
        try:
            from deerflow.agents.middlewares.model_concurrency_middleware import (
                async_provider_model_lease,
            )

            async with async_provider_model_lease():
                async with self._summary_admission(prompt, asynchronous=True) as ticket:
                    response = await self._summary_model.ainvoke(
                        prompt,
                        config={"metadata": {"lc_source": "summarization"}},
                    )
                    if ticket is not None:
                        await ticket.settle(response)
        except ResearchCompactionError:
            raise
        except Exception:
            raise ResearchCompactionError("summary_model_failed") from None
        return self._validated_summary_response(response)

    @staticmethod
    def _validated_summary_response(response: Any) -> str:
        try:
            text = response.text
        except ResearchCompactionError:
            raise
        except Exception:
            raise ResearchCompactionError("invalid_summary") from None
        if not isinstance(text, str) or not text.strip():
            raise ResearchCompactionError("invalid_summary")
        return text.strip()

    def _required_summary_prompt(self, messages_to_summarize: list[AnyMessage]) -> str:
        if not messages_to_summarize:
            raise ResearchCompactionError("empty_summary_input")
        try:
            prompt = self._build_summary_prompt(messages_to_summarize)
        except ResearchCompactionError:
            raise
        except Exception:
            raise ResearchCompactionError("summary_prompt_failed") from None
        if not isinstance(prompt, str) or not prompt.strip():
            raise ResearchCompactionError("empty_summary_input")
        return prompt

    def _build_summary_prompt(self, messages_to_summarize: list[AnyMessage]) -> str | None:
        """Build the summary prompt, returning ``None`` when trimming leaves nothing."""
        trimmed_messages = self._trim_messages_for_summary(messages_to_summarize)
        if not trimmed_messages:
            return None
        # Format messages to avoid token inflation from metadata when str() is called on
        # message objects.
        formatted_messages = get_buffer_string(trimmed_messages)
        return self.summary_prompt.format(messages=formatted_messages).rstrip()

    def before_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        try:
            return self._maybe_summarize(state, runtime)
        except ResearchCompactionError as exc:
            self._signal_owned_run_stop(exc)
            raise

    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        try:
            return await self._amaybe_summarize(state, runtime)
        except ResearchCompactionError as exc:
            self._signal_owned_run_stop(exc)
            raise

    @staticmethod
    def _signal_owned_run_stop(error: ResearchCompactionError) -> None:
        # A bridge subprocess belongs to one research attempt, so its sibling
        # lanes must stop together. Native gateway/CLI processes can serve many
        # unrelated sessions; their typed failures remain local to the thread.
        if (
            os.environ.get("RESEARCH_COMPACTION_RUN_SCOPED", "").strip().lower() == "true"
            or os.environ.get("RESEARCH_PROCESS_ATTEMPT_ID", "").strip()
        ):
            stop_after_compaction_failure(error)

    def _maybe_summarize(self, state: AgentState, runtime: Runtime) -> dict | None:
        messages = state["messages"]
        self._ensure_message_ids(messages)

        total_tokens = self.token_counter(messages)
        if not self._should_summarize(messages, total_tokens):
            return None

        cutoff_index = self._determine_cutoff_index(messages)
        if cutoff_index <= 0:
            return None

        thread_id = self._required_thread_id(runtime)
        messages_to_summarize, preserved_messages = self._partition_with_skill_rescue(messages, cutoff_index)
        messages_to_summarize, preserved_messages = self._preserve_dynamic_context_reminders(messages_to_summarize, preserved_messages)
        try:
            summary = self._create_summary(messages_to_summarize)
        except ResearchCompactionError as exc:
            if exc.thread_id:
                raise
            raise ResearchCompactionError(exc.reason, thread_id=thread_id) from None
        new_messages = self._record_summary(summary, messages[:cutoff_index], thread_id)
        # Optional hooks can enqueue memory work. Dispatch only after summary
        # validation and durable evidence capture, never for a failed attempt.
        self._fire_hooks(messages_to_summarize, preserved_messages, runtime)

        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *new_messages,
                *preserved_messages,
            ]
        }

    async def _amaybe_summarize(self, state: AgentState, runtime: Runtime) -> dict | None:
        messages = state["messages"]
        self._ensure_message_ids(messages)

        total_tokens = self.token_counter(messages)
        if not self._should_summarize(messages, total_tokens):
            return None

        cutoff_index = self._determine_cutoff_index(messages)
        if cutoff_index <= 0:
            return None

        thread_id = self._required_thread_id(runtime)
        messages_to_summarize, preserved_messages = self._partition_with_skill_rescue(messages, cutoff_index)
        messages_to_summarize, preserved_messages = self._preserve_dynamic_context_reminders(messages_to_summarize, preserved_messages)
        try:
            summary = await self._acreate_summary(messages_to_summarize)
        except ResearchCompactionError as exc:
            if exc.thread_id:
                raise
            raise ResearchCompactionError(exc.reason, thread_id=thread_id) from None
        # Serialization and SQLite's durable commit must not block concurrent
        # async research tasks. Await completion before any state replacement.
        new_messages = await asyncio.to_thread(
            self._record_summary, summary, messages[:cutoff_index], thread_id,
        )
        self._fire_hooks(messages_to_summarize, preserved_messages, runtime)

        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *new_messages,
                *preserved_messages,
            ]
        }

    @staticmethod
    def _required_thread_id(runtime: Runtime) -> str:
        thread_id = _resolve_thread_id(runtime)
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ResearchCompactionError("missing_thread_id")
        return thread_id

    def _record_summary(
        self, summary: str, original_segment: list[AnyMessage], thread_id: str,
    ) -> list[HumanMessage]:
        """Archive exact original messages before the reducer may remove them.

        Archive the pre-rescue partition, rather than prompt-trimmed input or
        split tool-call clones, so receipt provenance retains original bytes and
        complete tool-call/result relationships. A failed write stops this node;
        LangGraph can resume it from the preceding checkpoint on the same thread.
        """
        if not isinstance(summary, str) or not summary.strip():
            raise ResearchCompactionError("invalid_summary", thread_id=thread_id)
        new_messages = self._build_new_messages(summary)
        message = new_messages[0]
        message.id = str(uuid.uuid4())
        try:
            archive_options: dict[str, str] = {}
            if not (
                os.environ.get("RESEARCH_COMPACTION_DB", "").strip()
                or os.environ.get("RESEARCH_BUDGET_DB", "").strip()
            ):
                from deerflow.config.paths import get_paths

                # Native clients share this overlay without bridge env vars.
                # Resolve their durable data location without mutating process
                # environment shared by concurrent gateway sessions.
                archive_options["archive_path"] = str(get_paths().base_dir / "research_compaction.sqlite3")
            envelope = record_compaction(
                thread_id=thread_id,
                message_id=message.id,
                content=message.content,
                source_messages=messages_to_dict(original_segment),
                **archive_options,
            )
        except ResearchCompactionError as exc:
            raise ResearchCompactionError(exc.reason, thread_id=thread_id) from None
        except Exception:
            raise ResearchCompactionError("archive_write_failed", thread_id=thread_id) from None
        message.additional_kwargs = {**message.additional_kwargs, "drf_compaction": envelope}
        return new_messages

    @override
    def _build_new_messages(self, summary: str) -> list[HumanMessage]:
        """Override the base implementation to let the human message with the special name 'summary'.
        And this message will be ignored to display in the frontend, but still can be used as context for the model.
        """
        return [HumanMessage(content=f"Here is a summary of the conversation to date:\n\n{summary}", name="summary")]

    def _preserve_dynamic_context_reminders(
        self,
        messages_to_summarize: list[AnyMessage],
        preserved_messages: list[AnyMessage],
    ) -> tuple[list[AnyMessage], list[AnyMessage]]:
        """Keep hidden dynamic-context reminders out of summary compression.

        These reminders carry the current date and optional memory. If summarization
        removes them, DynamicContextMiddleware can mistake the summary HumanMessage
        for the first user message and inject the reminder in the wrong place.
        """
        reminders = [msg for msg in messages_to_summarize if is_dynamic_context_reminder(msg)]
        if not reminders:
            return messages_to_summarize, preserved_messages

        remaining = [msg for msg in messages_to_summarize if not is_dynamic_context_reminder(msg)]
        return remaining, reminders + preserved_messages

    def _partition_with_skill_rescue(
        self,
        messages: list[AnyMessage],
        cutoff_index: int,
    ) -> tuple[list[AnyMessage], list[AnyMessage]]:
        """Partition like the parent, then rescue recently-loaded skill bundles."""
        to_summarize, preserved = self._partition_messages(messages, cutoff_index)

        if self._preserve_recent_skill_count == 0 or self._preserve_recent_skill_tokens == 0 or not to_summarize:
            return to_summarize, preserved

        try:
            bundles = self._find_skill_bundles(to_summarize, self._skills_container_path)
        except Exception:
            logger.exception("Skill-preserving summarization rescue failed; falling back to default partition")
            return to_summarize, preserved

        if not bundles:
            return to_summarize, preserved

        rescue_bundles = self._select_bundles_to_rescue(bundles)
        if not rescue_bundles:
            return to_summarize, preserved

        bundles_by_ai_index = {bundle.ai_index: bundle for bundle in rescue_bundles}
        rescue_tool_indices = {idx for bundle in rescue_bundles for idx in bundle.skill_tool_indices}
        rescued: list[AnyMessage] = []
        remaining: list[AnyMessage] = []
        for i, msg in enumerate(to_summarize):
            bundle = bundles_by_ai_index.get(i)
            if bundle is not None and isinstance(msg, AIMessage):
                rescued_tool_calls = [tc for tc in msg.tool_calls if tc.get("id") in bundle.skill_tool_call_ids]
                remaining_tool_calls = [tc for tc in msg.tool_calls if tc.get("id") not in bundle.skill_tool_call_ids]

                if rescued_tool_calls:
                    rescued.append(_clone_ai_message(msg, rescued_tool_calls, content=""))
                if remaining_tool_calls or msg.content:
                    remaining.append(_clone_ai_message(msg, remaining_tool_calls))
                continue

            if i in rescue_tool_indices:
                rescued.append(msg)
                continue

            remaining.append(msg)

        return remaining, rescued + preserved

    def _find_skill_bundles(
        self,
        messages: list[AnyMessage],
        skills_root: str,
    ) -> list[_SkillBundle]:
        """Locate AIMessage + paired ToolMessage groups that load skill files."""
        bundles: list[_SkillBundle] = []
        n = len(messages)
        i = 0
        while i < n:
            msg = messages[i]
            if not (isinstance(msg, AIMessage) and msg.tool_calls):
                i += 1
                continue

            tool_calls = list(msg.tool_calls)
            skill_paths_by_id: dict[str, str] = {}
            for tc in tool_calls:
                if self._is_skill_tool_call(tc, skills_root):
                    tc_id = tc.get("id")
                    path = _tool_call_path(tc)
                    if tc_id and path:
                        skill_paths_by_id[tc_id] = path

            if not skill_paths_by_id:
                i += 1
                continue

            skill_tool_tokens = 0
            skill_key_parts: list[str] = []
            skill_tool_indices: list[int] = []
            matched_skill_call_ids: set[str] = set()

            j = i + 1
            while j < n and isinstance(messages[j], ToolMessage):
                j += 1

            for k in range(i + 1, j):
                tool_msg = messages[k]
                if isinstance(tool_msg, ToolMessage) and tool_msg.tool_call_id in skill_paths_by_id:
                    skill_tool_tokens += self.token_counter([tool_msg])
                    skill_key_parts.append(skill_paths_by_id[tool_msg.tool_call_id])
                    skill_tool_indices.append(k)
                    matched_skill_call_ids.add(tool_msg.tool_call_id)

            if not skill_tool_indices:
                i = j
                continue

            bundles.append(
                _SkillBundle(
                    ai_index=i,
                    skill_tool_indices=tuple(skill_tool_indices),
                    skill_tool_call_ids=frozenset(matched_skill_call_ids),
                    skill_tool_tokens=skill_tool_tokens,
                    skill_key="|".join(sorted(skill_key_parts)),
                )
            )
            i = j

        return bundles

    def _select_bundles_to_rescue(self, bundles: list[_SkillBundle]) -> list[_SkillBundle]:
        """Pick bundles to keep, walking newest-first under count/token budgets."""
        selected: list[_SkillBundle] = []
        if not bundles:
            return selected

        seen_skill_keys: set[str] = set()
        total_tokens = 0
        kept = 0

        for bundle in reversed(bundles):
            if kept >= self._preserve_recent_skill_count:
                break
            if bundle.skill_key in seen_skill_keys:
                continue
            if bundle.skill_tool_tokens > self._preserve_recent_skill_tokens_per_skill:
                continue
            if total_tokens + bundle.skill_tool_tokens > self._preserve_recent_skill_tokens:
                continue

            selected.append(bundle)
            total_tokens += bundle.skill_tool_tokens
            kept += 1
            seen_skill_keys.add(bundle.skill_key)

        selected.reverse()
        return selected

    def _is_skill_tool_call(self, tool_call: dict[str, Any], skills_root: str) -> bool:
        """Return True when ``tool_call`` reads a file under the configured skills root."""
        name = tool_call.get("name") or ""
        if name not in self._skill_file_read_tool_names:
            return False
        path = _tool_call_path(tool_call)
        if not path:
            return False
        normalized_root = skills_root.rstrip("/")
        return path == normalized_root or path.startswith(normalized_root + "/")

    def _fire_hooks(
        self,
        messages_to_summarize: list[AnyMessage],
        preserved_messages: list[AnyMessage],
        runtime: Runtime,
    ) -> None:
        if not self._before_summarization_hooks:
            return

        event = SummarizationEvent(
            messages_to_summarize=tuple(messages_to_summarize),
            preserved_messages=tuple(preserved_messages),
            thread_id=_resolve_thread_id(runtime),
            agent_name=_resolve_agent_name(runtime),
            runtime=runtime,
        )

        for hook in self._before_summarization_hooks:
            try:
                hook(event)
            except Exception:
                hook_name = getattr(hook, "__name__", None) or type(hook).__name__
                logger.exception("before_summarization hook %s failed", hook_name)
