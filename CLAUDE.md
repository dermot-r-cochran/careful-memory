# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`careful-memory` is a production-grade long-term memory system for LLM agents: a persistent, per-user belief store with Bayesian confidence updates, time-based decay, and explicit contradiction tracking. All learning happens in memory, not training — it is a write-gated platform service, not an agent-writable database. Python 3.11+, src-layout package under `src/careful_memory/`, Pydantic v2 domain models, SQLAlchemy storage.

## Commands

```bash
pip install -e ".[dev]"    # editable install — required: src-layout means bare pytest fails on imports
pytest                      # full suite (config in pyproject.toml: testpaths=["tests"], -v --tb=short)
pytest tests/test_gate.py   # one file; narrow further with -k <pattern> or ::TestClass::test_name
pytest --cov=careful_memory --cov-fail-under=91   # what CI runs; the 91% is a ratchet — raise with tests that earn it, never lower
ruff check .                # lint (CI job; line-length 100, E501 ignored)
mypy src                    # type-check, strict mode
```

CI (`.github/workflows/ci.yml`) runs pytest with the coverage ratchet on Python 3.11/3.12/3.13 plus `ruff check .`. **mypy is deliberately not a CI job yet** — it has known findings, and the job arrives in the same change that fixes them; don't add the job without the fixes. The `azure` extra (pyodbc, azure-identity) is a deployment concern, never a test dependency.

## Architecture

An agent never touches storage. Every interaction goes through `ToolDispatcher` (`tools/dispatcher.py`), which exposes exactly three tools — `propose_belief`, `report_evidence`, `query_beliefs` — and routes writes through a three-stage pipeline where a failure at any stage stops the request:

1. **MetaGate** (`core/meta_gate.py`) — stateless reasoning-quality check; blocks proposals with no documented evidence type.
2. **WriteGate** (`core/gate.py`) — hard rules: context isolation, authority ordering (`user < system < verified_system`), rate limits, outlier rejection.
3. **MemoryReviewer** (`review/reviewer.py`) — corpus-level judgment: near-duplicates, mass contradiction (>25% of active records), direct high-confidence semantic assertions. New checks are pure `_check_*` functions in `reviewer.py`.

Approved beliefs are `MemoryRecord`s (`models/memory.py`): subject–predicate–object triples with Beta(α,β) counters; confidence is always derived as `α/(α+β)`, never written. `core/bayesian.py`, `core/decay.py` and `core/contradiction.py` implement updates, symmetric decay toward the Beta(1,1) prior, and contradiction/supersession. `MemoryService` (`service.py`) orchestrates; `core/summarizer.py` + `inference/prompt.py` produce the read side — a confidence-weighted system-prompt block injected at inference time. Storage is behind the `MemoryStore` ABC (`storage/base.py`), SQLite default.

### Invariants a change must not violate

The ADRs in `docs/adr/` (0001–0017, indexed in `docs/adr/README.md`) are the decision record; read the relevant one before touching its area. The ones that function as safety invariants, not preferences:

- **No LLM self-reinforcement (ADR-0006).** `EvidenceType` intentionally has no `llm_inference` value, and prompt-assembly output is never fed back into the belief store. Do not add such an evidence type or any code path where model output increments α.
- **Append-only belief store (ADR-0007).** Records are never overwritten or deleted; contradiction/supersession creates a new record referencing the old, and only `status`, the Bayesian counters, and decay timestamps mutate in place, as controlled platform operations.
- **Context isolation (ADR-0004, ADR-0014).** Every query and write is scoped by `context_id` at every layer — storage, WriteGate, ToolDispatcher, and ownership validation at the API boundary. There is no "list all" without a scope.
- **Confidence is derived, never assigned (ADR-0001).** No API accepts a confidence value; decay shrinks excess evidence symmetrically toward the prior (ADR-0005), never below α,β = 1.
- **The three-tool surface is the whole agent API (ADR-0012).** Don't add tools or hand agents raw `MemoryRecord`s with mutable Bayesian fields.
- **Concurrency and production behaviour**: optimistic locking via `version` (ADR-0015), Redis-backed distributed rate limiting (ADR-0013, superseding 0008), auditable gate/reviewer decisions via telemetry (ADR-0016).

ADRs are immutable once accepted; a changed decision gets a new ADR that supersedes the old one.

## Testing

Mechanics, rationale and extension rules live in `TestingStrategy.md` — read it before adding or changing tests. The load-bearing points: `tests/test_poisoning_and_isolation.py` holds the security invariant guards (poisoned content gaining authority, cross-user leakage) — a failure there is an incident, and weakening one to get a change through is never the fix; anything touching isolation or poisoning resistance extends that file so the guards stay reviewable in one place. New admission/decay/authority rules land with tests on both sides (the case admitted and the case refused). Decay logic takes clocks as parameters so tests stay deterministic.

## Docs map

- `docs/adr/` — decision records (why things are the way they are)
- `docs/architecture/` — C4 diagrams, deployment topology (Azure), risk analysis
- `docs/deployment/production-prerequisites.md` — pre-deployment checklist
- `README.md` — mental model, quick start, safety-guarantee table, extension points

## Related repositories

The map of Dermot's public repositories and what crosses between them is
`RELATED-REPOSITORIES.md` in `dermot-r-cochran/star-rangers`; this section
names only this repository's own neighbours (added 2026-09-29 at his
direction). Nothing below shares code or data with this repository; what is
shared is stated exactly.

- **`dermot-r-cochran/swarm`** (EPISTEME) is the nearest in subject: beliefs
  with confidence and lifecycle state, evidence validated before it may
  influence them, an append-only revision history. The two are independent
  implementations of neighbouring ideas; neither imports the other, and this
  repository's ADRs bind only here.
- **`dermot-r-cochran/world-model`** draws the other boundary: it is governed
  state with causal history, and its ADR-001 says a world model is more than
  retrieval memory. This repository is the memory system in the account; that
  one is not. Nothing crosses.
- **Siblings by convention:** `careful-memory`, `world-model`, `foundation-model`,
  `shadow-architect`, `visual-llm`, `swarm`, `Voting` and
  `architecture-definition-model` all carry a `TestingStrategy.md` that keeps
  testing mechanics apart from the repository's rules; six run CI coverage as a
  ratchet at the measured baseline (`swarm`, `careful-memory`, `world-model`,
  `foundation-model`, `shadow-architect`, `visual-llm`); five keep
  architecture decision records with a guard test each (`swarm`,
  `careful-memory`, `world-model`, `shadow-architect`, the ADM). When a
  convention here needs changing, those are the reference for how it is done
  in the account, and a change to the convention itself is worth landing in
  all of them or in none.
