# Contributing to careful-memory

Contributions are welcome: bug reports, fixes, tests, documentation and focused improvements.

## Before you start

- For anything beyond a small fix, open an issue first describing the problem and the change you have in mind, so the approach can be agreed before you spend time on it.
- Read `docs/adr/` before touching the area a record covers. ADRs 0001, 0004, 0006, 0007, 0012 and 0014 are safety invariants (no model self-reinforcement, an append-only store, context isolation, derived confidence, the three-tool agent surface). A change that needs one undone is proposed as a new ADR.
- `tests/test_poisoning_and_isolation.py` holds the security guards. A change that weakens one will not be accepted; anything touching isolation or poisoning resistance extends that file.

## Making a change

- Keep a pull request to one change, and say in its description what it changes and why.
- Run what CI runs before you push:

```bash
pip install -e ".[dev]"
pytest --cov=careful_memory --cov-fail-under=96
ruff check .
mypy src
python .github/scripts/check_docs.py
```

- Every capability the README claims names the test that proves it, or says *no test yet* or *not yet implemented*. A new capability lands with its tests and its README row in the same pull request, and a renamed test updates the rows that cite it.
- The coverage threshold (96%) is a ratchet: it may rise with tests that earn it, never fall.
- A new admission, decay or authority rule lands with tests on both sides: the case admitted and the case refused.

## How a pull request is reviewed

The repository has one maintainer, Dermot Cochran, so a pull request may wait a while before anyone looks at it.

- CI must be green. A failing check is the first thing to fix.
- The review may be assisted by an AI tool, which reads the change and reports to the maintainer. It does not approve or merge anything.
- **Every outside pull request is merged by the maintainer, never by an automated process or an AI tool,** however small.
- A pull request may be declined, or asked to change shape, when it moves the project somewhere it is not meant to go. That is not a judgement on the work.

## Licence

Contributions are accepted under this repository's licence, the MIT License (`LICENSE`), the same terms everyone else receives the code under. No sign-off line and no contributor licence agreement are required. Only contribute work you have the right to license on these terms.

## Security

Please don't describe a security problem in a public issue or pull request. Report it privately instead, through the repository's **Security** tab (*Report a vulnerability*). Only the maintainer sees the report.
