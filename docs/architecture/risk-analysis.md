# Risk Analysis

This document catalogues the architectural risks identified during production readiness review, their current mitigations, residual risk, and roadmap status.

## Risk Register

### Critical Risks (P0 — Production Blockers)

| ID | Risk | Current State | Mitigation | ADR | Status |
|----|------|--------------|------------|-----|--------|
| R-01 | **Distributed rate limiting**: In-process rate limit is per-replica; multi-replica deployments multiply effective limit | `_RateLimitWindow` in-process dict | Replace with Redis-backed sliding window | [ADR-0013](../adr/0013-distributed-rate-limiting.md) | 🔴 Not implemented |
| R-02 | **Context ownership not enforced at API boundary**: Any caller with a valid token can submit requests for any `context_id` | `context_id` checked only inside pipeline | Add ownership validation middleware at API layer | [ADR-0014](../adr/0014-context-ownership-validation.md) | 🔴 Not implemented |
| R-03 | **Lost writes under concurrent load**: Two concurrent writes can both pass the reviewer and overwrite each other | No concurrency control in `SQLiteMemoryStore` or `SqlAlchemyStore` | Add `version` field + optimistic locking check | [ADR-0015](../adr/0015-optimistic-locking.md) | 🔴 Not implemented |
| R-04 | **No observability**: Gate and reviewer decisions are invisible in production; debugging requires log scraping | No structured telemetry | Add `ObservabilityAdapter` with structured decision logging | [ADR-0016](../adr/0016-observability-telemetry.md) | 🔴 Not implemented |
| R-05 | **No production storage implementation**: `SqlAlchemyStore` is listed as an extension point but not bundled | `SQLiteMemoryStore` only (not safe for multi-writer) | Implement and bundle `SqlAlchemyStore` | [ADR-0017](../adr/0017-sqlalchemy-store.md) | 🔴 Not implemented |

### High Risks (P1 — Pre-Production)

| ID | Risk | Current State | Mitigation | Status |
|----|------|--------------|------------|--------|
| R-06 | **No authentication**: The API layer has no bearer token validation; any caller can submit tool calls | No auth middleware | Implement Azure AD token validation (MSAL) middleware | 🔴 Not implemented |
| R-07 | **SQLite not safe for multi-writer**: SQLite's write lock serializes all writes; concurrent requests will queue or fail | Single writer by design for dev | Use `SqlAlchemyStore` + Azure SQL for production (ADR-0017) | 🟡 Documented in README; SqlAlchemyStore pending |
| R-08 | **No database migration tooling**: Schema changes require manual SQL or drop-and-recreate | SQLite only; no migration | Add Alembic migrations for `SqlAlchemyStore` | 🔴 Not implemented |
| R-09 | **No input sanitisation for `subject_label` / `object_value`**: Free-text fields could contain injection payloads | Pydantic validates types, not content | Add max-length constraints and strip control characters | 🟡 Partial (Pydantic validation) |

### Medium Risks (P2 — Production Hardening)

| ID | Risk | Current State | Mitigation | Status |
|----|------|--------------|------------|--------|
| R-10 | **No end-to-end integration tests**: Unit tests cover individual components; no test exercises the full pipeline against a real database | Unit tests only | Add integration test suite with PostgreSQL container | 🔴 Not implemented |
| R-11 | **Decay is applied at read time, not stored**: Confidence values in the database do not reflect current decay; queries with confidence thresholds may return stale results | Decay applied in `build_summary()` | Document the design decision; add decay-at-persist option | 🟢 Documented in ADR-0005 |
| R-12 | **No runbook for common incidents**: Operators have no documented procedure for rate-limit overrides, data exports, or context deletion | None | Create ops runbook | 🔴 Not implemented |
| R-13 | **`global` domain is a per-user namespace but could be misunderstood**: Operators might expect `global` to be a shared system namespace | Documented in ADR-0004 | Ensure README and ADR-0004 are clear; add validation | 🟢 Documented in ADR-0004 |

## Risk Matrix

```
        │  Low impact  │  Medium impact  │  High impact
────────┼──────────────┼─────────────────┼──────────────
  High  │              │  R-08, R-09     │  R-01, R-02
  prob  │              │                 │  R-03, R-04
────────┼──────────────┼─────────────────┼──────────────
  Med   │  R-13        │  R-06, R-07     │  R-05
  prob  │              │  R-10, R-11     │
────────┼──────────────┼─────────────────┼──────────────
  Low   │              │  R-12           │
  prob  │              │                 │
```

## Mitigation Roadmap

### Phase 1: Production Blockers (before first production deployment)

All P0 risks must be resolved before any production traffic:

- [ ] **R-01** Implement `RedisRateLimiter` (ADR-0013)
- [ ] **R-02** Implement `ContextOwnershipMiddleware` (ADR-0014)
- [ ] **R-03** Add `version` field + `OptimisticLockError` (ADR-0015)
- [ ] **R-04** Implement `ObservabilityAdapter` (ADR-0016)
- [ ] **R-05** Implement `SqlAlchemyStore` (ADR-0017)
- [ ] **R-06** Implement Azure AD token validation middleware

### Phase 2: Pre-Production Hardening (before GA)

- [ ] **R-07** Validate `SqlAlchemyStore` + Azure SQL under load testing
- [ ] **R-08** Add Alembic migrations for all schema changes
- [ ] **R-09** Add `max_length` validators + sanitisation to free-text fields

### Phase 3: Operational Readiness (post-launch)

- [ ] **R-10** Add integration test suite against PostgreSQL container
- [ ] **R-12** Write ops runbook (rate-limit override, data export, context deletion)

## Resolved / Accepted Risks

| ID | Risk | Resolution |
|----|------|-----------|
| R-11 | Decay at read-time, not stored | Accepted design (ADR-0005). Documented in ADR and README. No action required. |
| R-13 | `global` domain misunderstanding | Clarified in ADR-0004. |

## Related Documents

- [ADR-0013](../adr/0013-distributed-rate-limiting.md): Distributed Rate Limiting
- [ADR-0014](../adr/0014-context-ownership-validation.md): Context Ownership Validation
- [ADR-0015](../adr/0015-optimistic-locking.md): Optimistic Locking
- [ADR-0016](../adr/0016-observability-telemetry.md): Observability & Telemetry
- [ADR-0017](../adr/0017-sqlalchemy-store.md): SqlAlchemy Production Storage
- [Production Prerequisites](../deployment/production-prerequisites.md)
