"""
Tests for time-based decay behaviour.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from careful_memory.core.decay import (
    DECAY_RATES_BY_TYPE,
    PROJECT_DOMAIN_MULTIPLIER,
    apply_decay_to_record,
    compute_decay,
    decay_rate_for,
)
from careful_memory.models.enums import AuthorityLevel, Domain, MemoryType, RecordStatus
from careful_memory.models.memory import ContextScope, MemorySource
from tests.conftest import make_record


@pytest.fixture()
def personal_scope() -> ContextScope:
    return ContextScope(user_id="u1", domain=Domain.personal)


@pytest.fixture()
def project_scope() -> ContextScope:
    return ContextScope(user_id="u1", domain=Domain.project)


@pytest.fixture()
def source() -> MemorySource:
    return MemorySource(origin="test", authority_level=AuthorityLevel.system)


class TestDecayRates:
    def test_episodic_faster_than_semantic(self) -> None:
        assert DECAY_RATES_BY_TYPE[MemoryType.episodic] > DECAY_RATES_BY_TYPE[MemoryType.semantic]

    def test_project_multiplier_applied(self) -> None:
        base = DECAY_RATES_BY_TYPE[MemoryType.episodic]
        project_rate = decay_rate_for(MemoryType.episodic, Domain.project)
        assert project_rate == pytest.approx(min(base * PROJECT_DOMAIN_MULTIPLIER, 1.0))

    def test_non_project_domain_no_multiplier(self) -> None:
        base = DECAY_RATES_BY_TYPE[MemoryType.semantic]
        rate = decay_rate_for(MemoryType.semantic, Domain.personal)
        assert rate == pytest.approx(base)

    def test_override_respected(self) -> None:
        rate = decay_rate_for(MemoryType.episodic, Domain.personal, override=0.123)
        assert rate == pytest.approx(0.123)


class TestComputeDecay:
    def test_no_decay_if_no_time_elapsed(
        self, personal_scope: ContextScope, source: MemorySource
    ) -> None:
        r = make_record(personal_scope, source, alpha=5.0, beta=2.0)
        now = r.last_decayed_at
        result = compute_decay(r, domain=Domain.personal, as_of=now)
        assert result.new_alpha == pytest.approx(5.0)
        assert result.new_beta == pytest.approx(2.0)

    def test_evidence_mass_reduces(
        self, personal_scope: ContextScope, source: MemorySource
    ) -> None:
        r = make_record(personal_scope, source, alpha=10.0, beta=1.0)
        future = (r.last_decayed_at or r.created_at) + timedelta(days=30)
        result = compute_decay(r, domain=Domain.personal, as_of=future)
        assert result.new_alpha < 10.0
        assert result.new_alpha >= 1.0

    def test_archive_flag_when_below_threshold(
        self, personal_scope: ContextScope, source: MemorySource
    ) -> None:
        # α very close to β → confidence near 0.5; force it below threshold with more β
        r = make_record(personal_scope, source, alpha=1.0, beta=5.0)
        # confidence ≈ 0.167 already below ARCHIVE_THRESHOLD
        result = compute_decay(r, domain=Domain.personal)
        assert result.should_archive

    def test_no_archive_above_threshold(
        self, personal_scope: ContextScope, source: MemorySource
    ) -> None:
        r = make_record(personal_scope, source, alpha=10.0, beta=1.0)
        result = compute_decay(r, domain=Domain.personal)
        # confidence ≈ 0.909 → not archived
        assert not result.should_archive


class TestApplyDecayToRecord:
    def test_returns_new_record(
        self, personal_scope: ContextScope, source: MemorySource
    ) -> None:
        r = make_record(personal_scope, source, alpha=5.0, beta=2.0)
        future = r.last_decayed_at + timedelta(days=7)
        updated = apply_decay_to_record(r, domain=Domain.personal, as_of=future)
        assert updated is not r  # new object, not in-place

    def test_last_decayed_at_updated(
        self, personal_scope: ContextScope, source: MemorySource
    ) -> None:
        r = make_record(personal_scope, source)
        future = r.last_decayed_at + timedelta(days=1)
        updated = apply_decay_to_record(r, domain=Domain.personal, as_of=future)
        assert updated.last_decayed_at == future

    def test_status_becomes_archived_when_low_confidence(
        self, personal_scope: ContextScope, source: MemorySource
    ) -> None:
        # α=1, β=10 → confidence ≈ 0.09
        r = make_record(personal_scope, source, alpha=1.0, beta=10.0)
        updated = apply_decay_to_record(r, domain=Domain.personal)
        assert updated.status == RecordStatus.archived

    def test_non_active_status_not_re_archived(
        self, personal_scope: ContextScope, source: MemorySource
    ) -> None:
        r = make_record(personal_scope, source, alpha=1.0, beta=10.0)
        r = r.model_copy(update={"status": RecordStatus.contradicted})
        updated = apply_decay_to_record(r, domain=Domain.personal)
        # Should remain contradicted, not flipped to archived
        assert updated.status == RecordStatus.contradicted

    def test_project_domain_decays_faster(
        self,
        personal_scope: ContextScope,
        project_scope: ContextScope,
        source: MemorySource,
    ) -> None:
        future = datetime.now(tz=UTC) + timedelta(days=30)
        r_personal = make_record(personal_scope, source, alpha=10.0, beta=1.0)
        r_project = make_record(project_scope, source, alpha=10.0, beta=1.0)
        r_project = r_project.model_copy(
            update={
                "last_decayed_at": r_personal.last_decayed_at,
                "created_at": r_personal.created_at,
            }
        )

        updated_personal = apply_decay_to_record(r_personal, domain=Domain.personal, as_of=future)
        updated_project = apply_decay_to_record(r_project, domain=Domain.project, as_of=future)

        assert updated_project.alpha < updated_personal.alpha
