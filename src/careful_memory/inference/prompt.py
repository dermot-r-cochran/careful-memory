"""
Inference-time prompt assembly for careful-memory.

This module formats MemorySummary objects into a structured system-prompt
block that grounds an LLM exclusively in verified memory.

The contract (from the system design requirement):

    System:
    You are an assistant that must ground all personalization
    and long-term assumptions ONLY in the provided memory context.

    If memory is uncertain or contradictory, express uncertainty.

    User:
    <Task request>

    Context: Persistent Memory (derived, confidence-weighted)
    - [Summary A]
    - [Summary B]
    - [Summary C]

    Instructions:
    - Do not invent preferences or beliefs
    - Do not generalize beyond memory

INVARIANT: prompt assembly is a READ-ONLY operation.
           The output of this module MUST NOT be fed back into the
           memory store as evidence (no self-reinforcement loops).
"""

from __future__ import annotations

from dataclasses import dataclass

from careful_memory.models.memory import MemorySummary

# ---------------------------------------------------------------------------
# System instruction block — included verbatim in every assembled prompt.
# This text is intentionally conservative to minimise hallucination risk.
# ---------------------------------------------------------------------------

_SYSTEM_PREAMBLE = """\
You are an assistant that must ground all personalization \
and long-term assumptions ONLY in the provided memory context.

If memory is uncertain or contradictory, express uncertainty.\
"""

_MEMORY_HEADER = "Context: Persistent Memory (derived, confidence-weighted)"

_INSTRUCTIONS = """\
Instructions:
- Do not invent preferences or beliefs
- Do not generalize beyond memory\
"""

_NO_MEMORY_PLACEHOLDER = (
    "- No reliable memories are available for this user context. "
    "Do not make any personalisation assumptions."
)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssembledPrompt:
    """
    Result of assembling memory context into a prompt.

    Attributes
    ----------
    system_prompt  : the full system-prompt string to pass to the LLM
    user_prompt    : the user's task request, unchanged
    memory_lines   : the individual summary bullet points included
    record_count   : total number of memory records represented
    """

    system_prompt: str
    user_prompt: str
    memory_lines: list[str]
    record_count: int


# ---------------------------------------------------------------------------
# PromptBuilder
# ---------------------------------------------------------------------------


class PromptBuilder:
    """
    Assembles a memory-grounded system prompt from one or more MemorySummary
    objects.

    Usage::

        builder = PromptBuilder()
        prompt = builder.build(
            user_task="What editor should I open?",
            summaries=[summary_a, summary_b],
        )
        llm.call(system=prompt.system_prompt, user=prompt.user_prompt)

    Design notes
    ------------
    - Multiple summaries (e.g. personal + work domain) are merged in order.
    - Bullet points come from MemorySummary.text, split by newline.
    - Low-confidence summaries (record_count == 0) produce the no-memory
      placeholder rather than an empty context block, so the LLM always
      sees an explicit signal.
    - The user_prompt is returned unchanged; this class does not modify it.
    """

    def build(
        self,
        user_task: str,
        summaries: list[MemorySummary],
    ) -> AssembledPrompt:
        """
        Build the assembled prompt.

        Parameters
        ----------
        user_task  : the user's task request (passed through unchanged)
        summaries  : one or more MemorySummary objects from the summarizer
        """
        memory_lines: list[str] = []
        total_records = 0

        for summary in summaries:
            total_records += summary.record_count
            if summary.record_count == 0 or not summary.text.strip():
                continue
            for line in summary.text.splitlines():
                stripped = line.strip()
                if stripped:
                    memory_lines.append(stripped)

        if not memory_lines:
            memory_lines = [_NO_MEMORY_PLACEHOLDER]

        # Build context block: each line becomes a bullet point.
        bullet_block = "\n".join(f"- {line}" for line in memory_lines)

        system_prompt = (
            f"{_SYSTEM_PREAMBLE}\n\n"
            f"{_MEMORY_HEADER}\n"
            f"{bullet_block}\n\n"
            f"{_INSTRUCTIONS}"
        )

        return AssembledPrompt(
            system_prompt=system_prompt,
            user_prompt=user_task,
            memory_lines=memory_lines,
            record_count=total_records,
        )

    def format_for_display(self, prompt: AssembledPrompt) -> str:
        """
        Return the full prompt as it would be shown to a human reviewer.

        Format::

            System:
            <system_prompt>

            User:
            <user_prompt>
        """
        return (
            f"System:\n{prompt.system_prompt}\n\n"
            f"User:\n{prompt.user_prompt}"
        )
