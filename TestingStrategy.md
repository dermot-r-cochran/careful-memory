# Testing Strategy

How careful-memory is tested, and why the adversarial tests are the ones
that matter most.

## The governing principle

The product promise is in the tagline: *earns its beliefs, forgets
responsibly, never hallucinates authority*. Each clause is a testable claim,
and the suite is organised so each has named guards:

- **Earns its beliefs** → `test_bayesian.py` (confidence updates),
  `test_contradiction.py` (explicit contradiction tracking),
  `test_reviewer.py` (the review path that admits or rejects candidate
  memories).
- **Never hallucinates authority** → `test_poisoning_and_isolation.py` — the
  security-shaped tests: injected/poisoned content must not gain authority,
  and per-user memory must stay isolated. **These are invariant guards, not
  feature tests**: a failure here is an incident, and weakening one to get a
  change through is never the fix.
- The rest of the surface → `test_allocation.py`, `test_prompt.py`,
  `test_tools.py`, and the remaining files, one per module.

176 tests across 10 files, all verified passing locally on 2026-08-24.

## Running

`pip install -e ".[dev]"` then `pytest` (config in `pyproject.toml`:
`testpaths = ["tests"]`, `-v --tb=short`). The package is src-layout, so an
editable install (or `PYTHONPATH=src`) is required — plain `pytest` from a
bare checkout will fail on imports, which is an environment problem, not a
test failure.

The suite runs against in-process stores; the `azure` extra (pyodbc,
azure-identity) is a deployment concern and deliberately not a test
dependency.

## Extending

- A new admission, decay, or authority rule lands with tests on **both
  sides**: the case it admits and the case it refuses. A memory system tested
  only on honest input is untested where it counts.
- Anything touching isolation or poisoning resistance extends
  `test_poisoning_and_isolation.py` — keep the security guards in one place
  so their coverage is reviewable at a glance.
- Time-based decay logic should take clocks as parameters (or freeze them in
  tests) rather than reading wall time, so decay tests stay deterministic.

## Known gaps (candidates for next)

- **No CI.** `ruff` and `mypy` are declared in the dev extra and configured,
  `pytest-cov` too — none of the three runs anywhere automatically. The
  sibling `swarm` workflow is the template: pytest on a version matrix, a
  coverage ratchet at the measured baseline, lint added green.
- The SQL path is exercised via SQLAlchemy against local stores only; a CI
  job against a real database service container would cover dialect
  behaviour the in-process runs cannot.
- Bayesian update properties (confidence stays in bounds, repeated identical
  evidence converges rather than diverges) are natural `hypothesis`
  generalisations of the existing fixed cases.
