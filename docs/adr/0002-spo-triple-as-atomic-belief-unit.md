# ADR-0002: Subject-Predicate-Object Triple as Atomic Belief Unit

**Date:** 2024-01-01  
**Status:** Accepted

---

## Context

An LLM memory system must store beliefs that are queryable, auditable, and addressable by identity.  Several representation formats were considered:

| Option | Problem |
|--------|---------|
| Free-text blobs per session | Not queryable; no deduplication; no contradiction tracking |
| Key-value store (topic → value) | Loses relational context; cannot express "Alice prefers dark mode" vs "Bob prefers dark mode" distinctly |
| Full RDF/knowledge graph | Requires ontology management, SPARQL or equivalent, and a graph database; prohibitive operational complexity |
| Embedding vectors only | Opaque; cannot audit beliefs; no deterministic deduplication or contradiction detection |
| Subject-predicate-object triple (chosen) | Human-readable, minimally structured, supports deduplication and contradiction without a full ontology |

A subject-predicate-object (SPO) triple is the smallest unit that can express a directional relational belief.  It is the basis of RDF but does not require the full RDF stack.

---

## Decision

The atomic belief unit is a `MemoryRecord` containing:

- **`subject`**: an `EntityRef` (entity_type + label) — the entity the belief is *about*.
- **`predicate`**: a non-empty string relationship label (e.g. `"prefers"`, `"works_on"`).
- **`object_value`**: either a scalar string or an `EntityRef` — the value of the relationship.

`EntityRef` is a lightweight named reference, not a full graph node.  It carries an `entity_type` (e.g. `"person"`, `"tool"`, `"project"`) and a human-readable `label`.  No ontology or vocabulary constraint is imposed — the platform does not validate that entity types or predicates form a coherent schema.

Each `MemoryRecord` is independently addressable by its UUID `id`, carries its own Bayesian counters, and has a full provenance chain.

---

## Consequences

**Benefits**

- Near-duplicate detection is tractable: compare `(subject.label, predicate, object_value)` case-insensitively without embeddings.
- Contradiction detection is tractable: two records with the same `subject` and `predicate` but different `object_value` are likely contradictory.
- Beliefs are individually addressable and can be retracted, superseded, or contradicted without touching siblings.
- The structure is human-readable, audit-friendly, and serialisable to natural language for prompt injection.

**Obligations**

- Platform code must normalise entity labels to avoid spurious duplicates (e.g. `"Alice"` vs `"alice"`).  The near-duplicate check in `MemoryReviewer` performs case-insensitive comparison; callers are still encouraged to canonicalise labels.
- `object_value` being either a string or an `EntityRef` introduces a union type that all consumers must handle; the reviewer, summariser, and prompt builder all inspect `isinstance(object_value, str)`.

**Trade-offs**

- The triple format does not support n-ary relationships (e.g. "Alice sold Bob a car on Tuesday") without reification.  Complex events must be decomposed into multiple records.
- No vocabulary constraint means predicate labels are not validated for consistency across the corpus; application-level conventions (e.g. always use `"prefers"` not `"likes"`) must be enforced by callers, not by the library.
