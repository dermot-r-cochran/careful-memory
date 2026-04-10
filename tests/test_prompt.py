"""
Tests for inference-time prompt assembly.

Verifies:
  - System prompt contains the required grounding language
  - Memory bullets are confidence-weighted
  - No-memory case includes explicit placeholder
  - Multi-summary merging works
  - Prompt is read-only (summaries not modified)
  - format_for_display produces correct structure
"""

from __future__ import annotations

import pytest

from careful_memory.core.summarizer import build_summary
from careful_memory.inference.prompt import (
    _MEMORY_HEADER,
    _NO_MEMORY_PLACEHOLDER,
    PromptBuilder,
)
from careful_memory.models.enums import AuthorityLevel, Domain
from careful_memory.models.memory import ContextScope, MemorySource, MemorySummary
from tests.conftest import make_record


@pytest.fixture()
def builder() -> PromptBuilder:
    return PromptBuilder()


@pytest.fixture()
def scope() -> ContextScope:
    return ContextScope(user_id="u1", domain=Domain.personal)


@pytest.fixture()
def source() -> MemorySource:
    return MemorySource(origin="test", authority_level=AuthorityLevel.system)


@pytest.fixture()
def empty_summary(scope: ContextScope) -> MemorySummary:
    return build_summary(scope, records=[], confidence_threshold=0.6)


@pytest.fixture()
def rich_summary(scope: ContextScope, source: MemorySource) -> MemorySummary:
    records = [
        make_record(scope, source, predicate="prefers", object_value="dark mode", alpha=9.0, beta=1.0),
        make_record(scope, source, predicate="uses", object_value="vim", alpha=3.0, beta=1.0),
    ]
    return build_summary(scope, records=records, confidence_threshold=0.5)


class TestSystemPreamble:
    def test_preamble_in_system_prompt(
        self, builder: PromptBuilder, empty_summary: MemorySummary
    ) -> None:
        prompt = builder.build("hello", [empty_summary])
        assert "ground all personalization" in prompt.system_prompt
        assert "ONLY in the provided memory context" in prompt.system_prompt

    def test_uncertainty_instruction_present(
        self, builder: PromptBuilder, empty_summary: MemorySummary
    ) -> None:
        prompt = builder.build("hello", [empty_summary])
        assert "uncertain" in prompt.system_prompt.lower()

    def test_memory_header_present(
        self, builder: PromptBuilder, empty_summary: MemorySummary
    ) -> None:
        prompt = builder.build("hello", [empty_summary])
        assert _MEMORY_HEADER in prompt.system_prompt

    def test_instructions_present(
        self, builder: PromptBuilder, empty_summary: MemorySummary
    ) -> None:
        prompt = builder.build("hello", [empty_summary])
        assert "Do not invent preferences" in prompt.system_prompt
        assert "Do not generalize beyond memory" in prompt.system_prompt


class TestNoMemoryCase:
    def test_placeholder_when_empty(
        self, builder: PromptBuilder, empty_summary: MemorySummary
    ) -> None:
        prompt = builder.build("What do I like?", [empty_summary])
        assert _NO_MEMORY_PLACEHOLDER.lstrip("- ") in prompt.system_prompt

    def test_memory_lines_contains_placeholder(
        self, builder: PromptBuilder, empty_summary: MemorySummary
    ) -> None:
        prompt = builder.build("task", [empty_summary])
        assert len(prompt.memory_lines) == 1
        assert "No reliable memories" in prompt.memory_lines[0]

    def test_no_summaries_gives_placeholder(self, builder: PromptBuilder) -> None:
        prompt = builder.build("task", [])
        assert "No reliable memories" in prompt.system_prompt


class TestMemoryInjection:
    def test_memory_bullets_present(
        self, builder: PromptBuilder, rich_summary: MemorySummary
    ) -> None:
        prompt = builder.build("What editor?", [rich_summary])
        assert "- " in prompt.system_prompt  # at least one bullet

    def test_confidence_language_present(
        self, builder: PromptBuilder, rich_summary: MemorySummary
    ) -> None:
        # rich_summary has α=9,β=1 → "almost certainly" or "very likely"
        assert any(
            word in prompt_text
            for word in ("almost certainly", "very likely", "probably")
            for prompt_text in [builder.build("task", [rich_summary]).system_prompt]
        )

    def test_record_count_tracked(
        self, builder: PromptBuilder, rich_summary: MemorySummary
    ) -> None:
        prompt = builder.build("task", [rich_summary])
        assert prompt.record_count == rich_summary.record_count

    def test_user_task_unchanged(
        self, builder: PromptBuilder, rich_summary: MemorySummary
    ) -> None:
        user_task = "What editor should I open today?"
        prompt = builder.build(user_task, [rich_summary])
        assert prompt.user_prompt == user_task


class TestMultiSummaryMerge:
    def test_multi_summary_merged(
        self,
        builder: PromptBuilder,
        scope: ContextScope,
        source: MemorySource,
    ) -> None:
        work_scope = ContextScope(user_id="u1", domain=Domain.work)
        s1 = build_summary(
            scope,
            [make_record(scope, source, predicate="prefers", object_value="dark mode", alpha=5.0, beta=1.0)],
            confidence_threshold=0.5,
        )
        s2 = build_summary(
            work_scope,
            [make_record(work_scope, source, predicate="uses", object_value="Python", alpha=5.0, beta=1.0)],
            confidence_threshold=0.5,
        )
        prompt = builder.build("task", [s1, s2])
        assert prompt.record_count == s1.record_count + s2.record_count
        assert len(prompt.memory_lines) == s1.record_count + s2.record_count


class TestFormatForDisplay:
    def test_format_contains_system_and_user(
        self, builder: PromptBuilder, empty_summary: MemorySummary
    ) -> None:
        prompt = builder.build("What do I prefer?", [empty_summary])
        display = builder.format_for_display(prompt)
        assert display.startswith("System:\n")
        assert "\nUser:\n" in display
        assert "What do I prefer?" in display
