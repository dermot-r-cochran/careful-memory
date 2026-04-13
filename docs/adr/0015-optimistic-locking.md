# ADR-0015: Optimistic Locking for Concurrent Writes

**Date:** 2024-01-01  
**Status:** Accepted

---

## Context

The write pipeline (MetaGate → WriteGate → MemoryReviewer) reads existing memory records during the `MemoryReviewer` stage to perform near-duplicate detection and mass-contradiction checks.  Between the read and the subsequent write, another concurrent request could modify the same records.  This is a classic read-modify-write race condition.

In the current `SQLiteMemoryStore`, SQLite's writer lock serialises all writes, so this race does not manifest in practice.  However, the target production store (`SqlAlchemyStore` with Azure SQL or PostgreSQL) supports concurrent writers.  Without concurrency control, two concurrent `report_evidence` calls for the same `record_id` can both pass the reviewer (each reading the pre-update state) and then both commit, resulting in one update being silently lost.

A lost evidence update is a safety issue: if the first write incremented `alpha` (supporting evidence) and the second write overwrote it without seeing the first, the confidence value is understated and the belief's history is incomplete.

---

## Decision

Concurrency is controlled using **optimistic locking** with a `version` integer field on `MemoryRecord`.

```
MemoryRecord
  version : int  ← starts at 1; incremented on every update
```

The update logic is:

```sql
UPDATE memory_records
SET alpha = ?, beta = ?, status = ?, version = version + 1, updated_at = NOW()
WHERE record_id = ? AND context_id = ? AND version = ?
```

If the `WHERE` clause matches zero rows (because another writer incremented `version` since we read the record), the update returns `rowcount == 0`.  The caller raises `OptimisticLockError` and the request is retried with exponential back-off (up to 3 attempts).

The `version` field is exposed on `MemoryRecord` and persisted in the database.  It is read during the `MemoryReviewer` stage and passed to the storage update call.  The `MemoryStore` ABC is updated to require `version` on update operations.

**Retry policy:**

| Attempt | Delay |
|---------|-------|
| 1 (initial) | — |
| 2 | 50ms jitter |
| 3 | 100ms jitter |
| 4 (fail) | Raise `ConcurrentWriteError` to caller |

---

## Consequences

**Benefits**

- Eliminates silent lost-write bugs in multi-writer deployments without requiring database-level row locking.
- Optimistic locking has near-zero overhead on the happy path (no lock acquisition, no waiting); contention handling is only invoked on actual conflicts.
- The retry policy makes transient conflicts transparent to callers in the vast majority of cases.
- `SQLiteMemoryStore` can implement `version` as a no-op check (since SQLite already serialises writes), preserving test compatibility.

**Obligations**

- `MemoryRecord` must include a `version` field (default: 1).
- All `MemoryStore` implementations must check `version` on every update and raise `OptimisticLockError` on mismatch.  Implementations that do not enforce the check are non-conformant.
- The retry logic must be implemented in `MemoryService` (not in the store or the gate), so it operates at the full pipeline level (re-reading and re-reviewing, not just re-updating).
- Tests must cover the optimistic lock conflict scenario: two concurrent writes to the same record, where the second detects the conflict and retries.

**Trade-offs**

- Retries add latency on contention.  For the expected workload (evidence events are infrequent and per-record contention is low), the retry path is rare.  Under high-contention load (e.g. a bulk evidence import), the retry overhead could be significant; a bulk-import path should use explicit transactions rather than the retry mechanism.
- The `version` field adds one integer column to the `memory_records` table.  This requires a database migration when upgrading from the unversioned schema.
- Optimistic locking does not prevent phantoms (new records inserted between a `list_active` read and a subsequent write).  The mass-contradiction check reads existing active records; a new record inserted concurrently will not be seen.  This is acceptable: the check is a best-effort safety heuristic, not a hard invariant.
