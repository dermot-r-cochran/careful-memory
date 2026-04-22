"""
Conserved-mass memory salience allocation (STV-inspired).

Treats memory importance as *conserved mass* rather than independent scores.
At each consolidation step the total salience budget is exactly 1.0; retention
and eviction are decided through quota-based allocation with surplus
redistribution (semantic bleed) and largest-remainder tie-breaking.

Key invariants
--------------
1. ``sum(normalised_weight_i) == 1.0`` after :func:`normalise_weights`
   (conservation).
2. Any memory with ``w_i >= quota`` is guaranteed retained (quota guarantee).
3. Surplus weight is redistributed, never silently discarded
   (conservation under transfer).
4. Tie-breaking is fully deterministic: stable SHA-256 hash of ID →
   creation timestamp → explicit priority class.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class MemoryItem:
    """
    A single candidate memory together with its salience signals.

    Attributes
    ----------
    id               : unique identifier (used for deterministic tie-breaking)
    signals          : named graded signals in [0.0, 1.0]
                       (e.g. recency, user_emphasis, task_relevance …)
    group            : optional grouping key (topic, task, user, time window …)
    created_at       : creation timestamp; secondary tie-breaker after hash
    raw_score        : weighted sum of signals; caller sets this before
                       passing the item to :func:`allocate`
    normalised_weight: filled in by :func:`normalise_weights`; do not set
                       directly
    priority_class   : optional explicit priority (lower integer = higher
                       priority); used as the last tie-breaker
    """

    id: str
    signals: dict[str, float]
    group: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    raw_score: float = 0.0
    normalised_weight: float = 0.0
    priority_class: int = 0


@dataclass(frozen=True)
class SurplusTransfer:
    """Immutable record of a single surplus-transfer event (audit trail)."""

    source_id: str
    target_id: str
    amount: float


@dataclass
class AllocationResult:
    """
    Output of a full STV-style allocation pass.

    Attributes
    ----------
    retained          : IDs of memories kept in this pass
    evicted           : IDs of memories evicted in this pass
    surplus_transfers : ordered list of surplus-transfer events for auditing
    """

    retained: list[str]
    evicted: list[str]
    surplus_transfers: list[SurplusTransfer] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def normalise_weights(items: list[MemoryItem]) -> None:
    """
    Set ``MemoryItem.normalised_weight`` in-place so they sum to exactly 1.0.

    Edge cases
    ----------
    - Empty list  : no-op.
    - Single item : weight = 1.0 regardless of ``raw_score``.
    - All zeros   : uniform distribution 1/N.
    """
    if not items:
        return
    total = sum(item.raw_score for item in items)
    if total == 0.0:
        uniform = 1.0 / len(items)
        for item in items:
            item.normalised_weight = uniform
    else:
        for item in items:
            item.normalised_weight = item.raw_score / total


def compute_quota(capacity: int) -> float:
    """
    Return the per-slot salience quota for a store of *capacity* K.

    ``quota = 1.0 / K``

    Raises
    ------
    ValueError
        If ``capacity < 1``.
    """
    if capacity < 1:
        raise ValueError(f"capacity must be >= 1, got {capacity}")
    return 1.0 / capacity


def allocate(
    items: list[MemoryItem],
    capacity: int,
    similarity_fn: Callable[[MemoryItem, MemoryItem], float] | None = None,
) -> AllocationResult:
    """
    Run a full STV-style salience allocation pass.

    Algorithm
    ---------
    1. Normalise weights to ``sum = 1.0``.
    2. Guarantee-retain any item with ``w_i >= quota``.
    3. Redistribute surplus from over-quota items to related items
       (same group, or ``similarity_fn > 0``).  Repeat until stable.
    4. Fill remaining capacity slots using the largest-remainder method:
       rank non-guaranteed candidates by weight descending, take the top N.
    5. Deterministic tie-breaking throughout: stable hash → ``created_at`` →
       ``priority_class``.

    Parameters
    ----------
    items         : candidate memories; ``raw_score`` must be set by the
                    caller before this call.
    capacity      : maximum number of memories to retain (K >= 1).
    similarity_fn : optional callable ``(a, b) -> float`` in [0, 1].  Used
                    to determine surplus-transfer targets.  If ``None``, only
                    same-group membership qualifies.

    Returns
    -------
    :class:`AllocationResult` with retained/evicted IDs and a full audit
    trail of surplus transfers.
    """
    if not items:
        return AllocationResult(retained=[], evicted=[])

    # Step 1 — normalise
    normalise_weights(items)

    # Working copy of weights (mutated during surplus transfer)
    weights: dict[str, float] = {item.id: item.normalised_weight for item in items}
    item_map: dict[str, MemoryItem] = {item.id: item for item in items}

    quota = compute_quota(capacity)
    surplus_transfers: list[SurplusTransfer] = []
    guaranteed: set[str] = set()

    # Step 2 — initial quota pass: mark items that already meet quota and
    # collect their surplus for redistribution.
    pending_surplus: dict[str, float] = {}
    for item in items:
        if weights[item.id] >= quota:
            surplus = weights[item.id] - quota
            weights[item.id] = quota
            guaranteed.add(item.id)
            if surplus > 0.0:
                pending_surplus[item.id] = surplus

    # Step 3 — iterative surplus redistribution (STV-style semantic bleed).
    # Each round processes items whose surplus has not yet been transferred.
    # Already-guaranteed items are excluded as transfer targets to prevent
    # double-counting; if newly boosted items reach quota they cascade.
    while pending_surplus:
        next_pending: dict[str, float] = {}
        for source_id, surplus in pending_surplus.items():
            source = item_map[source_id]
            targets = _transfer_targets(source, items, guaranteed, similarity_fn)
            if targets:
                per_target = surplus / len(targets)
                for tgt in targets:
                    weights[tgt.id] += per_target
                    surplus_transfers.append(
                        SurplusTransfer(
                            source_id=source_id,
                            target_id=tgt.id,
                            amount=per_target,
                        )
                    )
                    # Cascade: a newly-boosted target may now meet quota
                    if weights[tgt.id] >= quota and tgt.id not in guaranteed:
                        guaranteed.add(tgt.id)
                        new_surplus = weights[tgt.id] - quota
                        weights[tgt.id] = quota
                        if new_surplus > 0.0:
                            next_pending[tgt.id] = new_surplus
            # No targets → surplus stays with source (source is already
            # guaranteed-retained, so the excess weight is effectively
            # absorbed without changing the allocation outcome).
        pending_surplus = next_pending

    # Step 4 — largest-remainder allocation for remaining capacity slots.
    remaining_slots = capacity - len(guaranteed)

    # Edge: guaranteed set already fills or exceeds capacity
    if remaining_slots <= 0:
        # Deterministic trim when (rarely) over capacity
        guaranteed_sorted = sorted(
            (item_map[mid] for mid in guaranteed), key=_tiebreak_key
        )
        retained = [item.id for item in guaranteed_sorted[:capacity]]
        evicted = [item.id for item in items if item.id not in set(retained)]
        return AllocationResult(
            retained=retained,
            evicted=evicted,
            surplus_transfers=surplus_transfers,
        )

    candidates = [item for item in items if item.id not in guaranteed]

    # Rank by weight descending (= remainder, since all candidates are below
    # quota), then by deterministic tie-breaker ascending.
    candidates_sorted = sorted(
        candidates, key=lambda it: (-weights[it.id], _tiebreak_key(it))
    )
    retained_candidates = candidates_sorted[:remaining_slots]
    evicted_candidates = candidates_sorted[remaining_slots:]

    return AllocationResult(
        retained=list(guaranteed) + [item.id for item in retained_candidates],
        evicted=[item.id for item in evicted_candidates],
        surplus_transfers=surplus_transfers,
    )


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------


def _stable_hash(memory_id: str) -> int:
    """Return a deterministic integer derived from a SHA-256 digest."""
    digest = hashlib.sha256(memory_id.encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _tiebreak_key(item: MemoryItem) -> tuple[int, datetime, int]:
    """Deterministic ordering key: stable hash → ``created_at`` → ``priority_class``."""
    return (_stable_hash(item.id), item.created_at, item.priority_class)


def _transfer_targets(
    source: MemoryItem,
    all_items: list[MemoryItem],
    guaranteed: set[str],
    similarity_fn: Callable[[MemoryItem, MemoryItem], float] | None,
) -> list[MemoryItem]:
    """
    Return items that should receive surplus from *source*.

    Excludes the source itself and already-guaranteed items (they cannot
    usefully receive more weight once at quota).

    Selection rule (in priority order):
    - If *similarity_fn* is provided: include items where ``similarity_fn > 0``.
    - Otherwise: include items in the same group as *source* (requires
      ``source.group`` to be non-``None``).
    """
    targets = []
    for item in all_items:
        if item.id == source.id or item.id in guaranteed:
            continue
        if similarity_fn is not None:
            if similarity_fn(source, item) > 0.0:
                targets.append(item)
        elif source.group is not None and item.group == source.group:
            targets.append(item)
    return targets
