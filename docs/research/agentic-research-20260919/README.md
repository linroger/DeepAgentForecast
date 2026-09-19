# Durable agentic research

This change implements the approved research slice on `codex/workflow-rearchitecture-2026-09-19`. It retains `PipelineOrchestrator` as pipeline authority and the native DeerFlow tool-using agents as research producers. It does not replace ontology, graph, simulation, or final forecast generation.

## Workflow and identity

New pipelines pin `research_engine=agentic` when admitted. Saved pipelines without that option retain `hybrid`; setting a new default does not migrate an existing run. Explicit `linear` remains available for compatibility. The parent runs one evidence lane, followed by the existing global synthesis and actor-intelligence handoff. It does not multiply five agents by three outer lanes or enable nested task delegation.

The coordinator runs scope, primary evidence, actors and incentives, contradictions and risks, and forecast implications in sequence. Each phase has five distinct scoped investigators running concurrently when capacity is available. Inside each investigation, the native agent chooses searches, fetches, verification and follow-up reasoning. Native model calls, section writers, and the supporting actor dossier share a five-call provider envelope. Tool waits release model slots. Five is the concurrency ceiling; it is not a speedup guarantee.

Agents can emit `DISCOVERED: <question>` while still streaming. The question is persisted immediately and deduplicated. Up to five additional questions over three discovery rounds receive follow-up work. These bounds control expansion, not evidence deletion. Phase inputs are frozen before admission, so a partial restart uses the same input identities instead of silently incorporating later observations into an already completed task.

The SQLite workspace stores full task results, source metadata, discovery events and SHA-256-addressed evidence before completing a task. Question, model, language, depth, run identity and context policy are bound to the workspace. Completed tasks are reconstructed from their individual receipts. Auxiliary actor research passes and successful raw synthesis completions are also reusable; a failed final gate does not erase the research. Unknown model expenditure from a killed process remains reserved rather than refunded.

A CLI ownership lease includes research, the actor dossier and synthesis. Producers reserve their exact invocation owner before executor dispatch, including time spent queued or preparing a model. Phase leases also protect component use. A timeout closes admission, cancels queued work, suppresses late publication and retains the OS lock until admitted producers drain. A stuck producer retains ownership until its process exits. The parent receives the reserved safe-stop status instead of automatically replaying research alongside a still-running process.

## Evidence, context and memory

Full evidence storage and prompt windows are separate. The archive retains the complete successful fetched body, tool results and completed task outputs. `read_evidence` takes an artifact ID, query or character range, and can retrieve tail evidence omitted from the current prompt. It does not accept arbitrary filesystem paths. Source bodies remain checksum-bound through the evidence manifest and final ordered citation ledger. Model notes and compaction summaries are never promoted to fetched evidence.

The native harness already supported tool offloading, prompt caching and durable compaction receipts before this slice. The new integration connects those mechanisms to task recovery and archive recall. The recall tool is injected only into the agentic client’s private configuration, leaving the legacy tool catalog unchanged. Raising all character caps would still increase every worker's repeated input and latency. Increase the *working view* only when measured retrieval or coverage failures justify it; there is no need to lower full artifact fidelity to constrain a prompt.

The local context selector uses a conservative UTF-8-byte estimate (one byte per estimated token), not a tokenizer measurement. In particular, English character counts and Chinese character counts are not interchangeable token limits. Providers may report actual token usage; the admission ledger distinguishes observed usage from conservative estimates.

| Configuration | Default | Meaning |
|---|---:|---|
| `RESEARCH_AGENTIC_CONTEXT_WINDOW_TOKENS` | 128000 | Declared model context capacity; configure for the selected provider |
| `RESEARCH_AGENTIC_WORKING_TOKENS` | 64000 | Maximum selected research working view |
| `RESEARCH_AGENTIC_RESERVED_OUTPUT_TOKENS` | 16000 | Output headroom |
| `RESEARCH_AGENTIC_PROMPT_OVERHEAD_TOKENS` | 16000 | Instruction/tool headroom |
| `RESEARCH_AGENTIC_SAFETY_MARGIN_TOKENS` | 8000 | Additional context headroom |
| `RESEARCH_AGENTIC_RETRIEVAL_TOKENS` | 8000 | One archive recall/advisory view envelope |
| `RESEARCH_AGENTIC_PARAGRAPH_TOKENS` | 2048 | Retrieval chunk size |
| `RESEARCH_AGENTIC_TASK_STEPS` | 12 | Native tool-loop envelope, translated to graph recursion steps |
| `RESEARCH_AGENTIC_MAX_FOLLOWUPS` | 5 | Global additional-question cap |
| `RESEARCH_AGENTIC_DISCOVERY_ROUNDS` | 3 | Discovery expansion rounds |
| `RESEARCH_AGENTIC_PHASE_DEADLINE_S` | 2700 | Coordinator and synthesis phase deadline |
| `RESEARCH_AGENTIC_CALL_TIMEOUT_S` | 600 | Bare synthesis call deadline |
| `RESEARCH_AGENTIC_PROMPT_BUDGET_TOKENS` | 4000000 | Durable input allowance **per workspace**; evidence and synthesis use separate namespaces; 0 explicitly disables it |

The four working/output/overhead/safety values must fit the declared window. The policy and ledger limit are immutable for a saved workspace; changing them requires a deliberately separate run. Output usage is tracked separately from the input allowance. This is a local admission budget, not invoice reconciliation or a provider-side hard spend cap.

## Advisory critique and publication

Five scoped advisory reviewers inspect bounded report/source views. Their `FAIL` verdicts and unavailable scores cannot veto publication. Structured weaknesses naming a unique section can trigger one bounded repair round with at most five proposals. Only the named section body may change; ambiguous, oversized, malformed or mechanically worse proposals retain the original. This validates structural safety, not semantic truth or guaranteed improvement.

The final receipt is written after citation processing, extraction, charts and actor contract normalization. It binds the exact report and ordered sources. The backend independently replays structural, citation and explicit scenario consistency checks and required actor coverage. Invalid citations remain visible to this gate instead of being silently stripped. Canonical source ordering cannot change after synthesis has assigned citation numbers.

The legacy report judge remains unchanged for legacy runs. The new actor-dossier judge is advisory, while its source-bound coverage ledger remains mandatory. Research summaries do not establish actor knowledge or access rights; the existing actor-intelligence contract still controls downstream ontology and simulation.

## Verification and rollout boundary

Tests use the guarded offline runner and scripted native/provider boundaries. They exercise real coordinator, workspace, CLI stream interpretation, archive, synthesis caching and backend contract promotion. A provider-free native configuration smoke test is separate from production execution. See `verification.json` and the handoff for exact run receipts, hashes, failures repaired and remaining limits.

No paid provider run, deployment, saved pipeline resume, service restart, credential change, or production performance benchmark is included. A local commit does not update an already-running backend. The complete project rearchitecture remains incremental; the earlier ontology/graph/report issues are tracked separately.
