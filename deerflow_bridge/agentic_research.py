"""Durable phase scheduling around the parent's native tool-using callback.

This module owns scheduling, not transport, source attestation, actor production,
or report synthesis. Complete worker output is retained independently of the
bounded context selected for subsequent workers. No provider/backend imports are
needed; the parent supplies the workspace, context policy, and native worker.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any

try:
    from .research_invocation import submit_producer
except ImportError:
    from research_invocation import submit_producer


SCHEMA = "agentic-research/v1"
PHASES = (
    "scope", "primary-evidence", "actors-and-incentives",
    "contradictions-and-risks", "forecast-implications",
)

# A role stays distinct within each phase; its goal changes with the phase.
_GOALS = {
    "scope": (
        "Define the decision, time horizon, geography, units, and answerable subquestions.",
        "Find the original documents and data series needed to answer those subquestions; record coverage gaps.",
        "Identify affected actors, decision owners, and whose incentives need investigation.",
        "Identify ambiguous definitions, conflicting premises, and plausible counterhypotheses.",
        "Specify forecast variables, observable outcomes, and indicators that would resolve uncertainty.",
    ),
    "primary-evidence": (
        "Check whether collected evidence actually covers the question's geography, horizon, and definitions.",
        "Read primary documents and datasets; retain exact dates, numbers, units, source references, and limitations.",
        "Collect actor-specific statements and observed actions, distinguishing commitments from aspirations.",
        "Find independent and contrary measurements; explain incompatible definitions and unresolved conflicts.",
        "Collect time series, base rates, milestones, and measurable constraints useful for forecast calibration.",
    ),
    "actors-and-incentives": (
        "Map dependencies and decision rights between the actors relevant to the question.",
        "Verify actors' capabilities, resources, obligations, and constraints against original documents.",
        "Analyze each key actor's incentives, options, likely responses, and conflicts of interest using evidence.",
        "Investigate actor vulnerabilities, competing incentives, information gaps, and credible opposing actions.",
        "Connect evidence about actor decisions to dated catalysts and conditional forecast implications.",
    ),
    "contradictions-and-risks": (
        "Audit scope coverage and detect hidden assumptions or missing causal links in the accumulated evidence.",
        "Recheck disputed quantitative claims against original sources, preserving both sides of unresolved disputes.",
        "Investigate incentive-driven bias, strategic statements, and actors able to disrupt the apparent consensus.",
        "Build and test the strongest countercases, bottlenecks, downside risks, and second-order effects.",
        "Determine which counterevidence changes scenarios and identify falsifiers and leading warning indicators.",
    ),
    "forecast-implications": (
        "Confirm the forecast answers the precise question and identify remaining material limits to coverage.",
        "Verify the factual inputs, units, dates, and source bindings behind the proposed forecast variables.",
        "Describe conditional actor moves and information available to each actor without inventing private knowledge.",
        "Stress-test scenario assumptions and record uncertainties, rival explanations, and reversal conditions.",
        "Organize sourced timelines, catalysts, leading indicators, and conditional base/upside/downside inputs.",
    ),
}

_REASONS = frozenset({
    "invalid_request", "identity_or_workspace", "context_selection", "invalid_worker_result",
    "worker_failed", "event_failed", "persistence_failed", "phase_deadline",
    "workers_unresolved", "control_failure", "phase_incomplete",
    "callback_closed",
})


class AgenticResearchHalt(RuntimeError):
    """Safe stage-wide stop; the parent MUST NOT publish/salvage this attempt.

    ``workspace`` retains the durable evidence and exact completed tasks. Reasons
    are allowlisted; exception messages/chains from workers are never persisted.
    Running callbacks cannot be forcibly killed by a Python thread executor.
    """

    code = "agentic_research_halted"

    def __init__(self, reason: str, workspace=None, *, phase: str = ""):
        self.reason = reason if reason in _REASONS else "control_failure"
        self.workspace = workspace
        self.phase = phase if phase in PHASES or phase.startswith("followup-") else ""
        super().__init__(f"{self.code}: {self.reason}")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _copy(value: Any) -> Any:
    return json.loads(_canonical(value))


def _diagnostic_payload(value):
    if isinstance(value, BaseException):
        return "[redacted diagnostic]"
    if isinstance(value, dict):
        return {key: "[redacted diagnostic]" if key.lower() in {
            "error", "error_message", "exception", "exception_message", "traceback", "stack_trace",
        } else _diagnostic_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_diagnostic_payload(item) for item in value]
    return value


def _validate_result(value: Any) -> dict:
    if not isinstance(value, dict) or not isinstance(value.get("text"), str) or not value["text"].strip():
        raise AgenticResearchHalt("invalid_worker_result")
    for key in ("evidence", "sources", "discoveries"):
        if not isinstance(value.get(key), list):
            raise AgenticResearchHalt("invalid_worker_result")
    if any(not isinstance(s, str) for s in value["evidence"] + value["discoveries"]):
        raise AgenticResearchHalt("invalid_worker_result")
    if any(not isinstance(s, dict) for s in value["sources"]):
        raise AgenticResearchHalt("invalid_worker_result")
    return _copy(value)


class _Run:
    def __init__(self, workspace, worker, policy, emit, lease, inputs, workers, deadline_s):
        self.workspace = workspace
        self.worker = worker
        self.policy = policy
        self.emit = emit
        self.lease = lease
        self.inputs = inputs
        self.run_hash = _hash(inputs)
        self.workers = workers
        self.deadline_s = deadline_s
        self.stop = threading.Event()
        self.events_closed = threading.Event()
        self.failure_mutex = threading.Lock()
        self.mutex = threading.RLock()
        self.failure = None
        self.phase = ""
        self.deadline = math.inf
        self.records = []
        self.discovery_refs = []
        self.reused = 0
        self.executed = 0

    def halt(self, reason):
        # Close admission before the failing callback releases its executor slot.
        # Never wait for archive I/O before closing callback admission. A
        # successful sibling may still be persisting while another fails.
        with self.failure_mutex:
            if self.failure is None:
                self.failure = AgenticResearchHalt(reason, self.workspace, phase=self.phase)
            self.stop.set()
            return self.failure

    def check(self):
        if self.stop.is_set():
            raise self.failure or AgenticResearchHalt("control_failure", self.workspace)
        if time.monotonic() >= self.deadline:
            raise self.halt("phase_deadline")

    def persist(self, operation, *args, **kwargs):
        # A disk call already in flight can finish after halt. Fence both sides
        # so its return cannot start another write, discovery or publication.
        # Never hold failure_mutex across I/O: halt must stay non-blocking.
        self.check()
        result = operation(*args, **kwargs)
        self.check()
        return result

    def event(self, task_id, kind, payload):
        try:
            with self.mutex:
                self.check()
                if self.events_closed.is_set():
                    raise AgenticResearchHalt("callback_closed", self.workspace)
                if not isinstance(kind, str) or not kind or not isinstance(payload, dict):
                    raise AgenticResearchHalt("event_failed")
                # Diagnostics must never archive exception strings, traceback text,
                # request headers, or arbitrary provider error detail.
                if any(word in kind.lower() for word in ("error", "exception", "fail", "halt")):
                    safe_payload = {"reason": "worker_diagnostic"}
                else:
                    safe_payload = _copy(_diagnostic_payload(payload))
                if kind == "discovery":
                    question = safe_payload.get("question")
                    if not isinstance(question, str) or not question.strip():
                        raise AgenticResearchHalt("event_failed")
                    self.persist(self.workspace.add_discovery, question, task_id, safe_payload.get("evidence_refs", []))
                self.persist(self.workspace.append_event, task_id, kind, safe_payload)
            if self.emit is not None:
                self.check()
                self.emit(kind, {**safe_payload, "task_id": task_id})
                self.check()
        except BaseException:
            raise self.halt("event_failed") from None

    def _context_blocks(self):
        blocks = []
        seen = set()
        for record in self.records:
            for stored in record["blocks"]:
                ref = stored["ref"]
                if ref["id"] in seen:
                    continue
                seen.add(ref["id"])
                # Offsets from select() address these exact archived strings,
                # never decoded substrings inside the result JSON artifact.
                block = {"id": ref["id"], "text": self.workspace.read_artifact(ref), "kind": stored["kind"]}
                if "source_url" in stored:
                    block["source_url"] = stored["source_url"]
                blocks.append(block)
        for ref in self.discovery_refs:
            if ref["id"] not in seen:
                seen.add(ref["id"])
                blocks.append({"id": ref["id"], "text": self.workspace.read_artifact(ref),
                               "kind": "discovery_evidence"})
        return blocks

    def _read_record(self, record):
        result = _validate_result(json.loads(self.workspace.read_artifact(record["output_ref"])))
        if _hash(result) != record["result_sha256"]:
            raise AgenticResearchHalt("identity_or_workspace")
        return result

    def plan_phase(self, phase, question):
        if _copy(self.policy.to_dict()) != self.inputs["context_policy"]:
            raise AgenticResearchHalt("identity_or_workspace")
        predecessors = [ref for record in self.records
                        for ref in [record["output_ref"], *(b["ref"] for b in record["blocks"])]]
        plan_inputs = {"run": self.run_hash, "phase": phase, "question": question, "predecessors": predecessors}
        plan_id = "agentic-plan-" + phase
        plan = self.persist(self.workspace.load_task, plan_id, plan_inputs)
        if plan is None:
            # Keep the lookup identity above unchanged for frozen legacy plans.
            # New plans bind the round's frozen discovery evidence into each
            # task before selection. It is archived evidence, not an attestation
            # that any source was fetched or independently verified.
            context_refs = predecessors[:]
            known_refs = {ref["id"] for ref in context_refs}
            for ref in self.discovery_refs:
                if ref["id"] not in known_refs:
                    context_refs.append(ref)
                    known_refs.add(ref["id"])
            blocks = self._context_blocks()
            tasks = []
            phase_goals = _GOALS.get(phase, _GOALS["primary-evidence"])
            for focus, instruction in zip(PHASES, phase_goals, strict=True):
                self.check()
                goal = (
                    f"Research question: {question}\nRoot decision: {self.inputs['question']}\n"
                    f"Phase: {phase}. Investigator focus: {focus}.\n{instruction}\n"
                    f"Depth: {self.inputs['depth']}. Output language: {self.inputs['language']}.\n"
                    "Use the existing native research tools where evidence is needed. Preserve source identities, "
                    "dates, units, exact attribution, and uncertainty. Distinguish observed evidence from inference; "
                    "never invent retrieval or verification status. Emit a discovery event with its question as soon "
                    "as a material new gap is found. Return full research notes and evidence for later synthesis; "
                    "do not write the final report."
                )
                # Blocks contain only immutable strings. Isolate the mutable
                # containers without serializing/copying full evidence five
                # times per phase, especially with large model envelopes.
                selection = self.policy.select([dict(block) for block in blocks], query=goal)
                if not isinstance(selection, dict) or not isinstance(selection.get("text"), str):
                    raise AgenticResearchHalt("context_selection")
                context_ref = self.persist(self.workspace.put_artifact, selection["text"], "phase_context")
                task_inputs = {
                    "schema": SCHEMA, "run": self.run_hash, "phase": phase, "question": question,
                    "focus": focus, "goal": goal, "context_refs": context_refs,
                    "context_ref": context_ref, "policy": self.inputs["context_policy"],
                }
                task_id = "ar-" + _hash(task_inputs)
                task = {
                    "id": task_id, "phase": phase, "question": question, "focus": focus,
                    "role": focus, "kind": "investigate", "goal": goal,
                    "depth": self.inputs["depth"], "language": self.inputs["language"],
                    "context_refs": context_refs, "context_ref": context_ref,
                }
                tasks.append({"task": task, "inputs": task_inputs, "selection": selection})
            plan = {"tasks": tasks}
            self.persist(self.workspace.save_task, plan_id, plan_inputs, plan)
        if not isinstance(plan, dict) or not isinstance(plan.get("tasks"), list) or len(plan["tasks"]) != 5:
            raise AgenticResearchHalt("identity_or_workspace")
        return plan

    def invoke(self, item, context):
        task = item["task"]
        task_id = task["id"]
        callback_mutex = threading.RLock()
        callback_open = True

        def observe(kind, payload):
            # Join any already-started observer before committing this task;
            # a retained callback cannot write after the native turn returns.
            with callback_mutex:
                if not callback_open:
                    raise AgenticResearchHalt("callback_closed", self.workspace)
                self.event(task_id, kind, payload)

        try:
            self.check()
            self.event(task_id, "task_started", {"phase": task["phase"], "focus": task["focus"]})
            self.check()
            try:
                value = self.worker(_copy(task), context, observe)
            except BaseException as exc:
                reason = exc.reason if isinstance(exc, AgenticResearchHalt) else "worker_failed"
                self.halt(reason)
                raise
            finally:
                with callback_mutex:
                    callback_open = False
            # A timed-out callback may return a very large result. Do not copy
            # or serialize it once the controller has already closed admission.
            self.check()
            result = _validate_result(value)
            with self.mutex:
                self.check()
                ref = self.persist(self.workspace.put_artifact, _canonical(result), "worker_result")
                blocks = [{"ref": self.persist(self.workspace.put_artifact, result["text"], "worker_output"), "kind": "worker_output"}]
                for text in result["evidence"]:
                    blocks.append({"ref": self.persist(self.workspace.put_artifact, text, "evidence"), "kind": "evidence"})
                for source in result["sources"]:
                    block = {"ref": self.persist(self.workspace.put_artifact, _canonical(source), "source"), "kind": "source"}
                    if isinstance(source.get("url"), str):
                        block["source_url"] = source["url"]
                    blocks.append(block)
                for question in result["discoveries"]:
                    if question.strip():
                        self.persist(self.workspace.add_discovery, question, task_id, [ref])
                record = {"task": task, "output_ref": ref, "blocks": blocks, "result_sha256": _hash(result)}
                self.check()
                self.persist(self.workspace.save_task, task_id, item["inputs"], record)
                self.persist(self.workspace.append_event, task_id, "task_done", {"output_ref": ref})
                self.executed += 1
                return record
        except BaseException as exc:
            reason = exc.reason if isinstance(exc, AgenticResearchHalt) else "worker_failed"
            raise self.halt(reason) from None

    def run_phase(self, phase, question):
        self.phase = phase
        # The limit is per phase attempt, including planning and persistence.
        # A restart receives a new time allowance but retains admission limits.
        self.deadline = time.monotonic() + self.deadline_s
        plan = self.plan_phase(phase, question)
        outputs = {}
        missing = []
        # Validate all five saved siblings before admitting a single callback.
        for item in plan["tasks"]:
            task = item["task"]
            if task["id"] != "ar-" + _hash(item["inputs"]):
                raise AgenticResearchHalt("identity_or_workspace")
            saved = self.persist(self.workspace.load_task, task["id"], item["inputs"])
            if saved is None:
                missing.append((item, self.workspace.read_artifact(task["context_ref"])))
            else:
                if saved["task"] != task:
                    raise AgenticResearchHalt("identity_or_workspace")
                self._read_record(saved)
                outputs[task["id"]] = saved
                self.reused += 1
        self.check()
        executor = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="research-phase")
        futures = {}
        try:
            for item, context in missing:
                self.check()
                futures[submit_producer(executor, self.invoke, item, context)] = item["task"]["id"]
            pending = set(futures)
            while pending:
                self.check()
                done, pending = wait(pending, timeout=min(0.05, max(0, self.deadline - time.monotonic())),
                                     return_when=FIRST_COMPLETED)
                self.check()
                for future in done:
                    outputs[futures[future]] = future.result()
            self.check()
        except BaseException as exc:
            reason = exc.reason if isinstance(exc, AgenticResearchHalt) else "control_failure"
            error = self.halt(reason)
            for future in futures:
                future.cancel()
            # The store retains its OS lock until actual callback completion.
            # Late callback/event writes are rejected by check() above.
            self.lease.defer_release_until(list(futures))
            raise error from None
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        if len(outputs) != 5:
            raise self.halt("phase_incomplete")
        ordered = [outputs[item["task"]["id"]] for item in plan["tasks"]]
        self.persist(self.workspace.append_event, "agentic-run", "phase_done", {"phase": phase, "task_ids": list(outputs)})
        self.records.extend(ordered)

    def execute(self, max_followups, max_rounds):
        run_record = self.workspace.load_task("agentic-run", self.inputs)
        if run_record is None:
            self.workspace.save_task("agentic-run", self.inputs, {"schema": SCHEMA})
        # snapshot() verifies durable state; no prompt is rebuilt from arbitrary
        # current task output. Only ordered predecessor records feed new phases.
        self.workspace.snapshot()
        for phase in PHASES:
            self.run_phase(phase, self.inputs["question"])
        admitted = []
        admitted_questions = {" ".join(self.inputs["question"].split()).casefold()}
        rounds = 0
        for round_index in range(1, max_rounds + 1):
            self.check()
            round_inputs = {"run": self.run_hash, "round": round_index, "admitted": admitted[:]}
            round_id = f"agentic-round-{round_index}"
            plan = self.persist(self.workspace.load_task, round_id, round_inputs)
            if plan is None:
                # Deduplicate only new admissions. Never rewrite a persisted
                # round, merge observations, or reinterpret punctuation/meaning.
                seen_questions = set(admitted_questions)
                candidates = []
                for discovery in sorted(self.workspace.discoveries(), key=lambda d: d["id"]):
                    normalized = " ".join(discovery["question"].split()).casefold()
                    if discovery["id"] not in admitted and normalized not in seen_questions:
                        candidates.append(discovery)
                        seen_questions.add(normalized)
                plan = {"discoveries": candidates[:max(0, max_followups - len(admitted))]}
                self.persist(self.workspace.save_task, round_id, round_inputs, plan)
            if not plan["discoveries"]:
                break
            rounds += 1
            for discovery in plan["discoveries"]:
                discovery_id = discovery["id"]
                phase = "followup-" + _hash({"id": discovery_id, "question": discovery["question"]})
                self.persist(self.workspace.mark_discovery, discovery_id, "dispatched", task_id=phase)
                self.discovery_refs = discovery["evidence_refs"]
                self.run_phase(phase, discovery["question"])
                self.persist(self.workspace.mark_discovery, discovery_id, "completed", task_id=phase)
                admitted.append(discovery_id)
                admitted_questions.add(" ".join(discovery["question"].split()).casefold())
            if len(admitted) >= max_followups:
                break
        self.check()
        self.events_closed.set()
        discoveries = self.workspace.discoveries()
        deferred = [d["id"] for d in discoveries if d["id"] not in admitted]
        evidence, texts, sources, refs = [], [], [], []
        for record in self.records:
            result = self._read_record(record)
            texts.append(result["text"])
            evidence.extend(result["evidence"])
            sources.extend(result["sources"])
            refs.append(record["output_ref"])
            refs.extend(block["ref"] for block in record["blocks"])
        # Preserve the existing result/ref order, then expose discovery-only
        # evidence bound by new plans for downstream local recall. Frozen legacy
        # tasks have no extra references and produce the same output as before.
        known_refs = {ref["id"] for ref in refs}
        for record in self.records:
            for ref in record["task"]["context_refs"]:
                if ref["id"] not in known_refs:
                    refs.append(ref)
                    known_refs.add(ref["id"])
        self.check()
        stats = {
            "completed_tasks": len(self.records), "executed_tasks": self.executed,
            "reused_tasks": self.reused, "base_phases": len(PHASES),
            "followup_questions": len(admitted), "discovery_rounds": rounds,
            "deferred_discoveries": deferred, "worker_limit": self.workers,
        }
        self.persist(self.workspace.append_event, "agentic-run", "research_complete", stats)
        return {"text": "\n\n".join(texts), "evidence": evidence, "discoveries": discoveries,
                "stats": stats, "evidence_refs": refs, "sources": sources}


def run_research(workspace, *, question, depth, language, worker, context_policy, emit=None,
                 workers=5, phase_deadline_s=2700, max_followups=5, max_discovery_rounds=3) -> dict:
    """Run five sequential research phases and bounded adaptive follow-up phases.

    Every phase has five distinct investigators, with at most five worker
    callbacks active. ``workers`` may reduce that limit; values above five are
    capped. Follow-up bounds apply to the entire durable run, not each restart.
    Deadlines apply per phase attempt. Full outputs are retained; only selected
    prompt views are bounded. Deferred discoveries are explicit in the result
    and are not assertions of research sufficiency or report publishability.

    The supplied execution lease must support ``defer_release_until(futures)``
    so non-waiting shutdown cannot overlap live abandoned workers with a resume.
    Filesystem operations and ``emit`` must return promptly; Python cannot kill
    a running callback or interrupt an unresponsive filesystem operation.
    """
    state = None
    try:
        if (not isinstance(question, str) or not question.strip()
                or not isinstance(depth, str) or not depth.strip()
                or not isinstance(language, str) or not language.strip()
                or not callable(worker) or (emit is not None and not callable(emit))
                or isinstance(workers, bool) or not isinstance(workers, int) or workers < 1
                or isinstance(phase_deadline_s, bool) or not isinstance(phase_deadline_s, (int, float))
                or not math.isfinite(phase_deadline_s) or phase_deadline_s <= 0
                or any(isinstance(v, bool) or not isinstance(v, int) or v < 0
                       for v in (max_followups, max_discovery_rounds))):
            raise AgenticResearchHalt("invalid_request")
        policy_identity = _copy(context_policy.to_dict())
        inputs = {"schema": SCHEMA, "question": question, "depth": depth, "language": language,
                  "workspace_identity": _copy(workspace.identity),
                  "context_policy": policy_identity, "goals_sha256": _hash(_GOALS),
                  "max_followups": max_followups, "max_discovery_rounds": max_discovery_rounds}
        with workspace.execution_lock() as lease:
            state = _Run(workspace, worker, context_policy, emit, lease, inputs, min(5, workers), phase_deadline_s)
            try:
                return state.execute(max_followups, max_discovery_rounds)
            except BaseException as exc:
                reason = exc.reason if isinstance(exc, AgenticResearchHalt) else "identity_or_workspace"
                error = state.halt(reason)
                # Only the invocation that owns the execution lease may record
                # a run halt. A competing resume must leave these records alone.
                try:
                    workspace.append_event("agentic-run", "research_halted", {"reason": error.reason})
                except BaseException:
                    pass
                raise error from None
    except BaseException as exc:
        reason = exc.reason if isinstance(exc, AgenticResearchHalt) else "identity_or_workspace"
        error = state.halt(reason) if state is not None else AgenticResearchHalt(reason, workspace)
        # Never expose the raw message or chain, including acquisition failure.
        raise error from None


# The bridge can import this file as a top-level module; keep typed halt identity
# identical when the parent/tests import it through the namespace package.
if __name__ == "agentic_research":
    sys.modules.setdefault("deerflow_bridge.agentic_research", sys.modules[__name__])
elif __name__ == "deerflow_bridge.agentic_research":
    sys.modules.setdefault("agentic_research", sys.modules[__name__])
