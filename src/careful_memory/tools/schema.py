"""
Tightly-scoped agent tools for careful-memory.

ARCHITECTURE: careful-memory is a platform-level service.
Agents NEVER write to memory directly.  Instead they submit tool calls
(proposals) that the platform evaluates through:

    1. WriteGate  — hard rule enforcement
    2. MemoryReviewer — reasoned judgment
    3. Storage    — commit only on approve/modify

Agents have access to exactly three tools:

    propose_belief   — propose a new fact for the platform to evaluate
    report_evidence  — report external evidence for or against a belief
    query_beliefs    — read active beliefs (read-only)

There is intentionally no tool for:
    - Directly writing memory
    - Updating confidence
    - Contradicting / retracting beliefs (those are platform decisions)
    - Deleting records

Each tool carries a JSON-schema definition so it can be registered as an
OpenAI-style function-calling tool or used with any compatible LLM API.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# ---------------------------------------------------------------------------
# Tool names (stable identifiers referenced in dispatch and schemas)
# ---------------------------------------------------------------------------


class ToolName(StrEnum):
    propose_belief = "propose_belief"
    report_evidence = "report_evidence"
    query_beliefs = "query_beliefs"


# ---------------------------------------------------------------------------
# Tool call / result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolCall:
    """
    A tool invocation submitted by an agent.

    The platform (ToolDispatcher) is the sole interpreter of this call.
    Agents submit the name and arguments; they receive a ToolResult.
    """

    tool_name: ToolName
    # Arguments as a free-form dict, validated against the tool schema.
    arguments: dict[str, Any]
    # Calling context — must be provided by the platform wrapper, not the agent.
    context_id: str
    # Session identifier for audit logging (not used for auth).
    session_id: str | None = None


@dataclass(frozen=True)
class ToolResult:
    """
    Platform response to a ToolCall.

    success    : whether the call was processed without error
    data       : result payload (structure depends on tool_name)
    message    : human-readable summary for logging / debugging
    review_decision : populated for propose_belief calls
    """

    success: bool
    data: dict[str, Any]
    message: str
    review_decision: str | None = None


# ---------------------------------------------------------------------------
# JSON schemas (OpenAI function-calling compatible)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: dict[ToolName, dict[str, Any]] = {
    ToolName.propose_belief: {
        "name": ToolName.propose_belief.value,
        "description": (
            "Propose a new belief for the memory platform to evaluate. "
            "The platform — not the agent — decides whether to accept, "
            "modify, defer, or reject the proposal. "
            "Do not call this to update or contradict existing beliefs; "
            "those are platform-level decisions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "subject_label": {
                    "type": "string",
                    "description": "Human-readable name of the subject entity (e.g. 'the user')",
                },
                "subject_type": {
                    "type": "string",
                    "description": "Entity type (e.g. 'person', 'project', 'tool')",
                },
                "predicate": {
                    "type": "string",
                    "description": "Relationship label (e.g. 'prefers', 'works on', 'dislikes')",
                },
                "object_value": {
                    "type": "string",
                    "description": "The value or entity label (e.g. 'dark mode', 'Python')",
                },
                "memory_type": {
                    "type": "string",
                    "enum": ["episodic", "semantic", "procedural"],
                    "description": "Type of memory; new observations should default to 'episodic'",
                },
                "notes": {
                    "type": "string",
                    "description": "Optional context for the platform reviewer",
                },
                "evidence_type": {
                    "type": "string",
                    "enum": [
                        "user_restatement",
                        "user_action",
                        "verified_system_outcome",
                    ],
                    "description": (
                        "The grounds for this proposal. REQUIRED for the meta-gate to pass. "
                        "Omitting this field will block the proposal at the reasoning-quality gate. "
                        "LLM inference alone is not a valid evidence type."
                    ),
                },
            },
            "required": ["subject_label", "subject_type", "predicate", "object_value", "evidence_type"],
        },
    },
    ToolName.report_evidence: {
        "name": ToolName.report_evidence.value,
        "description": (
            "Report external evidence that supports or contradicts an existing belief. "
            "The platform validates the evidence and decides whether to update the "
            "belief's confidence. "
            "Valid evidence types: user_restatement, user_action, verified_system_outcome. "
            "LLM inference alone is NOT valid evidence."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "record_id": {
                    "type": "string",
                    "description": "ID of the MemoryRecord this evidence relates to",
                },
                "supports": {
                    "type": "boolean",
                    "description": "True if this evidence supports the belief; False if it contradicts",
                },
                "evidence_type": {
                    "type": "string",
                    "enum": [
                        "user_restatement",
                        "user_action",
                        "verified_system_outcome",
                    ],
                    "description": "Nature of the evidence",
                },
                "notes": {
                    "type": "string",
                    "description": "Optional description of the observed evidence",
                },
            },
            "required": ["record_id", "supports", "evidence_type"],
        },
    },
    ToolName.query_beliefs: {
        "name": ToolName.query_beliefs.value,
        "description": (
            "Query active beliefs for the current context. "
            "Returns confidence-weighted records. "
            "This is a read-only operation; no memory is modified."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "min_confidence": {
                    "type": "number",
                    "description": "Only return beliefs at or above this confidence (0.0–1.0)",
                    "default": 0.5,
                },
                "memory_type": {
                    "type": "string",
                    "enum": ["episodic", "semantic", "procedural"],
                    "description": "Filter by memory type; omit for all types",
                },
                "subject_label": {
                    "type": "string",
                    "description": "Filter by subject entity label (case-insensitive partial match)",
                },
            },
            "required": [],
        },
    },
}
