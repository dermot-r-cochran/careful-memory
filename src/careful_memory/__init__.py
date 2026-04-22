"""
careful-memory: a production-grade long-term memory system for LLM agents.

All learning happens in memory, not training.

Tagline:
  "A careful memory that earns its beliefs, forgets responsibly,
   and never hallucinates authority."
"""

from careful_memory.core.allocation import (
    AllocationResult,
    MemoryItem,
    SurplusTransfer,
    allocate,
    compute_quota,
    normalise_weights,
)
from careful_memory.service import MemoryService

__all__ = [
    "AllocationResult",
    "MemoryItem",
    "MemoryService",
    "SurplusTransfer",
    "allocate",
    "compute_quota",
    "normalise_weights",
]
__version__ = "0.1.0"
