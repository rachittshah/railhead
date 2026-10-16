"""Reward functions. All return floats in [0, 1]; all inputs come from the replayed engine ledger.

``bid`` is a newsvendor contract: payout ``min(D, B) - (B - D)+`` for delivery D against promise B.
Its expectation is maximised by bidding the *median* of your own delivery distribution, i.e. it is a
(pinball-type) proper scoring rule on the agent's beliefs about the consequences of its own plan.
It is mapped into [0,1] *affinely* — clipping would break properness.
"""

from __future__ import annotations


def _clip(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else float(x)


def deliver(delivered: float, quantity: float) -> float:
    return _clip(delivered / quantity) if quantity > 0 else 0.0


def bid(delivered: float, bid: float, scale: float) -> float:
    b = min(max(bid, 0.0), scale)
    payout = min(delivered, b) - max(0.0, b - delivered)  # in [-scale, scale]
    return _clip(0.5 + payout / (2.0 * scale))


def simops(k: float, k0: float, kstar: float) -> float:
    """0 = no better than leaving the fault in place; 1 = as good as the fault-free network."""
    if kstar <= k0:
        return 1.0 if k >= kstar else 0.0
    return _clip((k - k0) / (kstar - k0))


def tycoon(w: float, w_idle: float, w_base: float) -> float:
    """0 = no better than doing nothing; 0.5 = scripted baseline; 1 = twice the baseline's gain."""
    gain_base = max(w_base - w_idle, 1.0)
    return _clip(0.5 * (w - w_idle) / gain_base)


def abstain(abstained: bool, feasible: bool) -> float:
    return 1.0 if (abstained and not feasible) else 0.0
