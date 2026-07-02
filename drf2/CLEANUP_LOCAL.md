# CLEANUP_LOCAL — local-only junk for the operator to review

These directories/files are **untracked** (not in git) and were deliberately **NOT deleted
by the cleanup automation** — removal is an operator decision. Everything tracked that
REDESIGN.md §4 marked dead has already been `git rm`-ed on branch `deerflow2-redesign`.

| Path | Size | What it is | Recommendation |
|---|---|---|---|
| `deer-flow/` | ~962MB | Assembled DeerFlow runtime (engine + ~900MB venv). **Fully regenerable** by re-running `./setup.sh` (now seeds from `deer-flow-2.0.0/`). | Safe to delete; re-run `./setup.sh` to rebuild. |
| `graphiti-0.29.2/` | ~23MB | Local reference checkout of upstream Graphiti source. Runtime uses the pip-installed `graphiti_core`; this tree is never imported. | Safe to delete (re-download from PyPI/GitHub if needed). |
| `log/` | 0B total | Zero-byte OASIS log debris (`oasis-2026-06-*.log` etc.), all empty files. | Safe to delete. |
| `SwiftMandarin/` | ~28KB | Stray files from an **unrelated Swift project** (Models/Resources/Services/Views). Does not belong in this repo. | Move out of the repo or delete. |
| `logs/` | ~117MB | Claude Code / agent session transcripts. **MAY CONTAIN SECRETS** (API keys echoed in tool output). Already gitignored (`logs/`, `*.jsonl`). | Operator decides: review/scrub before any sharing; delete when no longer needed for forensics. |

Also still present locally (kept intentionally):

- `deer-flow-2.0.0/` — the vendored DeerFlow 2.0 source drop that `setup.sh` now seeds from
  (`DEERFLOW_VENDOR_DIR` default). Newly added to `.gitignore` (was only in
  `.git/info/exclude`). **Keep** — it is the reference source for the DRF-2 rebuild.
- `EXECPLAN.md` and `CLAUDE_ONTOLOGY.md` at repo root were **kept** (not deleted with the
  other planning blobs): `DEERFLOW_INTEGRATION.md` links to `EXECPLAN.md`, and
  `deerflow_bridge/skills/actor-ontology-research/SKILL.md` links to `CLAUDE_ONTOLOGY.md`.
- `backend/scripts/run_twitter_simulation.py` / `run_reddit_simulation.py` were **kept**:
  contrary to REDESIGN.md §4's assumption, `simulation_runner.py` still invokes them for
  single-platform runs (`platform == "twitter"` / `"reddit"` branches, lines ~537/541),
  and `simulation_manager.py` + `api/simulation.py` + two test files reference them.
  Deleting them would break the live single-platform API path. Revisit at cut-over.
