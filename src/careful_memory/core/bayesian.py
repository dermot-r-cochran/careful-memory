"""
Bayesian confidence update logic for careful-memory.

Confidence is modelled as the mean of a Beta(α, β) distribution:

    confidence = α / (α + β)

α counts supporting evidence observations.
β counts contradicting evidence observations.

Both are initialised to 1.0 (Beta(1,1) = uniform prior over [0,1]).

This module is pure-function: no I/O, no side effects.
All mutations are returned as new values to the caller.

DESIGN DECISIONS
----------------
- We use direct Laplace (add-one) smoothing for each evidence event.
  More sophisticated Bayesian updates (e.g. weighted likelihood) can be
  introduced by subclassing or replacing update_from_event() without
  touching callers.

- We do NOT normalise (α, β) after accumulation.  Keeping raw counts
  lets the decay module shrink them symmetrically while preserving the
  confidence ratio.  Normalising would lose that history.

- The minimum floor for α and β is 1.0 (the uniform prior).
  Evidence removal (via decay) must never push below this floor.
"""

from __future__ import annotations

import math

from careful_memory.models.memory import MemoryRecord

# Floor for α and β: Beta(1,1) = uniform prior.
# Never go below this — it preserves statistical validity.
_PRIOR_FLOOR: float = 1.0

# Maximum single-event swing allowed in the confidence score.
# If an update would move confidence by more than this, it is flagged
# as an outlier (but still applied — the gate layer decides to reject).
# 0.25 is conservative: a 25 percentage-point jump on a single event
# is suspicious unless α+β is very small.
OUTLIER_SWING_THRESHOLD: float = 0.25


class BayesianUpdateResult:
    """
    Immutable result of a Bayesian evidence update.

    Attributes
    ----------
    new_alpha   : updated α counter
    new_beta    : updated β counter
    new_confidence: derived confidence after update
    delta_confidence: how much confidence changed (signed)
    is_outlier  : True if the swing exceeds OUTLIER_SWING_THRESHOLD
    """

    __slots__ = (
        "new_alpha",
        "new_beta",
        "new_confidence",
        "delta_confidence",
        "is_outlier",
    )

    def __init__(
        self,
        new_alpha: float,
        new_beta: float,
        old_confidence: float,
    ) -> None:
        self.new_alpha = new_alpha
        self.new_beta = new_beta
        self.new_confidence = new_alpha / (new_alpha + new_beta)
        self.delta_confidence = self.new_confidence - old_confidence
        self.is_outlier = abs(self.delta_confidence) > OUTLIER_SWING_THRESHOLD


def apply_supporting_evidence(record: MemoryRecord, weight: float = 1.0) -> BayesianUpdateResult:
    """
    Increment α by *weight* (supporting evidence).

    Parameters
    ----------
    record : the existing memory record
    weight : evidence weight; must be > 0.  Defaults to 1 (one observation).

    Returns
    -------
    BayesianUpdateResult with updated counters.
    """
    if weight <= 0:
        raise ValueError(f"Evidence weight must be > 0, got {weight}")
    old_confidence = record.confidence
    return BayesianUpdateResult(
        new_alpha=record.alpha + weight,
        new_beta=record.beta,
        old_confidence=old_confidence,
    )


def apply_contradicting_evidence(
    record: MemoryRecord, weight: float = 1.0
) -> BayesianUpdateResult:
    """
    Increment β by *weight* (contradicting evidence).

    Parameters
    ----------
    record : the existing memory record
    weight : evidence weight; must be > 0.  Defaults to 1 (one observation).

    Returns
    -------
    BayesianUpdateResult with updated counters.
    """
    if weight <= 0:
        raise ValueError(f"Evidence weight must be > 0, got {weight}")
    old_confidence = record.confidence
    return BayesianUpdateResult(
        new_alpha=record.alpha,
        new_beta=record.beta + weight,
        old_confidence=old_confidence,
    )


def apply_decay(record: MemoryRecord, elapsed_days: float, domain_decay_rate: float) -> BayesianUpdateResult:
    """
    Apply time-based decay to both α and β symmetrically.

    Decay reduces evidence mass, not confidence directly.
    The formula shrinks both counters toward the uniform prior (1.0, 1.0),
    preserving the confidence ratio while reducing certainty over time.

    Formula:
        decay_factor = (1 - decay_rate) ** elapsed_days
        alpha' = max(1 + (alpha - 1) * decay_factor, PRIOR_FLOOR)
        beta'  = max(1 + (beta  - 1) * decay_factor, PRIOR_FLOOR)

    This means:
    - A fresh record with no evidence (α=1, β=1) does not change.
    - A highly reinforced record slowly loses the extra evidence mass.
    - confidence ratio is preserved unless the counters are very asymmetric.

    Parameters
    ----------
    record            : the memory record to decay
    elapsed_days      : number of days since last decay
    domain_decay_rate : effective decay rate (may include domain multiplier)
    """
    if elapsed_days < 0:
        raise ValueError(f"elapsed_days must be >= 0, got {elapsed_days}")
    if not (0.0 < domain_decay_rate <= 1.0):
        raise ValueError(f"domain_decay_rate must be in (0,1], got {domain_decay_rate}")

    old_confidence = record.confidence

    decay_factor = (1.0 - domain_decay_rate) ** elapsed_days

    new_alpha = max(_PRIOR_FLOOR + (record.alpha - _PRIOR_FLOOR) * decay_factor, _PRIOR_FLOOR)
    new_beta = max(_PRIOR_FLOOR + (record.beta - _PRIOR_FLOOR) * decay_factor, _PRIOR_FLOOR)

    return BayesianUpdateResult(
        new_alpha=new_alpha,
        new_beta=new_beta,
        old_confidence=old_confidence,
    )


def effective_confidence_after_decay(
    alpha: float,
    beta: float,
    decay_rate: float,
    elapsed_days: float,
) -> float:
    """
    Calculate what confidence WOULD be after decay, without mutating anything.

    Useful for filtering/querying without side effects.
    """
    factor = (1.0 - decay_rate) ** elapsed_days
    a = max(_PRIOR_FLOOR + (alpha - _PRIOR_FLOOR) * factor, _PRIOR_FLOOR)
    b = max(_PRIOR_FLOOR + (beta - _PRIOR_FLOOR) * factor, _PRIOR_FLOOR)
    return a / (a + b)


def credible_interval(alpha: float, beta: float, z: float = 1.96) -> tuple[float, float]:
    """
    Approximate (1 - 2*Φ(-z)) credible interval for Beta(α, β).

    Uses the normal approximation to the Beta distribution, which is
    reasonable when α+β is not tiny.  For small counts the interval is
    wide and conservative, which is the correct behaviour.

    Returns (lower, upper) clipped to [0, 1].
    """
    n = alpha + beta
    mu = alpha / n
    # Variance of Beta(α, β) = αβ / (n²(n+1))
    variance = (alpha * beta) / (n * n * (n + 1))
    std = math.sqrt(variance)
    lower = max(0.0, mu - z * std)
    upper = min(1.0, mu + z * std)
    return lower, upper
