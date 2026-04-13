# ADR-0009: Pydantic v2 as Domain Model Foundation

**Date:** 2024-01-01  
**Status:** Accepted

---

## Context

The domain models (`MemoryRecord`, `ContextScope`, `EvidenceEvent`, `MemorySummary`) need:

1. Validated construction — callers must not be able to create an invalid record.
2. Immutability for value objects, controlled mutability for lifecycle fields.
3. Serialisation to and from JSON for storage and API boundaries.
4. Computed/derived properties (e.g. `confidence` derived from `alpha` and `beta`).
5. Typed field defaults and validators.

Alternatives considered:

| Option | Problem |
|--------|---------|
| Plain `dataclasses` | No built-in validation; JSON serialisation requires extra work |
| `attrs` | Validation is less ergonomic; smaller ecosystem |
| SQLAlchemy ORM models only | Couples domain logic to database schema; hard to test in isolation |
| Pydantic v1 | Superseded; v2 is substantially faster and has cleaner validator syntax |
| Pydantic v2 (chosen) | First-class validation, `@computed_field`, `model_copy`, frozen config, wide ecosystem |

---

## Decision

All domain models are **Pydantic v2 `BaseModel` subclasses**.

Key conventions:

- **Immutable value objects** (`ContextScope`, `EntityRef`, `MemorySource`, `EvidenceEvent`, `MemorySummary`, `MetaAssessment`) use `model_config = {"frozen": True}`.
- **Mutable lifecycle objects** (`MemoryRecord`) use `model_config = {"frozen": False}` with a documented rationale — only `status`, Bayesian counters, and timestamps are mutated, and only by controlled platform code paths.
- **Derived fields** (e.g. `confidence`) use `@computed_field` + `@property` so they are included in serialisation but never accepted as input.
- **Cross-field invariants** (e.g. a record may not both contradict and supersede the same target) are enforced via `@model_validator(mode="after")`.
- **Field invariants** (e.g. `alpha >= 1.0`, `decay_rate in (0, 1]`) are enforced via `@field_validator` or `Field(ge=...)`.

---

## Consequences

**Benefits**

- Invalid objects cannot be constructed; all invariants are checked at creation time.
- `model_copy(update={...})` enables immutable-style updates that return new instances, used throughout the decay module.
- `.model_dump()` / `.model_validate()` provide consistent JSON round-tripping for storage.
- `@computed_field` ensures `confidence` is always present in serialised output without ever being writable.
- The Pydantic v2 `strict` mypy plugin (enabled in `pyproject.toml`) provides static type-checking of model fields.

**Obligations**

- Pydantic v2 is a runtime dependency; it must not be removed without replacing all validation logic.
- `@computed_field` requires the `pydantic>=2.7` constraint; this must be maintained in `pyproject.toml`.
- Any new domain model must follow the `frozen=True` / `frozen=False` + documented rationale convention established here.

**Trade-offs**

- Pydantic v2 models are heavier than plain dataclasses.  For a memory system that processes at most thousands of records per request (not millions), this overhead is acceptable.
- The union type `object_value: str | EntityRef` is supported by Pydantic v2's discriminated union machinery but requires explicit `isinstance` checks in code that processes records.  A future ADR could consider introducing a wrapper type.
