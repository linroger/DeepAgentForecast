# DeerFlow bridge

DeepAgentForecast's **deep-research stage** (stage 1 of the pipeline) is powered by
[DeerFlow **2.0**](https://github.com/bytedance/deer-flow) — a ground-up rewrite that turned the
original deep-research framework into a **super agent harness** (LangGraph/LangChain-based, with
sub-agents, skills, sandbox, and long-term memory). We drive it headlessly through its embedded
`deerflow.client.DeerFlowClient`, so this project consumes the 2.0 harness *as the research
engine* while keeping its own pipeline contract.

DeerFlow runs in **its own Python environment** (Python 3.12, with its `langchain`/`langgraph`
dependency tree isolated from this backend) and is invoked as a **subprocess** that writes a
file‑based "handoff contract" the backend consumes (`research_report.md`, `actors.json`,
`sources.json`, `prediction_requirement.txt`, `meta.json`, `research_progress.log`).
`research_progress.log` is an append-only, human-readable event stream: it preserves
events across bounded retries/recovery while summarizing large tool payloads, so it is
operational provenance rather than a raw model transcript.

This folder is the **single source of truth** for that integration, so the whole bridge is
reproducible from this repo. `./setup.sh` (at the project root) assembles the engine — preferring
the pinned DeerFlow 2.0 build vendored at `deer-flow-2.0-m1-rc3/` (falling back to a pinned
upstream clone) — and applies everything here automatically. You normally never touch these files
by hand.

| File | What it is | Where `setup.sh` puts it |
|---|---|---|
| `deerflow_research.py` | The bridge entry point. Runs a DeerFlow research turn for a prompt and writes `research_report.md`, `actors.json`, `sources.json`, `prediction_requirement.txt`, `meta.json`, `research_progress.log` into an output dir. | → **root** of the `deer-flow` checkout. |
| `patches/models/claude_provider.py` | `ClaudeChatModel` with **OAuth‑preference** (prefers a Claude Code OAuth credential over an ambient non‑OAuth `ANTHROPIC_API_KEY`, fixing stray‑key 401s), a 0.5 thinking‑budget ratio, and **prompt caching default‑ON for both auth modes** (research token lever 1): ≤4 `cache_control` breakpoints — one on the last system block (caches tools + system) plus incremental markers on the newest turns — so the re‑sent research thread bills as cache reads. Kill‑switch: `DEERFLOW_CLAUDE_PROMPT_CACHE=0` (restores the historical marker‑free payload). | → `deer-flow/backend/packages/harness/deerflow/models/` |
| `patches/models/credential_loader.py` | Adds a **macOS Keychain** credential source (`security find-generic-password -s "Claude Code-credentials"`) so the local `claude` OAuth token is found even when it isn't in `~/.claude/.credentials.json`. | → same `models/` dir |
| `patches/models/patched_minimax.py` | DeerFlow 2.0's **own upstreamed** `PatchedChatMiniMax` — strips the per‑message `name` field from user-role messages, fixing MiniMax `400 user name must be consistent`; keeps tools + reasoning **on**. Carried here **verbatim** so it is a no‑op on the vendored 2.0 engine and back‑ports the fix on an older clone‑fallback base (it never downgrades the upstream role‑scoped implementation). | → same `models/` dir |
| `patches/apply_model_factory_overlays.py` | Idempotently keeps local `context_window_tokens` budgeting metadata out of provider constructor/request kwargs. | → transforms `deer-flow/backend/packages/harness/deerflow/models/factory.py` |
| `skills/deep-research/` | Compact always-injected research core (about 6.8K chars instead of ~29K) plus lazy `references/` for source tradecraft and the final-dossier contract. Working passes are explicitly exempt from the final dossier's 10K-word floor; KIQ/evidence-yield convergence replaces call/source quotas. | → `deer-flow/skills/public/deep-research/` |
| `patches/middlewares/loop_detection_middleware.py` | Loop detection with **per‑run counter resets**. Upstream accumulates per‑tool call counts across *all* turns of a thread, so multi‑pass deep research permanently force‑stops `web_search` from pass 2 onward (`[FORCED STOP] Tool web_search called N times…`) once the cumulative count crosses the limit. The patch resets the budget at the start of each agent run — full in‑run loop protection stays intact. | → `deer-flow/backend/packages/harness/deerflow/agents/middlewares/` |
| `patches/middlewares/model_concurrency_middleware.py` plus the patched runtime/title/summarization middleware modules | Acquires one shared SQLite permit at the **exact provider-call boundary** for lead agents, scoped subagents, title calls, and summarization calls. Tool execution does not hold a model permit, and concurrently running forecast pipelines share the same application-level envelope. The summarization middleware additionally wires research token lever 2: `RESEARCH_TRIM_TOKENS_TO_SUMMARIZE` overrides `trim_tokens_to_summarize` at its single construction site for A/B runs (unset preserves today's full-segment default exactly; positive integer caps the summarizer's input; `none`/`null`/`full` forces the full segment). | → same `middlewares/` dir |
| `patches/apply_subagent_overlays.py` | Idempotently hardens the embedded subagent path while preserving vendor tracing/session/callback behavior: the client passes its exact `AppConfig`, `model: inherit` falls back to `RunnableConfig.configurable.model_name` when tracing metadata is absent, provider-error fallback messages become failed tasks, and the executor retains the shared lifecycle lease. This prevents MiniMax leads from silently delegating to the first configured provider and prevents failed investigations from being labeled evidence. | narrow transforms of `client.py`, `tools/builtins/task_tool.py`, and `subagents/executor.py` |
| `patches/apply_lead_agent_overlays.py` | Narrow idempotent factory transforms that forward `trim_tokens_to_summarize: null` explicitly and make a null summarization-model setting inherit the active run model. This prevents both LangChain's 4K tail-only fallback and accidental cross-provider summarization through the first configured model. | narrow transform of `deer-flow/backend/packages/harness/deerflow/agents/lead_agent/agent.py` |
| `config.yaml` | A complete, ready‑to‑use DeerFlow config with active stanzas for **claude / minimax / deepseek / qwen / glm / codex / kimi**. All keys are `$VAR` references resolved from `.env` — **no secrets**. Bridge‑tuned: conversation **memory off** (no cross‑run fact contamination), **title generation off** (headless runs), and evidence-ledger summarization at **80K tokens** with a 16K recent tail; the whole discarded segment is summarized so early KIQ evidence is not silently lost. | → `deer-flow/config.yaml` (only if absent; never clobbers an existing one — diff against this copy to pick up new stanzas/tuning). |

## Automated install (recommended)

From the project root:

```bash
./setup.sh
```

It assembles the engine into `deer-flow/` (gitignored) — **preferring** the pinned DeerFlow 2.0
build vendored at `deer-flow-2.0-m1-rc3/`, and falling back to a shallow upstream clone if that
vendor dir is absent — then drops the research driver in, applies the provider + middleware
patches, installs the overhauled deep-research skill, installs `config.yaml` if there isn't one,
and builds DeerFlow's isolated venv (Python 3.12). Re‑running is safe (idempotent). Overrides:

- `DEERFLOW_DIR` — where to put / find the runtime checkout (default: `./deer-flow` in the repo root).
- `DEERFLOW_VENDOR_DIR` — the vendored 2.0 source to seed from (default: `./deer-flow-2.0-m1-rc3`).
  Drop a newer `deer-flow-2.0-*` build here to pin a different engine.
- `DEERFLOW_REPO` — fallback clone URL (default: the upstream ByteDance repo).
- `DEERFLOW_REF` — fallback commit/branch to pin (used only when no vendor dir is present; set
  `DEERFLOW_REF=main` to track upstream HEAD instead).

To upgrade the engine later, delete `deer-flow/` (or drop a newer `deer-flow-2.0-*` vendor dir)
and re-run `./setup.sh`.

## Manual install (equivalent steps)

```bash
# 1. Seed the runtime from the vendored DeerFlow 2.0 build (the backend auto-detects ./deer-flow).
#    (No vendor dir? Fall back to: git clone --depth 1 https://github.com/bytedance/deer-flow deer-flow)
cp -R deer-flow-2.0-m1-rc3 deer-flow

# 2. Drop the bridge entry point in
cp deerflow_bridge/deerflow_research.py deer-flow/deerflow_research.py

# 3. Apply the provider + middleware patches and the overhauled skill
cp deerflow_bridge/patches/models/*.py \
   deer-flow/backend/packages/harness/deerflow/models/
cp deerflow_bridge/patches/middlewares/*.py \
   deer-flow/backend/packages/harness/deerflow/agents/middlewares/
cp -R deerflow_bridge/skills/deep-research/. \
   deer-flow/skills/public/deep-research/
python3 deerflow_bridge/patches/apply_lead_agent_overlays.py deer-flow
python3 deerflow_bridge/patches/apply_model_factory_overlays.py deer-flow

# 4. Install the ready-to-use config (keys come from .env via $VAR)
cp deerflow_bridge/config.yaml deer-flow/config.yaml

# 5. Build DeerFlow's isolated venv (DeerFlow 2.0 pins Python 3.12)
UV_PROJECT_ENVIRONMENT=deer-flow/backend/.venv uv sync --project deer-flow/backend --python 3.12
```

The backend finds DeerFlow via `DEERFLOW_DIR` (defaults to `./deer-flow` in the repo) and the
research model via `DEERFLOW_MODEL` (`claude | minimax | deepseek | qwen | glm | codex | kimi`). See the
main `README.md` and `DEERFLOW_INTEGRATION.md` for the full contract.

## Notable bridge hardening (in `deerflow_research.py`)

- **Pre-flight credential check for every model** — runs BEFORE the DeerFlow client/config is constructed: `claude` (OAuth token present/fresh), `codex` (`~/.codex/auth.json`), and each API model (`kimi`/`minimax`/`deepseek`/`qwen`/`glm` → its `$KEY` env var). Fails fast (exit 3) with the exact variable to set instead of an opaque traceback mid-research.
- **Provider-key env hygiene** — DeerFlow's config loader greedily resolves every `$VAR` in `config.yaml`, so one unset key used to crash even the default claude path on standalone runs; the bridge now presets empty defaults for all known provider key vars (MiroFish's backend does the same before spawning it).
- **LLM-error guard** — a degraded provider message (rate limit, `422 new_sensitive`, `400 bad_request`, connection error) is never mistaken for a real research report; the run fails fast instead of contaminating the pipeline.
- **Tool-free synthesis net** — if the research agent exhausts its step budget on tool calls before writing, *or* hits a provider **structural** error on the final write (e.g. MiniMax `400 user name must be consistent`), the report is synthesized directly from the gathered, thread-checkpointed research via a clean single-turn call. (Only a genuine content-moderation block is excluded, since re-sending the same content would just be blocked again.)
- **Multi-pass `deep` research** — `quick` and `standard` remain single-turn research runs, while `deep` now runs a staged protocol in one thread: opening source map → primary evidence sweep → actor/incentive analysis → contradiction/risk testing → forecast-input pass → tool-free long-form synthesis. This makes deep runs slower, but much more detailed and less likely to stop after a short generic dossier.
- **Longer, richer reports** — a higher synthesis trigger and a model‑aware context cap (up to ~900K chars for million‑token models: minimax / qwen / deepseek) with explicit length mandates in the research/synthesis prompts. Deep synthesis targets an 8,000–12,000 word evidence-backed dossier when the model can support it.
- **`<think>` stripping** for reasoning models (MiniMax‑M3 inlines its chain‑of‑thought).

## Compaction evidence and recovery (ASTRA-01/02)

The tracked summarization overlay validates a successful model response and commits the original message partition before returning a message replacement. It archives the complete original LangChain dictionaries, including tool-call/result relationships and prior summary metadata. Empty, malformed, failed, or unarchivable summaries raise `ResearchCompactionError`; optional memory hooks run only after the durable commit.

A replacement message carries `additional_kwargs.drf_compaction` with schema `research-compaction/v1`, producer identity, summary/message/thread identity, and digests of the summary and original messages. Global and actor synthesis share the same collector and require an exact matching SQLite producer record. This attests to derivation. It does not mint a search-result receipt or establish that a generated claim was fetched independently. Ordinary human instructions and forged summary envelopes remain excluded.

Storage selection is explicit:

- The orchestrator sets `RESEARCH_COMPACTION_DB` to the handoff directory's `research_compaction.sqlite3`, independently of the tool-budget enable flag and rotating budget epochs.
- Direct bridge CLI execution supplies the same handoff-local default when the variable is absent.
- A native DeerFlow gateway/CLI session without research DB variables uses its configured `Paths.base_dir` archive. It keeps failures local to that session rather than setting the bridge's run-wide stop.
- `RESEARCH_BUDGET_DB` remains a compatibility fallback for callers that configure it directly. No in-memory archive or silent truncation is permitted. Oversized inputs stop safely (64 MiB original-message JSON; 4 MiB summary).

The SQLite commit uses `synchronous=FULL` and completes before removal. The compaction database is an evidence dependency; retain it with the handoff/checkpoint. Identical writes under one message ID are idempotent, while conflicting writes stop. Setup and runtime sync install the helper beside both the bridge script and native backend entry point.

Within one research bridge process, the first compaction failure closes admission to subsequent provider calls across its lanes. Calls already in progress may finish. The bridge preserves confirmed source receipts, avoids fetch retries after the stop, rejects completed-pass/publication updates, and returns reserved exit code **4**. Failure metadata carries the safe reason and failed thread; the exit code remains authoritative if a full disk prevents that metadata from being written. The parent binds metadata to the current child launch nonce, skips salvage and automatic synthesis retries, preserves stopped attempt directories, and refuses queued outer-lane launches. Already-running sibling subprocesses may finish and preserve their work. This is not a cross-process cumulative spending limit.

Before a first pass, the bridge saves the thread discovery checkpoint. An admitted resume preserves the existing checkpoint's exact progress and identity instead of replacing them with an empty record. Resuming an interrupted first pass requires retained thread evidence; absence does not silently launch a replacement thread. A typed stop requires investigation and an explicit subsequent resume through the existing lineage/provider checks. Do not restart a healthy saved pipeline to exercise this boundary.

Offline acceptance covers both summary paths, timeout/rate-limit/empty/malformed output, archive failure, native startup/isolation, exact message archival, SQLite close/reopen and same-thread resume, actual client serialization, forged/tampered envelopes, both synthesis consumers, shared provider admission, preserved resume bytes, and parent stop propagation when failure metadata is unavailable. See the new `test_research_compaction*` / `test_astra_compaction_bridge.py` backend tests and the `test_summarization_failure_contract.py` / `test_provider_compaction_stop.py` overlay tests. Implementation progress and exact verification commands are in [the optimization handoff](../docs/handoff/astra-optimization/handoff.md) and [the improvement ledger](../astra-improvement-state.json).
