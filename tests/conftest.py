"""
Shared pytest fixtures for careful-memory tests.
"""

from __future__ import annotations

import pytest

from careful_memory.models.enums import AuthorityLevel, Domain, MemoryType
from careful_memory.models.memory import (
    ContextScope,
    EntityRef,
    MemoryRecord,
    MemorySource,
)
from careful_memory.storage.sqlite import SQLiteMemoryStore


@pytest.fixture()
def store() -> SQLiteMemoryStore:
    """In-process SQLite store, fresh per test."""
    return SQLiteMemoryStore(":memory:")


@pytest.fixture()
def user_scope() -> ContextScope:
    return ContextScope(user_id="user-alice", domain=Domain.personal)


@pytest.fixture()
def work_scope() -> ContextScope:
    return ContextScope(user_id="user-alice", domain=Domain.work)


@pytest.fixture()
def system_source() -> MemorySource:
    return MemorySource(origin="test-system", authority_level=AuthorityLevel.system)


@pytest.fixture()
def user_source() -> MemorySource:
    return MemorySource(origin="test-agent", authority_level=AuthorityLevel.user)


@pytest.fixture()
def subject() -> EntityRef:
    return EntityRef(entity_type="person", label="Alice")


def make_record(
    scope: ContextScope,
    source: MemorySource,
    subject: EntityRef | None = None,
    predicate: str = "prefers",
    object_value: str = "dark mode",
    memory_type: MemoryType = MemoryType.episodic,
    alpha: float = 1.0,
    beta: float = 1.0,
) -> MemoryRecord:
    if subject is None:
        subject = EntityRef(entity_type="person", label="Alice")
    return MemoryRecord(
        context_id=scope.context_id,
        memory_type=memory_type,
        subject=subject,
        predicate=predicate,
        object_value=object_value,
        alpha=alpha,
        beta=beta,
        source=source,
    )
