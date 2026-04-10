# ADR-0005: Symmetric Time-Based Decay Toward the Uniform Prior

**Date:** 2024-01-01  
**Status:** Accepted

---

## Context

Memories that are not reinforced over time should become less certain — but they should not be deleted.  Several decay strategies were considered:

| Strategy | Problem |
|----------|---------|
| Delete records older than N days | Irreversible; no audit trail; abrupt cliff |
| Linearly reduce `confidence` field | Confidence becomes writable; violates the Bayesian invariant |
| Archive records below a threshold (no decay) | Records with high initial confidence never decrease without explicit contradiction |
| Decay toward zero | Eventually produces `alpha=0, beta=0` which is statistically invalid (Beta(0,0) is undefined) |
| Decay toward the uniform prior Beta(1,1) (chosen) | Converges to maximum uncertainty (confidence=0.5), not deletion; preserves statistical validity |

The decay formula must:
1. Reduce certainty over time without erasing the record.
2. Preserve the confidence ratio for moderate asymmetry (a record at 0.8 should not swing wildly on first decay).
3. Never push `α` or `β` below the prior floor of 1.0.

---

## Decision

Decay is applied **symmetrically** to both `α` and `β` using an exponential decay factor:

```
decay_factor = (1 − decay_rate) ^ elapsed_days
α' = max(1.0 + (α − 1.0) × decay_factor, 1.0)
β' = max(1.0 + (β − 1.0) × decay_factor, 1.0)
```

This formula:
- Shrinks the *excess evidence* `(α−1)` and `(β−1)` symmetrically.
- A record with no evidence beyond the prior (α=1, β=1) does not change.
- A record with strong evidence slowly converges toward Beta(1,1) (confidence = 0.5).
- `α` and `β` never fall below 1.0 (the prior floor).

Default decay rates by memory type:

| Type | Rate / day | Evidence half-life |
|------|-----------|-------------------|
| `episodic` | 5% | ~14 days |
| `semantic` | 0.5% | ~139 days |
| `procedural` | 1% | ~69 days |

Project-domain memories decay at **2× the base rate** (memories scoped to a specific project become irrelevant once the project ends).

When decayed confidence falls below `ARCHIVE_THRESHOLD` (default 0.30) and the record is `active`, `apply_decay_to_record` returns a new record with `status = archived`.  Archiving is not deletion.

---

## Consequences

**Benefits**

- Decay is entirely non-destructive; archived records remain queryable for audit.
- The symmetric formula is easy to reason about and test: given enough time, every record converges to `confidence = 0.5` regardless of its original evidence distribution.
- Decay is stateless from the module's perspective — callers supply the elapsed time; there is no background timer required.
- Per-record `decay_rate` override allows fine-grained tuning without changing defaults.

**Obligations**

- `apply_decay_to_record` must be called before including a record in a prompt or summary; stale `α`/`β` values produce inflated confidence.
- `MemoryService.run_decay()` (or equivalent scheduled task) must be called periodically in production to keep stored values current.
- The archive threshold is configurable via `MEMORY_ARCHIVE_THRESHOLD` environment variable; changing this in production will retroactively change which records appear active.

**Trade-offs**

- The exponential decay model is a heuristic.  It does not model actual human memory forgetting curves (which are better described by power laws).  The chosen model is simpler, more predictable, and easier to tune.
- Symmetric decay preserves the confidence ratio only approximately when `α` and `β` are very asymmetric.  In extreme cases (e.g. `α=100, β=1`), after significant decay the ratio is preserved faithfully; near the prior floor, slight distortion is possible — but at that point the record is approaching the archive threshold anyway.
