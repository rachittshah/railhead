"""Reward functions: bounded in [0,1], monotone in the right quantity, and proper where claimed."""

import pytest

from railhead import rewards


def test_deliver_is_fraction_of_quantity_capped_at_one():
    assert rewards.deliver(0, 500) == 0.0
    assert rewards.deliver(250, 500) == 0.5
    assert rewards.deliver(900, 500) == 1.0


def test_bid_rewards_keeping_promises_and_punishes_overpromising():
    # newsvendor payout min(D,B) - (B-D)+, mapped affinely (not clipped!) into [0,1]
    assert rewards.bid(delivered=400, bid=400, scale=500) == pytest.approx(0.9)
    # overpromise by 100: pay for the shortfall
    assert rewards.bid(delivered=400, bid=500, scale=500) == pytest.approx(0.8)
    # underpromise: you only get paid for what you promised
    assert rewards.bid(delivered=400, bid=300, scale=500) == pytest.approx(0.8)
    assert rewards.bid(delivered=0, bid=500, scale=500) == 0.0
    # bids above the scale are capped (the instruction states the maximum bid)
    assert rewards.bid(delivered=900, bid=9000, scale=500) == 1.0


def test_bid_is_maximised_in_expectation_at_the_median():
    outcomes = [100, 200, 300, 400, 500]  # equally likely deliveries
    best = max(range(0, 501, 50), key=lambda b: sum(rewards.bid(d, b, 500) for d in outcomes))
    assert best == 300


def test_simops_normalises_between_broken_and_healthy():
    assert rewards.simops(k=50, k0=50, kstar=150) == 0.0
    assert rewards.simops(k=100, k0=50, kstar=150) == 0.5
    assert rewards.simops(k=400, k0=50, kstar=150) == 1.0
    assert rewards.simops(k=10, k0=50, kstar=150) == 0.0


def test_tycoon_is_zero_for_doing_nothing_and_half_for_matching_baseline():
    assert rewards.tycoon(w=1000, w_idle=1000, w_base=3000) == 0.0
    assert rewards.tycoon(w=3000, w_idle=1000, w_base=3000) == 0.5
    assert rewards.tycoon(w=5000, w_idle=1000, w_base=3000) == 1.0
    assert rewards.tycoon(w=-500, w_idle=1000, w_base=3000) == 0.0


def test_abstention_pays_only_when_the_task_is_infeasible():
    assert rewards.abstain(abstained=True, feasible=False) == 1.0
    assert rewards.abstain(abstained=False, feasible=False) == 0.0
    assert rewards.abstain(abstained=True, feasible=True) == 0.0
