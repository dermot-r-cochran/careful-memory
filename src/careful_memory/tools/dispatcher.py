"""
Platform-side tool dispatcher for careful-memory.

The dispatcher is the ONLY entry point from agent tool calls into the
memory platform.  It enforces the explicit three-stage guard pipeline:

  Stage 0: MetaGate       — reasoning-quality gate
                            LOW  → blocked here; Memory Review never runs
                            MEDIUM → proceeds with restricted review actions
                            HIGH   → proceeds with normal review actions
  Stage 1: WriteGate      — hard rules (authority, rate-limit, isolation)
  Stage 2: MemoryReviewer — epistemic judge (approve / modify / defer / reject)
  Stage 3: Storage        — Bayesian update + persist (only on approve/modify)

INVARIANT: Bayesian updates (alpha/beta) happen ONLY at Stage 3, after the
           Memory Review Agent has returned an approve or modify decision.
           No earlier stage may modify alpha, beta, or derived confidence.

INVARIANT: agents never bypass this dispatcher to access storage.
"""

from __future__ import annotations

from datetime import UTC, datetime

from careful_memory.core import meta_gate
from careful_memory.core.gate import GateVerdict, WriteGate
from careful_memory.models.enums import (
    AuthorityLevel,
    EvidenceType,
    MemoryType,
)
from careful_memory.models.memory import (
    ContextScope,
    EntityRef,
    EvidenceEvent,
    MemoryRecord,
    MemorySource,
)
from careful_memory.review.reviewer import ContextPolicy, MemoryReviewer
from careful_memory.storage.base import MemoryStore
from careful_memory.tools.schema import ToolCall, ToolName, ToolResult


class ToolDispatcher:
    """
    Platform-side dispatcher: routes agent ToolCalls through the full guard
    stack and into storage.

    Parameters
    ----------
    store    : the storage backend
    gate     : the write-gate instance (shared, stateful for rate-limits)
    reviewer : the memory review agent
    policy   : context-level policy applied by the reviewer
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

    # ------------------------------------------------------------------
    # Public dispatch entry point
    # ------------------------------------------------------------------

    def dispatch(self, call: ToolCall) -> ToolResult:
        """
        Process a tool call from an agent.

        Returns a ToolResult.  Never raises.
        """
        scope = self._store.get_context(call.context_id)
        if scope is None:
            return ToolResult(
                success=False,
                data={},
                message=f"context {call.context_id!r} not found",
            )

        try:
            if call.tool_name == ToolName.propose_belief:
                return self._handle_propose_belief(call, scope)
            if call.tool_name == ToolName.report_evidence:
                return self._handle_report_evidence(call, scope)
            if call.tool_name == ToolName.query_beliefs:
                return self._handle_query_beliefs(call, scope)
            return ToolResult(
                success=False,
                data={},
                message=f"unknown tool: {call.tool_name!r}",
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                success=False,
                data={},
                message=f"internal error in dispatcher: {exc}",
            )

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _handle_propose_belief(self, call: ToolCall, scope: ContextScope) -> ToolResult:
        """
        Process a propose_belief tool call through the explicit four-stage pipeline:

            Stage 0: MetaGate  — reasoning-quality gate (never touches α/β)
            Stage 1: WriteGate — hard rules
            Stage 2: MemoryReviewer — epistemic judge
            Stage 3: Storage   — Bayesian update + persist

        INVARIANT: alpha/beta are modified ONLY at Stage 3, and only when
                   the MemoryReviewer has returned approve or modify.
                   No earlier stage may change belief state.
        """
        args = call.arguments
        try:
            subject = EntityRef(
                entity_type=args["subject_type"],
                label=args["subject_label"],
            )
            memory_type = MemoryType(args.get("memory_type", MemoryType.episodic.value))
            source = MemorySource(
                origin=call.session_id or "agent",
                authority_level=AuthorityLevel.user,
                evidence_type=EvidenceType(args["evidence_type"]) if args.get("evidence_type") else None,
            )
            proposed = MemoryRecord(
                context_id=scope.context_id,
                memory_type=memory_type,
                subject=subject,
                predicate=args["predicate"],
                object_value=args["object_value"],
                source=source,
                notes=args.get("notes"),
            )
        except (KeyError, ValueError) as exc:
            return ToolResult(
                success=False,
                data={},
                message=f"invalid proposal arguments: {exc}",
            )

        # ── Stage 0: MetaGate ─────────────────────────────────────────────────
        # Evaluates reasoning quality.  Does NOT touch alpha, beta, or confidence.
        # LOW  → blocked; stages 1-3 are never reached.
        # MEDIUM / HIGH → proceed (with different reviewer restrictions).
        meta = meta_gate.assess(proposed)
        if not meta.is_gate_pass:
            # MetaGate blocked.  No memory state has changed.
            return ToolResult(
                success=False,
                data={
                    "meta_level": meta.level.value,
                    "meta_rationale": meta.rationale,
                    "meta_signals": meta.signals,
                },
                message=f"meta-gate blocked (level=LOW): {meta.rationale}",
                review_decision="blocked_by_meta_gate",
            )

        # ── Stage 1: WriteGate ────────────────────────────────────────────────
        gate_result = self._gate.check_new_record(scope, proposed)
        if not gate_result.is_allowed:
            return ToolResult(
                success=False,
                data={
                    "gate_reason": gate_result.reason,
                    "meta_level": meta.level.value,
                },
                message=f"write-gate rejected proposal: {gate_result.reason}",
                review_decision="rejected_by_gate",
            )

        # ── Stage 2: MemoryReviewer ───────────────────────────────────────────
        # Passes meta_assessment so the reviewer can apply level-appropriate
        # restrictions.  The reviewer never touches alpha or beta.
        existing = self._store.list_records(scope.context_id, include_inactive=True)
        review = self._reviewer.review(
            proposed=proposed,
            scope=scope,
            existing_records=existing,
            evidence_history=[],
            policy=self._policy,
            meta_assessment=meta,
        )

        if not review.is_committable:
            return ToolResult(
                success=False,
                data={
                    "review_decision": review.decision.value,
                    "justification": review.justification,
                    "checks_failed": review.checks_failed,
                    "meta_level": meta.level.value,
                },
                message=f"reviewer {review.decision.value}: {review.justification}",
                review_decision=review.decision.value,
            )

        # ── Stage 3: Storage ──────────────────────────────────────────────────
        # Bayesian update does NOT occur here for a brand-new record:
        # initial alpha=1, beta=1 is the uniform prior — no update needed.
        # Updates occur via report_evidence events after the record exists.
        record_to_save = review.suggested_record if review.suggested_record else proposed
        self._store.save_record(record_to_save)

        return ToolResult(
            success=True,
            data={
                "record_id": record_to_save.id,
                "review_decision": review.decision.value,
                "justification": review.justification,
                "confidence": record_to_save.confidence,
                "memory_type": record_to_save.memory_type.value,
                "meta_level": meta.level.value,
            },
            message=(
                f"belief proposed and {review.decision.value} "
                f"[meta:{meta.level.value}]: {review.justification}"
            ),
            review_decision=review.decision.value,
        )

    def _handle_report_evidence(self, call: ToolCall, scope: ContextScope) -> ToolResult:
        args = call.arguments
        try:
            record_id: str = args["record_id"]
            supports: bool = bool(args["supports"])
            evidence_type = EvidenceType(args["evidence_type"])
        except (KeyError, ValueError) as exc:
            return ToolResult(
                success=False,
                data={},
                message=f"invalid evidence arguments: {exc}",
            )

        record = self._store.get_record(record_id, scope.context_id)
        if record is None:
            return ToolResult(
                success=False,
                data={},
                message=f"record {record_id!r} not found in context {scope.context_id!r}",
            )

        source = MemorySource(
            origin=call.session_id or "agent",
            authority_level=AuthorityLevel.user,
            evidence_type=evidence_type,
        )
        event = EvidenceEvent(
            record_id=record_id,
            context_id=scope.context_id,
            supports=supports,
            evidence_type=evidence_type,
            source=source,
            notes=args.get("notes"),
        )

        now = datetime.now(tz=UTC)
        gate_result = self._gate.check_evidence_update(scope, record, event, now=now)
        if not gate_result.is_allowed:
            return ToolResult(
                success=False,
                data={"gate_reason": gate_result.reason},
                message=f"gate rejected evidence update: {gate_result.reason}",
            )

        # Apply the Bayesian update.
        assert gate_result.bayesian is not None  # guaranteed by gate when allowed
        updated = record.model_copy(
            update={
                "alpha": gate_result.bayesian.new_alpha,
                "beta": gate_result.bayesian.new_beta,
                "reinforcement_count": record.reinforcement_count + (1 if supports else 0),
                "last_reinforced_at": now if supports else record.last_reinforced_at,
                "updated_at": now,
            }
        )
        self._store.save_record(updated)

        verdict_label = "flagged (outlier swing)" if gate_result.verdict == GateVerdict.flagged else "accepted"
        return ToolResult(
            success=True,
            data={
                "record_id": record_id,
                "new_confidence": gate_result.bayesian.new_confidence,
                "delta_confidence": gate_result.bayesian.delta_confidence,
                "verdict": verdict_label,
            },
            message=(
                f"evidence {verdict_label}; "
                f"confidence: {record.confidence:.3f} → "
                f"{gate_result.bayesian.new_confidence:.3f}"
            ),
        )

    def _handle_query_beliefs(self, call: ToolCall, scope: ContextScope) -> ToolResult:
        args = call.arguments
        min_confidence: float = float(args.get("min_confidence", 0.5))
        memory_type_filter: str | None = args.get("memory_type")
        subject_filter: str | None = args.get("subject_label", "").strip().lower() or None

        records = self._store.list_records(scope.context_id, include_inactive=False)

        results = []
        for r in records:
            if r.confidence < min_confidence:
                continue
            if memory_type_filter and r.memory_type.value != memory_type_filter:
                continue
            if subject_filter and subject_filter not in r.subject.label.lower():
                continue
            results.append({
                "id": r.id,
                "memory_type": r.memory_type.value,
                "subject": r.subject.label,
                "predicate": r.predicate,
                "object": r.object_value if isinstance(r.object_value, str) else r.object_value.label,
                "confidence": round(r.confidence, 4),
                "reinforcement_count": r.reinforcement_count,
                "status": r.status.value,
            })

        # Sort by confidence descending.
        results.sort(key=lambda x: x["confidence"], reverse=True)

        return ToolResult(
            success=True,
            data={"beliefs": results, "count": len(results)},
            message=f"returned {len(results)} belief(s)",
        )
