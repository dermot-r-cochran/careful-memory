"""Core business-logic package."""

from careful_memory.core import meta_gate
from careful_memory.core.allocation import (
    AllocationResult,
    MemoryItem,
    SurplusTransfer,
    allocate,
    compute_quota,
    normalise_weights,
)
from careful_memory.core.bayesian import (
    BayesianUpdateResult,
    apply_contradicting_evidence,
    apply_decay,
    apply_supporting_evidence,
    credible_interval,
)
from careful_memory.core.contradiction import (
    ContradictionResult,
    apply_contradiction,
    apply_supersession,
)
from careful_memory.core.decay import (
    DecayResult,
    apply_decay_to_record,
    compute_decay,
    decay_rate_for,
)
from careful_memory.core.gate import GateResult, GateVerdict, WriteGate
from careful_memory.core.summarizer import build_summary

__all__ = [
    "AllocationResult",
    "BayesianUpdateResult",
    "ContradictionResult",
    "DecayResult",
    "GateResult",
    "GateVerdict",
    "MemoryItem",
    "SurplusTransfer",
    "WriteGate",
    "allocate",
    "apply_contradiction",
    "apply_contradicting_evidence",
    "apply_decay",
    "apply_decay_to_record",
    "apply_supersession",
    "apply_supporting_evidence",
    "build_summary",
    "compute_decay",
    "compute_quota",
    "credible_interval",
    "decay_rate_for",
    "meta_gate",
    "normalise_weights",
]
