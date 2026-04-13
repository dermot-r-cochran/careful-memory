# ADR-0007: Append-Only Belief Store

**Date:** 2024-01-01  
**Status:** Accepted

---

## Context

Memory systems that allow in-place mutation of beliefs lose their audit trail.  If a record's `object_value` or Bayesian counters can be overwritten, there is no way to reconstruct the belief history, detect retroactive manipulation, or explain why the system currently holds a given belief.

Additionally, agents that can directly delete or overwrite records could erase inconvenient history — intentionally or through bugs.

---

## Decision

`MemoryRecord` is **append-only** after creation.  The only permitted in-place mutations are:

- `status` — may transition to `contradicted`, `superseded`, `retracted`, or `archived` to reflect lifecycle changes.
- `alpha`, `beta`, `last_decayed_at`, `updated_at` — updated by the decay module as a controlled platform operation.

Contradictions and supersessions are expressed by creating a **new record** that references the old one:

```
new_record.contradicts = [old_record.id]   # old record: status → contradicted
new_record.supersedes  = [old_record.id]   # old record: status → superseded
```

The old record is preserved in storage; its `status` field is updated to reflect the relationship.  A record with `status = retracted` (explicit withdrawal) cannot be further contradicted.

Evidence history (individual `EvidenceEvent` objects) is similarly append-only.

---

## Consequences

**Benefits**

- Full belief lineage is always available: given any record, it is possible to trace its origin, every evidence event that shaped it, and which records it superseded or contradicted.
- Retroactive manipulation is detectable: anomalous edits to historical records stand out against the append-only baseline.
- The system can answer "why does the agent believe X?" with a complete evidence chain.
- Archiving (via decay) does not delete data; archived records remain queryable.

**Obligations**

- Storage implementations must enforce that creates and status-updates are the only write operations; bulk updates or deletes must be restricted to platform-level maintenance operations (e.g. purging records for a deleted user, subject to data-retention policy).
- `MemoryRecord.model_config = {"frozen": False}` is required because the Pydantic model needs to allow status and Bayesian counter updates by the platform.  However, callers outside the core modules must treat records as logically immutable.
- Tests must verify that contradicting or superseding a record creates a new record rather than modifying the existing one.

**Trade-offs**

- Storage grows monotonically.  Old, superseded, and archived records accumulate over time and must be managed by a retention policy.
- Querying "current beliefs" requires filtering by `status = active` in every query; storage indexes on `(context_id, status)` are essential for performance at scale.
- The status field being mutable on an otherwise immutable Pydantic model is a deliberate exception documented in the model's `model_config` comment.  It is a controlled inconsistency.
