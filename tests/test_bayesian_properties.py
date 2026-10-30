"""
Property-based tests (hypothesis) for the Bayesian update rules.

``test_bayesian.py`` checks the update, decay and interval functions on
fixed cases. The promises those cases illustrate hold for every record, so
here hypothesis searches the space of (α, β, weight, decay) for a
counterexample instead:

- Direction: supporting evidence never lowers confidence, contradicting
  evidence never raises it, and repeated identical evidence converges on
  its limit rather than overshooting or oscillating.
- Bounded swing: one event of weight w moves confidence by at most
  w / (α + β + w), and the result stays inside [0, 1].
- Decay toward the prior (ADR-0005): decay never pushes α or β below the
  Beta(1, 1) floor and never moves confidence further from 0.5.

The same two properties of a Beta-mean confidence (a bounded single-record
swing that keeps the result in [0, 1], and an update that never runs
against its evidence) are pinned in ``dermot-r-cochran/swarm`` for its
ADR-0005, which cites this repository's ADR-0001 as the model it followed:
https://github.com/dermot-r-cochran/swarm/blob/512a74a/docs/adr/0005-beta-posterior-confidence.md
No code is shared and neither repository imports the other; these tests
state the properties for this repository's own functions.

Closes the Bayesian-properties gap that ``TestingStrategy.md`` listed
(2026-10-30, Dermot's go-ahead "All three").
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from careful_memory.core.bayesian import (
    apply_contradicting_evidence,
    apply_decay,
    apply_supporting_evidence,
)
from careful_memory.models.enums import AuthorityLevel, Domain
from careful_memory.models.memory import ContextScope, MemoryRecord, MemorySource
from tests.conftest import make_record

# Bounded so a true difference always exceeds float rounding: the smallest
# swing here is about 0.01 / (2e4)^2, far above 1e-12.
counts = st.floats(min_value=1.0, max_value=1e4, allow_nan=False, allow_infinity=False)
weights = st.floats(min_value=0.01, max_value=100.0, allow_nan=False, allow_infinity=False)
decay_rates = st.floats(min_value=1e-6, max_value=1.0, allow_nan=False, allow_infinity=False)
elapsed = st.floats(min_value=0.0, max_value=3650.0, allow_nan=False, allow_infinity=False)

TOLERANCE = 1e-12


def _record(alpha: float, beta: float) -> MemoryRecord:
    scope = ContextScope(user_id="u1", domain=Domain.personal)
    source = MemorySource(origin="test", authority_level=AuthorityLevel.user)
    return make_record(scope, source, alpha=alpha, beta=beta)


class TestDirection:
    @given(alpha=counts, beta=counts, weight=weights)
    def test_support_never_lowers_confidence(
        self, alpha: float, beta: float, weight: float
    ) -> None:
        record = _record(alpha, beta)
        result = apply_supporting_evidence(record, weight=weight)
        assert result.new_confidence >= record.confidence - TOLERANCE
        assert result.delta_confidence >= -TOLERANCE

    @given(alpha=counts, beta=counts, weight=weights)
    def test_contradiction_never_raises_confidence(
        self, alpha: float, beta: float, weight: float
    ) -> None:
        record = _record(alpha, beta)
        result = apply_contradicting_evidence(record, weight=weight)
        assert result.new_confidence <= record.confidence + TOLERANCE
        assert result.delta_confidence <= TOLERANCE

    @given(
        alpha=counts,
        beta=counts,
        steps=st.integers(min_value=2, max_value=30),
        supports=st.booleans(),
    )
    def test_repeated_identical_evidence_converges(
        self, alpha: float, beta: float, steps: int, supports: bool
    ) -> None:
        """Each repeat closes the gap to the limit (1 or 0) without crossing it."""
        apply = apply_supporting_evidence if supports else apply_contradicting_evidence
        limit = 1.0 if supports else 0.0
        record = _record(alpha, beta)
        gap = abs(limit - record.confidence)
        for _ in range(steps):
            result = apply(record)
            new_gap = abs(limit - result.new_confidence)
            assert new_gap <= gap + TOLERANCE
            assert 0.0 <= result.new_confidence <= 1.0
            gap = new_gap
            record = _record(result.new_alpha, result.new_beta)


class TestBoundedSwing:
    @given(alpha=counts, beta=counts, weight=weights, supports=st.booleans())
    def test_one_event_moves_confidence_at_most_its_share(
        self, alpha: float, beta: float, weight: float, supports: bool
    ) -> None:
        """|Δconfidence| ≤ w / (α + β + w): one event cannot outweigh the evidence held."""
        record = _record(alpha, beta)
        apply = apply_supporting_evidence if supports else apply_contradicting_evidence
        result = apply(record, weight=weight)
        assert abs(result.delta_confidence) <= weight / (alpha + beta + weight) + TOLERANCE
        assert 0.0 <= result.new_confidence <= 1.0


class TestDecayTowardPrior:
    @given(alpha=counts, beta=counts, rate=decay_rates, days=elapsed)
    def test_decay_keeps_floor_and_never_moves_away_from_half(
        self, alpha: float, beta: float, rate: float, days: float
    ) -> None:
        record = _record(alpha, beta)
        result = apply_decay(record, elapsed_days=days, domain_decay_rate=rate)
        assert result.new_alpha >= 1.0
        assert result.new_beta >= 1.0
        assert abs(result.new_confidence - 0.5) <= abs(record.confidence - 0.5) + TOLERANCE
