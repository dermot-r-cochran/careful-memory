# Architectural Decision Records

This directory contains the Architectural Decision Records (ADRs) for `careful-memory`.

ADRs capture significant design choices, the context that motivated them, and the consequences that follow.  Each record is immutable once accepted; superseded decisions link forward to their replacement.

| ID | Title | Status |
|----|-------|--------|
| [ADR-0001](0001-bayesian-beta-confidence-model.md) | Beta(α,β) Bayesian confidence model | Accepted |
| [ADR-0002](0002-spo-triple-as-atomic-belief-unit.md) | Subject-predicate-object triple as atomic belief unit | Accepted |
| [ADR-0003](0003-three-stage-write-pipeline.md) | Three-stage write pipeline: MetaGate → WriteGate → MemoryReviewer | Accepted |
| [ADR-0004](0004-context-isolation-via-contextscope.md) | Context isolation via ContextScope | Accepted |
| [ADR-0005](0005-symmetric-decay-toward-prior.md) | Symmetric time-based decay toward the uniform prior | Accepted |
| [ADR-0006](0006-no-llm-self-reinforcement.md) | Exclusion of LLM inference from evidence types | Accepted |
| [ADR-0007](0007-append-only-belief-store.md) | Append-only belief store | Accepted |
| [ADR-0008](0008-in-process-rate-limiting.md) | In-process rate limiting with explicit Redis upgrade path | Accepted |
| [ADR-0009](0009-pydantic-v2-domain-models.md) | Pydantic v2 as domain model foundation | Accepted |
| [ADR-0010](0010-storage-abstraction-sqlite-default.md) | Storage abstraction (MemoryStore ABC) with SQLite default | Accepted |
| [ADR-0011](0011-inference-time-prompt-injection.md) | Inference-time prompt injection instead of model training | Accepted |
| [ADR-0012](0012-three-tool-agent-api.md) | Strictly-bounded three-tool agent API (ToolDispatcher) | Accepted |

## Format

Each ADR uses the following sections:

- **Status** – Proposed / Accepted / Deprecated / Superseded by [ADR-NNNN]
- **Context** – The forces at play and the problem being solved
- **Decision** – What was decided
- **Consequences** – Trade-offs, benefits, and obligations that follow
