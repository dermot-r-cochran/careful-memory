# C1: System Context Diagram

This diagram shows careful-memory within its broader operational environment — the external actors and systems it interacts with.

## Diagram

```mermaid
C4Context
  title System Context — careful-memory

  Person(agent_developer, "Agent Developer", "Builds LLM-based applications\nthat use careful-memory as a memory service")
  Person(end_user, "End User", "Interacts with the LLM agent;\ntheir preferences and beliefs are stored")
  Person(platform_operator, "Platform Operator", "Deploys, configures, and monitors\nthe careful-memory service")

  System(careful_memory, "careful-memory", "Production-grade long-term memory\nfor LLM agents. Bayesian confidence\ntracking with write-gated safety pipeline.")

  System_Ext(llm_agent, "LLM Agent", "Language model agent (e.g. GPT-4,\nClaude) that proposes beliefs and\nqueries memory at inference time")
  System_Ext(azure_sql, "Azure SQL / PostgreSQL", "Relational database storing\nmemory records, belief history,\nand context metadata")
  System_Ext(azure_redis, "Azure Cache for Redis", "Distributed rate-limiting store\nfor multi-replica deployments")
  System_Ext(azure_ad, "Azure Active Directory", "Identity provider — supplies\nverified user_id (object ID)\nfor context isolation")
  System_Ext(azure_keyvault, "Azure Key Vault", "Secrets management — stores\nDATABASE_URL, Redis connection\nstrings, and API keys")
  System_Ext(azure_appinsights, "Azure Application Insights", "Observability — telemetry,\ntraces, metrics, and alerts\nfor production monitoring")

  Rel(end_user, llm_agent, "Interacts with", "HTTPS / chat")
  Rel(llm_agent, careful_memory, "Calls tools via", "propose_belief / report_evidence / query_beliefs")
  Rel(careful_memory, llm_agent, "Returns grounded context", "MemorySummary → system prompt")
  Rel(agent_developer, careful_memory, "Integrates", "Python SDK / REST API")
  Rel(platform_operator, careful_memory, "Configures & monitors", "Azure Portal / CLI")
  Rel(careful_memory, azure_sql, "Persists beliefs", "SQLAlchemy / ODBC")
  Rel(careful_memory, azure_redis, "Shares rate-limit state", "redis-py")
  Rel(careful_memory, azure_ad, "Validates identity", "OAuth2 / MSAL")
  Rel(careful_memory, azure_keyvault, "Reads secrets at startup", "Managed Identity")
  Rel(careful_memory, azure_appinsights, "Emits telemetry", "OpenCensus / OTLP")
```

## Key Relationships

### LLM Agent ↔ careful-memory

The agent interacts exclusively through a **3-tool API**:

| Tool | Direction | Purpose |
|------|-----------|---------|
| `propose_belief` | Agent → Memory | Propose a new Subject-Predicate-Object belief |
| `report_evidence` | Agent → Memory | Report supporting or contradicting evidence |
| `query_beliefs` | Agent → Memory | Retrieve confidence-weighted memory summary |

The agent **cannot** directly write to the database, modify confidence values, or access another user's context.

### Platform Operator

The operator configures the service through environment variables (sourced from Azure Key Vault) and monitors it through Azure Application Insights. The operator never directly interacts with individual memory records.

### End User

The end user's identity (Azure AD object ID) is the root of context isolation. The user never interacts with careful-memory directly — only through their LLM agent session.

## Security Boundary

The careful-memory service is the **sole trust boundary** between LLM agents and the persistent memory store. All agent requests are subject to:

1. **Authentication** — bearer token validated against Azure AD (ADR-0014)
2. **Rate limiting** — distributed counter in Redis (ADR-0013)
3. **Write-gate rules** — authority, context isolation, outlier detection
4. **Reviewer judgment** — duplication, mass contradiction, policy

No agent request can bypass any of these layers.

## Related Documents

- [C2: Container Diagram](container-diagram.md)
- [ADR-0004](../adr/0004-context-isolation-via-contextscope.md): Context Isolation via ContextScope
- [ADR-0013](../adr/0013-distributed-rate-limiting.md): Distributed Rate Limiting
- [ADR-0014](../adr/0014-context-ownership-validation.md): Context Ownership Validation
