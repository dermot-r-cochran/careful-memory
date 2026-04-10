"""
MemoryService — the platform authority for careful-memory.

This is the single orchestration point for:
  - Context management
  - Record writes (via gate + reviewer)
  - Evidence updates (via gate)
  - Decay runs
  - Summary generation
  - Prompt assembly for inference-time injection

Agents never call this class directly.
They use the tightly-scoped tools in careful_memory.tools, which
route through ToolDispatcher, which calls MemoryService internally.

Only platform code (e.g. middleware, scheduled tasks) calls MemoryService
directly.
"""

from __future__ import annotations

from datetime import datetime

from careful_memory.core.decay import apply_decay_to_record
from careful_memory.core.gate import GateResult, WriteGate
from careful_memory.core.summarizer import build_summary
from careful_memory.inference.prompt import AssembledPrompt, PromptBuilder
from careful_memory.models.memory import ContextScope, MemoryRecord, MemorySummary
from careful_memory.review.reviewer import ContextPolicy, MemoryReviewer, ReviewResult
from careful_memory.storage.base import MemoryStore
from careful_memory.tools.dispatcher import ToolDispatcher
from careful_memory.tools.schema import ToolCall, ToolResult


class MemoryService:
    """
    Platform-level orchestration service.

    Parameters
    ----------
    store    : storage backend (SQLiteMemoryStore for local, SqlAlchemyStore for Azure)
    gate     : write-gate instance; shared with ToolDispatcher
    reviewer : memory review agent; shared with ToolDispatcher
    policy   : context-level policy
    """

    def __init__(
        self,
        store: MemoryStore,
        gate: WriteGate | None = None,
        reviewer: MemoryReviewer | None = None,
        policy: ContextPolicy | None = None,
    ) -> None:
        self._store = store
        self._gate = gate or WriteGate()
        self._reviewer = reviewer or MemoryReviewer()
        self._policy = policy or ContextPolicy()
        self._dispatcher = ToolDispatcher(
            store=store,
            gate=self._gate,
            reviewer=self._reviewer,
            policy=self._policy,
        )
        self._prompt_builder = PromptBuilder()

    # ------------------------------------------------------------------
    # Context management
    # ------------------------------------------------------------------

    def create_context(self, scope: ContextScope) -> ContextScope:
        """Register a new ContextScope with the storage backend."""
        self._store.save_context(scope)
        return scope

    def get_context(self, context_id: str) -> ContextScope | None:
        return self._store.get_context(context_id)

    def list_contexts_for_user(self, user_id: str) -> list[ContextScope]:
        return self._store.list_contexts_for_user(user_id)

    # ------------------------------------------------------------------
    # Agent tool dispatch (platform entry point for agent calls)
    # ------------------------------------------------------------------

    def handle_tool_call(self, call: ToolCall) -> ToolResult:
        """
        Process a tool call submitted by an agent.

        This is the ONLY path from agent → memory write.
        """
        return self._dispatcher.dispatch(call)

    # ------------------------------------------------------------------
    # Platform-internal write operations
    # ------------------------------------------------------------------

    def write_record(
        self,
        scope: ContextScope,
        record: MemoryRecord,
    ) -> tuple[GateResult, ReviewResult, MemoryRecord | None]:
        """
        Platform-internal write path: gate → reviewer → storage.

        Returns (gate_result, review_result, saved_record_or_none).
        """
        gate_result = self._gate.check_new_record(scope, record)
        if not gate_result.is_allowed:
            return gate_result, _stub_deferred_review(gate_result.reason), None

        existing = self._store.list_records(scope.context_id, include_inactive=True)
        review_result = self._reviewer.review(
            proposed=record,
            scope=scope,
            existing_records=existing,
            evidence_history=[],
            policy=self._policy,
        )

        if not review_result.is_committable:
            return gate_result, review_result, None

        to_save = review_result.suggested_record if review_result.suggested_record else record
        self._store.save_record(to_save)
        return gate_result, review_result, to_save

    # ------------------------------------------------------------------
    # Decay
    # ------------------------------------------------------------------

    def run_decay(
        self,
        context_id: str,
        as_of: datetime | None = None,
    ) -> list[MemoryRecord]:
        """
        Apply decay to all active records in a context.

        Returns the list of updated records (already persisted).
        """
        scope = self._store.get_context(context_id)
        if scope is None:
            raise ValueError(f"context {context_id!r} not found")

        records = self._store.list_records(context_id, include_inactive=False)
        updated: list[MemoryRecord] = []
        for record in records:
            decayed = apply_decay_to_record(record, domain=scope.domain, as_of=as_of)
            if decayed != record:
                self._store.save_record(decayed)
            updated.append(decayed)
        return updated

    # ------------------------------------------------------------------
    # Summary & prompt assembly
    # ------------------------------------------------------------------

    def build_summary(
        self,
        context_id: str,
        confidence_threshold: float = 0.6,
        persist: bool = True,
    ) -> MemorySummary:
        """
        Generate a MemorySummary for the context and optionally persist it.

        INVARIANT: the summary must never be fed back as evidence.
        """
        scope = self._store.get_context(context_id)
        if scope is None:
            raise ValueError(f"context {context_id!r} not found")

        records = self._store.list_records(context_id, include_inactive=False)
        summary = build_summary(
            scope=scope,
            records=records,
            confidence_threshold=confidence_threshold,
            domain=scope.domain,
        )
        if persist:
            self._store.save_summary(summary)
        return summary

    def assemble_prompt(
        self,
        context_ids: list[str],
        user_task: str,
        confidence_threshold: float = 0.6,
    ) -> AssembledPrompt:
        """
        Build a memory-grounded system prompt for LLM inference.

        Generates fresh summaries from the given contexts and assembles
        them into the canonical inference prompt format.

        INVARIANT: this method is read-only with respect to the belief store.
                   It generates summaries in-memory; set persist=False.
        """
        summaries: list[MemorySummary] = []
        for cid in context_ids:
            summary = self.build_summary(cid, confidence_threshold=confidence_threshold, persist=False)
            summaries.append(summary)

        return self._prompt_builder.build(user_task=user_task, summaries=summaries)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def list_records(
        self,
        context_id: str,
        include_inactive: bool = False,
    ) -> list[MemoryRecord]:
        return self._store.list_records(context_id, include_inactive=include_inactive)

    def get_record(self, record_id: str, context_id: str) -> MemoryRecord | None:
        return self._store.get_record(record_id, context_id)


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


def _stub_deferred_review(reason: str) -> ReviewResult:
    """Create a synthetic ReviewResult for gate-rejected cases."""
    from careful_memory.review.reviewer import (  # local import to avoid circular
        ReviewDecision,
        ReviewResult,
    )

    return ReviewResult(
        decision=ReviewDecision.reject,
        justification=f"gate rejected before review: {reason}",
    )
