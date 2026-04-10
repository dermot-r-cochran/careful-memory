"""
Summarization pipeline for careful-memory.

Produces a MemorySummary from all active MemoryRecords in a ContextScope.

Design goals:
  - Confidence-weighted language ("very likely", "possibly", "uncertain")
  - Sorted by confidence descending so the LLM sees the strongest beliefs first
  - Only records at or above `confidence_threshold` are included
  - Derived artifact: summaries are NEVER fed back as evidence

INVARIANT: the output of this module must never be written back into
           the memory store as a MemoryRecord.
"""

from __future__ import annotations

from careful_memory.models.enums import Domain, RecordStatus
from careful_memory.models.memory import ContextScope, MemoryRecord, MemorySummary

# ---------------------------------------------------------------------------
# Confidence → language mapping
# ---------------------------------------------------------------------------
# These thresholds produce language that is calibrated to the Beta distribution
# mean, not to arbitrary labels.  The wording is intentionally conservative.

_CONFIDENCE_LABELS: list[tuple[float, str]] = [
    (0.95, "almost certainly"),
    (0.85, "very likely"),
    (0.70, "probably"),
    (0.55, "possibly"),
    (0.45, "uncertain whether"),
    (0.0,  "doubtful that"),
]


def _confidence_label(confidence: float) -> str:
    for threshold, label in _CONFIDENCE_LABELS:
        if confidence >= threshold:
            return label
    return "doubtful that"


def _record_to_sentence(record: MemoryRecord) -> str:
    """
    Render a MemoryRecord as a single natural-language sentence.

    Format: "<confidence_label> <subject> <predicate> <object>"
    Example: "very likely [user] prefers dark mode"
    """
    label = _confidence_label(record.confidence)
    subject = record.subject.label
    predicate = record.predicate

    obj = record.object_value if isinstance(record.object_value, str) else record.object_value.label

    return f"It is {label} that {subject} {predicate} {obj}."


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------


def build_summary(
    scope: ContextScope,
    records: list[MemoryRecord],
    confidence_threshold: float = 0.6,
    domain: Domain | None = None,
) -> MemorySummary:
    """
    Build a MemorySummary from active records in *scope*.

    Parameters
    ----------
    scope                : the context to summarise
    records              : ALL records for the context (filtered here)
    confidence_threshold : only beliefs at or above this are included
    domain               : if provided, restrict summary to this domain
    """
    # Filter to active records only (contradicted/archived are excluded).
    active = [
        r for r in records
        if r.status == RecordStatus.active
        and r.context_id == scope.context_id
        and r.confidence >= confidence_threshold
    ]

    if domain is not None:
        # Records don't carry their domain directly; the caller supplies domain-
        # filtered records.  This parameter is passed through for metadata only.
        pass

    # Sort by confidence descending — strongest beliefs first.
    active.sort(key=lambda r: r.confidence, reverse=True)

    if not active:
        text = (
            "No reliable memories are available for this context. "
            "Do not invent preferences or beliefs."
        )
    else:
        lines = [_record_to_sentence(r) for r in active]
        text = "\n".join(lines)

    return MemorySummary(
        context_id=scope.context_id,
        user_id=scope.user_id,
        domain=domain or scope.domain,
        confidence_threshold=confidence_threshold,
        record_count=len(active),
        text=text,
    )
