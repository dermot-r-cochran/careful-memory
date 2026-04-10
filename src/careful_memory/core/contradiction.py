"""
Contradiction handling for careful-memory.

When a new observation conflicts with an existing belief, careful-memory
NEVER overwrites the existing record. Instead:

  1. A new MemoryRecord is created (the challenger).
  2. The challenger's `contradicts` list references the existing record's id.
  3. The existing record's status is set to `contradicted`.
  4. The existing record receives a β-increment (contradicting evidence).

This preserves full belief history and audit trail.

INVARIANT: belief history is append-only.
           No record is deleted or overwritten by contradiction handling.
"""

from __future__ import annotations

from datetime import UTC, datetime

from careful_memory.core.bayesian import apply_contradicting_evidence
from careful_memory.models.enums import RecordStatus
from careful_memory.models.memory import MemoryRecord


class ContradictionResult:
    """
    Outcome of a contradiction operation.

    Attributes
    ----------
    challenger       : the NEW record that asserts the conflicting belief
    updated_existing : copy of the EXISTING record with updated status and β
    """

    __slots__ = ("challenger", "updated_existing")

    def __init__(self, challenger: MemoryRecord, updated_existing: MemoryRecord) -> None:
        self.challenger = challenger
        self.updated_existing = updated_existing


def apply_contradiction(
    existing: MemoryRecord,
    challenger: MemoryRecord,
    now: datetime | None = None,
) -> ContradictionResult:
    """
    Record that *challenger* contradicts *existing*.

    Mutates neither record in place; returns copies.

    Steps:
      1. Mark the existing record as `contradicted` and increment its β.
      2. Ensure the challenger's `contradicts` list includes existing.id.

    Parameters
    ----------
    existing   : the record that is being challenged
    challenger : the new record that asserts the contradiction
    now        : timestamp to use (defaults to UTC now)

    Raises
    ------
    ValueError if the records are from different contexts (safety check).
    """
    if existing.context_id != challenger.context_id:
        raise ValueError(
            f"Cannot contradict across contexts: "
            f"existing.context_id={existing.context_id!r} vs "
            f"challenger.context_id={challenger.context_id!r}"
        )

    now = now or datetime.now(tz=UTC)

    # Increment β on the existing record (contradicting evidence registered).
    bayes = apply_contradicting_evidence(existing)
    updated_existing = existing.model_copy(
        update={
            "beta": bayes.new_beta,
            "status": RecordStatus.contradicted,
            "updated_at": now,
        }
    )

    # Ensure the challenger's contradicts list is populated.
    new_contradicts = list(challenger.contradicts)
    if existing.id not in new_contradicts:
        new_contradicts.append(existing.id)

    updated_challenger = challenger.model_copy(
        update={
            "contradicts": new_contradicts,
            "updated_at": now,
        }
    )

    return ContradictionResult(
        challenger=updated_challenger,
        updated_existing=updated_existing,
    )


def apply_supersession(
    existing: MemoryRecord,
    successor: MemoryRecord,
    now: datetime | None = None,
) -> tuple[MemoryRecord, MemoryRecord]:
    """
    Record that *successor* supersedes *existing*.

    Supersession is a non-contradictory update — the existing belief is
    considered stale, not wrong.  Example: the user moved cities.

    Returns (updated_existing, updated_successor).
    """
    if existing.context_id != successor.context_id:
        raise ValueError(
            f"Cannot supersede across contexts: "
            f"existing.context_id={existing.context_id!r} vs "
            f"successor.context_id={successor.context_id!r}"
        )

    now = now or datetime.now(tz=UTC)

    updated_existing = existing.model_copy(
        update={
            "status": RecordStatus.superseded,
            "updated_at": now,
        }
    )

    new_supersedes = list(successor.supersedes)
    if existing.id not in new_supersedes:
        new_supersedes.append(existing.id)

    updated_successor = successor.model_copy(
        update={
            "supersedes": new_supersedes,
            "updated_at": now,
        }
    )

    return updated_existing, updated_successor
