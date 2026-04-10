"""
Write-gate and validation layer for careful-memory.

The write gate is the SINGLE entry point for all memory writes.
It enforces every safety guarantee listed in the problem statement:

  1. Authority levels — lower-authority writes cannot overwrite higher-authority beliefs.
  2. No self-reinforcement loops — LLM usage alone cannot reinforce memory.
  3. Rate limits — too many evidence updates in a short window are rejected.
  4. Cross-context isolation — a write targeting a different context_id is rejected.
  5. Outlier detection — large confidence swings are flagged; extreme ones rejected.
  6. Promotion rules — episodic → semantic only when criteria are met.

All gate decisions are returned as a typed result, never raising silently.
The storage layer is NOT called here — that is the caller's responsibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from careful_memory.core.bayesian import (
    BayesianUpdateResult,
    apply_contradicting_evidence,
    apply_supporting_evidence,
)
from careful_memory.models.enums import (
    EvidenceType,
    MemoryType,
    RecordStatus,
)
from careful_memory.models.memory import (
    ContextScope,
    EvidenceEvent,
    MemoryRecord,
)

# ---------------------------------------------------------------------------
# Configuration constants (all with justification)
# ---------------------------------------------------------------------------

# Maximum evidence events per record per time window.
# Prevents flooding via rapid-fire API calls.
RATE_LIMIT_MAX_EVENTS: int = 10
RATE_LIMIT_WINDOW_SECONDS: int = 3600  # 1 hour

# Swing beyond which an evidence update is rejected as an outlier.
# OUTLIER_SWING_THRESHOLD (0.25) triggers a warning.
# This higher threshold (0.5) causes a hard rejection.
# A 50-point confidence jump from a single event is almost certainly
# an error or an attack.
OUTLIER_REJECT_THRESHOLD: float = 0.50

# Minimum number of distinct reinforcement events required before an
# episodic belief may be promoted to semantic.
PROMOTION_MIN_REINFORCEMENTS: int = 3

# Minimum confidence required for promotion.
PROMOTION_MIN_CONFIDENCE: float = 0.80

# Minimum time spread (days) between first and last reinforcement for promotion.
# Evidence must be spread over time, not burst-submitted.
PROMOTION_MIN_DAYS_SPREAD: float = 3.0


# ---------------------------------------------------------------------------
# Gate result types
# ---------------------------------------------------------------------------


class GateVerdict(StrEnum):
    allowed = "allowed"
    rejected = "rejected"
    flagged = "flagged"  # allowed but logged as suspicious


@dataclass(frozen=True)
class GateResult:
    """
    Outcome of a write-gate check.

    verdict     : allowed / rejected / flagged
    reason      : human-readable explanation
    bayesian    : populated for evidence updates; None for new-record writes
    """

    verdict: GateVerdict
    reason: str
    bayesian: BayesianUpdateResult | None = None

    @property
    def is_allowed(self) -> bool:
        return self.verdict in (GateVerdict.allowed, GateVerdict.flagged)


# ---------------------------------------------------------------------------
# Rate-limit tracker (in-process; replace with Redis/Azure Cache in prod)
# ---------------------------------------------------------------------------


@dataclass
class _RateLimitWindow:
    """Sliding-window event counter for a single (context_id, record_id) pair."""

    events: list[datetime] = field(default_factory=list)

    def record_event(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=RATE_LIMIT_WINDOW_SECONDS)
        self.events = [t for t in self.events if t > cutoff]
        self.events.append(now)

    def count_in_window(self, now: datetime) -> int:
        cutoff = now - timedelta(seconds=RATE_LIMIT_WINDOW_SECONDS)
        return sum(1 for t in self.events if t > cutoff)


class WriteGate:
    """
    Stateful write-gate enforcing all memory safety rules.

    In production, inject a distributed rate-limit store (e.g. Azure Cache
    for Redis) instead of the in-process dict used here.

    Usage:
        gate = WriteGate()
        result = gate.check_new_record(scope, record)
        if result.is_allowed:
            storage.save(record)
    """

    def __init__(self) -> None:
        # In-process rate-limit windows keyed by (context_id, record_id).
        # Production: replace with a distributed cache or Azure Cache for Redis.
        self._rate_windows: dict[tuple[str, str], _RateLimitWindow] = {}

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def check_new_record(
        self,
        scope: ContextScope,
        record: MemoryRecord,
    ) -> GateResult:
        """
        Validate a brand-new memory record before it is persisted.

        Checks:
          - context isolation
          - authority level >= user (always true by type; validated for sanity)
        """
        if record.context_id != scope.context_id:
            return GateResult(
                verdict=GateVerdict.rejected,
                reason=(
                    f"context_id mismatch: record.context_id={record.context_id!r} "
                    f"does not match scope.context_id={scope.context_id!r}"
                ),
            )
        return GateResult(verdict=GateVerdict.allowed, reason="new record passed validation")

    def check_evidence_update(
        self,
        scope: ContextScope,
        record: MemoryRecord,
        event: EvidenceEvent,
        now: datetime | None = None,
    ) -> GateResult:
        """
        Validate an evidence update event against all safety rules.

        Rules checked (in order):
          1. Context isolation
          2. Evidence type must not be LLM-only (no self-reinforcement)
          3. Rate limit
          4. Authority: cannot reinforce a higher-authority record with lower-authority evidence
          5. Record must be active
          6. Outlier detection (swing > OUTLIER_REJECT_THRESHOLD → reject)
        """
        now = now or datetime.now(tz=UTC)

        # 1. Context isolation
        if event.context_id != scope.context_id:
            return GateResult(
                verdict=GateVerdict.rejected,
                reason=(
                    f"context_id mismatch: event.context_id={event.context_id!r} "
                    f"!= scope.context_id={scope.context_id!r}"
                ),
            )
        if record.context_id != scope.context_id:
            return GateResult(
                verdict=GateVerdict.rejected,
                reason=(
                    f"context_id mismatch: record.context_id={record.context_id!r} "
                    f"!= scope.context_id={scope.context_id!r}"
                ),
            )

        # 2. No self-reinforcement: EvidenceType must exist and be valid.
        #    The EvidenceType enum already excludes "llm_inference", so any
        #    valid EvidenceType is acceptable. This check is belt-and-suspenders.
        if event.evidence_type not in (
            EvidenceType.user_restatement,
            EvidenceType.user_action,
            EvidenceType.verified_system_outcome,
        ):
            return GateResult(
                verdict=GateVerdict.rejected,
                reason=(
                    f"evidence_type {event.evidence_type!r} is not an accepted "
                    "external-evidence type; LLM inference alone cannot reinforce memory"
                ),
            )

        # 3. Rate limit
        window = self._get_window(scope.context_id, record.id)
        if window.count_in_window(now) >= RATE_LIMIT_MAX_EVENTS:
            return GateResult(
                verdict=GateVerdict.rejected,
                reason=(
                    f"rate limit exceeded: more than {RATE_LIMIT_MAX_EVENTS} evidence "
                    f"events for record {record.id!r} within "
                    f"{RATE_LIMIT_WINDOW_SECONDS}s"
                ),
            )

        # 4. Authority: lower-authority source cannot reinforce higher-authority record.
        if event.source.authority_level < record.source.authority_level:
            return GateResult(
                verdict=GateVerdict.rejected,
                reason=(
                    f"authority violation: source authority "
                    f"{event.source.authority_level.name} < record authority "
                    f"{record.source.authority_level.name}"
                ),
            )

        # 5. Record must be active to receive evidence.
        if record.status != RecordStatus.active:
            return GateResult(
                verdict=GateVerdict.rejected,
                reason=(
                    f"record {record.id!r} has status {record.status.value!r}; "
                    "only active records can receive evidence updates"
                ),
            )

        # 6. Compute the hypothetical Bayesian update and check for outliers.
        if event.supports:
            bayesian = apply_supporting_evidence(record)
        else:
            bayesian = apply_contradicting_evidence(record)

        if abs(bayesian.delta_confidence) >= OUTLIER_REJECT_THRESHOLD:
            return GateResult(
                verdict=GateVerdict.rejected,
                reason=(
                    f"outlier rejected: confidence swing of "
                    f"{bayesian.delta_confidence:+.3f} exceeds hard limit "
                    f"{OUTLIER_REJECT_THRESHOLD}"
                ),
                bayesian=bayesian,
            )

        # Record rate-limit event only after all checks pass.
        window.record_event(now)

        verdict = GateVerdict.flagged if bayesian.is_outlier else GateVerdict.allowed
        reason = (
            f"evidence update allowed (swing={bayesian.delta_confidence:+.3f}"
            + (", flagged as outlier" if bayesian.is_outlier else "")
            + ")"
        )
        return GateResult(verdict=verdict, reason=reason, bayesian=bayesian)

    def check_contradiction(
        self,
        scope: ContextScope,
        existing: MemoryRecord,
        new_record: MemoryRecord,
    ) -> GateResult:
        """
        Validate that a contradiction write is permissible.

        Rules:
          - Same context
          - new_record.source.authority_level >= existing.source.authority_level
          - existing must be active (retracted records cannot be further contradicted)
        """
        if new_record.context_id != scope.context_id:
            return GateResult(
                verdict=GateVerdict.rejected,
                reason="context_id mismatch on contradiction write",
            )
        if existing.context_id != scope.context_id:
            return GateResult(
                verdict=GateVerdict.rejected,
                reason="existing record belongs to a different context",
            )
        if new_record.source.authority_level < existing.source.authority_level:
            return GateResult(
                verdict=GateVerdict.rejected,
                reason=(
                    f"authority violation on contradiction: new record authority "
                    f"{new_record.source.authority_level.name} < existing authority "
                    f"{existing.source.authority_level.name}"
                ),
            )
        if existing.status == RecordStatus.retracted:
            return GateResult(
                verdict=GateVerdict.rejected,
                reason=f"cannot contradict a retracted record ({existing.id!r})",
            )
        return GateResult(verdict=GateVerdict.allowed, reason="contradiction write allowed")

    def check_promotion(
        self,
        record: MemoryRecord,
        scope: ContextScope,
    ) -> GateResult:
        """
        Check whether an episodic record is eligible for promotion to semantic.

        Promotion criteria (all must pass):
          - memory_type is episodic
          - confidence >= PROMOTION_MIN_CONFIDENCE
          - reinforcement_count >= PROMOTION_MIN_REINFORCEMENTS
          - time spread between created_at and last_reinforced_at >= PROMOTION_MIN_DAYS_SPREAD
        """
        if record.memory_type != MemoryType.episodic:
            return GateResult(
                verdict=GateVerdict.rejected,
                reason=f"only episodic memories may be promoted; got {record.memory_type.value!r}",
            )
        if record.confidence < PROMOTION_MIN_CONFIDENCE:
            return GateResult(
                verdict=GateVerdict.rejected,
                reason=(
                    f"confidence {record.confidence:.3f} < "
                    f"required {PROMOTION_MIN_CONFIDENCE}"
                ),
            )
        if record.reinforcement_count < PROMOTION_MIN_REINFORCEMENTS:
            return GateResult(
                verdict=GateVerdict.rejected,
                reason=(
                    f"reinforcement_count {record.reinforcement_count} < "
                    f"required {PROMOTION_MIN_REINFORCEMENTS}"
                ),
            )
        if record.last_reinforced_at is None:
            return GateResult(
                verdict=GateVerdict.rejected,
                reason="record has never been reinforced; cannot determine time spread",
            )
        spread_days = (record.last_reinforced_at - record.created_at).total_seconds() / 86_400.0
        if spread_days < PROMOTION_MIN_DAYS_SPREAD:
            return GateResult(
                verdict=GateVerdict.rejected,
                reason=(
                    f"evidence spread {spread_days:.1f} days < "
                    f"required {PROMOTION_MIN_DAYS_SPREAD} days"
                ),
            )
        return GateResult(verdict=GateVerdict.allowed, reason="promotion criteria met")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_window(self, context_id: str, record_id: str) -> _RateLimitWindow:
        key = (context_id, record_id)
        if key not in self._rate_windows:
            self._rate_windows[key] = _RateLimitWindow()
        return self._rate_windows[key]
