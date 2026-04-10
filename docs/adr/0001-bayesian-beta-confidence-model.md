# ADR-0001: Beta(α,β) Bayesian Confidence Model

**Date:** 2024-01-01  
**Status:** Accepted

---

## Context

Long-term memory for LLM agents must express graded belief — a fact heard once should be treated very differently from one observed dozens of times across multiple sessions.  A scalar `confidence: float` field is insufficient: it cannot distinguish between "I am 70% confident because I have seen this many times" and "I am 70% confident because I have seen it once."  The first belief is robust; the second is fragile.

Alternatives considered:

| Option | Problem |
|--------|---------|
| Scalar `float` confidence written directly by callers | Confidence becomes an assertion, not evidence-derived; trivially poisoned |
| Counting positive observations only | No representation of contradicting evidence |
| Full probability distribution stored as a blob | Complex to update, query, and decay; over-engineered for this use case |
| Beta(α,β) distribution (chosen) | Compact (two floats), statistically well-founded, tractable to update and decay |

The Beta distribution is the conjugate prior for Bernoulli evidence, making Bayesian updates trivially additive: each supporting event increments α, each contradicting event increments β.  The mean `α/(α+β)` is confidence; the total `α+β` is the weight of evidence.

---

## Decision

Model every memory record's confidence as the mean of a **Beta(α, β)** distribution:

```
confidence = α / (α + β)
```

- `α` (alpha) counts supporting-evidence observations, initialised to **1.0** (uniform prior).
- `β` (beta) counts contradicting-evidence observations, initialised to **1.0** (uniform prior).
- Both are always ≥ 1.0 (the prior floor); they may never be written below this floor.
- `confidence` is a **computed property** — it is never written directly.
- A brand-new record starts at `confidence = 0.5` (maximum uncertainty), not at `1.0`.

Evidence updates are additive increments to α or β (Laplace/add-one smoothing by default, with an optional weight parameter for weighted evidence).

---

## Consequences

**Benefits**

- Confidence is entirely evidence-derived; no caller can assert certainty by writing a field.
- The weight of evidence (`α+β − 2`) captures *how well-established* a belief is, independent of its confidence value.
- Decay can be applied symmetrically to both counters (see ADR-0005), reducing certainty without deleting history.
- Credible intervals can be computed analytically from (α, β) without extra storage.
- The outlier detector in the WriteGate can compute the *expected confidence swing* before committing, and reject implausibly large jumps.

**Obligations**

- All code paths that touch Bayesian state must go through `core/bayesian.py`; direct field mutations on `alpha`/`beta` are forbidden outside that module.
- The model-level invariant (`alpha >= 1.0`, `beta >= 1.0`) must be enforced by field validators on `MemoryRecord`.
- Decay must respect the prior floor so that heavily decayed records converge toward `confidence = 0.5`, not toward `0`.

**Trade-offs**

- The normal approximation used for credible intervals is inaccurate when `α+β` is very small (≤ 4).  At those counts the interval is wide and conservative, which is the desired behaviour.
- Laplace smoothing treats all evidence events equally; weighted evidence requires passing an explicit `weight` argument to `apply_supporting_evidence` / `apply_contradicting_evidence`.
