"""
Tests for the write-gate: authority, rate-limits, self-reinforcement,
outlier detection, promotion criteria.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from careful_memory.core.bayesian import OUTLIER_SWING_THRESHOLD
from careful_memory.core.gate import (
    OUTLIER_REJECT_THRESHOLD,
    PROMOTION_MIN_DAYS_SPREAD,
    PROMOTION_MIN_REINFORCEMENTS,
    RATE_LIMIT_MAX_EVENTS,
    GateVerdict,
    WriteGate,
)
from careful_memory.models.enums import (
    AuthorityLevel,
    Domain,
    EvidenceType,
    MemoryType,
    RecordStatus,
)
from careful_memory.models.memory import (
    ContextScope,
    EvidenceEvent,
    MemoryRecord,
    MemorySource,
)
from tests.conftest import make_record


@pytest.fixture()
def scope() -> ContextScope:
    return ContextScope(user_id="u1", domain=Domain.personal)


@pytest.fixture()
def user_src() -> MemorySource:
    return MemorySource(origin="agent", authority_level=AuthorityLevel.user)


@pytest.fixture()
def system_src() -> MemorySource:
    return MemorySource(origin="system", authority_level=AuthorityLevel.system)


@pytest.fixture()
def verified_src() -> MemorySource:
    return MemorySource(origin="verified", authority_level=AuthorityLevel.verified_system)


@pytest.fixture()
def gate() -> WriteGate:
    return WriteGate()


class TestNewRecord:
    def test_valid_record_allowed(self, gate: WriteGate, scope: ContextScope, user_src: MemorySource) -> None:
        r = make_record(scope, user_src)
        result = gate.check_new_record(scope, r)
        assert result.is_allowed

    def test_context_mismatch_rejected(self, gate: WriteGate, scope: ContextScope, user_src: MemorySource) -> None:
        other = ContextScope(user_id="u2", domain=Domain.personal)
        r = make_record(other, user_src)  # record belongs to other context
        result = gate.check_new_record(scope, r)  # but we present scope
        assert result.verdict == GateVerdict.rejected


class TestEvidenceUpdate:
    def _make_event(
        self,
        record: MemoryRecord,
        scope: ContextScope,
        source: MemorySource,
        supports: bool = True,
        evidence_type: EvidenceType = EvidenceType.user_restatement,
    ) -> EvidenceEvent:
        return EvidenceEvent(
            record_id=record.id,
            context_id=scope.context_id,
            supports=supports,
            evidence_type=evidence_type,
            source=source,
        )

    def test_valid_event_allowed(
        self, gate: WriteGate, scope: ContextScope, user_src: MemorySource
    ) -> None:
        r = make_record(scope, user_src)
        event = self._make_event(r, scope, user_src)
        result = gate.check_evidence_update(scope, r, event)
        assert result.is_allowed

    def test_inactive_record_rejected(
        self, gate: WriteGate, scope: ContextScope, user_src: MemorySource
    ) -> None:
        r = make_record(scope, user_src)
        r = r.model_copy(update={"status": RecordStatus.contradicted})
        event = self._make_event(r, scope, user_src)
        result = gate.check_evidence_update(scope, r, event)
        assert result.verdict == GateVerdict.rejected
        assert "active" in result.reason

    def test_lower_authority_rejected(
        self, gate: WriteGate, scope: ContextScope, user_src: MemorySource, system_src: MemorySource
    ) -> None:
        # Record is system-authority; event is user-authority → rejected
        r = make_record(scope, system_src)
        event = self._make_event(r, scope, user_src)
        result = gate.check_evidence_update(scope, r, event)
        assert result.verdict == GateVerdict.rejected
        assert "authority" in result.reason.lower()

    def test_rate_limit_enforced(
        self, gate: WriteGate, scope: ContextScope, user_src: MemorySource
    ) -> None:
        r = make_record(scope, user_src)
        now = datetime.now(tz=UTC)
        for _ in range(RATE_LIMIT_MAX_EVENTS):
            event = self._make_event(r, scope, user_src)
            gate.check_evidence_update(scope, r, event, now=now)
        # One more should be rate-limited
        extra = self._make_event(r, scope, user_src)
        result = gate.check_evidence_update(scope, r, extra, now=now)
        assert result.verdict == GateVerdict.rejected
        assert "rate limit" in result.reason.lower()

    def test_context_isolation(
        self, gate: WriteGate, scope: ContextScope, user_src: MemorySource
    ) -> None:
        other = ContextScope(user_id="u2", domain=Domain.personal)
        r = make_record(scope, user_src)
        # Event has wrong context_id
        event = EvidenceEvent(
            record_id=r.id,
            context_id=other.context_id,
            supports=True,
            evidence_type=EvidenceType.user_action,
            source=user_src,
        )
        result = gate.check_evidence_update(scope, r, event)
        assert result.verdict == GateVerdict.rejected

    # The outlier rule, both sides and the bound between them. The gate adds
    # one observation per event and a record's counters never sit below the
    # Beta(1,1) prior, so the swing a single event can cause is
    # (the counter not incremented) / ((a+b)(a+b+1)), largest at (sqrt(2), 1)
    # or (1, sqrt(2)): 3 - 2*sqrt(2), about 0.172. That is below the flag
    # threshold (0.25) and the reject threshold (0.50), so neither branch is
    # reachable from a valid record. They are defence in depth against
    # counters that reach the gate below the prior, which the model refuses
    # to build; the two tests after the bound make such records with
    # model_copy, which skips validation, to show the branches still hold.

    def test_unit_evidence_on_valid_record_never_reaches_outlier_thresholds(
        self, gate: WriteGate, scope: ContextScope, user_src: MemorySource
    ) -> None:
        bound = 3 - 2 * math.sqrt(2)
        assert bound < OUTLIER_SWING_THRESHOLD < OUTLIER_REJECT_THRESHOLD
        counters = [1.0, 1.25, math.sqrt(2), 2.0, 5.0, 50.0, 1000.0]
        for alpha in counters:
            for beta in counters:
                for supports in (True, False):
                    r = make_record(scope, user_src, alpha=alpha, beta=beta)
                    event = self._make_event(r, scope, user_src, supports=supports)
                    result = gate.check_evidence_update(scope, r, event)
                    assert result.verdict == GateVerdict.allowed, (alpha, beta, supports)
                    assert result.bayesian is not None
                    assert abs(result.bayesian.delta_confidence) <= bound + 1e-12

    def test_outlier_swing_flagged(
        self, gate: WriteGate, scope: ContextScope, user_src: MemorySource
    ) -> None:
        # Beta(0.4, 0.4) + 1 support: 0.5 -> 1.4/1.8, a swing of about 0.278,
        # between the two thresholds: allowed, and marked as suspicious.
        r = make_record(scope, user_src).model_copy(update={"alpha": 0.4, "beta": 0.4})
        event = self._make_event(r, scope, user_src)
        result = gate.check_evidence_update(scope, r, event)
        assert result.verdict == GateVerdict.flagged
        assert result.is_allowed
        assert "flagged as outlier" in result.reason

    def test_outlier_swing_rejected(
        self, gate: WriteGate, scope: ContextScope, user_src: MemorySource
    ) -> None:
        # Beta(0.001, 0.01) + 1 support: about 0.09 -> 0.99, a swing of about
        # 0.90, past the hard limit: refused, with the computed update attached.
        r = make_record(scope, user_src).model_copy(update={"alpha": 0.001, "beta": 0.01})
        event = self._make_event(r, scope, user_src)
        result = gate.check_evidence_update(scope, r, event)
        assert result.verdict == GateVerdict.rejected
        assert not result.is_allowed
        assert "outlier rejected" in result.reason
        assert result.bayesian is not None
        assert abs(result.bayesian.delta_confidence) >= OUTLIER_REJECT_THRESHOLD

    def test_user_action_evidence_allowed(
        self, gate: WriteGate, scope: ContextScope, user_src: MemorySource
    ) -> None:
        r = make_record(scope, user_src)
        event = self._make_event(r, scope, user_src, evidence_type=EvidenceType.user_action)
        result = gate.check_evidence_update(scope, r, event)
        assert result.is_allowed

    def test_verified_outcome_evidence_allowed(
        self, gate: WriteGate, scope: ContextScope, verified_src: MemorySource
    ) -> None:
        r = make_record(scope, verified_src)
        event = EvidenceEvent(
            record_id=r.id,
            context_id=scope.context_id,
            supports=True,
            evidence_type=EvidenceType.verified_system_outcome,
            source=verified_src,
        )
        result = gate.check_evidence_update(scope, r, event)
        assert result.is_allowed


class TestPromotion:
    def _promotable_record(self, scope: ContextScope, source: MemorySource) -> MemoryRecord:
        """Build a record that satisfies all promotion criteria."""
        past = datetime.now(tz=UTC) - timedelta(days=PROMOTION_MIN_DAYS_SPREAD + 1)
        r = make_record(scope, source, alpha=9.0, beta=1.0)  # confidence = 0.9
        return r.model_copy(
            update={
                "reinforcement_count": PROMOTION_MIN_REINFORCEMENTS,
                "last_reinforced_at": datetime.now(tz=UTC),
                "created_at": past,
            }
        )

    def test_eligible_record_approved(
        self, gate: WriteGate, scope: ContextScope, user_src: MemorySource
    ) -> None:
        r = self._promotable_record(scope, user_src)
        result = gate.check_promotion(r, scope)
        assert result.is_allowed

    def test_wrong_type_rejected(
        self, gate: WriteGate, scope: ContextScope, user_src: MemorySource
    ) -> None:
        r = make_record(scope, user_src, memory_type=MemoryType.semantic)
        result = gate.check_promotion(r, scope)
        assert result.verdict == GateVerdict.rejected

    def test_low_confidence_rejected(
        self, gate: WriteGate, scope: ContextScope, user_src: MemorySource
    ) -> None:
        r = self._promotable_record(scope, user_src)
        r = r.model_copy(update={"alpha": 1.0, "beta": 1.0})  # confidence = 0.5
        result = gate.check_promotion(r, scope)
        assert result.verdict == GateVerdict.rejected

    def test_insufficient_reinforcements_rejected(
        self, gate: WriteGate, scope: ContextScope, user_src: MemorySource
    ) -> None:
        r = self._promotable_record(scope, user_src)
        r = r.model_copy(update={"reinforcement_count": 0})
        result = gate.check_promotion(r, scope)
        assert result.verdict == GateVerdict.rejected

    def test_insufficient_time_spread_rejected(
        self, gate: WriteGate, scope: ContextScope, user_src: MemorySource
    ) -> None:
        r = self._promotable_record(scope, user_src)
        # Make created_at == last_reinforced_at (zero spread)
        now = datetime.now(tz=UTC)
        r = r.model_copy(update={"created_at": now, "last_reinforced_at": now})
        result = gate.check_promotion(r, scope)
        assert result.verdict == GateVerdict.rejected
