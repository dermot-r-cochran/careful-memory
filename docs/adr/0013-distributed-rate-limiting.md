# ADR-0013: Distributed Rate Limiting (Redis)

**Date:** 2024-01-01  
**Status:** Accepted  
**Supersedes:** [ADR-0008](0008-in-process-rate-limiting.md)

---

## Context

ADR-0008 introduced an in-process sliding-window rate limiter (`_RateLimitWindow`) that tracks evidence events per `(context_id, record_id)` pair.  The in-process design was explicitly labelled as a single-instance stop-gap with a documented Redis upgrade path.

The requirement for production deployment is at least 2 replicas (for high availability on Azure Container Apps).  With in-process rate limiting, each replica maintains its own counter, so the effective rate limit is `RATE_LIMIT_MAX_EVENTS × replica_count`.  With 2 replicas and a 10-event limit, an adversary can submit 20 evidence events per hour by round-robining between replicas — doubling the allowed rate and partially defeating the safety guarantee.

---

## Decision

Rate limiting is implemented as a **Redis-backed sliding window** using `INCR` + `EXPIRE` on a key scoped to `(context_id, record_id, window_start_epoch)`.

The implementation is encapsulated in a `RedisRateLimiter` class that conforms to the same interface as the existing `_RateLimitWindow`, so `WriteGate._get_window()` requires only a one-line change to swap the implementation.

```
Key format: rate_limit:{context_id}:{record_id}:{window_epoch}
Command:    INCR key  →  returns new count
            EXPIRE key {RATE_LIMIT_WINDOW_SECONDS}  (set only if count == 1)
Decision:   count > RATE_LIMIT_MAX_EVENTS → reject
```

The `window_epoch` is `floor(unix_timestamp / RATE_LIMIT_WINDOW_SECONDS)`, making windows aligned to the epoch rather than rolling per-request.  This is a fixed-window approximation; it does not require sorted sets or Lua scripts, keeping the implementation simple and auditable.

**Configuration:**

| Parameter | Environment variable | Default |
|-----------|---------------------|---------|
| Max events per window | `MEMORY_RATE_LIMIT_MAX` | 10 |
| Window size | `MEMORY_RATE_LIMIT_WINDOW_SECONDS` | 3600 (1 hour) |
| Redis URL | `REDIS_URL` | (required in production) |

If `REDIS_URL` is not set, the system falls back to the in-process `_RateLimitWindow`.  This preserves the zero-dependency development experience.

---

## Consequences

**Benefits**

- Rate-limit state is shared across all replicas; the safety guarantee holds regardless of replica count or load-balancer routing.
- The Redis key TTL matches the window size, so stale keys are automatically evicted; no explicit cleanup is required.
- The fallback to in-process limiting when `REDIS_URL` is absent preserves the zero-dependency development experience from ADR-0008.
- A Redis `INCR` is atomic; there is no race condition between check and increment.

**Obligations**

- Production deployments with more than one replica **must** set `REDIS_URL`.  The service should log a warning at startup if `REDIS_URL` is absent and more than one replica is detected.
- Azure Cache for Redis must be provisioned in the same VNET as the Container Apps environment (private endpoint) to avoid public internet exposure.
- The Redis connection must use TLS (`rediss://` scheme).
- Integration tests must use a Redis container (or `fakeredis`) rather than mocking the `RedisRateLimiter` directly.

**Trade-offs**

- The fixed-window approximation can allow up to 2× the limit at a window boundary (one full window at the end of the old window, one full window at the start of the new).  For the memory safety use-case, this is acceptable: the Bayesian model requires many more than 20 events to push confidence to certainty.  If a stricter sliding window is required in future, it can be implemented with a Redis sorted set.
- Redis is a new infrastructure dependency for production.  The fallback mechanism ensures this does not block development or single-instance staging.
- A Redis outage will cause the rate limiter to fall back to in-process limiting (fail-open for availability) or hard-reject all writes (fail-closed for safety).  The default should be fail-open with a warning metric emitted; a configuration flag can switch to fail-closed for high-security deployments.
