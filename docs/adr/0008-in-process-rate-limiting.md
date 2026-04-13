# ADR-0008: In-Process Rate Limiting with Explicit Redis Upgrade Path

**Date:** 2024-01-01  
**Status:** Superseded by [ADR-0013](0013-distributed-rate-limiting.md)

---

## Context

The WriteGate must prevent a rapid burst of evidence events from artificially inflating the confidence of a memory record.  Without rate limiting, an adversarial agent could submit 1,000 supporting-evidence events in a second and push a belief to near-certainty before any human review.

Rate limiting requires stateful tracking of event timestamps per `(context_id, record_id)` pair.  In a distributed deployment (multiple service replicas), this state must be shared.  In a single-process deployment (local development, single-instance staging), sharing is unnecessary.

---

## Decision

Rate limiting is implemented as an **in-process sliding-window counter** (`_RateLimitWindow`) stored in a `dict` on the `WriteGate` instance.

- Default limit: **10 evidence events per record per hour** (`RATE_LIMIT_MAX_EVENTS = 10`, `RATE_LIMIT_WINDOW_SECONDS = 3600`).
- Both constants are named and documented; `MEMORY_RATE_LIMIT_MAX` can be overridden via environment variable in deployment.
- The in-process store is explicitly labelled in a comment: *"Production: replace with a distributed cache or Azure Cache for Redis."*

The upgrade path is clear and bounded: replacing `_RateLimitWindow` with a Redis-backed equivalent requires changing only `WriteGate._get_window()`.  The gate's public API and all callers are unchanged.

---

## Consequences

**Benefits**

- Zero additional infrastructure for local development and single-instance deployments.
- The extension point is documented and isolated to a single method (`_get_window`), making the Redis migration a small, contained change.
- Rate-limit state is garbage-collected with the process; there is no persistent state to migrate or clean up between deployments.

**Obligations**

- Production multi-replica deployments **must** replace the in-process store with a distributed cache before going live.  Running multiple replicas without this replacement allows each replica to apply its own per-process rate limit, effectively multiplying the effective rate limit by the replica count.
- The README documents this limitation in the Azure Deployment section.
- Tests must mock or inject `now` into rate-limit checks to avoid time-dependent test flakiness.

**Trade-offs**

- A process restart resets all rate-limit windows.  An adversary who can trigger restarts could reset their rate limit.  In practice, restart-rate limits are governed by the container orchestrator (e.g. Azure Container Apps restart policies).
- The in-process dict grows with the number of distinct `(context_id, record_id)` pairs seen since startup.  This is bounded by the product of active contexts and records per context; for typical agent workloads this is small.  For very high-volume deployments, a TTL-based eviction policy on the dict would be appropriate.
