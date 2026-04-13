# ADR-0016: Observability & Telemetry

**Date:** 2024-01-01  
**Status:** Accepted

---

## Context

In production, the write pipeline makes safety-critical decisions on every write: whether to approve, modify, or reject a belief proposal; which rule triggered a rejection; what the confidence trajectory of a record is over time.  Currently these decisions are invisible: there is no structured logging, no metrics, and no distributed tracing.

Without observability:

- Debugging a misbehaving agent requires reading raw database rows.
- It is impossible to answer "how many writes were rejected by the rate limiter in the last hour?"
- Audit requirements (who wrote what, when, with what decision) cannot be satisfied.
- Latency regressions in the pipeline are not detected until users report slowness.

---

## Decision

Observability is implemented as a first-class component: an **`ObservabilityAdapter`** that is injected into `MemoryService` (and `WriteGate`/`MemoryReviewer` where needed) and emits structured events to Azure Application Insights.

### Event Schema

Every write pipeline execution emits a structured `PipelineDecisionEvent`:

```json
{
  "event_type": "pipeline_decision",
  "trace_id": "w3c-traceparent-value",
  "context_id_hash": "sha256(context_id)[:16]",
  "record_id": "uuid",
  "stage": "write_gate | memory_reviewer",
  "decision": "approve | modify | reject",
  "rejection_reason": "rate_limit | authority | context_mismatch | duplicate | mass_contradiction | null",
  "latency_ms": 12,
  "timestamp_utc": "2024-01-01T00:00:00Z"
}
```

Note: `context_id_hash` is a truncated SHA-256 of the raw `context_id`.  The raw `context_id` (which is a UUID derived from `user_id + domain`) is never emitted to telemetry to avoid logging PII.

### Metrics

| Metric name | Type | Tags |
|------------|------|------|
| `cm.writes.approved` | Counter | `memory_type`, `domain` |
| `cm.writes.rejected` | Counter | `reason`, `stage` |
| `cm.writes.modified` | Counter | `reason` |
| `cm.rate_limit.hits` | Counter | — |
| `cm.pipeline.latency_ms` | Histogram | `stage` |
| `cm.confidence.on_write` | Histogram | `memory_type` |

### Distributed Tracing

All API requests carry a W3C `traceparent` header.  The `ObservabilityAdapter` propagates this context through the pipeline stages so that a single agent request can be traced end-to-end in Application Insights.

### Telemetry Sink

Azure Application Insights is the default sink, using the OpenCensus or OpenTelemetry SDK.  The adapter is interface-backed (`TelemetrySink`) so alternative sinks (e.g. Prometheus, Datadog) can be substituted without changing pipeline code.

---

## Consequences

**Benefits**

- Every gate and reviewer decision is recorded with a stable schema; audit queries are possible without reading raw database rows.
- Dashboards and alerts can be built directly on Application Insights metrics (e.g. alert on `cm.writes.rejected{reason=rate_limit}` spike).
- Distributed tracing correlates a single agent request across API, pipeline, and storage layers.
- PII protection: the raw `context_id` (which embeds user identity) is never emitted; only a truncated hash is logged.
- The `TelemetrySink` interface allows the adapter to be replaced with a no-op sink in tests and local development.

**Obligations**

- Every gate decision (approve/modify/reject) and every reviewer decision must emit a `PipelineDecisionEvent`.
- `context_id` must never appear in raw form in any log, trace, or metric tag.  Only `context_id_hash` (truncated SHA-256) is permitted.
- The `ObservabilityAdapter` must be injected via the constructor (not imported as a module-level singleton) to allow replacement in tests.
- Application Insights instrumentation key must be sourced from Azure Key Vault (not hardcoded).
- A no-op `NullTelemetrySink` must be provided for use in unit tests and local development where Application Insights is not configured.

**Trade-offs**

- Telemetry adds latency to the pipeline.  Application Insights uses batched, asynchronous flushing; the expected overhead is under 1ms per event on the hot path.
- The `context_id_hash` approach means that correlating telemetry events back to a specific user requires knowing the `context_id` (to compute the hash).  This is intentional: telemetry is not a user lookup table.
- Retaining 90 days of decision logs in Application Insights has a storage cost.  For high-volume deployments, adaptive sampling should be configured to reduce cost while preserving anomaly visibility.
