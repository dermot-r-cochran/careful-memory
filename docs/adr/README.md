# Architectural Decision Records

This directory contains the Architectural Decision Records (ADRs) for `careful-memory`.

ADRs capture significant design choices, the context that motivated them, and the consequences that follow.  Each record is immutable once accepted; superseded decisions link forward to their replacement.

**Implemented** says whether the decision is in `src/` today, checked against the code on 2026-10-06. An accepted ADR is a decision, not a claim about the code; where the two differ, this column is the place that says so, and the ADR text stays as accepted. The risk register in [`../architecture/risk-analysis.md`](../architecture/risk-analysis.md) tracks the work outstanding.

| ID | Title | Status | Implemented |
|----|-------|--------|-------------|
| [ADR-0001](0001-bayesian-beta-confidence-model.md) | Beta(α,β) Bayesian confidence model | Accepted | yes |
| [ADR-0002](0002-spo-triple-as-atomic-belief-unit.md) | Subject-predicate-object triple as atomic belief unit | Accepted | yes |
| [ADR-0003](0003-three-stage-write-pipeline.md) | Three-stage write pipeline: MetaGate → WriteGate → MemoryReviewer | Accepted | yes |
| [ADR-0004](0004-context-isolation-via-contextscope.md) | Context isolation via ContextScope | Accepted | yes |
| [ADR-0005](0005-symmetric-decay-toward-prior.md) | Symmetric time-based decay toward the uniform prior | Accepted | yes (the `MEMORY_ARCHIVE_THRESHOLD` override is not read; the threshold is an argument) |
| [ADR-0006](0006-no-llm-self-reinforcement.md) | Exclusion of LLM inference from evidence types | Accepted | yes |
| [ADR-0007](0007-append-only-belief-store.md) | Append-only belief store | Accepted | yes |
| [ADR-0008](0008-in-process-rate-limiting.md) | In-process rate limiting with explicit Redis upgrade path | Superseded by [ADR-0013](0013-distributed-rate-limiting.md) | yes, and still what runs (the `MEMORY_RATE_LIMIT_MAX` override is not read) |
| [ADR-0009](0009-pydantic-v2-domain-models.md) | Pydantic v2 as domain model foundation | Accepted | yes |
| [ADR-0010](0010-storage-abstraction-sqlite-default.md) | Storage abstraction (MemoryStore ABC) with SQLite default | Accepted | yes (`DATABASE_URL` is not read) |
| [ADR-0011](0011-inference-time-prompt-injection.md) | Inference-time prompt injection instead of model training | Accepted | yes |
| [ADR-0012](0012-three-tool-agent-api.md) | Strictly-bounded three-tool agent API (ToolDispatcher) | Accepted | yes |
| [ADR-0013](0013-distributed-rate-limiting.md) | Distributed rate limiting (Redis) | Accepted | not yet (R-01) |
| [ADR-0014](0014-context-ownership-validation.md) | Context ownership validation at API boundary | Accepted | not yet (R-02) |
| [ADR-0015](0015-optimistic-locking.md) | Optimistic locking for concurrent writes | Accepted | not yet (R-03; `MemoryRecord` has no `version` field) |
| [ADR-0016](0016-observability-telemetry.md) | Observability & telemetry (Azure Application Insights) | Accepted | not yet (R-04) |
| [ADR-0017](0017-sqlalchemy-store.md) | SqlAlchemy production storage | Accepted | not yet (R-05; `storage/sqlalchemy_store.py` does not exist) |

## Format

Each ADR uses the following sections:

- **Status** – Proposed / Accepted / Deprecated / Superseded by [ADR-NNNN]
- **Context** – The forces at play and the problem being solved
- **Decision** – What was decided
- **Consequences** – Trade-offs, benefits, and obligations that follow
