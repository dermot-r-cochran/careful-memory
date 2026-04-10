# ADR-0004: Context Isolation via ContextScope

**Date:** 2024-01-01  
**Status:** Accepted

---

## Context

A multi-user memory service must prevent one user's memories from leaking into another user's context.  Beyond user-level isolation, the same user may have logically separate memory domains (personal life, work, a specific project) that must not bleed into each other.

Naive isolation (filtering by `user_id`) is insufficient if a single user's domains are kept in the same namespace: an agent working in the `work` domain should not accidentally read or write `personal` beliefs.

---

## Decision

Every memory record belongs to exactly one `ContextScope`.  A scope is identified by a `context_id` (UUID), derived from the combination of `user_id` and `domain` at creation time.

```
ContextScope
  context_id : str (UUID)   ← the isolation key for every query and write
  user_id    : str           ← Azure AD object ID or equivalent UUID
  domain     : Domain        ← personal | work | project | global
```

Cross-context access is forbidden at **every layer**:

- **Storage**: every query includes `WHERE context_id = ?`; there is no "list all" without a scope.
- **WriteGate**: `record.context_id != scope.context_id` → hard rejection.
- **ToolDispatcher**: `ToolCall.context_id` is validated against the active scope before dispatch.

The `global` domain is intentionally included — it holds cross-domain facts (e.g. the user's name, preferred language) and is a separate scope, not a "shared namespace" visible to all domains.

---

## Consequences

**Benefits**

- A compromised or misbehaving agent in one domain cannot read or write another domain's memories.
- `context_id` is the single join key in storage; no complex multi-field filtering is required for isolation.
- Adding new domains in the future requires only extending the `Domain` enum; the isolation mechanism is unchanged.

**Obligations**

- Every storage query must pass `context_id` explicitly.  Helper methods that omit it are forbidden.
- Platform code that creates `ContextScope` objects must use the user's verified identity (e.g. Azure AD object ID), not a user-supplied string.
- The `global` domain must still be isolated per user; it is not a shared system namespace.

**Trade-offs**

- Cross-domain reasoning (e.g. "the user's name from `global` combined with a `work` preference") requires the prompt builder to be explicitly given multiple `context_id`s.  This is a deliberate friction point: cross-context reads must be an explicit platform decision, not a default.
- There is no mechanism to merge or transfer records between scopes; such operations must be handled at the application layer if needed.
