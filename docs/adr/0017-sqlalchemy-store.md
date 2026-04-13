# ADR-0017: SqlAlchemy Production Storage

**Date:** 2024-01-01  
**Status:** Accepted

---

## Context

ADR-0010 established a `MemoryStore` ABC with `SQLiteMemoryStore` as the bundled default for development and tests.  The ADR explicitly deferred the production storage implementation, noting that a `SqlAlchemyStore` should be provided as an extension point.

The risks of relying solely on `SQLiteMemoryStore` in production are:

1. **SQLite's write lock serialises all writes** — a single slow write blocks every other writer.  Under concurrent load (multiple agents, multiple replicas), this becomes a bottleneck.
2. **SQLite is not designed for multi-process access** — running multiple Container App replicas pointing at the same SQLite file will cause database corruption.
3. **No migration tooling** — SQLite's `ALTER TABLE` support is limited; schema evolution requires manual workarounds.
4. **No connection pooling** — each request opens and closes a connection; under high concurrency, this is expensive.

---

## Decision

A `SqlAlchemyStore` is implemented and bundled in `careful_memory/storage/sqlalchemy_store.py`.  It implements the `MemoryStore` ABC using SQLAlchemy Core (not ORM) for explicit control over queries and to keep the domain models free of SQLAlchemy annotations.

### Schema

```sql
CREATE TABLE memory_records (
    record_id     VARCHAR(36)   PRIMARY KEY,
    context_id    VARCHAR(36)   NOT NULL,
    memory_type   VARCHAR(32)   NOT NULL,
    subject_type  VARCHAR(64)   NOT NULL,
    subject_label VARCHAR(512)  NOT NULL,
    predicate     VARCHAR(256)  NOT NULL,
    object_value  TEXT          NOT NULL,
    alpha         FLOAT         NOT NULL DEFAULT 1.0,
    beta          FLOAT         NOT NULL DEFAULT 1.0,
    status        VARCHAR(32)   NOT NULL DEFAULT 'active',
    authority_level VARCHAR(32) NOT NULL,
    source_origin VARCHAR(512)  NOT NULL,
    decay_rate    FLOAT         NOT NULL,
    created_at    TIMESTAMP     NOT NULL,
    updated_at    TIMESTAMP     NOT NULL,
    version       INTEGER       NOT NULL DEFAULT 1,   -- optimistic locking (ADR-0015)
    INDEX idx_context_status (context_id, status)
);

CREATE TABLE memory_contexts (
    context_id    VARCHAR(36)   PRIMARY KEY,
    user_id       VARCHAR(256)  NOT NULL,
    domain        VARCHAR(32)   NOT NULL,
    created_at    TIMESTAMP     NOT NULL,
    INDEX idx_user_id (user_id)
);
```

### Connection Configuration

```python
engine = create_engine(
    DATABASE_URL,              # from Key Vault via environment variable
    pool_size=5,               # per-replica connection pool
    max_overflow=10,
    pool_pre_ping=True,        # validate connections before use
    connect_args={"timeout": 30},
)
```

### Supported Backends

| Backend | Connection string format | Azure service |
|---------|-------------------------|---------------|
| Azure SQL (MSSQL) | `mssql+pyodbc://<user>:<pw>@<server>/<db>?driver=ODBC+Driver+18+for+SQL+Server` | Azure SQL Database |
| PostgreSQL | `postgresql+psycopg2://<user>:<pw>@<host>/<db>` | Azure PostgreSQL Flexible Server |
| SQLite (test only) | `sqlite:///:memory:` | — |

### Migrations

Database schema migrations are managed with **Alembic**.  Migration scripts are in `alembic/versions/`.  Migrations must be run before deploying a new application version.

---

## Consequences

**Benefits**

- Supports all major relational databases via SQLAlchemy's dialect system; no vendor lock-in.
- Connection pooling (`pool_size=5`) reduces connection overhead under concurrent load.
- `pool_pre_ping=True` automatically reconnects stale connections after database restarts or network interruptions.
- The `version` column supports the optimistic locking protocol defined in ADR-0015.
- Alembic provides a repeatable, auditable migration path for schema evolution.
- The `memory_contexts` table enables the context ownership validation defined in ADR-0014.

**Obligations**

- `DATABASE_URL` must be stored in Azure Key Vault and injected via Managed Identity.  It must never be hardcoded or committed to source control.
- Alembic migrations must be run as a pre-deployment step (e.g. via a Container Apps job) before any new application replica starts.
- The `SqlAlchemyStore` must be covered by integration tests running against a real PostgreSQL instance (using a Docker container in CI).
- Connection string credentials must use a least-privilege database user: `SELECT`, `INSERT`, `UPDATE` on `memory_records` and `memory_contexts`; no `DROP`, `ALTER`, or `TRUNCATE`.
- The `SQLiteMemoryStore` remains the default for development and tests; `SqlAlchemyStore` is opt-in via `DATABASE_URL`.

**Trade-offs**

- SQLAlchemy and a database driver (`psycopg2` or `pyodbc`) become required dependencies for production use.  They are listed as optional extras (`pip install careful-memory[sqlalchemy]`) to keep the base installation lightweight.
- SQLAlchemy Core requires writing explicit SQL strings or expression objects; there is no automatic model mapping.  This is a deliberate choice to keep the domain model free of ORM annotations, but it means the storage layer requires more boilerplate than an ORM approach.
- The connection pool is per-process; each replica maintains its own pool.  Pool size must be tuned to the database's `max_connections` setting divided by the number of replicas.
