# ADR-0012: Strictly-Bounded Three-Tool Agent API (ToolDispatcher)

**Date:** 2024-01-01  
**Status:** Accepted

---

## Context

LLM agents need to interact with the memory system, but they must not have unrestricted write access.  An agent with direct database access could:

- Write arbitrary confidence values (bypassing the Bayesian invariant).
- Delete or overwrite existing records (violating append-only).
- Read memories belonging to other users or contexts.
- Flood the store with synthetic beliefs faster than any rate-limit can detect.

The surface area exposed to agents must be the minimum necessary for legitimate memory operations.

---

## Decision

Agents interact with the memory system through exactly **three tools**, exposed via `ToolDispatcher`:

| Tool | Purpose | What it cannot do |
|------|---------|------------------|
| `propose_belief` | Propose a new `(subject, predicate, object_value)` belief | Write directly; set confidence; specify α/β |
| `report_evidence` | Report supporting or contradicting evidence for an existing record | Create new records; set evidence type to anything not in `EvidenceType`; exceed rate limit |
| `query_beliefs` | Read active beliefs for the current context | Read other contexts; read archived/retracted records by default |

`ToolDispatcher` is the **only** entry point from agent code into the memory system.  It:

1. Validates the `ToolCall` schema (tool name, required arguments, `context_id`).
2. Routes to the full three-stage pipeline (MetaGate → WriteGate → MemoryReviewer) for write tools.
3. Returns a `ToolResult` — a typed, serialisable response the agent can consume.

Agents receive `ToolResult` objects; they never receive raw `MemoryRecord` instances with mutable Bayesian fields.

---

## Consequences

**Benefits**

- The attack surface from agent code is three named operations with validated schemas; no other memory operation is reachable.
- Each tool call goes through the full safety pipeline; there is no "fast path" that bypasses the gate or reviewer.
- `ToolResult` is a stable, serialisable value type; changes to internal `MemoryRecord` structure do not break agent-facing contracts.
- Adding a new tool requires explicitly extending the `ToolName` enum and `ToolDispatcher.dispatch()`; there is no implicit exposure of new capabilities.

**Obligations**

- Platform code (scheduled tasks, admin operations) must use `MemoryService` directly, not `ToolDispatcher`, to avoid the agent-facing rate limits and authority restrictions being applied to trusted platform operations.
- `ToolCall.context_id` must be set by the platform (e.g. from the authenticated session), never by the agent.  An agent that can set its own `context_id` can escape context isolation.
- Tests must verify that `ToolDispatcher` rejects unknown tool names and malformed arguments before any storage or gate code is reached.

**Trade-offs**

- Three tools is a deliberate limitation.  Legitimate use cases that require bulk operations (e.g. migrating a user's memories, batch-seeding initial beliefs from structured data) must be handled by platform code, not agent tool calls.
- The `query_beliefs` tool returns a filtered, confidence-hedged view; it does not expose raw `alpha`/`beta` counters to the agent.  Agents cannot know the exact evidence weight behind a belief — only its confidence hedge level.  This is intentional.
