# Architecture Documentation

This directory contains the C4 model architecture documentation for careful-memory.

## Overview

careful-memory is a production-grade long-term memory system for LLM agents with Bayesian confidence tracking, designed to operate in multi-tenant Azure environments.

## Documentation Structure

### C4 Model Diagrams

The architecture follows the [C4 model](https://c4model.com/) with four levels of detail:

- **[C1: System Context](system-context.md)** — careful-memory in its operational environment
- **[C2: Container Diagram](container-diagram.md)** — High-level technology choices and container responsibilities
- **[C3: Component Diagrams](component-diagrams.md)** — Internal component structure and dependencies
- **[C4: Code Diagrams](../adr/README.md)** — Class-level design (documented in ADRs)

### Supporting Documentation

- **[Deployment Architecture](deployment-architecture.md)** — Azure production topology and configuration
- **[Risk Analysis](risk-analysis.md)** — Architectural risks, mitigations, and roadmap

## Key Architectural Principles

1. **Security-First**: Multi-layered defense with authentication at API boundary
2. **Write-Gated**: 3-stage validation pipeline (MetaGate → WriteGate → MemoryReviewer)
3. **Context Isolation**: Per-user, per-domain memory boundaries enforced at every layer
4. **Evidence-Derived Confidence**: Bayesian Beta(α,β) model, never directly asserted
5. **Append-Only**: Immutable belief history with status lifecycle
6. **Inference-Time Grounding**: Memory injected into prompts, not model weights

## Architecture Decision Records

All significant architectural decisions are documented in [ADRs](../adr/README.md).

**Production readiness ADRs:**
- [ADR-0013](../adr/0013-distributed-rate-limiting.md): Distributed Rate Limiting (Redis)
- [ADR-0014](../adr/0014-context-ownership-validation.md): Context Ownership Validation
- [ADR-0015](../adr/0015-optimistic-locking.md): Optimistic Locking for Concurrency
- [ADR-0016](../adr/0016-observability-telemetry.md): Observability & Telemetry
- [ADR-0017](../adr/0017-sqlalchemy-store.md): SqlAlchemy Production Storage

## Quick Links

- [System Context Diagram](system-context.md) — Start here for high-level overview
- [Production Deployment Guide](deployment-architecture.md) — Azure setup instructions
- [Risk Analysis](risk-analysis.md) — Known risks and mitigation status
- [ADR Index](../adr/README.md) — All architectural decisions
