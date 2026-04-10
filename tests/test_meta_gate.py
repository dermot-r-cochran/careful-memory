"""
Tests for the MetaGate — reasoning-quality gate.

Verifies the core invariants:
  - MetaGate never modifies alpha, beta, or confidence
  - LOW level blocks before review (no belief state changes)
  - MEDIUM level restricts reviewer actions
  - HIGH level allows normal review
  - Rationale is always non-empty and auditable
  - Signal list references the deciding factors
"""

from __future__ import annotations

import pytest

from careful_memory.core import meta_gate
from careful_memory.models.enums import AuthorityLevel, EvidenceType, MemoryType
from careful_memory.models.memory import ContextScope, MemorySource
from careful_memory.models.meta import MetaConfidenceLevel
from careful_memory.review.reviewer import MemoryReviewer, ReviewDecision
from tests.conftest import make_record


@pytest.fixture()
def scope() -> ContextScope:
    from careful_memory.models.enums import Domain
    return ContextScope(user_id="u1", domain=Domain.personal)


# ---------------------------------------------------------------------------
# Level determination
# ---------------------------------------------------------------------------


class TestMetaGateLevels:
    def test_no_evidence_type_is_low(self, scope: ContextScope) -> None:
        """
        A proposal with no evidence_type has undocumented reasoning grounds.
        The meta-gate must block it (LOW).
        """
        src = MemorySource(origin="agent", authority_level=AuthorityLevel.user, evidence_type=None)
        record = make_record(scope, src)
        result = meta_gate.assess(record)
        assert result.level == MetaConfidenceLevel.low
        assert not result.is_gate_pass

    def test_user_with_restatement_is_medium(self, scope: ContextScope) -> None:
        src = MemorySource(
            origin="agent",
            authority_level=AuthorityLevel.user,
            evidence_type=EvidenceType.user_restatement,
        )
        record = make_record(scope, src)
        result = meta_gate.assess(record)
        assert result.level == MetaConfidenceLevel.medium
        assert result.is_gate_pass
        assert result.restricts_review

    def test_user_with_action_is_medium(self, scope: ContextScope) -> None:
        src = MemorySource(
            origin="agent",
            authority_level=AuthorityLevel.user,
            evidence_type=EvidenceType.user_action,
        )
        record = make_record(scope, src)
        result = meta_gate.assess(record)
        assert result.level == MetaConfidenceLevel.medium

    def test_verified_system_outcome_is_high(self, scope: ContextScope) -> None:
        src = MemorySource(
            origin="sys",
            authority_level=AuthorityLevel.verified_system,
            evidence_type=EvidenceType.verified_system_outcome,
        )
        record = make_record(scope, src)
        result = meta_gate.assess(record)
        assert result.level == MetaConfidenceLevel.high
        assert result.is_gate_pass
        assert not result.restricts_review

    def test_system_with_verified_outcome_is_medium(self, scope: ContextScope) -> None:
        """
        A system-authority source with verified_system_outcome evidence gets MEDIUM,
        not HIGH: the weakest-link rule applies — source_authority=MEDIUM caps the result.
        Only verified_system authority + verified_system_outcome evidence reaches HIGH.
        """
        src = MemorySource(
            origin="sys",
            authority_level=AuthorityLevel.system,
            evidence_type=EvidenceType.verified_system_outcome,
        )
        record = make_record(scope, src)
        result = meta_gate.assess(record)
        assert result.level == MetaConfidenceLevel.medium

    def test_semantic_type_downgrades_to_medium(self, scope: ContextScope) -> None:
        """
        A semantic write with otherwise HIGH signals is capped at MEDIUM
        because the memory_type_fit signal returns MEDIUM for non-episodic.
        """
        src = MemorySource(
            origin="sys",
            authority_level=AuthorityLevel.verified_system,
            evidence_type=EvidenceType.verified_system_outcome,
        )
        record = make_record(scope, src, memory_type=MemoryType.semantic)
        result = meta_gate.assess(record)
        assert result.level == MetaConfidenceLevel.medium


# ---------------------------------------------------------------------------
# Invariant: MetaGate never touches alpha/beta
# ---------------------------------------------------------------------------


class TestMetaGateNoBeliefMutation:
    def test_alpha_unchanged_after_assess(self, scope: ContextScope) -> None:
        src = MemorySource(
            origin="agent",
            authority_level=AuthorityLevel.user,
            evidence_type=EvidenceType.user_restatement,
        )
        record = make_record(scope, src, alpha=3.0, beta=1.5)
        before_alpha = record.alpha
        before_beta = record.beta
        meta_gate.assess(record)
        # record is a Pydantic model; assert the original object is unchanged
        assert record.alpha == before_alpha
        assert record.beta == before_beta

    def test_confidence_unchanged_after_assess(self, scope: ContextScope) -> None:
        src = MemorySource(
            origin="agent",
            authority_level=AuthorityLevel.user,
            evidence_type=EvidenceType.user_action,
        )
        record = make_record(scope, src, alpha=7.0, beta=2.0)
        before_conf = record.confidence
        meta_gate.assess(record)
        assert record.confidence == before_conf

    def test_assess_is_deterministic(self, scope: ContextScope) -> None:
        """Same input must always produce the same output (pure function)."""
        src = MemorySource(
            origin="agent",
            authority_level=AuthorityLevel.user,
            evidence_type=EvidenceType.user_restatement,
        )
        record = make_record(scope, src)
        r1 = meta_gate.assess(record)
        r2 = meta_gate.assess(record)
        assert r1.level == r2.level
        assert r1.rationale == r2.rationale


# ---------------------------------------------------------------------------
# Auditability
# ---------------------------------------------------------------------------


class TestMetaGateAuditability:
    def test_rationale_always_non_empty(self, scope: ContextScope) -> None:
        for evidence_type in [None, EvidenceType.user_restatement, EvidenceType.verified_system_outcome]:
            src = MemorySource(
                origin="agent",
                authority_level=AuthorityLevel.user,
                evidence_type=evidence_type,
            )
            record = make_record(scope, src)
            result = meta_gate.assess(record)
            assert result.rationale.strip(), f"empty rationale for evidence_type={evidence_type}"

    def test_signals_list_non_empty(self, scope: ContextScope) -> None:
        src = MemorySource(origin="agent", authority_level=AuthorityLevel.user)
        record = make_record(scope, src)
        result = meta_gate.assess(record)
        assert len(result.signals) > 0

    def test_low_rationale_references_blocking_signal(self, scope: ContextScope) -> None:
        src = MemorySource(origin="agent", authority_level=AuthorityLevel.user, evidence_type=None)
        record = make_record(scope, src)
        result = meta_gate.assess(record)
        assert result.level == MetaConfidenceLevel.low
        assert "evidence_type" in result.rationale

    def test_meta_assessment_not_persistent(self, scope: ContextScope) -> None:
        """
        MetaAssessment must not be a MemoryRecord — it carries no alpha/beta
        and must not be stored as a belief.
        """
        from careful_memory.models.memory import MemoryRecord
        from careful_memory.models.meta import MetaAssessment
        assert not issubclass(MetaAssessment, MemoryRecord)


# ---------------------------------------------------------------------------
# Reviewer respects MetaAssessment level
# ---------------------------------------------------------------------------


class TestReviewerRespectsMetaLevel:
    def test_medium_blocks_semantic_write(self, scope: ContextScope) -> None:
        """
        At MEDIUM meta-level, the reviewer must defer (not approve) semantic writes.
        Only the reviewer makes this call — the meta-gate does not.
        """
        src = MemorySource(
            origin="agent",
            authority_level=AuthorityLevel.user,
            evidence_type=EvidenceType.user_restatement,
        )
        record = make_record(scope, src, memory_type=MemoryType.semantic, alpha=3.0, beta=1.0)
        meta = meta_gate.assess(record)
        assert meta.level == MetaConfidenceLevel.medium

        reviewer = MemoryReviewer()
        result = reviewer.review(record, scope, [], [], meta_assessment=meta)
        # MEDIUM restricts semantic writes → reviewer must defer (not approve/modify)
        assert result.decision == ReviewDecision.defer
        assert "MEDIUM" in result.justification
        # The meta_assessment is embedded in the result for auditability
        assert result.meta_assessment is not None
        assert result.meta_assessment.level == MetaConfidenceLevel.medium

    def test_medium_blocks_contradiction_write(self, scope: ContextScope) -> None:
        """At MEDIUM, contradiction writes are deferred by the reviewer."""
        src = MemorySource(
            origin="agent",
            authority_level=AuthorityLevel.user,
            evidence_type=EvidenceType.user_restatement,
        )
        existing = make_record(scope, src)
        proposed = make_record(scope, src, object_value="light mode")
        proposed = proposed.model_copy(update={"contradicts": [existing.id]})

        meta = meta_gate.assess(proposed)
        assert meta.level == MetaConfidenceLevel.medium

        reviewer = MemoryReviewer()
        result = reviewer.review(proposed, scope, [existing], [], meta_assessment=meta)
        assert result.decision == ReviewDecision.defer
        assert "MEDIUM" in result.justification

    def test_high_allows_normal_review(self, scope: ContextScope) -> None:
        """At HIGH meta-level, the reviewer applies normal rules (may approve episodic)."""
        src = MemorySource(
            origin="sys",
            authority_level=AuthorityLevel.verified_system,
            evidence_type=EvidenceType.verified_system_outcome,
        )
        record = make_record(scope, src)  # episodic, default priors
        meta = meta_gate.assess(record)
        assert meta.level == MetaConfidenceLevel.high

        reviewer = MemoryReviewer()
        result = reviewer.review(record, scope, [], [], meta_assessment=meta)
        assert result.decision == ReviewDecision.approve
        assert result.meta_assessment is not None

    def test_reviewer_justification_includes_meta_prefix(self, scope: ContextScope) -> None:
        """Every ReviewResult justification must be prefixed with the meta level."""
        src = MemorySource(
            origin="agent",
            authority_level=AuthorityLevel.user,
            evidence_type=EvidenceType.user_action,
        )
        record = make_record(scope, src)
        meta = meta_gate.assess(record)

        reviewer = MemoryReviewer()
        result = reviewer.review(record, scope, [], [], meta_assessment=meta)
        # Justification must start with the meta-level prefix for auditability
        assert result.justification.startswith(f"[meta:{meta.level.value}]")
