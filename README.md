# careful-memory

> *"A careful memory that earns its beliefs, forgets responsibly, and never hallucinates authority."*

`careful-memory` is a production-grade long-term memory system for LLM agents, designed to learn slowly, safely, and with evidence. It provides persistent, per-user memory using Bayesian confidence updates, time-based decay, and explicit contradiction tracking — without modifying model weights.

**All learning happens in memory, not training.**

---

## Table of Contents

- [What careful-memory Is (and Is Not)](#what-careful-memory-is-and-is-not)
- [Mental Model](#mental-model)
- [Architecture Overview](#architecture-overview)
- [Core Concepts](#core-concepts)
- [Safety Guarantees](#safety-guarantees)
- [Quick Start](#quick-start)
- [Inference-Time Prompt Injection](#inference-time-prompt-injection)
- [Azure Deployment](#azure-deployment)
- [Extension Points](#extension-points)
- [Development](#development)
- [Architecture Documentation](#architecture-documentation)

---

## What careful-memory Is (and Is Not)

| It IS | It IS NOT |
|---|---|
| A persistent, per-user belief store | A knowledge graph |
| A Bayesian confidence tracker | A training pipeline |
| A write-gated, auditable memory layer | A model fine-tuner |
| A platform service with tightly-scoped agent tools | An agent-writable database |
| A RAG-ready summary generator | An authoritative fact source |

---

## Mental Model

Think of careful-memory as a **careful human memory**:

- It is **slow to trust**: new beliefs start with uniform prior confidence (50%) (`tests/test_bayesian.py::TestInitialConfidence::test_uniform_prior`).
- It is **quick to revise**: contradicting evidence moves the Beta distribution immediately (`tests/test_bayesian.py::TestContradictingEvidence::test_confidence_decreases`).
- It is **never dogmatic**: it decays beliefs that are no longer reinforced (`tests/test_decay.py::TestComputeDecay::test_evidence_mass_reduces`).
- It **never hallucinates authority**: summaries are clearly marked as derived, confidence-weighted artifacts (`tests/test_prompt.py::TestSystemPreamble::test_memory_header_present`).

Beliefs are atoms, not rules. An LLM can *propose* a belief. Only the **platform** decides whether to commit it, and it does so only after:

1. The **MetaGate** passes the proposal's reasoning quality (a documented evidence type), then
2. The **WriteGate** passes hard rules (authority, rate-limits, context isolation), then
3. The **MemoryReviewer** applies reasoned judgment (duplication, mass contradiction, policy).

---

## Architecture Overview

```
  LLM Agent
     │
     │ tool call (propose_belief / report_evidence / query_beliefs)
     ▼
  ToolDispatcher  ◄── only entry point from agents
     │
     ├─ Stage 0: MetaGate         ← reasoning-quality gate
     │     documented evidence type required
     │     never touches α/β
     │
     ├─ Stage 1: WriteGate        ← hard rules
     │     authority levels
     │     rate limits
     │     context isolation
     │     outlier detection
     │
     └─ Stage 2: MemoryReviewer   ← reasoned judgment
           near-duplicate detection
           mass contradiction check
           direct semantic assertion downgrade
           context policy enforcement
     │
     ▼
  Storage (SQLite / Azure SQL)
     │
     ▼
  MemoryService.build_summary()
     │
     ▼
  PromptBuilder.build()           ← inference-time injection
     │
     ▼
  LLM inference (grounded in memory, never modifying it)
```

---

## Core Concepts

### ContextScope

Every memory record belongs to exactly one `ContextScope`. Cross-context access is forbidden at every layer.

```python
scope = ContextScope(
    user_id="azure-ad-object-id",   # Azure AD object ID or UUID
    domain=Domain.personal,          # personal | work | project | global
)
```

### MemoryRecord

Atomic belief unit. Subject-predicate-object triple with:

- **Bayesian counters** `alpha` (support) and `beta` (contradiction)
- **Derived confidence** `= alpha / (alpha + beta)`
- **Decay rate** per memory type
- **Status lifecycle**: `active → contradicted | superseded | retracted | archived`
- **Full provenance**: source origin + authority level

```python
record = MemoryRecord(
    context_id=scope.context_id,
    memory_type=MemoryType.episodic,
    subject=EntityRef(entity_type="person", label="Alice"),
    predicate="prefers",
    object_value="dark mode",
    source=MemorySource(origin="session-123", authority_level=AuthorityLevel.user),
)
# record.confidence == 0.5 (uniform prior — not yet trusted)
```

### Bayesian Confidence

```
confidence = α / (α + β)

Supporting evidence  → α += 1
Contradicting evidence → β += 1
Time decay → both α,β shrink symmetrically toward (1,1)
```

Confidence is **always derived** — never directly written. The write gate rejects any attempt to assert near-certainty from a user-authority source.

### Memory Types and Decay

| Type | Default decay rate | Half-life (excess evidence) |
|---|---|---|
| `episodic` | 5% / day | ~14 days |
| `semantic` | 0.5% / day | ~139 days |
| `procedural` | 1% / day | ~69 days |

Project-domain memories decay **2× faster** than other domains (`tests/test_decay.py::TestDecayRates::test_project_multiplier_applied`).

### Authority Levels

```
user  <  system  <  verified_system
```

A lower-authority source **cannot** overwrite or reinforce a higher-authority belief.

---

## Safety Guarantees

| Guarantee | Enforcement layer | Status |
|---|---|---|
| Agents cannot write to memory directly | ToolDispatcher — only 3 tools exposed | in code, `tests/test_tools.py::TestProposeBelief::test_platform_decides_not_agent` (no test pins the count of three) |
| Caller can only access their own contexts | API-layer context ownership check ([ADR-0014](docs/adr/0014-context-ownership-validation.md)) | designed, not implemented (R-02) |
| Lower-authority evidence rejected | WriteGate (hard rule) | in code, `tests/test_poisoning_and_isolation.py::TestAuthorityEnforcement::test_lower_authority_cannot_update_higher` |
| Rate limit: max 10 evidence events / record / hour | WriteGate — Redis-backed in production ([ADR-0013](docs/adr/0013-distributed-rate-limiting.md)) | in code in-process, `tests/test_gate.py::TestEvidenceUpdate::test_rate_limit_enforced`; Redis-backed: designed, not implemented (R-01) |
| Cross-context reads blocked | Storage query always includes `context_id` | in code, `tests/test_poisoning_and_isolation.py::TestCrossContextIsolation::test_storage_context_isolation` |
| LLM inference alone cannot reinforce memory | `EvidenceType` enum has no `llm_inference` value | in code, `tests/test_poisoning_and_isolation.py::TestSelfReinforcement::test_no_llm_inference_evidence_type` |
| Mass contradiction (>25% of active records) rejected | MemoryReviewer | in code, `tests/test_poisoning_and_isolation.py::TestMassContradiction::test_mass_contradiction_rejected_by_reviewer` |
| Direct high-confidence semantic assertion blocked | MemoryReviewer (modify → episodic, or reject) | in code, `tests/test_poisoning_and_isolation.py::TestDirectSemanticAssertion::test_high_confidence_semantic_downgraded` and `::test_very_high_confidence_semantic_rejected` |
| Near-duplicate writes deferred | MemoryReviewer | in code, `tests/test_poisoning_and_isolation.py::TestNearDuplicate::test_duplicate_deferred` |
| Summaries are never authoritative | `MemorySummary` is a read-only derived artifact | in code, `tests/test_prompt.py::TestSystemPreamble::test_memory_header_present` (the block is headed "derived, confidence-weighted") and `tests/test_tools.py::TestQueryBeliefs::test_query_does_not_modify_storage` |
| Belief history is append-only | Contradiction/supersession creates new records; old ones preserved | in code, `tests/test_contradiction.py::TestApplyContradiction::test_history_is_preserved` |
| Concurrent writes do not silently overwrite each other | Optimistic locking with `version` field ([ADR-0015](docs/adr/0015-optimistic-locking.md)) | designed, not implemented (R-03) |
| All gate/reviewer decisions are auditable | Structured telemetry via ObservabilityAdapter ([ADR-0016](docs/adr/0016-observability-telemetry.md)) | designed, not implemented (R-04) |

Status names the test that guards a row (file, class and test name, as `pytest` addresses it), or the row of the [risk register](docs/architecture/risk-analysis.md) that tracks a guarantee still on the roadmap. The convention, from 2026-10-10: a row that claims a capability names its test or says "designed, not implemented".

---

## Quick Start

```python
from careful_memory import MemoryService
from careful_memory.models import ContextScope, Domain
from careful_memory.storage import SQLiteMemoryStore
from careful_memory.tools.schema import ToolCall, ToolName

# 1. Set up the platform service
store = SQLiteMemoryStore(":memory:")   # or path to a file
svc = MemoryService(store=store)

# 2. Create a user context
scope = ContextScope(user_id="alice-uuid", domain=Domain.personal)
svc.create_context(scope)

# 3. Agent proposes a belief (platform decides)
result = svc.handle_tool_call(ToolCall(
    tool_name=ToolName.propose_belief,
    arguments={
        "subject_label": "Alice",
        "subject_type": "person",
        "predicate": "prefers",
        "object_value": "dark mode",
    },
    context_id=scope.context_id,
))
print(result.review_decision)   # "approve" or "modify"
print(result.data["confidence"])  # 0.5 (uniform prior — not yet trusted)

# 4. Report external evidence
svc.handle_tool_call(ToolCall(
    tool_name=ToolName.report_evidence,
    arguments={
        "record_id": result.data["record_id"],
        "supports": True,
        "evidence_type": "user_restatement",
    },
    context_id=scope.context_id,
))

# 5. Build a memory-grounded prompt for LLM inference
prompt = svc.assemble_prompt(
    context_ids=[scope.context_id],
    user_task="What theme should I use?",
)
print(prompt.system_prompt)
# System:
# You are an assistant that must ground all personalization
# and long-term assumptions ONLY in the provided memory context.
# ...
```

---

## Inference-Time Prompt Injection

The canonical inference-time prompt format injects memory as a confidence-weighted system block:

```
System:
You are an assistant that must ground all personalization
and long-term assumptions ONLY in the provided memory context.

If memory is uncertain or contradictory, express uncertainty.

Context: Persistent Memory (derived, confidence-weighted)
- It is probably that Alice prefers dark mode.
- It is possibly that Alice uses vim.

Instructions:
- Do not invent preferences or beliefs
- Do not generalize beyond memory

User:
What editor should I open?
```

The agent receiving this prompt is grounded in verifiable, decay-adjusted beliefs. If no memories are available, the placeholder `"No reliable memories are available..."` is injected instead — explicitly signalling the model not to invent.

**INVARIANT**: The output of prompt assembly is never fed back into the belief store.

---

## Azure Deployment

See the [Deployment Architecture](docs/architecture/deployment-architecture.md) for the full Azure production topology and the [Production Prerequisites Checklist](docs/deployment/production-prerequisites.md) for the complete pre-deployment checklist.

### Recommended Architecture

```
┌────────────────────────────────────────────┐
│  Azure Container Apps (or AKS)             │
│  Stateless API — careful-memory service    │
│  Identity: Azure Managed Identity          │
└────────────────────┬───────────────────────┘
                     │ connection string (from Key Vault)
          ┌──────────▼──────────┐     ┌──────────────────────┐
          │  Azure SQL Database │     │  Azure Cache for Redis│
          │  or PostgreSQL      │     │  (distributed rate    │
          │  (SqlAlchemyStore)  │     │   limiting, ADR-0013) │
          └─────────────────────┘     └──────────────────────┘
```

### Environment Variables

| Variable | Description | Status |
|---|---|---|
| `DATABASE_URL` | SQLAlchemy connection string (from Key Vault) | designed, not implemented (R-05); the store is constructed by the caller |
| `REDIS_URL` | Redis TLS connection string for distributed rate limiting (required for multi-replica; see [ADR-0013](docs/adr/0013-distributed-rate-limiting.md)) | designed, not implemented (R-01) |
| `MEMORY_RATE_LIMIT_MAX` | Override default rate limit (default: 10) | designed, not implemented (R-01); the limit is the constant `RATE_LIMIT_MAX_EVENTS` in `core/gate.py` |
| `MEMORY_ARCHIVE_THRESHOLD` | Confidence below which records are archived (default: 0.30) | not read; the threshold is `ARCHIVE_THRESHOLD` / the `archive_threshold` argument in `core/decay.py` (no risk-register row) |

No code under `src/` reads environment variables yet; the variables above are the deployment design. In the same spirit, `sqlalchemy>=2.0` is a declared runtime dependency that nothing under `src/` imports: it is kept (decision of 2026-10-10) as the first step toward the `SqlAlchemyStore` of [ADR-0017](docs/adr/0017-sqlalchemy-store.md), which is designed, not implemented (R-05).

### Key Vault Integration

Store `DATABASE_URL` in Azure Key Vault and inject it at runtime via Managed Identity. The application code reads only environment variables — no secrets in source.

### Local Development

```bash
# SQLite (no extra setup needed)
DATABASE_URL=sqlite:///./dev.db

# Local PostgreSQL (Docker)
docker run -e POSTGRES_PASSWORD=dev -p 5432:5432 postgres:16
DATABASE_URL=postgresql+psycopg2://postgres:dev@localhost/careful_memory
```

---

## Extension Points

| Concern | Extension point |
|---|---|
| Production storage | Implement `MemoryStore` ABC; provide `SqlAlchemyStore` (designed in [ADR-0017](docs/adr/0017-sqlalchemy-store.md), not yet implemented) |
| Distributed rate limits | Replace `_RateLimitWindow` in `WriteGate` with Redis-backed store |
| Custom context policies | Pass `ContextPolicy` to `MemoryReviewer` (`tests/test_poisoning_and_isolation.py::TestContextPolicy::test_policy_min_authority_enforced`) |
| Vector embeddings | Populate `MemorySummary.embedding_stub` with any byte-serialised vector (a placeholder field; nothing reads it, no test yet) |
| Custom decay rates | Pass `override` to `decay_rate_for()` (`tests/test_decay.py::TestDecayRates::test_override_respected`) or set `MemoryRecord.decay_rate` (no test yet) |
| Additional review checks | Add pure functions to `reviewer.py` following the `_check_*` pattern |
| Observability | Wrap `ToolDispatcher.dispatch()` with Azure Application Insights telemetry |

---

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check src tests

# Type-check
mypy src
```

### Test Coverage

176 tests across 10 files:

```
tests/test_allocation.py        — Conserved-mass salience allocation (38 tests)
tests/test_bayesian.py          — Bayesian update correctness (21 tests)
tests/test_contradiction.py     — Contradiction / supersession handling (11 tests)
tests/test_decay.py             — Time decay behaviour (13 tests)
tests/test_gate.py              — Write-gate rules (15 tests)
tests/test_meta_gate.py         — MetaGate reasoning-quality gate (17 tests)
tests/test_poisoning_and_isolation.py — Memory poisoning defences + isolation (18 tests)
tests/test_prompt.py            — Inference-time prompt assembly (13 tests)
tests/test_reviewer.py          — MemoryReviewer decisions (15 tests)
tests/test_tools.py             — Agent tool dispatch (15 tests)
```

---

## Architecture Documentation

Full architecture documentation is in [`docs/architecture/`](docs/architecture/):

- **[System Context (C1)](docs/architecture/system-context.md)** — careful-memory in its operational environment
- **[Container Diagram (C2)](docs/architecture/container-diagram.md)** — High-level technology choices
- **[Component Diagrams (C3)](docs/architecture/component-diagrams.md)** — Internal component structure
- **[Deployment Architecture](docs/architecture/deployment-architecture.md)** — Azure production topology
- **[Risk Analysis](docs/architecture/risk-analysis.md)** — Architectural risks and mitigations

### Production Readiness ADRs

| ADR | Title | Risk addressed |
|-----|-------|---------------|
| [ADR-0013](docs/adr/0013-distributed-rate-limiting.md) | Distributed Rate Limiting (Redis) | Per-replica rate limit bypass in multi-instance deployments |
| [ADR-0014](docs/adr/0014-context-ownership-validation.md) | Context Ownership Validation | Cross-user context access at API boundary |
| [ADR-0015](docs/adr/0015-optimistic-locking.md) | Optimistic Locking | Lost writes under concurrent load |
| [ADR-0016](docs/adr/0016-observability-telemetry.md) | Observability & Telemetry | Invisible gate/reviewer decisions in production |
| [ADR-0017](docs/adr/0017-sqlalchemy-store.md) | SqlAlchemy Production Storage | SQLite unsuitability for multi-writer deployments |

All ADRs: [`docs/adr/README.md`](docs/adr/README.md)

---

## License

See [LICENSE](LICENSE).

