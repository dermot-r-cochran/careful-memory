"""
Tests for MemoryService, the platform authority, and for the README's Quick Start.

Verifies:
  - The README Quick Start runs exactly as printed (the block is read from
    README.md and executed, so the first promise a reader meets is proven,
    not restated)
  - Platform-internal writes go through gate then reviewer, and a gate
    rejection returns a synthetic reject review and writes nothing
  - run_decay persists the decayed records and archives the ones whose
    confidence falls below the threshold
  - build_summary persists when asked, assemble_prompt never does, and
    neither touches the belief store
  - Context and record lookups are scoped to the context asked for
"""

from __future__ import annotations

import io
import re
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from careful_memory.models.enums import AuthorityLevel, Domain, RecordStatus
from careful_memory.models.memory import ContextScope, MemorySource
from careful_memory.review.reviewer import ReviewDecision
from careful_memory.service import MemoryService
from careful_memory.storage.sqlite import SQLiteMemoryStore
from tests.conftest import make_record

README = Path(__file__).resolve().parents[1] / "README.md"
QUICK_START = re.compile(r"^## Quick Start\n.*?^```python\n(.*?)^```", re.MULTILINE | re.DOTALL)


def readme_quick_start() -> str:
    match = QUICK_START.search(README.read_text(encoding="utf-8").replace("\r\n", "\n"))
    assert match is not None, "README.md has no ```python block under ## Quick Start"
    return match.group(1)


@pytest.fixture()
def scope() -> ContextScope:
    return ContextScope(user_id="u1", domain=Domain.personal)


@pytest.fixture()
def svc(store: SQLiteMemoryStore, scope: ContextScope) -> MemoryService:
    svc = MemoryService(store=store)
    svc.create_context(scope)
    return svc


class TestQuickStart:
    def test_readme_quick_start_runs_as_written(self) -> None:
        """The block under '## Quick Start' in README.md executes without error."""
        namespace: dict[str, Any] = {}
        out = io.StringIO()
        with redirect_stdout(out):
            exec(compile(readme_quick_start(), str(README), "exec"), namespace)  # noqa: S102
        printed = out.getvalue().splitlines()
        # Step 3: the platform approves the proposal at the uniform prior.
        assert printed[0] == "approve"
        assert printed[1] == "0.5"
        # Step 5: the prompt is grounded in the one reinforced belief.
        assert "ONLY in the provided memory context" in out.getvalue()
        assert "- It is possibly that Alice prefers dark mode." in printed

    def test_quick_start_leaves_one_reinforced_belief(self) -> None:
        namespace: dict[str, Any] = {}
        with redirect_stdout(io.StringIO()):
            exec(compile(readme_quick_start(), str(README), "exec"), namespace)  # noqa: S102
        svc: MemoryService = namespace["svc"]
        scope: ContextScope = namespace["scope"]
        records = svc.list_records(scope.context_id)
        assert len(records) == 1
        assert records[0].alpha == 2.0 and records[0].beta == 1.0
        assert namespace["prompt"].record_count == 1


class TestContexts:
    def test_get_context_round_trips(self, svc: MemoryService, scope: ContextScope) -> None:
        assert svc.get_context(scope.context_id) == scope

    def test_get_context_unknown_is_none(self, svc: MemoryService) -> None:
        assert svc.get_context("no-such-context") is None

    def test_list_contexts_for_user_is_per_user(self, svc: MemoryService, scope: ContextScope) -> None:
        other = ContextScope(user_id="u2", domain=Domain.personal)
        svc.create_context(other)
        assert svc.list_contexts_for_user("u1") == [scope]
        assert svc.list_contexts_for_user("u2") == [other]


class TestWriteRecord:
    def test_approved_record_is_saved(self, svc: MemoryService, scope: ContextScope) -> None:
        source = MemorySource(origin="platform", authority_level=AuthorityLevel.system)
        record = make_record(scope, source)
        gate, review, saved = svc.write_record(scope, record)
        assert gate.is_allowed
        assert review.decision == ReviewDecision.approve
        assert saved is not None
        assert svc.get_record(saved.id, scope.context_id) == saved

    def test_gate_rejection_writes_nothing(self, svc: MemoryService, scope: ContextScope) -> None:
        """A record for another context fails the gate; the reviewer never runs."""
        other = ContextScope(user_id="u1", domain=Domain.work)
        source = MemorySource(origin="platform", authority_level=AuthorityLevel.system)
        record = make_record(other, source)
        gate, review, saved = svc.write_record(scope, record)
        assert not gate.is_allowed
        assert review.decision == ReviewDecision.reject
        assert review.justification.startswith("gate rejected before review")
        assert saved is None
        assert svc.list_records(scope.context_id, include_inactive=True) == []

    def test_reviewer_rejection_writes_nothing(self, svc: MemoryService, scope: ContextScope) -> None:
        """A near-certain semantic assertion is refused by the reviewer, not the gate."""
        from careful_memory.models.enums import MemoryType

        source = MemorySource(origin="agent", authority_level=AuthorityLevel.user)
        record = make_record(scope, source, memory_type=MemoryType.semantic, alpha=99.0, beta=1.0)
        gate, review, saved = svc.write_record(scope, record)
        assert gate.is_allowed
        assert not review.is_committable
        assert saved is None
        assert svc.list_records(scope.context_id, include_inactive=True) == []


class TestRunDecay:
    def test_unknown_context_raises(self, svc: MemoryService) -> None:
        with pytest.raises(ValueError, match="not found"):
            svc.run_decay("no-such-context")

    def test_decay_is_persisted_and_archives_doubtful_records(
        self, svc: MemoryService, scope: ContextScope
    ) -> None:
        source = MemorySource(origin="platform", authority_level=AuthorityLevel.system)
        strong = make_record(scope, source, object_value="dark mode", alpha=9.0, beta=1.0)
        doubtful = make_record(scope, source, object_value="light mode", alpha=1.0, beta=9.0)
        for record in (strong, doubtful):
            svc.write_record(scope, record)

        later = datetime.now(tz=UTC) + timedelta(days=30)
        updated = {r.id: r for r in svc.run_decay(scope.context_id, as_of=later)}

        assert updated[strong.id].alpha < strong.alpha, "evidence mass shrinks toward the prior"
        assert updated[strong.id].status == RecordStatus.active
        assert updated[doubtful.id].status == RecordStatus.archived
        # Persisted, not just returned.
        stored = svc.get_record(doubtful.id, scope.context_id)
        assert stored is not None and stored.status == RecordStatus.archived
        assert svc.list_records(scope.context_id) == [updated[strong.id]]


class TestSummaryAndPrompt:
    def test_unknown_context_raises(self, svc: MemoryService) -> None:
        with pytest.raises(ValueError, match="not found"):
            svc.build_summary("no-such-context")

    def test_build_summary_persists_by_default(
        self, svc: MemoryService, store: SQLiteMemoryStore, scope: ContextScope
    ) -> None:
        source = MemorySource(origin="platform", authority_level=AuthorityLevel.system)
        svc.write_record(scope, make_record(scope, source, alpha=9.0, beta=1.0))
        summary = svc.build_summary(scope.context_id)
        assert summary.record_count == 1
        assert store.list_summaries(scope.context_id) == [summary]

    def test_build_summary_without_persist_saves_nothing(
        self, svc: MemoryService, store: SQLiteMemoryStore, scope: ContextScope
    ) -> None:
        svc.build_summary(scope.context_id, persist=False)
        assert store.list_summaries(scope.context_id) == []

    def test_assemble_prompt_is_read_only(
        self, svc: MemoryService, store: SQLiteMemoryStore, scope: ContextScope
    ) -> None:
        source = MemorySource(origin="platform", authority_level=AuthorityLevel.system)
        svc.write_record(scope, make_record(scope, source, alpha=9.0, beta=1.0))
        before = svc.list_records(scope.context_id, include_inactive=True)

        prompt = svc.assemble_prompt(context_ids=[scope.context_id], user_task="Which theme?")

        assert prompt.user_prompt == "Which theme?"
        assert prompt.record_count == 1
        assert "It is very likely that Alice prefers dark mode." in prompt.memory_lines
        assert svc.list_records(scope.context_id, include_inactive=True) == before
        assert store.list_summaries(scope.context_id) == [], "assemble_prompt never persists"

    def test_assemble_prompt_merges_contexts(self, svc: MemoryService, scope: ContextScope) -> None:
        work = ContextScope(user_id="u1", domain=Domain.work)
        svc.create_context(work)
        source = MemorySource(origin="platform", authority_level=AuthorityLevel.system)
        svc.write_record(scope, make_record(scope, source, object_value="dark mode", alpha=9.0, beta=1.0))
        svc.write_record(work, make_record(work, source, predicate="uses", object_value="vim", alpha=9.0, beta=1.0))

        prompt = svc.assemble_prompt(context_ids=[scope.context_id, work.context_id], user_task="?")

        assert prompt.record_count == 2
        assert len(prompt.memory_lines) == 2


class TestLookups:
    def test_get_record_is_scoped_to_context(self, svc: MemoryService, scope: ContextScope) -> None:
        source = MemorySource(origin="platform", authority_level=AuthorityLevel.system)
        _, _, saved = svc.write_record(scope, make_record(scope, source))
        assert saved is not None
        assert svc.get_record(saved.id, "another-context") is None

    def test_store_closes_as_a_context_manager(self, scope: ContextScope) -> None:
        with SQLiteMemoryStore(":memory:") as store:
            MemoryService(store=store).create_context(scope)
            assert store.get_context(scope.context_id) == scope
        with pytest.raises(Exception, match="closed"):
            store.get_context(scope.context_id)
