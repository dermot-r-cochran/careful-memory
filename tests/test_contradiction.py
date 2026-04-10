"""
Tests for contradiction handling and belief supersession.
"""

from __future__ import annotations

import pytest

from careful_memory.core.contradiction import apply_contradiction, apply_supersession
from careful_memory.models.enums import AuthorityLevel, Domain, RecordStatus
from careful_memory.models.memory import ContextScope, MemorySource
from tests.conftest import make_record


@pytest.fixture()
def scope() -> ContextScope:
    return ContextScope(user_id="u1", domain=Domain.personal)


@pytest.fixture()
def source() -> MemorySource:
    return MemorySource(origin="test", authority_level=AuthorityLevel.user)


class TestApplyContradiction:
    def test_existing_status_becomes_contradicted(self, scope: ContextScope, source: MemorySource) -> None:
        existing = make_record(scope, source, object_value="dark mode")
        challenger = make_record(scope, source, object_value="light mode")
        result = apply_contradiction(existing, challenger)
        assert result.updated_existing.status == RecordStatus.contradicted

    def test_existing_beta_incremented(self, scope: ContextScope, source: MemorySource) -> None:
        existing = make_record(scope, source, beta=1.0)
        challenger = make_record(scope, source, object_value="light mode")
        result = apply_contradiction(existing, challenger)
        assert result.updated_existing.beta > existing.beta

    def test_challenger_contradicts_list_populated(self, scope: ContextScope, source: MemorySource) -> None:
        existing = make_record(scope, source)
        challenger = make_record(scope, source, object_value="light mode")
        result = apply_contradiction(existing, challenger)
        assert existing.id in result.challenger.contradicts

    def test_original_not_mutated(self, scope: ContextScope, source: MemorySource) -> None:
        existing = make_record(scope, source)
        challenger = make_record(scope, source, object_value="light mode")
        original_status = existing.status
        _ = apply_contradiction(existing, challenger)
        assert existing.status == original_status  # unchanged

    def test_cross_context_raises(self) -> None:
        s1 = ContextScope(user_id="u1", domain=Domain.personal)
        s2 = ContextScope(user_id="u2", domain=Domain.personal)
        src = MemorySource(origin="t", authority_level=AuthorityLevel.user)
        existing = make_record(s1, src)
        challenger = make_record(s2, src)
        with pytest.raises(ValueError, match="Cannot contradict across contexts"):
            apply_contradiction(existing, challenger)

    def test_history_is_preserved(self, scope: ContextScope, source: MemorySource) -> None:
        """Original record still accessible after contradiction (append-only)."""
        existing = make_record(scope, source, alpha=5.0, beta=1.0)
        challenger = make_record(scope, source, object_value="light mode")
        result = apply_contradiction(existing, challenger)
        # updated_existing retains original alpha
        assert result.updated_existing.alpha == pytest.approx(existing.alpha)

    def test_idempotent_contradicts_list(self, scope: ContextScope, source: MemorySource) -> None:
        """Calling twice should not duplicate the id in contradicts."""
        existing = make_record(scope, source)
        challenger = make_record(scope, source, object_value="light mode")
        result1 = apply_contradiction(existing, challenger)
        result2 = apply_contradiction(existing, result1.challenger)
        assert result2.challenger.contradicts.count(existing.id) == 1


class TestApplySupersession:
    def test_existing_status_becomes_superseded(self, scope: ContextScope, source: MemorySource) -> None:
        old = make_record(scope, source, object_value="London")
        new = make_record(scope, source, object_value="Paris")
        updated_old, updated_new = apply_supersession(old, new)
        assert updated_old.status == RecordStatus.superseded

    def test_successor_supersedes_list_populated(self, scope: ContextScope, source: MemorySource) -> None:
        old = make_record(scope, source, object_value="London")
        new = make_record(scope, source, object_value="Paris")
        updated_old, updated_new = apply_supersession(old, new)
        assert old.id in updated_new.supersedes

    def test_cross_context_raises(self) -> None:
        s1 = ContextScope(user_id="u1", domain=Domain.personal)
        s2 = ContextScope(user_id="u2", domain=Domain.personal)
        src = MemorySource(origin="t", authority_level=AuthorityLevel.user)
        old = make_record(s1, src)
        new = make_record(s2, src)
        with pytest.raises(ValueError, match="Cannot supersede across contexts"):
            apply_supersession(old, new)

    def test_old_beta_not_incremented(self, scope: ContextScope, source: MemorySource) -> None:
        """Supersession is non-contradictory; β should not increase."""
        old = make_record(scope, source, beta=1.0)
        new = make_record(scope, source, object_value="Paris")
        updated_old, _ = apply_supersession(old, new)
        assert updated_old.beta == pytest.approx(old.beta)
