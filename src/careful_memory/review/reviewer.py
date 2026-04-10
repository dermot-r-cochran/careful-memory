"""
Memory Review Agent for careful-memory.

The reviewer is a platform-internal component — NOT an LLM agent.
It applies structured, rule-based judgment to proposed memory records
and returns a typed verdict with explicit justification.

Role contract (from system design):
  - Does not help the user
  - Does not invent new information
  - Must not add beliefs
  - Inputs: proposed record, related existing records, evidence history,
            context policies, and a MetaAssessment from the meta-gate
  - Output: approve / defer / modify / reject + justification

INVARIANT: Bayesian updates (alpha/beta) happen ONLY after this reviewer
           returns an approve or modify decision.  The reviewer itself
           never touches alpha, beta, or derived confidence.

PIPELINE POSITION:

    agent tool call
          │
          ▼
    ┌─────────────┐
    │  MetaGate   │  ← reasoning-quality gate (blocks LOW; restricts MEDIUM)
    └──────┬──────┘
           │ is_gate_pass=True
           ▼
    ┌─────────────┐
    │  WriteGate  │  ← hard rules (authority, rate-limit, isolation)
    └──────┬──────┘
           │ is_allowed=True
           ▼
    ┌──────────────────┐
    │  MemoryReviewer  │  ← epistemic judge (this module)
    └──────┬───────────┘
           │ approve / modify
           ▼
    ┌─────────────┐
    │   Storage   │  ← Bayesian update + persist
    └─────────────┘
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from careful_memory.models.enums import (
    AuthorityLevel,
    MemoryType,
    RecordStatus,
)
from careful_memory.models.memory import (
    ContextScope,
    EvidenceEvent,
    MemoryRecord,
)
from careful_memory.models.meta import MetaAssessment

# ---------------------------------------------------------------------------
# Policy defaults (all justified)
# ---------------------------------------------------------------------------

# Maximum fraction of active records that a single new record may contradict
# in one write.  If a proposed record contradicts more than this share it is
# almost certainly a poisoning attempt or a data-quality problem.
MAX_CONTRADICTION_FRACTION: float = 0.25  # 25% of active records

# Minimum alpha+beta mass required before an episodic memory may be promoted
# to semantic via a write (vs. gradual reinforcement).
MIN_EVIDENCE_MASS_FOR_SEMANTIC: float = 4.0  # α+β > 4 → at least 2 extra obs each side

# Age (days) below which a proposed SEMANTIC record is suspicious if it
# arrives with no supporting episodic parents.  Semantic beliefs should
# be *promoted*, not directly asserted with high confidence.
SUSPICIOUS_DIRECT_SEMANTIC_AGE_DAYS: float = 0.0  # any direct semantic write triggers review

# Confidence ceiling for a brand-new record submitted via a user-authority
# source.  A user cannot directly assert near-certainty.
USER_AUTHORITY_CONFIDENCE_CEILING: float = 0.80

# If a proposed record is near-duplicate of an active record (same subject /
# predicate / object within the same context), defer rather than create noise.
# Similarity is checked lexically (case-insensitive) — no embeddings required.
NEAR_DUPLICATE_SIMILARITY: bool = True  # feature flag; disable to skip

# ---------------------------------------------------------------------------
# Review types
# ---------------------------------------------------------------------------


class ReviewDecision(StrEnum):
    """
    Verdict returned by the MemoryReviewer.

    approve  — record is valid; platform may commit as-is
    defer    — insufficient evidence; do not write now; revisit when more
               evidence arrives
    modify   — record is acceptable in modified form; `suggested_record`
               carries the adjusted version
    reject   — record must not be written; reason explains why
    """

    approve = "approve"
    defer = "defer"
    modify = "modify"
    reject = "reject"


@dataclass(frozen=True)
class ReviewResult:
    """
    Outcome of the MemoryReviewer evaluation.

    Attributes
    ----------
    decision          : approve / defer / modify / reject
    justification     : human-readable, explicit explanation (REQUIRED).
                        Must reference both review findings and the meta-gate
                        level so that post-hoc auditors can reconstruct why
                        this decision was reached.
    suggested_record  : only populated when decision == modify;
                        the platform-adjusted version of the proposal.
                        Alpha/beta are NOT changed here — Bayesian updates
                        happen only after this result is acted on.
    checks_run        : names of all checks that were evaluated
    checks_failed     : subset of checks_run that returned a finding
    meta_assessment   : the MetaAssessment that gated this review;
                        always present when the reviewer was reached.
                        None only if reviewer was called without a gate
                        (tests / direct platform calls).
    """

    decision: ReviewDecision
    justification: str
    suggested_record: MemoryRecord | None = None
    checks_run: list[str] = field(default_factory=list)
    checks_failed: list[str] = field(default_factory=list)
    meta_assessment: MetaAssessment | None = None

    @property
    def is_committable(self) -> bool:
        """True when the platform should proceed to write storage."""
        return self.decision in (ReviewDecision.approve, ReviewDecision.modify)


# ---------------------------------------------------------------------------
# Individual checks (pure functions → easy to test, extend, override)
# ---------------------------------------------------------------------------


def _check_context_isolation(
    proposed: MemoryRecord,
    scope: ContextScope,
) -> str | None:
    """Return a finding string if isolation is violated, else None."""
    if proposed.context_id != scope.context_id:
        return (
            f"proposed record context_id {proposed.context_id!r} does not match "
            f"scope {scope.context_id!r}"
        )
    return None


def _check_authority_confidence_ceiling(
    proposed: MemoryRecord,
) -> str | None:
    """
    A user-authority source must not assert near-certainty confidence.

    High initial alpha/beta on a user-level write is suspicious:
    it implies the user is claiming verified certainty, which they cannot.
    """
    if proposed.source.authority_level == AuthorityLevel.user and proposed.confidence > USER_AUTHORITY_CONFIDENCE_CEILING:
        return (
            f"user-authority record has confidence {proposed.confidence:.3f} "
            f"> ceiling {USER_AUTHORITY_CONFIDENCE_CEILING}; "
            "user sources cannot assert near-certainty directly"
        )
    return None


def _check_direct_semantic_assertion(
    proposed: MemoryRecord,
    existing_records: Sequence[MemoryRecord],
) -> str | None:
    """
    Semantic memories should be promoted from episodic, not directly asserted.

    A directly-asserted semantic record with high initial confidence and no
    episodic parents is suspicious.
    """
    if proposed.memory_type != MemoryType.semantic:
        return None
    # If the record supersedes an episodic record it is a legitimate promotion.
    if proposed.supersedes:
        return None
    # Check whether there are related episodic records in the same context.
    related_episodic = [
        r for r in existing_records
        if r.memory_type == MemoryType.episodic
        and r.subject.entity_id == proposed.subject.entity_id
        and r.predicate == proposed.predicate
        and r.status == RecordStatus.active
    ]
    if not related_episodic and proposed.confidence > 0.65:
        return (
            "direct high-confidence semantic assertion with no supporting "
            "episodic parent records; semantic beliefs should be promoted, "
            "not directly asserted"
        )
    return None


def _check_contradiction_volume(
    proposed: MemoryRecord,
    existing_records: Sequence[MemoryRecord],
) -> str | None:
    """
    A single write should not contradict an unreasonably large fraction of
    existing active records.  Mass contradiction is a poisoning signal.
    """
    active = [r for r in existing_records if r.status == RecordStatus.active]
    if not active:
        return None
    n_contradicts = len(proposed.contradicts)
    fraction = n_contradicts / len(active)
    if fraction > MAX_CONTRADICTION_FRACTION:
        return (
            f"proposal contradicts {n_contradicts}/{len(active)} "
            f"({fraction:.0%}) active records; "
            f"exceeds max allowed fraction {MAX_CONTRADICTION_FRACTION:.0%}"
        )
    return None


def _check_near_duplicate(
    proposed: MemoryRecord,
    existing_records: Sequence[MemoryRecord],
) -> str | None:
    """
    Detect near-duplicate writes (same subject, predicate, object, context).

    A duplicate write does not add information; it should be deferred and
    handled as a reinforcement event instead.
    """
    if not NEAR_DUPLICATE_SIMILARITY:
        return None

    def _obj_str(r: MemoryRecord) -> str:
        return (
            r.object_value.label.lower()
            if hasattr(r.object_value, "label")
            else str(r.object_value).lower()
        )

    proposed_obj = _obj_str(proposed)
    for r in existing_records:
        if r.status != RecordStatus.active:
            continue
        if (
            r.subject.label.lower() == proposed.subject.label.lower()
            and r.predicate.lower() == proposed.predicate.lower()
            and _obj_str(r) == proposed_obj
            and r.id != proposed.id
            # A record that explicitly supersedes this existing record is a
            # legitimate promotion, not a duplicate write.
            and r.id not in proposed.supersedes
        ):
            return (
                f"near-duplicate of active record {r.id!r} "
                f"(same subject/predicate/object); "
                "submit a reinforcement event instead of a new record"
            )
    return None


def _check_evidence_mass_for_semantic(
    proposed: MemoryRecord,
) -> str | None:
    """
    A semantic record must have sufficient evidence mass (α+β).

    Brand-new semantic records with default priors (α=β=1) are suspicious;
    they should arrive with at least some accumulated evidence.
    """
    if proposed.memory_type != MemoryType.semantic:
        return None
    if (proposed.alpha + proposed.beta) < MIN_EVIDENCE_MASS_FOR_SEMANTIC:
        return (
            f"semantic record has insufficient evidence mass "
            f"(α+β = {proposed.alpha + proposed.beta:.1f} < "
            f"{MIN_EVIDENCE_MASS_FOR_SEMANTIC}); "
            "semantic beliefs require accumulated evidence"
        )
    return None


def _check_contradicts_higher_authority(
    proposed: MemoryRecord,
    existing_records: Sequence[MemoryRecord],
) -> str | None:
    """
    A record must not contradict a higher-authority active record unless it
    carries at least equal authority.

    (The write-gate also checks this, but the reviewer provides a richer
    justification for audit purposes.)
    """
    for target_id in proposed.contradicts:
        target = next((r for r in existing_records if r.id == target_id), None)
        if target is None:
            continue
        if proposed.source.authority_level < target.source.authority_level:
            return (
                f"proposed record (authority={proposed.source.authority_level.name}) "
                f"attempts to contradict record {target_id!r} "
                f"(authority={target.source.authority_level.name}); "
                "cannot contradict a higher-authority belief"
            )
    return None


# ---------------------------------------------------------------------------
# ContextPolicy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContextPolicy:
    """
    Per-context configuration that the reviewer enforces.

    Parameters
    ----------
    allowed_memory_types  : set of MemoryType values accepted in this context.
                            Empty set means all types allowed.
    min_authority_to_write: minimum AuthorityLevel required to add new records.
    max_records_per_context: hard cap on active records per context (0 = unlimited).
    require_evidence_type : if True, source.evidence_type must be set on writes.
    """

    allowed_memory_types: frozenset[MemoryType] = field(
        default_factory=lambda: frozenset(MemoryType)
    )
    min_authority_to_write: AuthorityLevel = AuthorityLevel.user
    max_records_per_context: int = 0  # 0 = unlimited
    require_evidence_type: bool = False


# ---------------------------------------------------------------------------
# MemoryReviewer
# ---------------------------------------------------------------------------


class MemoryReviewer:
    """
    Platform-internal memory review agent.

    Applies structured, rule-based judgment to proposed memory records.
    Returns a ReviewResult with a decision and explicit justification.

    ROLE CONTRACT:
      - Does not help the user.
      - Does not invent new information.
      - Must not add beliefs.
      - Does not update alpha, beta, or confidence — ever.
      - Only evaluates the proposal against existing records, evidence
        history, context policies, and the meta-gate assessment.

    The MetaAssessment level restricts which decisions are reachable:
      - MetaConfidenceLevel.high   → all decisions available (normal rules)
      - MetaConfidenceLevel.medium → approve/modify only for episodic writes;
                                     semantic/procedural → defer;
                                     contradiction writes → defer
      - MetaConfidenceLevel.low    → reviewer must NOT be called;
                                     the MetaGate blocks before this point

    Usage::

        reviewer = MemoryReviewer()
        result = reviewer.review(
            proposed=new_record,
            scope=context_scope,
            existing_records=store.list_records(scope.context_id),
            evidence_history=[],
            policy=ContextPolicy(),
            meta_assessment=meta,   # from MetaGate.assess()
        )
        if result.is_committable:
            record_to_save = result.suggested_record or proposed
            # Bayesian update + persist happens HERE, not inside the reviewer
            store.save_record(record_to_save)
    """

    def review(
        self,
        proposed: MemoryRecord,
        scope: ContextScope,
        existing_records: Sequence[MemoryRecord],
        evidence_history: Sequence[EvidenceEvent],
        policy: ContextPolicy | None = None,
        meta_assessment: MetaAssessment | None = None,
    ) -> ReviewResult:
        """
        Evaluate a proposed MemoryRecord.

        Parameters
        ----------
        proposed         : the record being proposed for write
        scope            : the context scope
        existing_records : all existing records in this context (any status)
        evidence_history : recent evidence events for this context
        policy           : context-level policy; uses defaults if None
        meta_assessment  : gate result from MetaGate.assess(); if None the
                           reviewer runs without meta-level restrictions
                           (used in direct platform calls and tests)

        Returns
        -------
        ReviewResult with decision, justification, and the meta_assessment.

        INVARIANT: this method never modifies alpha, beta, or confidence.
                   Bayesian updates are the caller's responsibility and must
                   only occur when result.is_committable is True.
        """
        policy = policy or ContextPolicy()
        checks_run: list[str] = []
        findings: list[tuple[str, str]] = []  # (check_name, finding_message)

        def run(name: str, finding: str | None) -> None:
            checks_run.append(name)
            if finding:
                findings.append((name, finding))

        def _result(
            decision: ReviewDecision,
            justification: str,
            suggested: MemoryRecord | None = None,
        ) -> ReviewResult:
            """
            Construct a ReviewResult, always embedding the meta_assessment
            so that every decision is auditable back to its gate context.
            """
            meta_prefix = (
                f"[meta:{meta_assessment.level.value}] "
                if meta_assessment is not None
                else ""
            )
            return ReviewResult(
                decision=decision,
                justification=meta_prefix + justification,
                suggested_record=suggested,
                checks_run=list(checks_run),
                checks_failed=[f[0] for f in findings],
                meta_assessment=meta_assessment,
            )

        # ── Stage 0: Meta-level restriction check ────────────────────────────
        # If MetaAssessment is MEDIUM, restrict allowed write types.
        # This is NOT a policy check — it is a gate-level restriction that
        # cannot be overridden by ContextPolicy.
        if meta_assessment is not None and meta_assessment.restricts_review:
            checks_run.append("meta_level_restriction")
            # MEDIUM: only episodic writes may be approved; others deferred.
            if proposed.memory_type != MemoryType.episodic:
                findings.append((
                    "meta_level_restriction",
                    f"meta-gate is MEDIUM; only episodic writes may be approved "
                    f"at this reasoning quality level; got {proposed.memory_type.value!r}",
                ))
                return _result(
                    ReviewDecision.defer,
                    (
                        f"deferred: meta-gate level is MEDIUM (restricted review); "
                        f"non-episodic write ({proposed.memory_type.value!r}) requires "
                        f"HIGH meta-gate level. Meta rationale: {meta_assessment.rationale}"
                    ),
                )
            # MEDIUM: contradiction writes are deferred regardless.
            if proposed.contradicts:
                findings.append((
                    "meta_level_restriction",
                    "meta-gate is MEDIUM; contradiction writes require HIGH level",
                ))
                return _result(
                    ReviewDecision.defer,
                    (
                        "deferred: meta-gate level is MEDIUM (restricted review); "
                        "contradiction writes require HIGH meta-gate level. "
                        f"Meta rationale: {meta_assessment.rationale}"
                    ),
                )

        # ── Stage 1: Hard isolation check ────────────────────────────────────
        run("context_isolation", _check_context_isolation(proposed, scope))
        if findings:
            return _result(ReviewDecision.reject, findings[0][1])

        # ── Stage 2: Policy checks ────────────────────────────────────────────
        if policy.allowed_memory_types and proposed.memory_type not in policy.allowed_memory_types:
            checks_run.append("policy_memory_type")
            return _result(
                ReviewDecision.reject,
                (
                    f"memory_type {proposed.memory_type.value!r} is not permitted "
                    f"by context policy; allowed: "
                    f"{[t.value for t in policy.allowed_memory_types]}"
                ),
            )
        checks_run.append("policy_memory_type")

        if proposed.source.authority_level < policy.min_authority_to_write:
            checks_run.append("policy_min_authority")
            return _result(
                ReviewDecision.reject,
                (
                    f"source authority {proposed.source.authority_level.name} "
                    f"< context policy minimum {policy.min_authority_to_write.name}"
                ),
            )
        checks_run.append("policy_min_authority")

        if policy.max_records_per_context > 0:
            active_count = sum(
                1 for r in existing_records if r.status == RecordStatus.active
            )
            if active_count >= policy.max_records_per_context:
                checks_run.append("policy_record_cap")
                return _result(
                    ReviewDecision.defer,
                    (
                        f"context has reached its active-record cap "
                        f"({active_count}/{policy.max_records_per_context}); "
                        "defer until older records are archived"
                    ),
                )
        checks_run.append("policy_record_cap")

        if policy.require_evidence_type and proposed.source.evidence_type is None:
            checks_run.append("policy_evidence_type")
            return _result(
                ReviewDecision.reject,
                (
                    "context policy requires evidence_type to be set on all writes; "
                    "source.evidence_type is None"
                ),
            )
        checks_run.append("policy_evidence_type")

        # ── Stage 3: Substantive checks (accumulate findings) ─────────────────
        run("authority_confidence_ceiling", _check_authority_confidence_ceiling(proposed))
        run("direct_semantic_assertion", _check_direct_semantic_assertion(proposed, existing_records))
        run("contradiction_volume", _check_contradiction_volume(proposed, existing_records))
        run("near_duplicate", _check_near_duplicate(proposed, existing_records))
        run("evidence_mass_for_semantic", _check_evidence_mass_for_semantic(proposed))
        run("contradicts_higher_authority", _check_contradicts_higher_authority(proposed, existing_records))

        if not findings:
            return _result(
                ReviewDecision.approve,
                "all review checks passed; no findings",
            )

        # ── Stage 4: Verdict from findings ────────────────────────────────────
        finding_names = {f[0] for f in findings}
        combined = "; ".join(f[1] for f in findings)

        # Hard-reject conditions.
        HARD_REJECT = {
            "contradiction_volume",
            "contradicts_higher_authority",
            "authority_confidence_ceiling",
        }
        if finding_names & HARD_REJECT:
            return _result(ReviewDecision.reject, combined)

        # Defer conditions.
        DEFER = {"near_duplicate", "evidence_mass_for_semantic"}
        if finding_names & DEFER:
            return _result(ReviewDecision.defer, combined)

        # Modify: direct semantic assertion → downgrade to episodic.
        if "direct_semantic_assertion" in finding_names:
            suggested = proposed.model_copy(
                update={"memory_type": MemoryType.episodic}
            )
            return _result(
                ReviewDecision.modify,
                combined + "; modified: memory_type downgraded to episodic pending promotion",
                suggested=suggested,
            )

        # Fallback: defer.
        return _result(ReviewDecision.defer, combined)
