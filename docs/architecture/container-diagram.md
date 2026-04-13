# C2: Container Diagram

This diagram shows the high-level containers (deployable units) within the careful-memory system and their primary technology choices.

## Diagram

```mermaid
C4Container
  title Container Diagram — careful-memory

  Person(llm_agent, "LLM Agent", "Calls the 3-tool API to propose beliefs,\nreport evidence, and query memory")
  Person(platform_operator, "Platform Operator", "Deploys and configures the service")

  System_Boundary(careful_memory_system, "careful-memory") {

    Container(api_layer, "API / ToolDispatcher", "Python (FastAPI or SDK entry point)",
      "Authenticates requests, validates context ownership,\nenforces the 3-tool contract, dispatches to MemoryService")

    Container(memory_service, "MemoryService", "Python",
      "Orchestrates the write pipeline and read path.\nBuilds memory summaries and prompts.\nCoordinates gate, reviewer, and store.")

    Container(write_pipeline, "Write Pipeline", "Python",
      "MetaGate → WriteGate → MemoryReviewer.\nThree-stage validation before any write is committed.")

    Container(prompt_builder, "PromptBuilder", "Python",
      "Assembles inference-time system prompts from\nconfidence-weighted, decay-adjusted memory summaries.")

    Container(storage_adapter, "MemoryStore", "Python ABC + implementation",
      "Storage abstraction. SQLiteMemoryStore for dev/test.\nSqlAlchemyStore for production.")

    Container(rate_limiter, "RateLimiter", "Python (in-process or Redis-backed)",
      "Sliding-window rate limit per (context_id, record_id).\nIn-process for single instance; Redis for multi-replica.")

    Container(observability, "ObservabilityAdapter", "Python + Azure App Insights SDK",
      "Emits structured traces, decision logs, and metrics\nfor every write pipeline execution and query.")
  }

  System_Ext(azure_sql, "Azure SQL / PostgreSQL", "Relational database\n(production persistence)")
  System_Ext(azure_redis, "Azure Cache for Redis", "Distributed rate-limit\nand cache store")
  System_Ext(azure_ad, "Azure Active Directory", "Identity & token validation")
  System_Ext(azure_appinsights, "Azure Application Insights", "Telemetry sink")

  Rel(llm_agent, api_layer, "Calls tools", "HTTPS JSON / Python function call")
  Rel(platform_operator, api_layer, "Manages contexts", "Admin API / CLI")

  Rel(api_layer, memory_service, "Dispatches validated calls")
  Rel(api_layer, azure_ad, "Validates bearer token", "MSAL / OAuth2")

  Rel(memory_service, write_pipeline, "Routes writes through")
  Rel(memory_service, prompt_builder, "Requests prompt assembly")
  Rel(memory_service, storage_adapter, "Reads and writes records")

  Rel(write_pipeline, rate_limiter, "Checks rate limits")
  Rel(write_pipeline, storage_adapter, "Reads existing records for duplicate/contradiction checks")

  Rel(storage_adapter, azure_sql, "Persists records", "SQLAlchemy / ODBC")
  Rel(rate_limiter, azure_redis, "Increments counters", "redis-py INCR / EXPIRE")
  Rel(observability, azure_appinsights, "Flushes telemetry", "OpenCensus / OTLP")

  Rel(memory_service, observability, "Emits decision events")
  Rel(write_pipeline, observability, "Emits gate/reviewer decisions")
  Rel(api_layer, observability, "Emits request traces")
```

## Container Responsibilities

### API / ToolDispatcher

The sole entry point for agent interactions. Responsibilities:

- Validate authentication (bearer token → Azure AD)
- Validate `context_id` ownership (caller may only access their own contexts)
- Enforce the 3-tool contract (no other operations exposed to agents)
- Forward validated calls to `MemoryService`

See [ADR-0014](../adr/0014-context-ownership-validation.md) for the ownership validation design.

### MemoryService

The central orchestrator. Responsibilities:

- Coordinate the write pipeline stages in order
- Build memory summaries (`MemorySummary`) for the prompt builder
- Expose `assemble_prompt()` for inference-time use
- Never expose raw `MemoryRecord` objects to agent callers

### Write Pipeline (MetaGate → WriteGate → MemoryReviewer)

A three-stage validation pipeline (see [ADR-0003](../adr/0003-three-stage-write-pipeline.md)):

| Stage | Type | Checks |
|-------|------|--------|
| MetaGate | Pure function | Input shape, memory type, authority level |
| WriteGate | Stateful | Rate limits, context isolation, confidence outlier |
| MemoryReviewer | Stateful + reasoned | Near-duplicates, mass contradiction, policy |

### MemoryStore

Storage abstraction (see [ADR-0010](../adr/0010-storage-abstraction-sqlite-default.md) and [ADR-0017](../adr/0017-sqlalchemy-store.md)):

| Implementation | Use case |
|---------------|---------|
| `SQLiteMemoryStore` | Development, tests, single-instance staging |
| `SqlAlchemyStore` | Production (Azure SQL, PostgreSQL) |

### RateLimiter

Sliding-window counter per `(context_id, record_id)` pair (see [ADR-0013](../adr/0013-distributed-rate-limiting.md)):

| Implementation | Use case |
|---------------|---------|
| `_RateLimitWindow` (in-process dict) | Single-instance deployments |
| `RedisRateLimiter` | Multi-replica production |

### PromptBuilder

Converts `MemorySummary` into a confidence-weighted, safety-annotated system prompt block. Pure function; no I/O (see [ADR-0011](../adr/0011-inference-time-prompt-injection.md)).

### ObservabilityAdapter

Emits structured events for all gate/reviewer decisions, writes, and queries. See [ADR-0016](../adr/0016-observability-telemetry.md).

## Related Documents

- [C1: System Context](system-context.md)
- [C3: Component Diagrams](component-diagrams.md)
- [Deployment Architecture](deployment-architecture.md)
