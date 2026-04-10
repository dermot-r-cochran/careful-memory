# ADR-0006: Exclusion of LLM Inference from Evidence Types (No Self-Reinforcement)

**Date:** 2024-01-01  
**Status:** Accepted

---

## Context

One of the most dangerous failure modes for an LLM memory system is a **self-reinforcement loop**: the model reads its own memories in a prompt, generates a response that references those memories, and that response is then fed back as new evidence to reinforce the same memories.  Over time, high-confidence but incorrect beliefs become entrenched because the model keeps "re-observing" its own outputs.

This is analogous to a human who gains false confidence by repeating their own beliefs to themselves, never consulting external reality.

---

## Decision

`EvidenceType` is an exhaustive enum that **intentionally omits** any value for LLM inference or model output:

```python
class EvidenceType(StrEnum):
    user_restatement        = "user_restatement"
    user_action             = "user_action"
    verified_system_outcome = "verified_system_outcome"
    # NOTE: intentionally no "llm_inference" here
```

The WriteGate validates that every `EvidenceEvent` carries one of these three values.  Any other value — including any future value that might be added naively — is rejected with the explicit reason: *"LLM inference alone cannot reinforce memory."*

Additionally, the MetaGate's `_signal_evidence_type` function returns `LOW` (gate-blocking) for any `MemoryRecord` whose `source.evidence_type` is `None`, which would be required if a caller attempted to propose a belief without documenting grounds.

---

## Consequences

**Benefits**

- The self-reinforcement loop is structurally impossible, not just discouraged by convention.  There is no code path through which an LLM's output can increment `α` on a memory record.
- The invariant is enforced at the type level (enum exhaustiveness) and at the gate level (runtime check), providing defence in depth.
- Auditors inspecting evidence history can rely on every `EvidenceEvent` having a real-world grounding: a user action, a user statement, or a verified system outcome.

**Obligations**

- Any future addition to `EvidenceType` must be reviewed against this invariant.  A value representing model output or derived inference must never be added.
- Integration code that processes LLM responses must not automatically call `report_evidence` after generating a response, even if the response appears to confirm an existing belief.
- The prompt builder's `INVARIANT` comment must be preserved: *"The output of prompt assembly is never fed back into the belief store."*

**Trade-offs**

- Verified system outcomes (e.g. the model correctly answered a factual question that can be cross-checked) are out of scope unless an external verifier produces an `EvidenceType.verified_system_outcome` event.  There is no lightweight mechanism for a model to self-verify.
- The three permitted evidence types are coarse.  Applications with more nuanced evidence provenance (e.g. sensor readings, database lookups) must map to one of these values or extend the enum — with care not to introduce self-reinforcement.
