# Deployment Architecture — Azure Production Topology

This document describes the recommended Azure production deployment for careful-memory.

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│  Azure Resource Group: careful-memory-prod                           │
│                                                                      │
│  ┌────────────────────────────────────┐                              │
│  │  Azure Container Apps Environment  │                              │
│  │                                    │                              │
│  │  ┌──────────────────────────────┐  │                              │
│  │  │  careful-memory (2+ replicas) │  │                              │
│  │  │  Managed Identity: cm-prod-id │  │                              │
│  │  │  Min replicas: 2              │  │                              │
│  │  │  Max replicas: 10             │  │                              │
│  │  └──────────┬───────────────────┘  │                              │
│  └─────────────┼─────────────────────┘                              │
│                │                                                     │
│     ┌──────────┼──────────────────┐                                 │
│     │          │                  │                                  │
│     ▼          ▼                  ▼                                  │
│  ┌────────┐ ┌─────────────┐  ┌──────────────────────┐              │
│  │ Azure  │ │ Azure Cache │  │ Azure Application     │              │
│  │  SQL   │ │  for Redis  │  │ Insights (monitoring) │              │
│  │ (prod) │ │  (C1/Basic) │  └──────────────────────┘              │
│  └────────┘ └─────────────┘                                         │
│       ▲                                                              │
│  ┌────┴──────────────────────────┐                                   │
│  │  Azure Key Vault              │                                   │
│  │  Secrets: DATABASE_URL,       │                                   │
│  │           REDIS_URL,          │                                   │
│  │           APPINSIGHTS_KEY     │                                   │
│  └───────────────────────────────┘                                   │
└──────────────────────────────────────────────────────────────────────┘
```

## Azure Services

### Azure Container Apps

| Setting | Value |
|---------|-------|
| Environment | Workload profiles (Consumption) |
| Min replicas | 2 (for HA) |
| Max replicas | 10 (auto-scale on CPU/HTTP) |
| Identity | System-assigned Managed Identity |
| Ingress | HTTPS-only, external |
| Scale rule | HTTP concurrent requests > 50 → scale out |

### Azure SQL Database (or PostgreSQL Flexible Server)

| Setting | Value |
|---------|-------|
| SKU | General Purpose, 4 vCores (prod) |
| Backup | Geo-redundant, 7-day retention |
| TLS | Required (TLS 1.2+) |
| Connection | Via private endpoint in VNET |
| Auth | SQL user + password (stored in Key Vault) |

### Azure Cache for Redis

| Setting | Value |
|---------|-------|
| SKU | C1 Basic (or Standard for HA) |
| TLS | Required |
| Persistence | Not required (rate-limit state is ephemeral) |
| Max memory policy | `allkeys-lru` |

See [ADR-0013](../adr/0013-distributed-rate-limiting.md) for the rate-limiting design.

### Azure Key Vault

All secrets are stored in Key Vault and injected at runtime via Managed Identity:

| Secret name | Value |
|------------|-------|
| `DATABASE-URL` | SQLAlchemy connection string (e.g. `mssql+pyodbc://...`) |
| `REDIS-URL` | Redis connection string (e.g. `rediss://:password@host:6380/0`) |
| `APPINSIGHTS-INSTRUMENTATION-KEY` | Azure Application Insights key |

### Azure Application Insights

See [ADR-0016](../adr/0016-observability-telemetry.md) for the observability design.

| Setting | Value |
|---------|-------|
| Retention | 90 days |
| Sampling | Adaptive (default) |
| Alerts | Configured for error rate > 1%, latency > 500ms p95 |

## Network Architecture

```
Internet
   │
   │  HTTPS (443)
   ▼
Azure Front Door (optional, for global load balancing)
   │
   │
   ▼
Azure Container Apps Ingress (HTTPS)
   │
   │  Internal VNET
   ├──► Azure SQL (private endpoint, port 1433)
   ├──► Azure Cache for Redis (private endpoint, port 6380 TLS)
   └──► Azure Key Vault (private endpoint, port 443)
```

## Environment Variables

| Variable | Source | Description |
|----------|--------|-------------|
| `DATABASE_URL` | Key Vault secret `DATABASE-URL` | SQLAlchemy connection string |
| `REDIS_URL` | Key Vault secret `REDIS-URL` | Redis connection string for rate limiting |
| `APPINSIGHTS_INSTRUMENTATIONKEY` | Key Vault secret `APPINSIGHTS-INSTRUMENTATION-KEY` | App Insights telemetry key |
| `MEMORY_RATE_LIMIT_MAX` | App setting | Max evidence events per record per hour (default: 10) |
| `MEMORY_ARCHIVE_THRESHOLD` | App setting | Confidence threshold for archiving (default: 0.30) |

## Managed Identity Configuration

```bash
# Assign Key Vault access to the Container Apps managed identity
az keyvault set-policy \
  --name careful-memory-kv \
  --object-id <managed-identity-principal-id> \
  --secret-permissions get list

# Assign storage access (if using Azure SQL with AAD auth)
az sql server ad-admin set \
  --resource-group careful-memory-prod \
  --server careful-memory-sql \
  --display-name "cm-prod-identity" \
  --object-id <managed-identity-principal-id>
```

## Deployment

### Container Image

```bash
# Build and push to Azure Container Registry
az acr build \
  --registry carefulmemoryprod \
  --image careful-memory:$(git rev-parse --short HEAD) \
  .
```

### Container App Deployment

```bash
az containerapp update \
  --name careful-memory \
  --resource-group careful-memory-prod \
  --image carefulmemorryprod.azurecr.io/careful-memory:<tag> \
  --set-env-vars \
    "DATABASE_URL=secretref:DATABASE-URL" \
    "REDIS_URL=secretref:REDIS-URL" \
    "APPINSIGHTS_INSTRUMENTATIONKEY=secretref:APPINSIGHTS-INSTRUMENTATION-KEY"
```

## Database Migrations

```bash
# Run migrations before updating the container app
# (use a one-time Container Apps job or Azure Container Instance)
az containerapp job run \
  --name careful-memory-migrate \
  --resource-group careful-memory-prod
```

See [production-prerequisites.md](../deployment/production-prerequisites.md) for the full pre-deployment checklist.

## Health Checks

Configure the Container App with:

```yaml
probes:
  liveness:
    httpGet:
      path: /health
      port: 8000
    initialDelaySeconds: 10
    periodSeconds: 30
  readiness:
    httpGet:
      path: /ready
      port: 8000
    initialDelaySeconds: 5
    periodSeconds: 10
```

## Related Documents

- [System Context](system-context.md)
- [Container Diagram](container-diagram.md)
- [Risk Analysis](risk-analysis.md)
- [Production Prerequisites](../deployment/production-prerequisites.md)
- [ADR-0013](../adr/0013-distributed-rate-limiting.md): Distributed Rate Limiting
- [ADR-0014](../adr/0014-context-ownership-validation.md): Context Ownership Validation
- [ADR-0015](../adr/0015-optimistic-locking.md): Optimistic Locking
- [ADR-0016](../adr/0016-observability-telemetry.md): Observability & Telemetry
- [ADR-0017](../adr/0017-sqlalchemy-store.md): SqlAlchemy Production Storage
