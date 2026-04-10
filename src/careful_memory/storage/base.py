"""
Abstract storage interface for careful-memory.

All persistence implementations must satisfy this interface.
The domain layer never calls storage directly; it goes through MemoryService.

DESIGN: The interface is deliberately narrow.  It intentionally avoids
        "update" to reinforce the append-only nature of the data model.
        The only mutation allowed is updating a record's status and
        Bayesian counters (via save_record, which is an upsert).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from careful_memory.models.memory import ContextScope, MemoryRecord, MemorySummary


class MemoryStore(ABC):
    """Abstract base class for memory persistence backends."""

    # ------------------------------------------------------------------
    # Context
    # ------------------------------------------------------------------

    @abstractmethod
    def save_context(self, scope: ContextScope) -> None:
        """Persist a ContextScope (upsert by context_id)."""

    @abstractmethod
    def get_context(self, context_id: str) -> ContextScope | None:
        """Retrieve a ContextScope by its id, or None if not found."""

    @abstractmethod
    def list_contexts_for_user(self, user_id: str) -> list[ContextScope]:
        """Return all ContextScopes belonging to *user_id*."""

    # ------------------------------------------------------------------
    # Records
    # ------------------------------------------------------------------

    @abstractmethod
    def save_record(self, record: MemoryRecord) -> None:
        """
        Persist a MemoryRecord (upsert by id).

        Called both for new records and for status/Bayesian updates.
        The storage backend must never change the record's context_id.
        """

    @abstractmethod
    def get_record(self, record_id: str, context_id: str) -> MemoryRecord | None:
        """
        Retrieve a record by id within a specific context.

        The context_id parameter enforces tenant isolation at the query level:
        even if a record_id were somehow known across contexts, this method
        must never return it unless the context_id also matches.
        """

    @abstractmethod
    def list_records(
        self,
        context_id: str,
        include_inactive: bool = False,
    ) -> list[MemoryRecord]:
        """
        Return records for *context_id*.

        Parameters
        ----------
        include_inactive : if False (default), return only active records.
        """

    # ------------------------------------------------------------------
    # Summaries
    # ------------------------------------------------------------------

    @abstractmethod
    def save_summary(self, summary: MemorySummary) -> None:
        """Persist a derived MemorySummary (append; summaries are immutable)."""

    @abstractmethod
    def list_summaries(self, context_id: str) -> list[MemorySummary]:
        """Return all summaries for *context_id*, ordered by generated_at descending."""
