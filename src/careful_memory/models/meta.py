"""
MetaAssessment — the reasoning-quality gate for careful-memory.

ROLE (non-negotiable):
    Meta-reasoning is a GATE, not a decision-maker.
    It evaluates the quality of the reasoning behind a memory proposal
    and determines whether that proposal is ALLOWED to proceed to review.

    It does NOT:
      - decide whether a belief is true
      - update alpha, beta, or confidence
      - approve, reject, or modify memory records
      - replace or supplement the Memory Review Agent

    The Memory Review Agent remains the sole judge of belief state.
    Meta-reasoning controls when that judge is consulted.

INVARIANT:
    MetaAssessment is a stateless, non-persistent, control-only artifact.
    It must never be stored as a MemoryRecord or feed into Bayesian updates.

Pipeline position:
    Proposal → [MetaGate] → WriteGate → MemoryReviewer → Storage
                   ↑
              This module
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class MetaConfidenceLevel(StrEnum):
    """
    The output level of a MetaAssessment.

    Controls pipeline access — nothing more.

    low    : proposal is blocked; Memory Review is NOT consulted.
    medium : proposal may proceed; Memory Review is restricted to
             a subset of allowed actions (see reviewer.py).
    high   : proposal may proceed; Memory Review applies normal rules.

    INVARIANT: this level is never stored in a MemoryRecord.
               It is discarded after the current pipeline run.
    """

    low = "low"
    medium = "medium"
    high = "high"


@dataclass(frozen=True)
class MetaAssessment:
    """
    Immutable gate result produced by MetaGate.assess().

    Attributes
    ----------
    level       : the gate level (low / medium / high)
    rationale   : explicit, human-readable explanation of the level.
                  Must reference the signals that drove the decision.
                  Required — empty rationale is rejected at construction.
    signals     : individual named signals that contributed to the level,
                  in order of weight.  Used for post-hoc audit.
    is_gate_pass: True when level is MEDIUM or HIGH (proposal may proceed).
                  False when level is LOW (proposal is blocked here).

    DESIGN NOTE:
        `is_gate_pass` is derived from `level` and exists solely to make
        the gate-pass condition explicit at call sites, avoiding implicit
        string comparisons throughout the pipeline.
    """

    level: MetaConfidenceLevel
    rationale: str
    signals: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.rationale.strip():
            raise ValueError("MetaAssessment.rationale must not be empty")

    @property
    def is_gate_pass(self) -> bool:
        """True when the proposal is allowed to proceed to Memory Review."""
        return self.level != MetaConfidenceLevel.low

    @property
    def restricts_review(self) -> bool:
        """True when Memory Review must apply restricted-action rules."""
        return self.level == MetaConfidenceLevel.medium
