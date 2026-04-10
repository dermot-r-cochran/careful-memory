"""
Tests for the tightly-scoped agent tools and ToolDispatcher.

Verifies that:
  - Agents can only propose, report evidence, and query
  - Platform (dispatcher) decides all writes
  - Invalid tool arguments are rejected gracefully
  - Query is read-only
  - LLM-only evidence is rejected
"""

from __future__ import annotations

import pytest

from careful_memory.models.enums import Domain
from careful_memory.models.memory import ContextScope
from careful_memory.service import MemoryService
from careful_memory.storage.sqlite import SQLiteMemoryStore
from careful_memory.tools.schema import TOOL_SCHEMAS, ToolCall, ToolName


@pytest.fixture()
def store() -> SQLiteMemoryStore:
    return SQLiteMemoryStore(":memory:")


@pytest.fixture()
def scope() -> ContextScope:
    return ContextScope(user_id="u1", domain=Domain.personal)


@pytest.fixture()
def svc(store: SQLiteMemoryStore, scope: ContextScope) -> MemoryService:
    svc = MemoryService(store=store)
    svc.create_context(scope)
    return svc


class TestToolSchemas:
    def test_all_tools_have_schemas(self) -> None:
        for name in ToolName:
            assert name in TOOL_SCHEMAS

    def test_schema_has_required_keys(self) -> None:
        for _name, schema in TOOL_SCHEMAS.items():
            assert "name" in schema
            assert "description" in schema
            assert "parameters" in schema


class TestProposeBelief:
    def test_valid_proposal_succeeds(self, svc: MemoryService, scope: ContextScope) -> None:
        call = ToolCall(
            tool_name=ToolName.propose_belief,
            arguments={
                "subject_label": "Alice",
                "subject_type": "person",
                "predicate": "prefers",
                "object_value": "dark mode",
                "evidence_type": "user_restatement",
            },
            context_id=scope.context_id,
        )
        result = svc.handle_tool_call(call)
        assert result.success
        assert "record_id" in result.data
        assert "meta_level" in result.data  # gate level is always surfaced

    def test_missing_argument_fails(self, svc: MemoryService, scope: ContextScope) -> None:
        call = ToolCall(
            tool_name=ToolName.propose_belief,
            arguments={"subject_label": "Alice"},  # missing required fields
            context_id=scope.context_id,
        )
        result = svc.handle_tool_call(call)
        assert not result.success

    def test_unknown_context_fails(self, svc: MemoryService) -> None:
        call = ToolCall(
            tool_name=ToolName.propose_belief,
            arguments={
                "subject_label": "Alice",
                "subject_type": "person",
                "predicate": "prefers",
                "object_value": "dark mode",
            },
            context_id="nonexistent-context-id",
        )
        result = svc.handle_tool_call(call)
        assert not result.success

    def test_review_decision_included_in_result(
        self, svc: MemoryService, scope: ContextScope
    ) -> None:
        call = ToolCall(
            tool_name=ToolName.propose_belief,
            arguments={
                "subject_label": "Alice",
                "subject_type": "person",
                "predicate": "likes",
                "object_value": "Python",
                "evidence_type": "user_restatement",
            },
            context_id=scope.context_id,
        )
        result = svc.handle_tool_call(call)
        assert result.review_decision is not None

    def test_platform_decides_not_agent(
        self, svc: MemoryService, scope: ContextScope
    ) -> None:
        """
        The agent cannot force a semantic write with high confidence.
        The platform (reviewer) downgrades it to episodic.
        With evidence_type supplied, the meta-gate passes (MEDIUM for semantic).
        """
        call = ToolCall(
            tool_name=ToolName.propose_belief,
            arguments={
                "subject_label": "Alice",
                "subject_type": "person",
                "predicate": "works at",
                "object_value": "Acme Corp",
                "memory_type": "semantic",  # agent attempts semantic
                "evidence_type": "user_restatement",
            },
            context_id=scope.context_id,
        )
        result = svc.handle_tool_call(call)
        # Should either succeed (modified to episodic) or be deferred/rejected —
        # but NEVER blocked by meta-gate because evidence_type is supplied.
        assert result.review_decision != "blocked_by_meta_gate"
        # The review_decision reflects platform authority (not agent choice).
        assert result.review_decision is not None

    def test_no_evidence_type_blocked_by_meta_gate(
        self, svc: MemoryService, scope: ContextScope
    ) -> None:
        """
        A proposal with no evidence_type has undocumented reasoning grounds.
        The meta-gate blocks it (LOW level) before the reviewer is consulted.
        """
        call = ToolCall(
            tool_name=ToolName.propose_belief,
            arguments={
                "subject_label": "Alice",
                "subject_type": "person",
                "predicate": "prefers",
                "object_value": "dark mode",
                # no evidence_type — undocumented grounds
            },
            context_id=scope.context_id,
        )
        result = svc.handle_tool_call(call)
        assert not result.success
        assert result.review_decision == "blocked_by_meta_gate"
        assert result.data.get("meta_level") == "low"


class TestReportEvidence:
    def _create_belief(self, svc: MemoryService, scope: ContextScope) -> str:
        call = ToolCall(
            tool_name=ToolName.propose_belief,
            arguments={
                "subject_label": "Alice",
                "subject_type": "person",
                "predicate": "uses",
                "object_value": "vim",
                "evidence_type": "user_restatement",
            },
            context_id=scope.context_id,
        )
        result = svc.handle_tool_call(call)
        assert result.success
        return result.data["record_id"]

    def test_valid_evidence_accepted(
        self, svc: MemoryService, scope: ContextScope
    ) -> None:
        record_id = self._create_belief(svc, scope)
        call = ToolCall(
            tool_name=ToolName.report_evidence,
            arguments={
                "record_id": record_id,
                "supports": True,
                "evidence_type": "user_restatement",
            },
            context_id=scope.context_id,
        )
        result = svc.handle_tool_call(call)
        assert result.success
        assert "new_confidence" in result.data

    def test_invalid_evidence_type_rejected(
        self, svc: MemoryService, scope: ContextScope
    ) -> None:
        record_id = self._create_belief(svc, scope)
        call = ToolCall(
            tool_name=ToolName.report_evidence,
            arguments={
                "record_id": record_id,
                "supports": True,
                "evidence_type": "llm_inference",  # not valid
            },
            context_id=scope.context_id,
        )
        result = svc.handle_tool_call(call)
        assert not result.success

    def test_confidence_increases_on_support(
        self, svc: MemoryService, scope: ContextScope, store: SQLiteMemoryStore
    ) -> None:
        record_id = self._create_belief(svc, scope)
        before = store.get_record(record_id, scope.context_id)
        assert before is not None

        call = ToolCall(
            tool_name=ToolName.report_evidence,
            arguments={
                "record_id": record_id,
                "supports": True,
                "evidence_type": "user_action",
            },
            context_id=scope.context_id,
        )
        svc.handle_tool_call(call)

        after = store.get_record(record_id, scope.context_id)
        assert after is not None
        assert after.confidence > before.confidence

    def test_unknown_record_fails(
        self, svc: MemoryService, scope: ContextScope
    ) -> None:
        call = ToolCall(
            tool_name=ToolName.report_evidence,
            arguments={
                "record_id": "does-not-exist",
                "supports": True,
                "evidence_type": "user_restatement",
            },
            context_id=scope.context_id,
        )
        result = svc.handle_tool_call(call)
        assert not result.success


class TestQueryBeliefs:
    def test_empty_context_returns_empty(
        self, svc: MemoryService, scope: ContextScope
    ) -> None:
        call = ToolCall(
            tool_name=ToolName.query_beliefs,
            arguments={},
            context_id=scope.context_id,
        )
        result = svc.handle_tool_call(call)
        assert result.success
        assert result.data["count"] == 0

    def test_returns_active_records(
        self, svc: MemoryService, scope: ContextScope
    ) -> None:
        svc.handle_tool_call(ToolCall(
            tool_name=ToolName.propose_belief,
            arguments={
                "subject_label": "Alice", "subject_type": "person",
                "predicate": "likes", "object_value": "tea",
                "evidence_type": "user_restatement",
            },
            context_id=scope.context_id,
        ))
        call = ToolCall(
            tool_name=ToolName.query_beliefs,
            arguments={"min_confidence": 0.0},
            context_id=scope.context_id,
        )
        result = svc.handle_tool_call(call)
        assert result.success

    def test_query_does_not_modify_storage(
        self, svc: MemoryService, scope: ContextScope, store: SQLiteMemoryStore
    ) -> None:
        before = store.list_records(scope.context_id)
        svc.handle_tool_call(ToolCall(
            tool_name=ToolName.query_beliefs,
            arguments={},
            context_id=scope.context_id,
        ))
        after = store.list_records(scope.context_id)
        assert len(before) == len(after)
