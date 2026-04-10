"""
Enumerations used throughout careful-memory.

These are stable, well-typed constants with no embedded business logic.
Any behaviour that changes based on these values lives in the core modules.
"""

from __future__ import annotations

from enum import Enum, StrEnum


class Domain(StrEnum):
    """
    Logical isolation bucket within a user's memory store.

    - personal : private, high-intimacy facts (health, finance, relationships)
    - work     : professional context, project affiliations
    - project  : scoped to a specific project or task (decays faster)
    - global   : cross-domain facts (the user's name, language preference)
    """

    personal = "personal"
    work = "work"
    project = "project"
    global_ = "global"


class MemoryType(StrEnum):
    """
    Cognitive classification of a belief.

    - episodic   : single-event memories ("I had a meeting on Tuesday")
    - semantic   : generalised knowledge ("The user prefers dark mode")
    - procedural : how-to knowledge ("To deploy: run make deploy")

    Promotion from episodic → semantic requires high confidence, multiple
    reinforcements, and evidence spread over time (see core/gate.py).
    """

    episodic = "episodic"
    semantic = "semantic"
    procedural = "procedural"


class RecordStatus(StrEnum):
    """
    Lifecycle state of a MemoryRecord.

    Transitions:
        active → contradicted  (a newer record contradicts this one)
        active → superseded    (a newer record supersedes/updates this one)
        active → retracted     (explicitly withdrawn, e.g. user correction)
        Any state may be archived when confidence decays below threshold.

    INVARIANT: status transitions are append-only.
                No record is mutated in-place; superseding/contradicting
                creates a new record and updates this status field only.
    """

    active = "active"
    contradicted = "contradicted"
    superseded = "superseded"
    retracted = "retracted"
    archived = "archived"


class AuthorityLevel(int, Enum):
    """
    Write authority levels for memory sources.

    Higher integer = higher authority.
    A lower-authority source MUST NOT overwrite a higher-authority belief.

    - user            : direct user statement (lowest trust; subject to bias)
    - system          : internal inference, not externally verified
    - verified_system : externally verified system outcome (highest trust)
    """

    user = 1
    system = 2
    verified_system = 3


class EvidenceType(StrEnum):
    """
    The nature of the evidence that triggered a reinforcement.

    LLM_USAGE is explicitly excluded from reinforcing memory
    (see core/gate.py: no self-reinforcement loop).
    """

    user_restatement = "user_restatement"
    user_action = "user_action"
    verified_system_outcome = "verified_system_outcome"
    # NOTE: intentionally no "llm_inference" here — LLM usage alone
    #       must never reinforce a memory (architectural principle #5).
