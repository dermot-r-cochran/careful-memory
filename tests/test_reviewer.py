"""
Tests for the MemoryReviewer (review agent).

Covers:
  - Approve path (clean record)
  - Reject paths (authority, mass contradiction, policy)
  - Defer paths (near-duplicate, insufficient evidence mass)
  - Modify path (direct semantic assertion → downgrade to episodic)
  - Reviewer never invents data (suggested_record only changes memory_type)
  - ReviewResult.is_committable semantics
"""

from __future__ import annotations

import pytest

from careful_memory.models.enums import AuthorityLevel, Domain, MemoryType
from careful_memory.models.memory import ContextScope, MemorySource
from careful_memory.review.reviewer import (
    ContextPolicy,
    MemoryReviewer,
    ReviewDecision,
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
    return MemorySource(origin="sys", authority_level=AuthorityLevel.system)


@pytest.fixture()
def reviewer() -> MemoryReviewer:
    return MemoryReviewer()


class TestApprove:
    def test_clean_episodic_approved(
        self, reviewer: MemoryReviewer, scope: ContextScope, user_src: MemorySource
    ) -> None:
        record = make_record(scope, user_src)
        result = reviewer.review(record, scope, [], [])
        assert result.decision == ReviewDecision.approve
        assert result.is_committable

    def test_justification_always_present(
        self, reviewer: MemoryReviewer, scope: ContextScope, user_src: MemorySource
    ) -> None:
        record = make_record(scope, user_src)
        result = reviewer.review(record, scope, [], [])
        assert result.justification  # non-empty

    def test_checks_run_populated(
        self, reviewer: MemoryReviewer, scope: ContextScope, user_src: MemorySource
    ) -> None:
        record = make_record(scope, user_src)
        result = reviewer.review(record, scope, [], [])
        assert len(result.checks_run) > 0

    def test_no_suggested_record_on_approve(
        self, reviewer: MemoryReviewer, scope: ContextScope, user_src: MemorySource
    ) -> None:
        record = make_record(scope, user_src)
        result = reviewer.review(record, scope, [], [])
        assert result.suggested_record is None


class TestReject:
    def test_wrong_context_rejected(
        self, reviewer: MemoryReviewer, user_src: MemorySource
    ) -> None:
        scope_a = ContextScope(user_id="u1", domain=Domain.personal)
        scope_b = ContextScope(user_id="u2", domain=Domain.personal)
        record = make_record(scope_a, user_src)
        result = reviewer.review(record, scope_b, [], [])
        assert result.decision == ReviewDecision.reject

    def test_authority_ceiling_rejected(
        self, reviewer: MemoryReviewer, scope: ContextScope, user_src: MemorySource
    ) -> None:
        # User source, confidence > 0.80 (α=9, β=1 → 0.9)
        record = make_record(scope, user_src, alpha=9.0, beta=1.0)
        result = reviewer.review(record, scope, [], [])
        assert result.decision == ReviewDecision.reject
        assert "ceiling" in result.justification.lower() or "authority" in result.justification.lower()

    def test_mass_contradiction_rejected(
        self, reviewer: MemoryReviewer, scope: ContextScope, user_src: MemorySource
    ) -> None:
        existing = [make_record(scope, user_src, predicate=f"fact_{i}") for i in range(5)]
        proposed = make_record(scope, user_src, predicate="poison")
        proposed = proposed.model_copy(update={"contradicts": [r.id for r in existing]})
        result = reviewer.review(proposed, scope, existing, [])
        assert result.decision == ReviewDecision.reject

    def test_reject_not_committable(
        self, reviewer: MemoryReviewer, scope: ContextScope, user_src: MemorySource
    ) -> None:
        scope_b = ContextScope(user_id="u2", domain=Domain.personal)
        record = make_record(scope_b, user_src)
        result = reviewer.review(record, scope, [], [])
        assert not result.is_committable


class TestDefer:
    def test_near_duplicate_deferred(
        self, reviewer: MemoryReviewer, scope: ContextScope, user_src: MemorySource
    ) -> None:
        existing = make_record(scope, user_src, predicate="prefers", object_value="vim")
        duplicate = make_record(scope, user_src, predicate="prefers", object_value="vim")
        result = reviewer.review(duplicate, scope, [existing], [])
        assert result.decision == ReviewDecision.defer

    def test_defer_not_committable(
        self, reviewer: MemoryReviewer, scope: ContextScope, user_src: MemorySource
    ) -> None:
        existing = make_record(scope, user_src, predicate="prefers", object_value="vim")
        duplicate = make_record(scope, user_src, predicate="prefers", object_value="vim")
        result = reviewer.review(duplicate, scope, [existing], [])
        assert not result.is_committable


class TestModify:
    def test_direct_semantic_downgraded(
        self, reviewer: MemoryReviewer, scope: ContextScope, user_src: MemorySource
    ) -> None:
        # α=3, β=1 → confidence = 0.75: above semantic threshold (0.65),
        # below user authority ceiling (0.80), and α+β = 4 satisfies evidence mass.
        proposed = make_record(
            scope, user_src,
            memory_type=MemoryType.semantic,
            alpha=3.0, beta=1.0,
        )
        result = reviewer.review(proposed, scope, [], [])
        assert result.decision == ReviewDecision.modify
        assert result.suggested_record is not None
        assert result.suggested_record.memory_type == MemoryType.episodic

    def test_modify_is_committable(
        self, reviewer: MemoryReviewer, scope: ContextScope, user_src: MemorySource
    ) -> None:
        proposed = make_record(scope, user_src, memory_type=MemoryType.semantic, alpha=3.0, beta=1.0)
        result = reviewer.review(proposed, scope, [], [])
        assert result.is_committable

    def test_suggested_record_only_changes_type(
        self, reviewer: MemoryReviewer, scope: ContextScope, user_src: MemorySource
    ) -> None:
        proposed = make_record(
            scope, user_src,
            memory_type=MemoryType.semantic,
            predicate="uses",
            object_value="nvim",
            alpha=3.0, beta=1.0,
        )
        result = reviewer.review(proposed, scope, [], [])
        assert result.suggested_record is not None
        # Only memory_type changed; all other fields preserved
        s = result.suggested_record
        assert s.predicate == proposed.predicate
        assert s.object_value == proposed.object_value
        assert s.context_id == proposed.context_id
        assert s.subject.label == proposed.subject.label


class TestPolicyEnforcement:
    def test_disallowed_memory_type_rejected(
        self, reviewer: MemoryReviewer, scope: ContextScope, user_src: MemorySource
    ) -> None:
        policy = ContextPolicy(
            allowed_memory_types=frozenset({MemoryType.episodic})
        )
        proposed = make_record(scope, user_src, memory_type=MemoryType.procedural)
        result = reviewer.review(proposed, scope, [], [], policy=policy)
        assert result.decision == ReviewDecision.reject
        assert "not permitted" in result.justification.lower()

    def test_evidence_type_required_enforced(
        self, reviewer: MemoryReviewer, scope: ContextScope, user_src: MemorySource
    ) -> None:
        policy = ContextPolicy(require_evidence_type=True)
        proposed = make_record(scope, user_src)
        # user_src has evidence_type=None
        result = reviewer.review(proposed, scope, [], [], policy=policy)
        assert result.decision == ReviewDecision.reject
