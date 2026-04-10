"""
Time-based decay management for careful-memory.

Decay reduces the evidence mass in (α, β) toward the uniform prior,
making beliefs less certain over time without deleting them.

Key rules:
  1. Decay is SYMMETRIC — both α and β shrink at the same rate.
     This preserves the confidence ratio while reducing certainty.
  2. Neither α nor β may fall below the prior floor (1.0).
  3. When effective confidence falls below ARCHIVE_THRESHOLD, the record
     is eligible for archiving — it is not deleted.
  4. Decay rates differ by MemoryType and are multiplied for project-domain.

This module is stateless — callers supply the record and elapsed time.
"""

from __future__ import annotations

from datetime import UTC, datetime

from careful_memory.core.bayesian import BayesianUpdateResult, apply_decay
from careful_memory.models.enums import Domain, MemoryType, RecordStatus
from careful_memory.models.memory import MemoryRecord

# ---------------------------------------------------------------------------
# Constants (all justified below)
# ---------------------------------------------------------------------------

# Default decay rates (fraction of excess evidence lost per day).
# Episodic memories decay the fastest; semantic the slowest.
# These are conservative baselines — adjust per deployment config.
DECAY_RATES_BY_TYPE: dict[MemoryType, float] = {
    MemoryType.episodic: 0.05,    # ~14 days to halve excess evidence
    MemoryType.semantic: 0.005,   # ~139 days to halve — very stable
    MemoryType.procedural: 0.01,  # ~69 days to halve
}

# Project-domain multiplier: project memories are transient.
PROJECT_DOMAIN_MULTIPLIER: float = 2.0

# When confidence drops below this threshold, the record should be archived.
# 0.3 means we are more uncertain than not — archiving is warranted.
ARCHIVE_THRESHOLD: float = 0.3


class DecayResult:
    """Result of applying decay to a single record."""

    __slots__ = (
        "new_alpha",
        "new_beta",
        "new_confidence",
        "should_archive",
        "bayesian_result",
    )

    def __init__(self, bayesian: BayesianUpdateResult, threshold: float) -> None:
        self.new_alpha = bayesian.new_alpha
        self.new_beta = bayesian.new_beta
        self.new_confidence = bayesian.new_confidence
        self.should_archive = bayesian.new_confidence < threshold
        self.bayesian_result = bayesian


def decay_rate_for(memory_type: MemoryType, domain: Domain, override: float | None = None) -> float:
    """
    Return the effective daily decay rate for a memory type + domain combination.

    Parameters
    ----------
    memory_type : MemoryType
    domain      : Domain
    override    : if provided, use this rate instead of the default
                  (still subject to the project multiplier)
    """
    base = override if override is not None else DECAY_RATES_BY_TYPE[memory_type]
    if domain == Domain.project:
        base = min(base * PROJECT_DOMAIN_MULTIPLIER, 1.0)
    return base


def compute_decay(
    record: MemoryRecord,
    domain: Domain,
    as_of: datetime | None = None,
    archive_threshold: float = ARCHIVE_THRESHOLD,
) -> DecayResult:
    """
    Compute the decayed (α, β) for *record* as of *as_of*.

    Does NOT mutate the record.  The caller is responsible for persisting
    the result.

    Parameters
    ----------
    record            : the record to decay
    domain            : the domain of the owning ContextScope
    as_of             : reference time for decay (defaults to now)
    archive_threshold : confidence level below which the record is archived
    """
    now = as_of or datetime.now(tz=UTC)
    reference = record.last_decayed_at or record.created_at

    elapsed_seconds = (now - reference).total_seconds()
    elapsed_days = max(elapsed_seconds / 86_400.0, 0.0)  # never negative

    effective_rate = decay_rate_for(record.memory_type, domain, override=record.decay_rate)

    bayesian = apply_decay(record, elapsed_days=elapsed_days, domain_decay_rate=effective_rate)
    return DecayResult(bayesian=bayesian, threshold=archive_threshold)


def apply_decay_to_record(
    record: MemoryRecord,
    domain: Domain,
    as_of: datetime | None = None,
    archive_threshold: float = ARCHIVE_THRESHOLD,
) -> MemoryRecord:
    """
    Return a NEW MemoryRecord with updated α, β, status, and last_decayed_at.

    If the decayed confidence falls below *archive_threshold* and the record
    is currently active, its status is set to 'archived'.

    Parameters
    ----------
    record            : the source record (not mutated)
    domain            : the domain of the owning ContextScope
    as_of             : reference time (defaults to now)
    archive_threshold : threshold below which status → archived
    """
    now = as_of or datetime.now(tz=UTC)
    result = compute_decay(record, domain=domain, as_of=now, archive_threshold=archive_threshold)

    new_status = record.status
    if result.should_archive and record.status == RecordStatus.active:
        new_status = RecordStatus.archived

    return record.model_copy(
        update={
            "alpha": result.new_alpha,
            "beta": result.new_beta,
            "status": new_status,
            "last_decayed_at": now,
            "updated_at": now,
        }
    )
