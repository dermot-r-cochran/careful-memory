"""
Tests for memory poisoning defences and multi-context isolation.

Covers:
  - Cross-context writes are blocked at every layer
  - Lower-authority writes cannot overwrite higher-authority beliefs
  - Mass-contradiction (>25% active records) is rejected by reviewer
  - Direct high-confidence semantic assertions are modified to episodic
  - Near-duplicate writes are deferred
  - Rate-limit prevents flooding
  - Self-reinforcement: LLM-only evidence path does not exist
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from careful_memory.core.gate import RATE_LIMIT_MAX_EVENTS, WriteGate
from careful_memory.models.enums import (
    AuthorityLevel,
    Domain,
    EvidenceType,
    MemoryType,
)
from careful_memory.models.memory import (
    ContextScope,
    EvidenceEvent,
    MemorySource,
)
from careful_memory.review.reviewer import (
    ContextPolicy,
    MemoryReviewer,
    ReviewDecision,
)
from careful_memory.service import MemoryService
from careful_memory.storage.sqlite import SQLiteMemoryStore
from careful_memory.tools.schema import ToolCall, ToolName
from tests.conftest import make_record


@pytest.fixture()
def store() -> SQLiteMemoryStore:
    return SQLiteMemoryStore(":memory:")


@pytest.fixture()
def scope_a() -> ContextScope:
    return ContextScope(user_id="tenant-A", domain=Domain.personal)


@pytest.fixture()
def scope_b() -> ContextScope:
    return ContextScope(user_id="tenant-B", domain=Domain.personal)


@pytest.fixture()
def user_src() -> MemorySource:
    return MemorySource(origin="agent", authority_level=AuthorityLevel.user)


@pytest.fixture()
def system_src() -> MemorySource:
    return MemorySource(origin="sys", authority_level=AuthorityLevel.system)


@pytest.fixture()
def verified_src() -> MemorySource:
    return MemorySource(origin="verified", authority_level=AuthorityLevel.verified_system)


# ---------------------------------------------------------------------------
# Cross-context isolation
# ---------------------------------------------------------------------------


class TestCrossContextIsolation:
    def test_write_gate_blocks_wrong_context(
        self, scope_a: ContextScope, scope_b: ContextScope, user_src: MemorySource
    ) -> None:
        gate = WriteGate()
        record_for_b = make_record(scope_b, user_src)
        # Attempt to write scope_b's record through scope_a's gate check
        result = gate.check_new_record(scope_a, record_for_b)
        assert not result.is_allowed

    def test_storage_context_isolation(
        self,
        store: SQLiteMemoryStore,
        scope_a: ContextScope,
        scope_b: ContextScope,
        user_src: MemorySource,
    ) -> None:
        store.save_context(scope_a)
        store.save_context(scope_b)
        r_a = make_record(scope_a, user_src)
        store.save_record(r_a)
        # Looking up scope_a's record using scope_b's context_id returns None
        result = store.get_record(r_a.id, scope_b.context_id)
        assert result is None

    def test_list_records_scoped(
        self,
        store: SQLiteMemoryStore,
        scope_a: ContextScope,
        scope_b: ContextScope,
        user_src: MemorySource,
    ) -> None:
        store.save_context(scope_a)
        store.save_context(scope_b)
        store.save_record(make_record(scope_a, user_src))
        store.save_record(make_record(scope_b, user_src))
        records_a = store.list_records(scope_a.context_id)
        assert all(r.context_id == scope_a.context_id for r in records_a)

    def test_service_context_isolation(
        self,
        store: SQLiteMemoryStore,
        scope_a: ContextScope,
        scope_b: ContextScope,
    ) -> None:
        svc = MemoryService(store=store)
        svc.create_context(scope_a)
        svc.create_context(scope_b)

        call_a = ToolCall(
            tool_name=ToolName.propose_belief,
            arguments={
                "subject_label": "Alice",
                "subject_type": "person",
                "predicate": "prefers",
                "object_value": "dark mode",
                "evidence_type": "user_restatement",
            },
            context_id=scope_a.context_id,
        )
        svc.handle_tool_call(call_a)

        # Querying scope_b should return no beliefs
        call_q = ToolCall(
            tool_name=ToolName.query_beliefs,
            arguments={},
            context_id=scope_b.context_id,
        )
        result = svc.handle_tool_call(call_q)
        assert result.success
        assert result.data["count"] == 0


# ---------------------------------------------------------------------------
# Authority enforcement
# ---------------------------------------------------------------------------


class TestAuthorityEnforcement:
    def test_lower_authority_cannot_update_higher(
        self,
        store: SQLiteMemoryStore,
        scope_a: ContextScope,
        user_src: MemorySource,
        system_src: MemorySource,
    ) -> None:
        gate = WriteGate()
        r = make_record(scope_a, system_src)  # system-level record
        event = EvidenceEvent(
            record_id=r.id,
            context_id=scope_a.context_id,
            supports=True,
            evidence_type=EvidenceType.user_restatement,
            source=user_src,  # user-level evidence
        )
        result = gate.check_evidence_update(scope_a, r, event)
        assert not result.is_allowed
        assert "authority" in result.reason.lower()

    def test_equal_authority_allowed(
        self,
        scope_a: ContextScope,
        system_src: MemorySource,
    ) -> None:
        gate = WriteGate()
        r = make_record(scope_a, system_src)
        event = EvidenceEvent(
            record_id=r.id,
            context_id=scope_a.context_id,
            supports=True,
            evidence_type=EvidenceType.user_action,
            source=system_src,
        )
        result = gate.check_evidence_update(scope_a, r, event)
        assert result.is_allowed

    def test_higher_authority_allowed(
        self,
        scope_a: ContextScope,
        user_src: MemorySource,
        verified_src: MemorySource,
    ) -> None:
        gate = WriteGate()
        r = make_record(scope_a, user_src)  # user-level record
        event = EvidenceEvent(
            record_id=r.id,
            context_id=scope_a.context_id,
            supports=True,
            evidence_type=EvidenceType.verified_system_outcome,
            source=verified_src,  # higher authority
        )
        result = gate.check_evidence_update(scope_a, r, event)
        assert result.is_allowed


# ---------------------------------------------------------------------------
# Mass-contradiction poisoning
# ---------------------------------------------------------------------------


class TestMassContradiction:
    def test_mass_contradiction_rejected_by_reviewer(
        self,
        scope_a: ContextScope,
        user_src: MemorySource,
    ) -> None:
        """A single record contradicting >25% of active records is rejected."""
        reviewer = MemoryReviewer()
        # Create 4 existing active records
        existing = [make_record(scope_a, user_src, predicate=f"fact_{i}") for i in range(4)]
        # Proposing a record that contradicts all 4 (100% > 25%)
        proposed = make_record(scope_a, user_src, predicate="contradiction")
        proposed = proposed.model_copy(update={"contradicts": [r.id for r in existing]})

        result = reviewer.review(
            proposed=proposed,
            scope=scope_a,
            existing_records=existing,
            evidence_history=[],
        )
        assert result.decision == ReviewDecision.reject
        assert "contradict" in result.justification.lower()


# ---------------------------------------------------------------------------
# Direct semantic assertion
# ---------------------------------------------------------------------------


class TestDirectSemanticAssertion:
    def test_high_confidence_semantic_downgraded(
        self,
        scope_a: ContextScope,
        user_src: MemorySource,
    ) -> None:
        """
        A user-authority agent proposing a direct semantic write with
        confidence > 0.65 but <= authority ceiling (0.80) is modified
        to episodic.  Above the ceiling it is rejected outright.
        Both outcomes prevent the agent from asserting a semantic belief.
        """
        reviewer = MemoryReviewer()
        # α=3, β=1 → confidence = 0.75: above semantic threshold (0.65),
        # below user ceiling (0.80), and α+β = 4 satisfies evidence mass.
        # This exercises the pure modify path (semantic → episodic downgrade).
        proposed = make_record(
            scope_a, user_src,
            memory_type=MemoryType.semantic,
            alpha=3.0, beta=1.0,
        )
        result = reviewer.review(
            proposed=proposed,
            scope=scope_a,
            existing_records=[],
            evidence_history=[],
        )
        assert result.decision == ReviewDecision.modify
        assert result.suggested_record is not None
        assert result.suggested_record.memory_type == MemoryType.episodic

    def test_very_high_confidence_semantic_rejected(
        self,
        scope_a: ContextScope,
        user_src: MemorySource,
    ) -> None:
        """
        A user-authority direct semantic write with confidence > 0.80
        is hard-rejected by the authority ceiling check.
        """
        reviewer = MemoryReviewer()
        # α=8, β=1 → confidence ≈ 0.889: exceeds USER_AUTHORITY_CONFIDENCE_CEILING
        proposed = make_record(
            scope_a, user_src,
            memory_type=MemoryType.semantic,
            alpha=8.0, beta=1.0,
        )
        result = reviewer.review(
            proposed=proposed,
            scope=scope_a,
            existing_records=[],
            evidence_history=[],
        )
        assert not result.is_committable  # rejected or deferred — never committed

    def test_semantic_with_episodic_parent_approved(
        self,
        scope_a: ContextScope,
        user_src: MemorySource,
    ) -> None:
        reviewer = MemoryReviewer()
        episodic_parent = make_record(scope_a, user_src, memory_type=MemoryType.episodic)
        proposed = make_record(
            scope_a, user_src,
            memory_type=MemoryType.semantic,
            alpha=3.0, beta=1.0,
        )
        # Link via supersedes (indicates legitimate promotion)
        proposed = proposed.model_copy(update={"supersedes": [episodic_parent.id]})
        result = reviewer.review(
            proposed=proposed,
            scope=scope_a,
            existing_records=[episodic_parent],
            evidence_history=[],
        )
        assert result.decision in (ReviewDecision.approve, ReviewDecision.modify)


# ---------------------------------------------------------------------------
# Near-duplicate detection
# ---------------------------------------------------------------------------


class TestNearDuplicate:
    def test_duplicate_deferred(
        self,
        scope_a: ContextScope,
        user_src: MemorySource,
    ) -> None:
        reviewer = MemoryReviewer()
        existing = make_record(scope_a, user_src, predicate="prefers", object_value="dark mode")
        duplicate = make_record(scope_a, user_src, predicate="prefers", object_value="dark mode")
        result = reviewer.review(
            proposed=duplicate,
            scope=scope_a,
            existing_records=[existing],
            evidence_history=[],
        )
        assert result.decision == ReviewDecision.defer
        assert "duplicate" in result.justification.lower()


# ---------------------------------------------------------------------------
# Rate-limit (anti-flooding)
# ---------------------------------------------------------------------------


class TestRateLimit:
    def test_flood_rejected(self, scope_a: ContextScope, user_src: MemorySource) -> None:
        gate = WriteGate()
        r = make_record(scope_a, user_src)
        now = datetime.now(tz=UTC)

        for _ in range(RATE_LIMIT_MAX_EVENTS):
            event = EvidenceEvent(
                record_id=r.id,
                context_id=scope_a.context_id,
                supports=True,
                evidence_type=EvidenceType.user_action,
                source=user_src,
            )
            gate.check_evidence_update(scope_a, r, event, now=now)

        flood_event = EvidenceEvent(
            record_id=r.id,
            context_id=scope_a.context_id,
            supports=True,
            evidence_type=EvidenceType.user_action,
            source=user_src,
        )
        result = gate.check_evidence_update(scope_a, r, flood_event, now=now)
        assert not result.is_allowed

    def test_rate_limit_resets_after_window(
        self, scope_a: ContextScope, user_src: MemorySource
    ) -> None:
        gate = WriteGate()
        r = make_record(scope_a, user_src)
        old_now = datetime.now(tz=UTC) - timedelta(hours=2)

        for _ in range(RATE_LIMIT_MAX_EVENTS):
            event = EvidenceEvent(
                record_id=r.id,
                context_id=scope_a.context_id,
                supports=True,
                evidence_type=EvidenceType.user_action,
                source=user_src,
            )
            gate.check_evidence_update(scope_a, r, event, now=old_now)

        # Fresh window — should succeed
        new_event = EvidenceEvent(
            record_id=r.id,
            context_id=scope_a.context_id,
            supports=True,
            evidence_type=EvidenceType.user_action,
            source=user_src,
        )
        result = gate.check_evidence_update(
            scope_a, r, new_event, now=datetime.now(tz=UTC)
        )
        assert result.is_allowed


# ---------------------------------------------------------------------------
# Self-reinforcement prevention
# ---------------------------------------------------------------------------


class TestSelfReinforcement:
    def test_no_llm_inference_evidence_type(self) -> None:
        """
        EvidenceType enum must not contain an 'llm_inference' value.
        Its absence is the hard prevention; this test documents the invariant.
        """
        names = {e.value for e in EvidenceType}
        assert "llm_inference" not in names
        # Valid types only
        assert names == {
            "user_restatement",
            "user_action",
            "verified_system_outcome",
        }

    def test_agent_cannot_supply_arbitrary_evidence_type(
        self,
        store: SQLiteMemoryStore,
        scope_a: ContextScope,
    ) -> None:
        """Submitting an invalid evidence_type via tool call is rejected."""
        svc = MemoryService(store=store)
        svc.create_context(scope_a)

        # First, create a belief (evidence_type required by meta-gate)
        create_call = ToolCall(
            tool_name=ToolName.propose_belief,
            arguments={
                "subject_label": "User",
                "subject_type": "person",
                "predicate": "likes",
                "object_value": "Python",
                "evidence_type": "user_restatement",
            },
            context_id=scope_a.context_id,
        )
        create_result = svc.handle_tool_call(create_call)
        assert create_result.success
        record_id = create_result.data["record_id"]

        # Attempt to report evidence with an invalid type
        ev_call = ToolCall(
            tool_name=ToolName.report_evidence,
            arguments={
                "record_id": record_id,
                "supports": True,
                "evidence_type": "llm_inference",  # should not be accepted
            },
            context_id=scope_a.context_id,
        )
        ev_result = svc.handle_tool_call(ev_call)
        assert not ev_result.success


# ---------------------------------------------------------------------------
# Context policy enforcement
# ---------------------------------------------------------------------------


class TestContextPolicy:
    def test_policy_min_authority_enforced(
        self,
        scope_a: ContextScope,
        user_src: MemorySource,
    ) -> None:
        policy = ContextPolicy(min_authority_to_write=AuthorityLevel.system)
        reviewer = MemoryReviewer()
        proposed = make_record(scope_a, user_src)  # user-level — below policy minimum
        result = reviewer.review(
            proposed=proposed,
            scope=scope_a,
            existing_records=[],
            evidence_history=[],
            policy=policy,
        )
        assert result.decision == ReviewDecision.reject
        assert "authority" in result.justification.lower()

    def test_policy_record_cap_defers(
        self,
        scope_a: ContextScope,
        user_src: MemorySource,
    ) -> None:
        policy = ContextPolicy(max_records_per_context=2)
        reviewer = MemoryReviewer()
        existing = [
            make_record(scope_a, user_src, predicate=f"fact_{i}") for i in range(2)
        ]
        proposed = make_record(scope_a, user_src, predicate="one_more")
        result = reviewer.review(
            proposed=proposed,
            scope=scope_a,
            existing_records=existing,
            evidence_history=[],
            policy=policy,
        )
        assert result.decision == ReviewDecision.defer
        assert "cap" in result.justification.lower()
