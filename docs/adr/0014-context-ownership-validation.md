# ADR-0014: Context Ownership Validation

**Date:** 2024-01-01  
**Status:** Accepted

---

## Context

ADR-0004 established that context isolation is enforced at every layer: storage, WriteGate, and ToolDispatcher.  However, none of these layers validates that the **caller** (the authenticated user) is the **owner** of the `context_id` they are submitting.

Concretely: if user Alice has `context_id = "alice-personal-uuid"` and user Bob has `context_id = "bob-personal-uuid"`, a malicious Bob who knows Alice's `context_id` can submit tool calls with Alice's `context_id`.  The WriteGate will accept these calls because the `context_id` in the request matches the `context_id` on the record — it does not check whether the caller is Alice.

This is a security boundary violation: the trust boundary between the authenticated caller identity and the memory namespace they are accessing is not enforced.

---

## Decision

Context ownership is validated at the **API layer**, before the request reaches `MemoryService`.  This is implemented as a `ContextOwnershipMiddleware` (or equivalent decorator/dependency).

```
Incoming request
  │
  │  bearer_token → Azure AD → verified user_id (object ID)
  │  request.context_id
  │
  ▼
ContextOwnershipMiddleware
  │
  ├─ Look up owner of context_id in context registry
  │  (in-memory cache + storage fallback)
  │
  ├─ Assert: owner.user_id == verified user_id
  │
  ├─ If match   → allow request
  └─ If mismatch → 403 Forbidden (no information disclosure about the context)
```

The context registry maps `context_id → user_id`.  This mapping is created by `MemoryService.create_context()` and is immutable: once a context is created for a user, its ownership cannot be transferred.

**Platform code** (operators acting on behalf of users) is permitted to access any context provided they present a service-level token with an elevated scope.  This is documented but kept out of the agent-facing API.

---

## Consequences

**Benefits**

- Closes the context ownership gap: an authenticated user can only interact with contexts they created.
- The check is at the API boundary — the outermost layer — so it cannot be bypassed by any internal code path.
- 403 responses do not reveal whether the `context_id` exists; this prevents enumeration attacks.
- The in-memory cache for the context registry means the ownership check adds negligible latency for warm paths.

**Obligations**

- `MemoryService.create_context()` must atomically register the `(context_id, user_id)` mapping in the context registry at the same time as it creates the context in storage.
- The context registry must be durable (persisted in the same database as memory records); an in-memory-only registry would lose ownership mappings on restart.
- All API endpoints that accept `context_id` as a parameter must pass through `ContextOwnershipMiddleware`.  There must be no API endpoint that bypasses this middleware.
- Tests must cover: valid ownership (200), mismatched ownership (403), and unknown `context_id` (403).

**Trade-offs**

- The additional database lookup (for cold cache misses) adds a small latency overhead per request.  With an LRU in-memory cache of recently-seen `context_id`s, this is a cold-path-only cost.
- If the context registry table becomes unavailable, the middleware must fail-closed (reject all requests) rather than fail-open.  This is a deliberate safety trade-off: it is better to be temporarily unavailable than to allow unauthorised access.
- Multi-domain access (e.g. a user accessing both their `personal` and `work` contexts in the same request) is supported by submitting separate requests per `context_id`, each passing the ownership check.
