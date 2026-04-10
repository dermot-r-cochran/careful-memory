"""
Tests for Bayesian confidence update logic.
"""

from __future__ import annotations

import pytest

from careful_memory.core.bayesian import (
    apply_contradicting_evidence,
    apply_decay,
    apply_supporting_evidence,
    credible_interval,
    effective_confidence_after_decay,
)
from careful_memory.models.enums import AuthorityLevel, Domain
from careful_memory.models.memory import ContextScope, MemoryRecord, MemorySource
from tests.conftest import make_record


@pytest.fixture()
def scope() -> ContextScope:
    return ContextScope(user_id="u1", domain=Domain.personal)


@pytest.fixture()
def source() -> MemorySource:
    return MemorySource(origin="test", authority_level=AuthorityLevel.user)


@pytest.fixture()
def record(scope: ContextScope, source: MemorySource) -> MemoryRecord:
    return make_record(scope, source)


class TestInitialConfidence:
    def test_uniform_prior(self, record: MemoryRecord) -> None:
        """Beta(1,1) → confidence = 0.5."""
        assert record.confidence == pytest.approx(0.5)

    def test_alpha_raises_confidence(self, record: MemoryRecord) -> None:
        result = apply_supporting_evidence(record)
        assert result.new_confidence > 0.5

    def test_beta_lowers_confidence(self, record: MemoryRecord) -> None:
        result = apply_contradicting_evidence(record)
        assert result.new_confidence < 0.5


class TestSupportingEvidence:
    def test_alpha_increments(self, record: MemoryRecord) -> None:
        result = apply_supporting_evidence(record)
        assert result.new_alpha == pytest.approx(record.alpha + 1.0)
        assert result.new_beta == pytest.approx(record.beta)

    def test_confidence_formula(self, record: MemoryRecord) -> None:
        result = apply_supporting_evidence(record, weight=2.0)
        expected = (record.alpha + 2.0) / (record.alpha + 2.0 + record.beta)
        assert result.new_confidence == pytest.approx(expected)

    def test_zero_weight_raises(self, record: MemoryRecord) -> None:
        with pytest.raises(ValueError):
            apply_supporting_evidence(record, weight=0.0)

    def test_negative_weight_raises(self, record: MemoryRecord) -> None:
        with pytest.raises(ValueError):
            apply_supporting_evidence(record, weight=-1.0)


class TestContradictingEvidence:
    def test_beta_increments(self, record: MemoryRecord) -> None:
        result = apply_contradicting_evidence(record)
        assert result.new_beta == pytest.approx(record.beta + 1.0)
        assert result.new_alpha == pytest.approx(record.alpha)

    def test_confidence_decreases(self, record: MemoryRecord) -> None:
        result = apply_contradicting_evidence(record)
        assert result.new_confidence < record.confidence


class TestOutlierDetection:
    def test_large_swing_flagged(self, scope: ContextScope, source: MemorySource) -> None:
        """A record with tiny evidence mass has a large swing per event."""
        # α=1, β=50 → adding 1 support gives a small swing (denominator is large)
        r2 = make_record(scope, source, alpha=1.0, beta=50.0)
        result2 = apply_supporting_evidence(r2)
        assert not result2.is_outlier

    def test_outlier_flag_on_large_swing(self, scope: ContextScope, source: MemorySource) -> None:
        """Artificially craft a scenario where swing > threshold."""
        # α=1, β=1 → confidence=0.5.  Add weight=100 → new conf ≈ 101/102 ≈ 0.99
        # swing ≈ 0.49 > 0.25
        r = make_record(scope, source, alpha=1.0, beta=1.0)
        result = apply_supporting_evidence(r, weight=100.0)
        assert result.is_outlier


class TestDecay:
    def test_zero_elapsed_no_change(self, record: MemoryRecord) -> None:
        result = apply_decay(record, elapsed_days=0.0, domain_decay_rate=0.01)
        assert result.new_alpha == pytest.approx(record.alpha)
        assert result.new_beta == pytest.approx(record.beta)

    def test_decay_reduces_excess_evidence(self, scope: ContextScope, source: MemorySource) -> None:
        r = make_record(scope, source, alpha=10.0, beta=1.0)
        result = apply_decay(r, elapsed_days=30.0, domain_decay_rate=0.05)
        # α should be lower but still ≥ 1.0
        assert result.new_alpha < 10.0
        assert result.new_alpha >= 1.0

    def test_floor_never_broken(self, scope: ContextScope, source: MemorySource) -> None:
        r = make_record(scope, source, alpha=1.0, beta=1.0)
        # Even with 100% decay over 1000 days, floor is 1.0
        result = apply_decay(r, elapsed_days=1000.0, domain_decay_rate=0.99)
        assert result.new_alpha >= 1.0
        assert result.new_beta >= 1.0

    def test_negative_elapsed_raises(self, record: MemoryRecord) -> None:
        with pytest.raises(ValueError):
            apply_decay(record, elapsed_days=-1.0, domain_decay_rate=0.01)


class TestConfidenceDerived:
    def test_confidence_not_directly_writable(self, scope: ContextScope, source: MemorySource) -> None:
        """confidence is a computed_field; direct assignment has no effect."""
        r = make_record(scope, source, alpha=3.0, beta=1.0)
        # confidence = 3/4 = 0.75
        assert r.confidence == pytest.approx(0.75)

    def test_accumulated_evidence(self, scope: ContextScope, source: MemorySource) -> None:
        r = make_record(scope, source, alpha=8.0, beta=2.0)
        assert r.confidence == pytest.approx(0.8)


class TestCredibleInterval:
    def test_symmetric_at_half(self) -> None:
        lo, hi = credible_interval(1.0, 1.0)
        assert lo < 0.5 < hi
        # Symmetric around 0.5
        assert abs((0.5 - lo) - (hi - 0.5)) < 0.01

    def test_interval_within_bounds(self) -> None:
        lo, hi = credible_interval(5.0, 5.0)
        assert 0.0 <= lo <= hi <= 1.0

    def test_high_confidence_interval(self) -> None:
        lo, hi = credible_interval(90.0, 10.0)
        assert lo > 0.5
        assert hi <= 1.0


class TestEffectiveConfidenceAfterDecay:
    def test_decreases_over_time(self) -> None:
        c0 = effective_confidence_after_decay(10.0, 1.0, decay_rate=0.05, elapsed_days=0)
        c1 = effective_confidence_after_decay(10.0, 1.0, decay_rate=0.05, elapsed_days=100)
        assert c1 < c0
