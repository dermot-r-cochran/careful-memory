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
- The README's first promise → `test_service.py::TestQuickStart` reads the
  `## Quick Start` block out of `README.md` and runs it as printed, so the
  example a reader meets first is proven by the suite rather than restated
  by it. The same file covers `MemoryService`'s platform-internal paths
  (`write_record`, `run_decay`, `build_summary`, `assemble_prompt`), which
  had no tests before 2026-10-19.
- The citations themselves → `.github/scripts/check_docs.py` (the CI docs
  job) resolves every `file::Class::test` named in the README, this file,
  `CLAUDE.md` and `docs/` against the test files, since 2026-10-27. A guard
  that is renamed without its citations fails the pull request, so a row in
  the README's safety table cannot point at a test that no longer exists.

193 tests across 11 files; 176 verified passing locally on 2026-08-24, the
17 in `test_service.py` added 2026-10-19 (measured total 95.83%).

## The coverage ratchet, exactly

CI runs `pytest --cov=careful_memory --cov-fail-under=95`. pytest-cov
compares the total against that figure at coverage's report precision,
which defaults to 0 decimals: a total of 90.74% rounded to 91 and passed the
old ratchet of 91 from 2026-10-10 to 2026-10-19, while the same run printed
`FAIL Required test coverage of 91% not reached`. The CI job was green and
its log said FAIL. `[tool.coverage.report] precision = 2` in
`pyproject.toml` makes the comparison exact (95.83 against 95.84 fails,
verified locally), and the ratchet is set at the floor of the measured
total, so it can only be passed by coverage that is really there.

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

- ~~No CI~~ — **closed 2026-08-24**: `.github/workflows/ci.yml` runs pytest
  on Python 3.11/3.12/3.13 with a coverage ratchet at the measured 91%
  baseline, plus a `ruff check .` job (already clean, so it arrived green).
  **mypy stays out deliberately**: it reports 2 findings on the current code,
  and a permanently red check teaches everyone to ignore checks — the job
  arrives in the same change that fixes them, per the house rule.
- The storage path is exercised against the in-process SQLite store only
  (`storage/sqlite.py`); SQLAlchemy is a declared dependency with no store
  behind it yet (ADR-0017, not implemented). A CI job against a real
  database service container would cover dialect behaviour the in-process
  runs cannot.
- Bayesian update properties (confidence stays in bounds, repeated identical
  evidence converges rather than diverges) are natural `hypothesis`
  generalisations of the existing fixed cases.
