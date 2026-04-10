# ADR-0003: Three-Stage Write Pipeline (MetaGate → WriteGate → MemoryReviewer)

**Date:** 2024-01-01  
**Status:** Accepted

---

## Context

Memory writes from LLM agents are inherently untrusted.  A single monolithic validator is difficult to reason about, test, and extend.  The failure modes are also different:

- Some rejections are unconditional (wrong context_id, excessive rate, authority downgrade) — they should never reach reasoned judgment.
- Some rejections require structural judgment about the memory corpus (near-duplicate, mass contradiction) — they require access to existing records.
- Some proposals are malformed at the reasoning level (no evidence type documented, blank predicate) — they should fail early, before storage is consulted at all.

Separating these concerns into distinct stages makes each stage independently testable and replaceable.

---

## Decision

Every write request passes through three stages in sequence.  A failure at any stage stops the pipeline.

```
Proposal
   │
   ▼
┌───────────┐
│ MetaGate  │  Stateless, pure. Evaluates reasoning quality of the proposal.
│           │  Blocks proposals with no documented evidence type (LOW signal).
│           │  Returns: MetaAssessment (low / medium / high)
└─────┬─────┘
      │ is_gate_pass=True
      ▼
┌───────────┐
│ WriteGate │  Stateful (rate-limit windows). Enforces hard rules:
│           │  context isolation, authority ordering, rate limits, outlier rejection.
│           │  Returns: GateResult (allowed / flagged / rejected)
└─────┬─────┘
      │ is_allowed=True
      ▼
┌─────────────────┐
│ MemoryReviewer  │  Stateless judgment. Consults existing records.
│                 │  Checks duplication, mass contradiction, direct semantic assertion.
│                 │  May restrict its own actions when MetaAssessment is MEDIUM.
│                 │  Returns: ReviewResult (approve / modify / defer / reject)
└─────┬───────────┘
      │ approve / modify
      ▼
   Storage
```

The `MetaAssessment` level propagates into `MemoryReviewer`: when the level is `MEDIUM`, the reviewer applies a restricted set of allowed actions (it may approve or reject, but not freely modify — see `reviewer.py` for the constraint).

---

## Consequences

**Benefits**

- Each stage has a single, well-documented responsibility and can be tested in isolation without mocking the others.
- The MetaGate is pure (no I/O, no state); it can be called speculatively without side effects.
- The WriteGate's hard rules are enumerated and documented in `core/gate.py`; no rule can silently bypass it.
- The MemoryReviewer's checks follow the `_check_*` pure-function pattern, making it straightforward to add new checks without touching existing ones.
- The `MetaAssessment.restricts_review` flag allows the reviewer to adapt without duplicating the MetaGate's logic.

**Obligations**

- The pipeline must be invoked in order; bypassing any stage invalidates the safety guarantees.  `ToolDispatcher` is the only caller of the full pipeline; all other code paths (e.g. direct `MemoryService` calls from platform code) must still invoke the gate and reviewer.
- The MetaGate must never make persistent changes; violating this would break its idempotency guarantee.
- The MemoryReviewer must never directly modify Bayesian counters; that happens only after it returns an `approve` or `modify` decision.

**Trade-offs**

- Three separate call sites add latency for each write.  For a memory system that is deliberately slow to trust, this is acceptable and expected.
- The MetaGate's weakest-link aggregation means a single LOW signal blocks a proposal even if all other signals are HIGH.  This is intentionally conservative.
