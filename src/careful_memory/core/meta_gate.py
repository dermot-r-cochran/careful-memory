"""
MetaGate — stateless gate that produces a MetaAssessment for a proposal.

RESPONSIBILITY (single):
    Evaluate the reasoning quality of a memory proposal and return a
    MetaAssessment with a level (low / medium / high) and explicit rationale.

WHAT THIS MODULE MUST NOT DO:
    - Update alpha, beta, or confidence on any record.
    - Approve or reject belief state transitions.
    - Contradict, supersede, or archive any record.
    - Call the MemoryReviewer or any storage layer.
    - Make any persistent change whatsoever.

The Memory Review Agent remains the sole judge of belief state.
This gate controls when that judge is consulted.

────────────────────────────────────────────────────────
SIGNAL MODEL
────────────────────────────────────────────────────────

Each named signal independently returns one of three weights:

    HIGH   (+2) : strong evidence that reasoning quality is good
    MEDIUM (+1) : neutral or uncertain
    LOW    ( 0) : evidence that reasoning quality is poor

The overall level is the minimum of all signals:
    - Any LOW signal → level = LOW  (hard block; gate fails)
    - No LOW, any MEDIUM → level = MEDIUM  (restricted review)
    - All HIGH → level = HIGH  (normal review)

This is a conservative "weakest link" model: one bad signal is
enough to block the proposal, regardless of other good signals.

────────────────────────────────────────────────────────
SIGNALS EVALUATED
────────────────────────────────────────────────────────

1. source_authority   : verified_system → HIGH; system → MEDIUM; user → MEDIUM
2. evidence_type      : verified_system_outcome → HIGH
                        user_action / user_restatement → MEDIUM
                        None → LOW
3. predicate_quality  : non-empty, non-whitespace predicate → HIGH; else LOW
4. object_quality     : non-empty object value → HIGH; else LOW
5. memory_type_fit    : episodic proposed → HIGH (safe default)
                        semantic/procedural proposed → MEDIUM (needs scrutiny)
"""

from __future__ import annotations

from careful_memory.models.enums import AuthorityLevel, EvidenceType, MemoryType
from careful_memory.models.memory import MemoryRecord
from careful_memory.models.meta import MetaAssessment, MetaConfidenceLevel

# Signal weight constants — kept explicit and named, not magic integers.
_WEIGHT_HIGH: int = 2
_WEIGHT_MEDIUM: int = 1
_WEIGHT_LOW: int = 0


# ---------------------------------------------------------------------------
# Individual signal functions (pure, no I/O, no side effects)
# ---------------------------------------------------------------------------


def _signal_source_authority(record: MemoryRecord) -> tuple[int, str]:
    """
    Evaluate the authority level of the record's source.

    verified_system sources have external verification → HIGH.
    system sources are internal but not user-generated → MEDIUM.
    user sources are the lowest trust tier → MEDIUM (not LOW; users are
    legitimate proposers, but their proposals need scrutiny).
    """
    level = record.source.authority_level
    if level == AuthorityLevel.verified_system:
        return _WEIGHT_HIGH, f"source_authority=HIGH (verified_system source: {record.source.origin!r})"
    if level == AuthorityLevel.system:
        return _WEIGHT_MEDIUM, f"source_authority=MEDIUM (system source: {record.source.origin!r})"
    return _WEIGHT_MEDIUM, f"source_authority=MEDIUM (user source: {record.source.origin!r})"


def _signal_evidence_type(record: MemoryRecord) -> tuple[int, str]:
    """
    Evaluate whether a valid external evidence type is present.

    No evidence type means the proposer did not document the grounds
    for the belief → LOW.  This is the primary anti-hallucination signal.

    INVARIANT: 'llm_inference' is not a valid EvidenceType value, so it
               cannot appear here.  Any valid value is at least MEDIUM.
    """
    et = record.source.evidence_type
    if et is None:
        return _WEIGHT_LOW, (
            "evidence_type=LOW (no evidence_type set; "
            "reasoning grounds are undocumented — proposal blocked)"
        )
    if et == EvidenceType.verified_system_outcome:
        return _WEIGHT_HIGH, f"evidence_type=HIGH ({et.value})"
    # user_restatement or user_action
    return _WEIGHT_MEDIUM, f"evidence_type=MEDIUM ({et.value})"


def _signal_predicate_quality(record: MemoryRecord) -> tuple[int, str]:
    """
    A blank or whitespace-only predicate indicates a malformed proposal.
    The model validates non-empty at construction, but this signal makes
    the check explicit at the gate layer as well.
    """
    if record.predicate.strip():
        return _WEIGHT_HIGH, "predicate_quality=HIGH (non-empty predicate)"
    return _WEIGHT_LOW, "predicate_quality=LOW (blank predicate; malformed proposal)"


def _signal_object_quality(record: MemoryRecord) -> tuple[int, str]:
    """
    An empty object value carries no information.
    """
    obj_str = (
        record.object_value
        if isinstance(record.object_value, str)
        else record.object_value.label
    )
    if obj_str.strip():
        return _WEIGHT_HIGH, "object_quality=HIGH (non-empty object value)"
    return _WEIGHT_LOW, "object_quality=LOW (blank object value; malformed proposal)"


def _signal_memory_type_fit(record: MemoryRecord) -> tuple[int, str]:
    """
    Episodic proposals are the safe, expected type for new information.
    Semantic and procedural proposals are higher-stakes and require that
    the reviewer apply additional scrutiny (MEDIUM restricts allowed actions).
    """
    if record.memory_type == MemoryType.episodic:
        return _WEIGHT_HIGH, "memory_type_fit=HIGH (episodic; safe default type)"
    return _WEIGHT_MEDIUM, (
        f"memory_type_fit=MEDIUM ({record.memory_type.value}; "
        "non-episodic writes require reviewer scrutiny)"
    )


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


_ALL_SIGNALS = [
    _signal_source_authority,
    _signal_evidence_type,
    _signal_predicate_quality,
    _signal_object_quality,
    _signal_memory_type_fit,
]


def assess(record: MemoryRecord) -> MetaAssessment:
    """
    Evaluate the reasoning quality of a memory proposal.

    Returns a MetaAssessment whose level determines whether the proposal
    may proceed to Memory Review:

        LOW    → blocked; Memory Review is NOT consulted
        MEDIUM → proceed with restricted reviewer actions
        HIGH   → proceed with normal reviewer actions

    INVARIANT: this function makes no changes to any record, store, or
               external state.  It is safe to call multiple times with
               the same input and will always return the same result.

    Parameters
    ----------
    record : the proposed MemoryRecord (not yet persisted)

    Returns
    -------
    MetaAssessment
    """
    weights: list[int] = []
    signal_descriptions: list[str] = []

    for signal_fn in _ALL_SIGNALS:
        weight, description = signal_fn(record)
        weights.append(weight)
        signal_descriptions.append(description)

    # Conservative weakest-link aggregation:
    # one LOW signal blocks the entire proposal.
    overall_level: MetaConfidenceLevel
    if _WEIGHT_LOW in weights:
        low_signals = [
            d for w, d in zip(weights, signal_descriptions, strict=True) if w == _WEIGHT_LOW
        ]
        overall_level = MetaConfidenceLevel.low
        rationale = (
            "meta-gate BLOCKED: reasoning quality insufficient to proceed to review. "
            "Blocking signals: " + "; ".join(low_signals)
        )
    elif all(w == _WEIGHT_HIGH for w in weights):
        overall_level = MetaConfidenceLevel.high
        rationale = (
            "meta-gate PASS (HIGH): all reasoning-quality signals are strong; "
            "normal review rules apply"
        )
    else:
        medium_signals = [
            d for w, d in zip(weights, signal_descriptions, strict=True) if w == _WEIGHT_MEDIUM
        ]
        overall_level = MetaConfidenceLevel.medium
        rationale = (
            "meta-gate PASS (MEDIUM): some reasoning-quality signals are uncertain; "
            "review proceeds with restricted actions. "
            "Medium signals: " + "; ".join(medium_signals)
        )

    return MetaAssessment(
        level=overall_level,
        rationale=rationale,
        signals=signal_descriptions,
    )
