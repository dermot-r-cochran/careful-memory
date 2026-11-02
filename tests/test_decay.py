"""
Tests for time-based decay behaviour.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from careful_memory.core.decay import (
    DECAY_RATES_BY_TYPE,
    PROJECT_DOMAIN_MULTIPLIER,
    apply_decay_to_record,
    compute_decay,
    decay_rate_for,
)
from careful_memory.models.enums import AuthorityLevel, Domain, MemoryType, RecordStatus
from careful_memory.models.memory import ContextScope, EntityRef, MemoryRecord, MemorySource
from careful_memory.service import MemoryService
from careful_memory.storage.sqlite import SQLiteMemoryStore
from careful_memory.tools.schema import ToolCall, ToolName
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


# Half-lives the README, ADR-0005 and decay.py state for the default rates,
# in days. They are ln 2 / rate rounded; the exact discrete half-life
# ln 0.5 / ln(1 - rate) is within a day of each.
DOCUMENTED_HALF_LIFE_DAYS: dict[MemoryType, int] = {
    MemoryType.episodic: 14,
    MemoryType.semantic: 139,
    MemoryType.procedural: 69,
}


class TestTypeDefaultDecayRate:
    """
    A record decays at its memory type's rate unless told otherwise (ADR-0005).

    Until 2026-11-02 every record started at 0.01/day whatever its type: the
    model's field default was a single number and compute_decay always passed
    it as the override, so DECAY_RATES_BY_TYPE was never read on the live
    path and the README's half-life table described a table, not the code.
    """

    def test_half_life_table_derived_from_rates(self) -> None:
        """The ~14 / ~139 / ~69 day figures follow from the rates, within a day."""
        assert set(DOCUMENTED_HALF_LIFE_DAYS) == set(MemoryType)
        for memory_type, documented in DOCUMENTED_HALF_LIFE_DAYS.items():
            rate = DECAY_RATES_BY_TYPE[memory_type]
            exact = math.log(0.5) / math.log(1.0 - rate)
            assert abs(exact - documented) < 1.0, (memory_type, exact, documented)

    def test_default_rate_follows_memory_type(
        self, personal_scope: ContextScope, source: MemorySource
    ) -> None:
        """A record built without a decay_rate carries its type's table entry."""
        for memory_type in MemoryType:
            r = make_record(personal_scope, source, memory_type=memory_type)
            assert r.decay_rate == pytest.approx(DECAY_RATES_BY_TYPE[memory_type]), memory_type

    def test_episodic_record_forgets_before_semantic(
        self, personal_scope: ContextScope, source: MemorySource
    ) -> None:
        """Same evidence, same 30 days: episodic loses most, semantic least."""
        remaining: dict[MemoryType, float] = {}
        for memory_type in MemoryType:
            r = make_record(personal_scope, source, memory_type=memory_type, alpha=10.0, beta=1.0)
            future = r.last_decayed_at + timedelta(days=30)
            remaining[memory_type] = apply_decay_to_record(
                r, domain=Domain.personal, as_of=future
            ).alpha
        assert (
            remaining[MemoryType.episodic]
            < remaining[MemoryType.procedural]
            < remaining[MemoryType.semantic]
        )

    def test_stored_record_carries_its_type_rate_end_to_end(
        self, store: SQLiteMemoryStore, source: MemorySource
    ) -> None:
        """
        What the platform stores has the type's rate, by both write paths.

        An agent's proposal (episodic, the only type a user-authority source
        may land: the MetaGate defers the rest) goes through the tool; a
        semantic record goes through the platform-internal write_record.
        """
        scope = ContextScope(user_id="u1", domain=Domain.personal)
        svc = MemoryService(store=store)
        svc.create_context(scope)

        result = svc.handle_tool_call(
            ToolCall(
                tool_name=ToolName.propose_belief,
                arguments={
                    "subject_label": "Alice",
                    "subject_type": "person",
                    "predicate": "mentioned",
                    "object_value": "rain",
                    "evidence_type": "user_restatement",
                },
                context_id=scope.context_id,
            )
        )
        assert result.success, result.message
        episodic = svc.get_record(result.data["record_id"], scope.context_id)
        assert episodic is not None
        assert episodic.memory_type == MemoryType.episodic
        assert episodic.decay_rate == pytest.approx(DECAY_RATES_BY_TYPE[MemoryType.episodic])

        # The reviewer admits a semantic record only with evidence mass
        # (alpha + beta >= 4) and no high direct confidence (<= 0.65).
        _, _, saved = svc.write_record(
            scope,
            make_record(
                scope, source, memory_type=MemoryType.semantic, object_value="tea", alpha=3.0, beta=2.0
            ),
        )
        assert saved is not None
        semantic = svc.get_record(saved.id, scope.context_id)
        assert semantic is not None
        assert semantic.decay_rate == pytest.approx(DECAY_RATES_BY_TYPE[MemoryType.semantic])

    def test_unknown_memory_type_still_refused(
        self, personal_scope: ContextScope, source: MemorySource
    ) -> None:
        """Filling the rate does not swallow a bad memory_type: the field refuses it."""
        with pytest.raises(ValidationError, match="memory_type"):
            MemoryRecord(
                context_id=personal_scope.context_id,
                memory_type="mythic",  # type: ignore[arg-type]
                subject=EntityRef(entity_type="person", label="Alice"),
                predicate="prefers",
                object_value="dark mode",
                source=source,
            )

    def test_explicit_decay_rate_is_kept_end_to_end(
        self, personal_scope: ContextScope, source: MemorySource, store: SQLiteMemoryStore
    ) -> None:
        """A per-record decay_rate survives storage and is the rate decay uses."""
        default = make_record(personal_scope, source, memory_type=MemoryType.semantic, alpha=10.0)
        tuned = default.model_copy(update={"decay_rate": 0.5})
        store.save_context(personal_scope)
        store.save_record(tuned)
        reloaded = store.get_record(tuned.id, personal_scope.context_id)
        assert reloaded is not None
        assert reloaded.decay_rate == pytest.approx(0.5)

        future = default.last_decayed_at + timedelta(days=1)
        decayed_default = apply_decay_to_record(default, domain=Domain.personal, as_of=future)
        decayed_tuned = apply_decay_to_record(reloaded, domain=Domain.personal, as_of=future)
        assert decayed_tuned.alpha < decayed_default.alpha
        assert decayed_tuned.alpha == pytest.approx(1.0 + 9.0 * 0.5)

    def test_stored_record_keeps_saved_rate_on_reload(
        self, personal_scope: ContextScope, source: MemorySource
    ) -> None:
        """A record saved with a rate (as every record before 2026-11-02 was) keeps it."""
        r = make_record(personal_scope, source, memory_type=MemoryType.episodic)
        saved = r.model_copy(update={"decay_rate": 0.01}).model_dump_json()
        reloaded = MemoryRecord.model_validate_json(saved)
        assert reloaded.memory_type == MemoryType.episodic
        assert reloaded.decay_rate == pytest.approx(0.01)
