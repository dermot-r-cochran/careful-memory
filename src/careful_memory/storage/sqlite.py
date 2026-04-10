"""
SQLite-backed memory store for local development and testing.

This implementation uses Python's built-in sqlite3 module with a simple
JSON serialization strategy.  It is NOT suitable for production (no
concurrent writes, no transactions across calls).

For Azure production use the SqlAlchemyStore with a PostgreSQL or
Azure SQL connection string.

DESIGN: Every record is stored as a JSON blob plus indexed scalar
        columns for filtering.  This keeps the schema simple while
        preserving full model fidelity.  A migration to a relational
        schema (one column per field) is straightforward when needed.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from careful_memory.models.enums import RecordStatus
from careful_memory.models.memory import ContextScope, MemoryRecord, MemorySummary
from careful_memory.storage.base import MemoryStore

# Use :memory: for ephemeral in-process stores (tests).
IN_MEMORY_URI = ":memory:"


class SQLiteMemoryStore(MemoryStore):
    """
    SQLite-backed implementation of MemoryStore.

    Parameters
    ----------
    db_path : path to the SQLite database file, or ":memory:" for in-process.
    """

    def __init__(self, db_path: str | Path = IN_MEMORY_URI) -> None:
        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS contexts (
                context_id  TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                domain      TEXT NOT NULL,
                data        TEXT NOT NULL  -- full JSON blob
            );
            CREATE INDEX IF NOT EXISTS idx_ctx_user ON contexts(user_id);

            CREATE TABLE IF NOT EXISTS records (
                id          TEXT PRIMARY KEY,
                context_id  TEXT NOT NULL,
                status      TEXT NOT NULL,
                confidence  REAL NOT NULL,
                data        TEXT NOT NULL,  -- full JSON blob
                FOREIGN KEY (context_id) REFERENCES contexts(context_id)
            );
            CREATE INDEX IF NOT EXISTS idx_rec_ctx    ON records(context_id);
            CREATE INDEX IF NOT EXISTS idx_rec_status ON records(status);

            CREATE TABLE IF NOT EXISTS summaries (
                summary_id   TEXT PRIMARY KEY,
                context_id   TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                data         TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sum_ctx ON summaries(context_id);
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # ContextScope
    # ------------------------------------------------------------------

    def save_context(self, scope: ContextScope) -> None:
        self._conn.execute(
            """
            INSERT INTO contexts (context_id, user_id, domain, data)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(context_id) DO UPDATE SET
                user_id = excluded.user_id,
                domain  = excluded.domain,
                data    = excluded.data
            """,
            (scope.context_id, scope.user_id, scope.domain.value, scope.model_dump_json()),
        )
        self._conn.commit()

    def get_context(self, context_id: str) -> ContextScope | None:
        row = self._conn.execute(
            "SELECT data FROM contexts WHERE context_id = ?", (context_id,)
        ).fetchone()
        if row is None:
            return None
        return ContextScope.model_validate_json(row["data"])

    def list_contexts_for_user(self, user_id: str) -> list[ContextScope]:
        rows = self._conn.execute(
            "SELECT data FROM contexts WHERE user_id = ?", (user_id,)
        ).fetchall()
        return [ContextScope.model_validate_json(r["data"]) for r in rows]

    # ------------------------------------------------------------------
    # MemoryRecord
    # ------------------------------------------------------------------

    def save_record(self, record: MemoryRecord) -> None:
        self._conn.execute(
            """
            INSERT INTO records (id, context_id, status, confidence, data)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                context_id = excluded.context_id,
                status     = excluded.status,
                confidence = excluded.confidence,
                data       = excluded.data
            """,
            (
                record.id,
                record.context_id,
                record.status.value,
                record.confidence,
                record.model_dump_json(),
            ),
        )
        self._conn.commit()

    def get_record(self, record_id: str, context_id: str) -> MemoryRecord | None:
        row = self._conn.execute(
            "SELECT data FROM records WHERE id = ? AND context_id = ?",
            (record_id, context_id),
        ).fetchone()
        if row is None:
            return None
        return MemoryRecord.model_validate_json(row["data"])

    def list_records(
        self,
        context_id: str,
        include_inactive: bool = False,
    ) -> list[MemoryRecord]:
        if include_inactive:
            rows = self._conn.execute(
                "SELECT data FROM records WHERE context_id = ?", (context_id,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT data FROM records WHERE context_id = ? AND status = ?",
                (context_id, RecordStatus.active.value),
            ).fetchall()
        return [MemoryRecord.model_validate_json(r["data"]) for r in rows]

    # ------------------------------------------------------------------
    # MemorySummary
    # ------------------------------------------------------------------

    def save_summary(self, summary: MemorySummary) -> None:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO summaries (summary_id, context_id, generated_at, data)
            VALUES (?, ?, ?, ?)
            """,
            (
                summary.summary_id,
                summary.context_id,
                summary.generated_at.isoformat(),
                summary.model_dump_json(),
            ),
        )
        self._conn.commit()

    def list_summaries(self, context_id: str) -> list[MemorySummary]:
        rows = self._conn.execute(
            "SELECT data FROM summaries WHERE context_id = ? ORDER BY generated_at DESC",
            (context_id,),
        ).fetchall()
        return [MemorySummary.model_validate_json(r["data"]) for r in rows]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SQLiteMemoryStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
