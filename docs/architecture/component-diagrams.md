# C3: Component Diagrams

These diagrams show the internal structure of the key containers within careful-memory.

## 1. Write Pipeline Components

```mermaid
C4Component
  title C3 — Write Pipeline (MetaGate → WriteGate → MemoryReviewer)

  Container_Boundary(write_pipeline, "Write Pipeline") {

    Component(meta_gate, "MetaGate", "Pure function (core/meta_gate.py)",
      "Validates input shape, memory type enum,\nauthority level, and subject/predicate/object constraints.\nNo I/O. Returns MetaAssessment.")

    Component(write_gate, "WriteGate", "Stateful class (core/gate.py)",
      "Enforces hard rules:\n- Authority level hierarchy\n- Rate limit per (context_id, record_id)\n- Context isolation check\n- Confidence outlier rejection\nReturns GateDecision (approve/reject).")

    Component(rate_limit_window, "_RateLimitWindow", "In-process dict (core/gate.py)",
      "Sliding-window counter for rate limiting.\nProduction: replace with RedisRateLimiter (ADR-0013).")

    Component(memory_reviewer, "MemoryReviewer", "Stateful class (core/reviewer.py)",
      "Applies reasoned checks:\n- Near-duplicate detection\n- Mass contradiction check (>25% active records)\n- Direct high-confidence semantic assertion\n- Context policy enforcement\nReturns ReviewDecision (approve/modify/reject).")
  }

  Container_Ext(memory_service, "MemoryService", "Calls pipeline in order")
  Container_Ext(storage_adapter, "MemoryStore", "Provides existing records for checks")

  Rel(memory_service, meta_gate, "1. Calls first (pure)")
  Rel(meta_gate, write_gate, "2. Passes MetaAssessment to")
  Rel(write_gate, rate_limit_window, "Checks/increments window")
  Rel(write_gate, memory_reviewer, "3. Passes approved writes to")
  Rel(memory_reviewer, storage_adapter, "Reads active records for checks")
  Rel(memory_reviewer, memory_service, "Returns ReviewDecision")
```

## 2. Storage Components

```mermaid
C4Component
  title C3 — Storage Layer

  Container_Boundary(storage_layer, "Storage Layer") {

    Component(memory_store_abc, "MemoryStore ABC", "Abstract base class (storage/base.py)",
      "Defines the storage contract:\nsave(record) / get(record_id, context_id)\nlist_active(context_id) / update_status(record_id, context_id, status)\nAll methods require context_id for isolation.")

    Component(sqlite_store, "SQLiteMemoryStore", "Concrete class (storage/sqlite.py)",
      "In-process SQLite store.\nZero extra dependencies.\nUse for dev, tests, single-instance staging.")

    Component(sqlalchemy_store, "SqlAlchemyStore", "Extension point (storage/sqlalchemy_store.py)",
      "Production relational store.\nSupports Azure SQL, PostgreSQL, any SQLAlchemy backend.\nIncludes version field for optimistic locking (ADR-0015).\nConnection string from Azure Key Vault.")
  }

  Container_Ext(write_pipeline, "Write Pipeline", "Calls save/update")
  Container_Ext(memory_service, "MemoryService", "Calls list_active/get")
  System_Ext(azure_sql, "Azure SQL / PostgreSQL", "Production database")

  Rel(write_pipeline, memory_store_abc, "Saves records via")
  Rel(memory_service, memory_store_abc, "Queries records via")
  Rel(memory_store_abc, sqlite_store, "Implemented by (dev/test)")
  Rel(memory_store_abc, sqlalchemy_store, "Implemented by (production)")
  Rel(sqlalchemy_store, azure_sql, "Persists to")
```

## 3. MemoryService & PromptBuilder Components

```mermaid
C4Component
  title C3 — MemoryService and PromptBuilder

  Container_Boundary(memory_svc, "MemoryService") {

    Component(context_manager, "Context Manager", "Methods on MemoryService",
      "create_context(scope) / list_contexts(user_id)\nValidates scope uniqueness.\nRegisters context_id → scope mapping.")

    Component(belief_writer, "Belief Writer", "Methods on MemoryService",
      "propose_belief() — routes through write pipeline.\nreport_evidence() — updates alpha/beta counters.\nApplies Bayesian update (ADR-0001) after gate approval.")

    Component(belief_reader, "Belief Reader", "Methods on MemoryService",
      "query_beliefs() — list_active() filtered by confidence threshold.\nbuild_summary() — applies decay, sorts by confidence, builds MemorySummary.")

    Component(prompt_builder, "PromptBuilder", "Pure function (prompt/builder.py)",
      "build(summary, user_task) — produces AssembledPrompt.\nFormats beliefs as confidence-weighted natural language.\nInjects safety disclaimer.\nNever reads from storage directly.")
  }

  Container_Ext(api_layer, "API / ToolDispatcher", "Calls service methods")
  Container_Ext(write_pipeline, "Write Pipeline", "Invoked by Belief Writer")
  Container_Ext(storage, "MemoryStore", "Read/write by Context Manager, Belief Writer/Reader")

  Rel(api_layer, context_manager, "create_context / list_contexts")
  Rel(api_layer, belief_writer, "propose_belief / report_evidence")
  Rel(api_layer, belief_reader, "query_beliefs")
  Rel(belief_writer, write_pipeline, "Routes writes through")
  Rel(belief_reader, prompt_builder, "Passes MemorySummary to")
  Rel(context_manager, storage, "Persists context metadata")
  Rel(belief_writer, storage, "Saves approved records")
  Rel(belief_reader, storage, "Reads active records")
```

## 4. Observability Components

```mermaid
C4Component
  title C3 — Observability Layer (ADR-0016)

  Container_Boundary(obs_layer, "ObservabilityAdapter") {

    Component(decision_logger, "DecisionLogger", "Component of ObservabilityAdapter",
      "Logs every gate and reviewer decision with:\n- context_id (hashed, not raw UUID)\n- decision outcome\n- latency_ms\n- timestamp\nCorrelates with trace_id.")

    Component(metrics_emitter, "MetricsEmitter", "Component of ObservabilityAdapter",
      "Emits counters and histograms:\n- writes_approved / writes_rejected\n- rate_limit_hits\n- confidence distribution\n- pipeline latency")

    Component(trace_propagator, "TracePropagator", "Component of ObservabilityAdapter",
      "Propagates W3C TraceContext headers.\nCorrelates agent request → pipeline stages → storage.")
  }

  Container_Ext(write_pipeline, "Write Pipeline", "Emits decision events")
  Container_Ext(memory_service, "MemoryService", "Emits query events")
  Container_Ext(api_layer, "API Layer", "Emits request traces")
  System_Ext(azure_appinsights, "Azure Application Insights", "Telemetry sink")

  Rel(write_pipeline, decision_logger, "Emits gate/reviewer decision")
  Rel(memory_service, metrics_emitter, "Emits query metrics")
  Rel(api_layer, trace_propagator, "Injects trace context")
  Rel(decision_logger, azure_appinsights, "Flushes")
  Rel(metrics_emitter, azure_appinsights, "Flushes")
  Rel(trace_propagator, azure_appinsights, "Flushes")
```

## Component Interaction Summary

The following sequence shows the full path of a `propose_belief` call:

```
LLM Agent
  │
  │  POST /tools/propose_belief  (bearer token, context_id, SPO)
  ▼
API / ToolDispatcher
  │  1. Validate bearer token → Azure AD
  │  2. Validate context_id ownership (ADR-0014)
  │  3. Check rate limit → Redis (ADR-0013)
  ▼
MemoryService.propose_belief()
  │
  ├─ MetaGate.assess()          [pure: shape, types, authority]
  │    └── MetaAssessment
  │
  ├─ WriteGate.evaluate()       [stateful: rate limit, context, outlier]
  │    └── GateDecision
  │
  ├─ MemoryReviewer.review()    [stateful+reasoned: duplicates, mass contradiction]
  │    └── ReviewDecision
  │
  ├─ Storage.save(record)       [optimistic lock check, ADR-0015]
  │
  └─ ObservabilityAdapter.log_decision()   [structured telemetry, ADR-0016]
       └── Azure Application Insights
```

## Related Documents

- [C2: Container Diagram](container-diagram.md)
- [ADR-0001](../adr/0001-bayesian-beta-confidence-model.md): Bayesian confidence model
- [ADR-0003](../adr/0003-three-stage-write-pipeline.md): Three-stage write pipeline
- [ADR-0010](../adr/0010-storage-abstraction-sqlite-default.md): Storage abstraction
- [ADR-0013](../adr/0013-distributed-rate-limiting.md): Distributed rate limiting
- [ADR-0014](../adr/0014-context-ownership-validation.md): Context ownership validation
- [ADR-0015](../adr/0015-optimistic-locking.md): Optimistic locking
- [ADR-0016](../adr/0016-observability-telemetry.md): Observability & telemetry
- [ADR-0017](../adr/0017-sqlalchemy-store.md): SqlAlchemy production storage
