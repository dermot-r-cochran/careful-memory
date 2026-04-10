"""
Core domain models for careful-memory.

These Pydantic v2 models define the data contract for the entire system.
Cloud-specific concerns (storage format, serialisation) live in the
storage layer. Business logic lives in the core modules.

INVARIANTS enforced here (as validators):
  - confidence is derived from alpha / (alpha + beta); never written directly
  - alpha and beta must always be >= 1.0 (Beta(1,1) prior = uniform)
  - decay_rate must be in (0.0, 1.0]
  - a record may not both contradict AND supersede the same target
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from careful_memory.models.enums import (
    AuthorityLevel,
    Domain,
    EvidenceType,
    MemoryType,
    RecordStatus,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Return timezone-aware current UTC time."""
    return datetime.now(tz=UTC)


def _new_uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# ContextScope
# ---------------------------------------------------------------------------


class ContextScope(BaseModel):
    """
    Identifies the isolation boundary for a set of memories.

    Every MemoryRecord belongs to exactly one ContextScope.
    Cross-context access is forbidden at the API level (see service.py).

    user_id is typically an Azure AD object ID (UUID-like string).
    """

    context_id: str = Field(default_factory=_new_uuid)
    user_id: str = Field(..., min_length=1, description="Azure AD object ID or UUID")
    domain: Domain = Domain.personal
    created_at: datetime = Field(default_factory=_utcnow)

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# EntityRef
# ---------------------------------------------------------------------------


class EntityRef(BaseModel):
    """
    A lightweight reference to a named entity (subject or object of a belief).

    This is intentionally NOT a full knowledge-graph node; it is a label
    and type annotation that allows grouping without requiring ontology
    management.
    """

    entity_id: str = Field(default_factory=_new_uuid)
    entity_type: str = Field(..., min_length=1, description="e.g. 'person', 'project', 'tool'")
    label: str = Field(..., min_length=1, description="Human-readable name")

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# MemorySource
# ---------------------------------------------------------------------------


class MemorySource(BaseModel):
    """
    Provenance metadata for a MemoryRecord.

    authority_level governs write permissions: a lower-authority source
    cannot overwrite or supersede a higher-authority belief.
    """

    origin: str = Field(..., description="Who/what produced this memory (session_id, tool name)")
    authority_level: AuthorityLevel = AuthorityLevel.user
    evidence_type: EvidenceType | None = None

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# MemoryRecord
# ---------------------------------------------------------------------------


# Minimum value for alpha and beta priors (Beta(1, 1) = uniform).
# Never go below 1.0 — this preserves the statistical meaning of the Beta.
_PRIOR_MIN: float = 1.0

# Default decay rates by memory type (fraction of evidence lost per day).
# These are justified conservative defaults; override per-record or via config.
_DEFAULT_DECAY_RATES: dict[MemoryType, float] = {
    MemoryType.episodic: 0.05,   # fades relatively quickly
    MemoryType.semantic: 0.005,  # very stable once established
    MemoryType.procedural: 0.01, # moderately stable
}

# Project-domain memories decay twice as fast (scoped, transient context).
_PROJECT_DECAY_MULTIPLIER: float = 2.0


class MemoryRecord(BaseModel):
    """
    An atomic belief unit — the fundamental element of careful-memory.

    Beliefs are represented as subject-predicate-object triples, each
    carrying a Beta-distributed confidence, time-decay metadata, full
    provenance, and a status lifecycle.

    APPEND-ONLY: existing records must not be mutated after creation
    (except for the status field, which is updated by the write gate
    to reflect contradictions / supersessions).

    Confidence is a DERIVED field: confidence = alpha / (alpha + beta).
    Never write confidence directly.
    """

    # Identity
    id: str = Field(default_factory=_new_uuid)
    context_id: str = Field(..., description="Must match a ContextScope.context_id")

    # Belief content
    memory_type: MemoryType = MemoryType.episodic
    subject: EntityRef
    predicate: str = Field(..., min_length=1, description="Relationship label, e.g. 'prefers'")
    object_value: str | EntityRef = Field(
        ..., description="Either a scalar value or an EntityRef"
    )

    # Bayesian evidence counters.
    # alpha: supporting-evidence count (initialised to 1 for uniform prior)
    # beta:  contradicting-evidence count (initialised to 1 for uniform prior)
    # INVARIANT: alpha >= _PRIOR_MIN and beta >= _PRIOR_MIN at all times.
    alpha: float = Field(default=_PRIOR_MIN, ge=_PRIOR_MIN)
    beta: float = Field(default=_PRIOR_MIN, ge=_PRIOR_MIN)

    # Decay rate: fraction of total evidence lost per day.
    # Smaller = slower decay.  Must be in (0, 1].
    decay_rate: float = Field(default=0.01, gt=0.0, le=1.0)

    # How many distinct external reinforcement events have occurred.
    reinforcement_count: int = Field(default=0, ge=0)

    # Lifecycle
    status: RecordStatus = RecordStatus.active

    # Linked records (UUIDs only, to avoid circular references)
    supersedes: list[str] = Field(default_factory=list)
    contradicts: list[str] = Field(default_factory=list)

    # Provenance
    source: MemorySource

    # Timestamps
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    last_reinforced_at: datetime | None = None
    # Effective time reference for decay computation.
    last_decayed_at: datetime = Field(default_factory=_utcnow)

    # Metadata / notes (arbitrary, non-authoritative)
    notes: str | None = None

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("decay_rate", mode="before")
    @classmethod
    def _validate_decay_rate(cls, v: Any) -> float:
        f = float(v)
        if not (0.0 < f <= 1.0):
            raise ValueError(f"decay_rate must be in (0, 1], got {f}")
        return f

    @model_validator(mode="after")
    def _no_self_overlap(self) -> MemoryRecord:
        """A record may not reference the same id in both contradicts and supersedes."""
        overlap = set(self.supersedes) & set(self.contradicts)
        if overlap:
            raise ValueError(
                f"A record cannot both contradict and supersede the same targets: {overlap}"
            )
        return self

    # ------------------------------------------------------------------
    # Derived property
    # ------------------------------------------------------------------

    @computed_field  # type: ignore[misc]
    @property
    def confidence(self) -> float:
        """
        Derived Bayesian confidence: mean of Beta(alpha, beta).

        INVARIANT: confidence is never written directly.
                   It is always derived from alpha and beta.
        Returns a value in (0, 1).
        """
        return self.alpha / (self.alpha + self.beta)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def effective_decay_rate(self, domain: Domain) -> float:
        """
        Apply the project-domain multiplier if relevant.

        Project memories are transient and should decay faster so that
        completed-project context does not pollute later sessions.
        """
        rate = self.decay_rate
        if domain == Domain.project:
            rate = min(rate * _PROJECT_DECAY_MULTIPLIER, 1.0)
        return rate

    model_config = {"frozen": False}  # status is mutated in write-gate


# ---------------------------------------------------------------------------
# EvidenceEvent
# ---------------------------------------------------------------------------


class EvidenceEvent(BaseModel):
    """
    A request to update a MemoryRecord's evidence counters.

    supports=True  → increment alpha (supporting evidence)
    supports=False → increment beta  (contradicting evidence)
    """

    record_id: str
    context_id: str
    supports: bool
    evidence_type: EvidenceType
    source: MemorySource
    occurred_at: datetime = Field(default_factory=_utcnow)
    notes: str | None = None

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# MemorySummary
# ---------------------------------------------------------------------------


class MemorySummary(BaseModel):
    """
    A derived, human-readable summary of a ContextScope's active memories.

    INVARIANT: summaries are DERIVED artifacts, never authoritative.
               They must not be fed back as evidence into the memory store.

    confidence_threshold: only beliefs above this confidence are included.
    text: the generated natural-language summary (RAG-ready).
    embedding_stub: placeholder for a future vector embedding; vendors must
                    not be assumed here (use Optional bytes / None).
    """

    summary_id: str = Field(default_factory=_new_uuid)
    context_id: str
    user_id: str
    domain: Domain
    generated_at: datetime = Field(default_factory=_utcnow)
    confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    record_count: int = Field(default=0, ge=0)
    text: str = Field(default="", description="Confidence-weighted natural-language summary")
    # Placeholder — no vendor assumed.
    embedding_stub: bytes | None = None

    model_config = {"frozen": True}
