"""
Tests for the conserved-mass salience allocation module.

Covers:
  - Normalisation invariant (weights sum to 1.0)
  - Quota retention guarantees
  - Surplus redistribution conserves total mass
  - Largest-remainder resolution
  - Deterministic tie-breaking (same input → same output)
  - Edge cases: empty list, single item, all equal weights, capacity=1
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from careful_memory.core.allocation import (
    AllocationResult,
    MemoryItem,
    SurplusTransfer,
    allocate,
    compute_quota,
    normalise_weights,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_TIME = datetime(2024, 1, 1, tzinfo=UTC)


def make_item(
    id: str,
    raw_score: float,
    group: str | None = None,
    created_at: datetime | None = None,
    priority_class: int = 0,
) -> MemoryItem:
    return MemoryItem(
        id=id,
        signals={},
        group=group,
        created_at=created_at or _BASE_TIME,
        raw_score=raw_score,
        priority_class=priority_class,
    )


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


class TestNormaliseWeights:
    def test_weights_sum_to_one(self) -> None:
        items = [make_item("a", 2.0), make_item("b", 3.0), make_item("c", 5.0)]
        normalise_weights(items)
        assert sum(i.normalised_weight for i in items) == pytest.approx(1.0)

    def test_proportions_correct(self) -> None:
        items = [make_item("a", 1.0), make_item("b", 3.0)]
        normalise_weights(items)
        assert items[0].normalised_weight == pytest.approx(0.25)
        assert items[1].normalised_weight == pytest.approx(0.75)

    def test_single_item_gets_weight_one(self) -> None:
        items = [make_item("a", 0.7)]
        normalise_weights(items)
        assert items[0].normalised_weight == pytest.approx(1.0)

    def test_empty_list_is_noop(self) -> None:
        normalise_weights([])  # must not raise

    def test_all_zero_raw_scores_give_uniform(self) -> None:
        items = [make_item("a", 0.0), make_item("b", 0.0), make_item("c", 0.0)]
        normalise_weights(items)
        for item in items:
            assert item.normalised_weight == pytest.approx(1.0 / 3)

    def test_invariant_holds_for_many_items(self) -> None:
        import random

        rng = random.Random(42)
        items = [make_item(str(i), rng.random()) for i in range(100)]
        normalise_weights(items)
        assert sum(i.normalised_weight for i in items) == pytest.approx(1.0, abs=1e-9)

    def test_in_place_mutation(self) -> None:
        item = make_item("x", 4.0)
        items = [item, make_item("y", 1.0)]
        normalise_weights(items)
        assert item.normalised_weight == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# Quota computation
# ---------------------------------------------------------------------------


class TestComputeQuota:
    def test_capacity_one(self) -> None:
        assert compute_quota(1) == pytest.approx(1.0)

    def test_capacity_four(self) -> None:
        assert compute_quota(4) == pytest.approx(0.25)

    def test_capacity_ten(self) -> None:
        assert compute_quota(10) == pytest.approx(0.1)

    def test_zero_capacity_raises(self) -> None:
        with pytest.raises(ValueError):
            compute_quota(0)

    def test_negative_capacity_raises(self) -> None:
        with pytest.raises(ValueError):
            compute_quota(-1)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestAllocateEdgeCases:
    def test_empty_list_returns_empty_result(self) -> None:
        result = allocate([], capacity=5)
        assert result.retained == []
        assert result.evicted == []

    def test_single_item_always_retained(self) -> None:
        items = [make_item("a", 1.0)]
        result = allocate(items, capacity=1)
        assert "a" in result.retained
        assert result.evicted == []

    def test_capacity_larger_than_items_retains_all(self) -> None:
        items = [make_item("a", 1.0), make_item("b", 2.0)]
        result = allocate(items, capacity=10)
        assert set(result.retained) == {"a", "b"}
        assert result.evicted == []

    def test_capacity_one_retains_highest_weight(self) -> None:
        items = [make_item("low", 1.0), make_item("high", 9.0)]
        result = allocate(items, capacity=1)
        assert result.retained == ["high"]
        assert "low" in result.evicted

    def test_retained_plus_evicted_equals_all_items(self) -> None:
        items = [make_item(f"m{i}", float(i + 1)) for i in range(7)]
        result = allocate(items, capacity=3)
        assert set(result.retained) | set(result.evicted) == {f"m{i}" for i in range(7)}
        assert len(set(result.retained) & set(result.evicted)) == 0


# ---------------------------------------------------------------------------
# Quota retention guarantees
# ---------------------------------------------------------------------------


class TestQuotaRetentionGuarantees:
    def test_dominant_item_always_retained(self) -> None:
        # "a" has 80 % of salience — far above quota=0.25
        items = [
            make_item("a", 8.0),
            make_item("b", 0.5),
            make_item("c", 0.5),
            make_item("d", 1.0),
        ]
        result = allocate(items, capacity=4)
        assert "a" in result.retained

    def test_both_equal_quota_items_retained(self) -> None:
        # Two items with equal weight; capacity=2 → quota=0.5; both meet quota exactly
        items = [make_item("x", 1.0), make_item("y", 1.0)]
        result = allocate(items, capacity=2)
        assert set(result.retained) == {"x", "y"}
        assert result.evicted == []

    def test_below_quota_items_may_be_evicted(self) -> None:
        items = [
            make_item("dominant", 90.0),
            make_item("tiny1", 1.0),
            make_item("tiny2", 1.0),
            make_item("tiny3", 1.0),
            make_item("tiny4", 7.0),
        ]
        result = allocate(items, capacity=2)
        assert "dominant" in result.retained
        assert len(result.retained) == 2
        assert len(result.evicted) == 3

    def test_retained_count_equals_capacity(self) -> None:
        items = [make_item(f"m{i}", float(i + 1)) for i in range(8)]
        result = allocate(items, capacity=5)
        assert len(result.retained) == 5
        assert len(result.evicted) == 3


# ---------------------------------------------------------------------------
# Surplus transfer / semantic bleed
# ---------------------------------------------------------------------------


class TestSurplusTransfer:
    def test_all_items_accounted_for_after_transfer(self) -> None:
        """Every item must appear in exactly one of retained / evicted."""
        items = [
            make_item("a", 7.0, group="g1"),
            make_item("b", 1.0, group="g1"),
            make_item("c", 1.0, group="g1"),
            make_item("d", 1.0, group="g2"),
        ]
        result = allocate(items, capacity=4)
        assert set(result.retained) | set(result.evicted) == {"a", "b", "c", "d"}

    def test_surplus_transfer_recorded_in_audit(self) -> None:
        """Over-quota surplus is logged in the audit trail."""
        # a has 70 % weight → surplus above quota=0.25; b shares the group
        items = [
            make_item("a", 7.0, group="g1"),
            make_item("b", 1.0, group="g1"),
            make_item("c", 2.0, group="g2"),
        ]
        result = allocate(items, capacity=4)
        sources = {t.source_id for t in result.surplus_transfers}
        assert "a" in sources

    def test_same_group_receives_surplus_not_other_groups(self) -> None:
        items = [
            make_item("dominant", 9.0, group="topic-a"),
            make_item("related", 0.5, group="topic-a"),
            make_item("unrelated", 0.5, group="topic-b"),
        ]
        result = allocate(items, capacity=3)
        related_transfers = [t for t in result.surplus_transfers if t.target_id == "related"]
        unrelated_transfers = [t for t in result.surplus_transfers if t.target_id == "unrelated"]
        assert len(related_transfers) > 0
        assert len(unrelated_transfers) == 0

    def test_similarity_fn_overrides_group_for_targeting(self) -> None:
        """When similarity_fn is provided it determines targets, not group."""
        items = [
            make_item("a", 9.0, group="g1"),
            make_item("b", 0.5, group="g2"),  # different group, but similar to a
            make_item("c", 0.5, group="g1"),  # same group, but not similar to a
        ]

        def sim(x: MemoryItem, y: MemoryItem) -> float:
            return 1.0 if {x.id, y.id} == {"a", "b"} else 0.0

        result = allocate(items, capacity=3, similarity_fn=sim)
        transfer_targets = {t.target_id for t in result.surplus_transfers}
        assert "b" in transfer_targets
        assert "c" not in transfer_targets

    def test_surplus_transfer_amount_positive(self) -> None:
        """Every recorded transfer must carry a positive amount."""
        items = [
            make_item("big", 8.0, group="grp"),
            make_item("sm1", 1.0, group="grp"),
            make_item("sm2", 1.0, group="grp"),
        ]
        result = allocate(items, capacity=3)
        for xfer in result.surplus_transfers:
            assert xfer.amount > 0.0

    def test_surplus_transfer_dataclass_is_frozen(self) -> None:
        xfer = SurplusTransfer(source_id="a", target_id="b", amount=0.1)
        with pytest.raises((AttributeError, TypeError)):
            xfer.amount = 0.5  # type: ignore[misc]

    def test_no_group_means_no_surplus_transfer(self) -> None:
        """Items without a group do not transfer surplus to anyone."""
        items = [
            make_item("lone", 9.0, group=None),
            make_item("other", 1.0, group=None),
        ]
        result = allocate(items, capacity=2)
        assert result.surplus_transfers == []


# ---------------------------------------------------------------------------
# Largest-remainder resolution
# ---------------------------------------------------------------------------


class TestLargestRemainder:
    def test_largest_weight_wins_single_slot(self) -> None:
        items = [
            make_item("small", 1.0),
            make_item("medium", 3.0),
            make_item("large", 6.0),
        ]
        result = allocate(items, capacity=1)
        assert result.retained == ["large"]

    def test_n_largest_weights_retained(self) -> None:
        items = [
            make_item("a", 1.0),
            make_item("b", 2.0),
            make_item("c", 3.0),
            make_item("d", 4.0),
        ]
        result = allocate(items, capacity=2)
        assert set(result.retained) == {"c", "d"}
        assert set(result.evicted) == {"a", "b"}

    def test_all_equal_weights_retains_correct_count(self) -> None:
        items = [make_item(f"m{i}", 1.0) for i in range(6)]
        result = allocate(items, capacity=4)
        assert len(result.retained) == 4
        assert len(result.evicted) == 2

    def test_capacity_matches_item_count_retains_all(self) -> None:
        items = [make_item(f"m{i}", 1.0) for i in range(4)]
        result = allocate(items, capacity=4)
        assert len(result.retained) == 4
        assert result.evicted == []


# ---------------------------------------------------------------------------
# Deterministic tie-breaking
# ---------------------------------------------------------------------------


class TestDeterministicTieBreaking:
    def test_same_input_same_output(self) -> None:
        """Identical inputs must always produce identical outputs."""
        t = datetime(2024, 6, 1, tzinfo=UTC)
        items_a = [make_item(f"m{i}", 1.0, created_at=t) for i in range(5)]
        items_b = [make_item(f"m{i}", 1.0, created_at=t) for i in range(5)]
        result_a = allocate(items_a, capacity=3)
        result_b = allocate(items_b, capacity=3)
        assert result_a.retained == result_b.retained
        assert result_a.evicted == result_b.evicted

    def test_repeated_calls_identical(self) -> None:
        """Calling allocate multiple times on the same items is idempotent."""
        t = datetime(2024, 3, 15, tzinfo=UTC)
        items = [make_item(f"item-{i:03d}", 1.0, created_at=t) for i in range(10)]
        r1 = allocate(items, capacity=5)
        # Re-create items to reset normalised_weight
        items2 = [make_item(f"item-{i:03d}", 1.0, created_at=t) for i in range(10)]
        r2 = allocate(items2, capacity=5)
        assert sorted(r1.retained) == sorted(r2.retained)
        assert sorted(r1.evicted) == sorted(r2.evicted)

    def test_all_equal_weights_deterministic(self) -> None:
        t = datetime(2024, 3, 15, tzinfo=UTC)
        items_run1 = [make_item(f"item-{i:03d}", 1.0, created_at=t) for i in range(10)]
        items_run2 = [make_item(f"item-{i:03d}", 1.0, created_at=t) for i in range(10)]
        r1 = allocate(items_run1, capacity=5)
        r2 = allocate(items_run2, capacity=5)
        assert sorted(r1.retained) == sorted(r2.retained)
        assert sorted(r1.evicted) == sorted(r2.evicted)

    def test_hash_based_ordering_stable_across_calls(self) -> None:
        """Two items with equal weight; ordering must not flip between calls."""
        t = datetime(2024, 1, 1, tzinfo=UTC)
        for _ in range(5):
            items = [make_item("alpha", 1.0, created_at=t), make_item("beta", 1.0, created_at=t)]
            result = allocate(items, capacity=1)
            assert len(result.retained) == 1
            # Whichever wins, it must be the *same* winner every time
        winner = result.retained[0]
        for _ in range(10):
            items2 = [
                make_item("alpha", 1.0, created_at=t),
                make_item("beta", 1.0, created_at=t),
            ]
            r = allocate(items2, capacity=1)
            assert r.retained[0] == winner


# ---------------------------------------------------------------------------
# AllocationResult structure
# ---------------------------------------------------------------------------


class TestAllocationResult:
    def test_result_is_dataclass(self) -> None:
        result = AllocationResult(retained=["a"], evicted=["b"])
        assert result.retained == ["a"]
        assert result.evicted == ["b"]
        assert result.surplus_transfers == []

    def test_surplus_transfers_default_empty(self) -> None:
        result = AllocationResult(retained=[], evicted=[])
        assert isinstance(result.surplus_transfers, list)
        assert len(result.surplus_transfers) == 0
