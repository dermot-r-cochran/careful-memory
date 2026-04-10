# ADR-0010: Storage Abstraction (MemoryStore ABC) with SQLite Default

**Date:** 2024-01-01  
**Status:** Accepted

---

## Context

The platform needs a persistence layer that is:

1. Trivial to use in local development and tests (no external infrastructure).
2. Swappable for a production-grade relational database (Azure SQL, PostgreSQL) without changing business logic.
3. Not tightly coupled to any ORM or database driver in the core domain modules.

Embedding SQLAlchemy or a cloud SDK directly into the domain or gate layers would make unit tests infrastructure-dependent and violate the single-responsibility principle.

---

## Decision

Storage is accessed exclusively through a **`MemoryStore` abstract base class** (`storage/base.py`).  All business logic, gate, reviewer, and service code depends only on this interface.

Two implementations are provided:

| Implementation | Use case |
|---------------|---------|
| `SQLiteMemoryStore` (`storage/sqlite.py`) | Local development, tests, single-instance deployments |
| `SqlAlchemyStore` (extension point, not bundled) | Production: Azure SQL, PostgreSQL, or any SQLAlchemy-supported database |

`SQLiteMemoryStore` uses Python's built-in `sqlite3` module — zero extra dependencies for the common case.

The README documents `DATABASE_URL` as the environment variable for the SQLAlchemy connection string, injected at runtime from Azure Key Vault (see ADR-0010 and the Azure Deployment section of the README).

---

## Consequences

**Benefits**

- Tests run without any external services; `SQLiteMemoryStore(":memory:")` provides a fully functional, isolated store per test run.
- Switching to a production database requires only providing a different `MemoryStore` implementation; no business logic changes.
- The storage interface is minimal and explicit: `save`, `get`, `list_active`, `update_status` — no ORM leakage into domain code.
- Adding a new storage backend (e.g. Cosmos DB, DynamoDB) requires only implementing the ABC, not modifying any existing code.

**Obligations**

- Every query in any `MemoryStore` implementation must include `context_id` as a filter parameter; the ABC method signatures enforce this.
- Storage implementations must enforce the append-only constraint (see ADR-0007): no bulk-update or delete operations should be exposed through the ABC.
- Integration tests for production backends must be run separately; the default test suite uses only the SQLite in-memory store.

**Trade-offs**

- The ABC is a thin facade; it does not provide connection pooling, transaction management, or migration tooling.  These concerns belong to the concrete implementation.
- `SQLiteMemoryStore` is not suitable for multi-writer deployments (SQLite's writer lock would serialize all writes).  The README documents this limitation and the Azure deployment section provides the recommended alternative.
- The `SqlAlchemyStore` is listed as an extension point (not bundled) to avoid the `sqlalchemy` and database driver dependencies being mandatory for all users.  Users who need it must implement it or use a community-provided adapter.
