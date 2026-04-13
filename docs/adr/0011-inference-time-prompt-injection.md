# ADR-0011: Inference-Time Prompt Injection Instead of Model Training

**Date:** 2024-01-01  
**Status:** Accepted

---

## Context

Personalising an LLM to a user's long-term preferences and history can be done in several ways:

| Approach | Problem |
|----------|---------|
| Fine-tune the model on user data | Catastrophic forgetting; prohibitive cost per user; cannot "un-learn" a retracted belief; violates data isolation between users |
| LoRA / adapter per user | Still requires retraining; adapters grow with users; latency at serving time |
| Retrieval-augmented generation (RAG) with raw chunks | Chunks lack confidence weighting; outdated or contradicted chunks may still appear |
| Confidence-weighted prompt injection (chosen) | No training; instant update when beliefs change; confidence is explicit in the prompt; beliefs can be retracted immediately |

The core insight is that an LLM does not need to *know* a user's preferences — it needs to be *told* them at inference time, grounded in evidence.

---

## Decision

Memory influences LLM behaviour exclusively through **inference-time prompt injection**, never through model weight updates.

At inference time, `MemoryService.assemble_prompt()` (via `PromptBuilder`) constructs a system prompt block that:

1. Lists active memories above the confidence threshold, rendered as natural-language confidence hedges:
   - `confidence ≥ 0.85` → "It is very likely that …"
   - `confidence ≥ 0.70` → "It is probably that …"
   - `confidence ≥ 0.55` → "It is possibly that …"
   - `confidence < 0.55` → omitted (below the inclusion threshold)
2. Instructs the model explicitly: *"Ground all personalization and long-term assumptions ONLY in the provided memory context."*
3. When no memories are available, injects: *"No reliable memories are available…"* — explicitly preventing the model from inventing context.

**INVARIANT**: The output of prompt assembly is **never** fed back into the belief store.  `MemorySummary` is a read-only derived artifact; it is never treated as evidence.

---

## Consequences

**Benefits**

- Beliefs can be retracted, updated, or archived immediately; the change is reflected in the next inference call with no retraining lag.
- Memory is fully auditable: every belief injected into a prompt is a `MemoryRecord` with a confidence value, provenance, and evidence history.
- No per-user model weights, adapters, or fine-tuning infrastructure.
- The prompt block is self-describing; the model is told it is working from "derived, confidence-weighted" memory, not authoritative fact.
- Works with any LLM API (OpenAI, Azure OpenAI, Anthropic, open-source) — no vendor-specific training pipeline.

**Obligations**

- `PromptBuilder` must never include records with `status != active` or confidence below the threshold.
- Decay must be applied before building the prompt (see ADR-0005); including stale Bayesian values produces inflated confidence in the prompt.
- Platform code that calls `assemble_prompt` must not pass the returned `AssembledPrompt.system_prompt` back to any function that writes to the memory store.
- The confidence-to-hedge mapping must be kept consistent; changing hedge wording could mislead the model about uncertainty.

**Trade-offs**

- Long-term memory is bounded by the context window.  For users with many memories, the summariser (`core/summarizer.py`) must rank and prune the injected block.  The current implementation injects all active records above the threshold; a production deployment with many users may require embedding-based selection (the `MemorySummary.embedding_stub` field is the planned extension point).
- The model is instructed not to invent context, but instruction-following is probabilistic.  The memory layer provides the grounding signal; it cannot guarantee the model respects it.
