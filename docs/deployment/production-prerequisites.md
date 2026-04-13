# Production Prerequisites Checklist

This checklist must be completed before deploying careful-memory to a production Azure environment.

## 1. Azure Infrastructure

### 1.1 Resource Group

- [ ] Create resource group: `careful-memory-prod` (or equivalent naming convention)
- [ ] Apply mandatory tags: `environment=prod`, `service=careful-memory`, `owner=<team>`

### 1.2 Azure Container Registry

- [ ] Provision Azure Container Registry (ACR): `carefulmemorryprod`
- [ ] Enable admin user or configure managed identity pull access
- [ ] Configure geo-replication if multi-region deployment is required

### 1.3 Azure SQL Database (or PostgreSQL Flexible Server)

- [ ] Provision Azure SQL Database (General Purpose, 4 vCores minimum)  
  _or_ Azure PostgreSQL Flexible Server (Standard D4s_v3 minimum)
- [ ] Enable Azure AD authentication
- [ ] Create least-privilege database user (SELECT, INSERT, UPDATE on `memory_records` and `memory_contexts`; no DDL)
- [ ] Enable TLS (TLS 1.2 minimum); disable plain-text connections
- [ ] Configure private endpoint in production VNET
- [ ] Enable geo-redundant backup with 7-day retention
- [ ] Note the connection string (to be stored in Key Vault, not committed)

### 1.4 Azure Cache for Redis

- [ ] Provision Azure Cache for Redis (C1 Basic minimum; Standard for HA)
- [ ] Enable TLS; note the TLS port (6380)
- [ ] Configure private endpoint in production VNET
- [ ] Set `maxmemory-policy` to `allkeys-lru`
- [ ] Note the connection string with password (to be stored in Key Vault)

### 1.5 Azure Key Vault

- [ ] Provision Azure Key Vault: `careful-memory-kv`
- [ ] Configure private endpoint in production VNET
- [ ] Store the following secrets:

  | Secret name | Value |
  |------------|-------|
  | `DATABASE-URL` | Full SQLAlchemy connection string (e.g. `mssql+pyodbc://user:pass@server/db?driver=...`) |
  | `REDIS-URL` | Redis TLS connection string (e.g. `rediss://:password@host:6380/0`) |
  | `APPINSIGHTS-INSTRUMENTATION-KEY` | Application Insights instrumentation key |

### 1.6 Azure Application Insights

- [ ] Provision Application Insights workspace
- [ ] Configure retention to 90 days
- [ ] Note the instrumentation key (store in Key Vault)
- [ ] Create alert rules:
  - [ ] Error rate > 1% over 5 minutes
  - [ ] p95 pipeline latency > 500ms
  - [ ] `cm.rate_limit.hits` spike (threshold TBD)

### 1.7 Azure Container Apps

- [ ] Provision Container Apps Environment (Workload Profiles — Consumption)
- [ ] Configure VNET integration (same VNET as SQL, Redis, Key Vault private endpoints)
- [ ] Create system-assigned Managed Identity for the Container App
- [ ] Grant Managed Identity `Key Vault Secrets User` role on `careful-memory-kv`
- [ ] Configure ingress: HTTPS-only, external
- [ ] Configure scale rules: min 2 replicas, max 10 replicas, scale on HTTP concurrency > 50

## 2. Security

### 2.1 Authentication

- [ ] Configure Azure AD application registration for careful-memory API
- [ ] Note the `client_id` and `tenant_id` for token validation
- [ ] Configure allowed token audiences
- [ ] Implement bearer token validation middleware (see [ADR-0014](../adr/0014-context-ownership-validation.md))

### 2.2 Network Security

- [ ] All services (SQL, Redis, Key Vault) use private endpoints; no public internet access
- [ ] Container Apps ingress restricted to expected caller IP ranges (if applicable)
- [ ] Redis TLS enforced (`rediss://` scheme in `REDIS_URL`)
- [ ] SQL TLS enforced (TLS 1.2+)

### 2.3 Secrets Management

- [ ] No secrets in source code, container images, or environment variable plain text
- [ ] All secrets sourced from Key Vault via Managed Identity
- [ ] Secret rotation procedure documented and tested

## 3. Database

### 3.1 Schema Migration

- [ ] Run Alembic migrations against the production database **before** deploying the new application version
- [ ] Verify migration ran successfully: `alembic current` should show the expected revision
- [ ] Take a database backup immediately before running migrations

### 3.2 Schema Validation

- [ ] `memory_records` table exists with all required columns including `version` (for ADR-0015)
- [ ] `memory_contexts` table exists (for ADR-0014 context ownership)
- [ ] Index `idx_context_status` exists on `(context_id, status)`
- [ ] Index `idx_user_id` exists on `memory_contexts(user_id)`

## 4. Application Configuration

### 4.1 Environment Variables

Verify all required environment variables are set via Key Vault secret references:

| Variable | Source | Required |
|----------|--------|----------|
| `DATABASE_URL` | Key Vault `DATABASE-URL` | ✅ Required |
| `REDIS_URL` | Key Vault `REDIS-URL` | ✅ Required (multi-replica) |
| `APPINSIGHTS_INSTRUMENTATIONKEY` | Key Vault `APPINSIGHTS-INSTRUMENTATION-KEY` | ✅ Required |
| `MEMORY_RATE_LIMIT_MAX` | App setting | Optional (default: 10) |
| `MEMORY_ARCHIVE_THRESHOLD` | App setting | Optional (default: 0.30) |

### 4.2 Startup Validation

Verify the application logs the following at startup (no errors):

- [ ] Database connection established and pool initialised
- [ ] Redis connection established
- [ ] Application Insights telemetry client initialised
- [ ] Rate limiter type: `RedisRateLimiter` (not `_RateLimitWindow`)

## 5. Observability

- [ ] Application Insights dashboard created with key metrics (see [ADR-0016](../adr/0016-observability-telemetry.md))
- [ ] Alert rules configured and tested (fire a test alert to verify notifications)
- [ ] Log query verified: confirm `PipelineDecisionEvent` appears in Application Insights logs
- [ ] Distributed trace verified: a single API request trace is visible end-to-end in Application Insights

## 6. Load and Resilience Testing

- [ ] Load test at 10× expected peak RPS; confirm p95 latency < 200ms
- [ ] Redis failover test: stop Redis; confirm API returns appropriate error (not silent data corruption)
- [ ] Database failover test: confirm reconnection after database restart
- [ ] Optimistic lock contention test: concurrent writes to the same record; confirm retry succeeds

## 7. Documentation

- [ ] Runbook created for common incidents (rate-limit override, context deletion, bulk data export)
- [ ] On-call escalation path documented
- [ ] Architecture documentation updated to reflect production topology ([deployment-architecture.md](../architecture/deployment-architecture.md))

## 8. Final Sign-Off

- [ ] Security review completed
- [ ] Load test results reviewed and accepted
- [ ] Runbook reviewed by on-call team
- [ ] Deployment approved by service owner

---

## Related Documents

- [Deployment Architecture](../architecture/deployment-architecture.md)
- [Risk Analysis](../architecture/risk-analysis.md)
- [ADR-0013](../adr/0013-distributed-rate-limiting.md): Distributed Rate Limiting
- [ADR-0014](../adr/0014-context-ownership-validation.md): Context Ownership Validation
- [ADR-0015](../adr/0015-optimistic-locking.md): Optimistic Locking
- [ADR-0016](../adr/0016-observability-telemetry.md): Observability & Telemetry
- [ADR-0017](../adr/0017-sqlalchemy-store.md): SqlAlchemy Production Storage
