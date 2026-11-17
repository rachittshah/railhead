"""The wait barrier of the Waybill coordinator (docs/WAYBILL.md, section 4.2). Pure Python, no engine.

Each agent's ``wait`` registers a :class:`Waiter` with a target tick. When every live company waits,
the coordinator steps the world by :meth:`Barrier.next_ticks` ticks: to the earliest target, at most
``chunk_ticks`` when a waiter has wake conditions, and never past the horizon. After the step,
:meth:`Barrier.wake` returns the companies to wake: target reached (``elapsed``), a wake condition of
their own (fallback: evaluated in Python after each chunk from an :class:`Observation`, because the
GameScript cannot scope its wake events to one company), or the horizon (``horizon``).

The wall clock is injectable (``clock``), so the straggler rule (:meth:`Barrier.overdue`) is testable.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from railhead.multi.log import TICKS_PER_DAY

DEFAULT_CHUNK_DAYS = 7


@dataclass
class Waiter:
    company: int
    target: int
    until: list[str] = field(default_factory=list)
    start: int = 0
    month: int | None = None  # the month when the wait started (for new_month)
    vehicles: frozenset[int] = frozenset()  # the company's vehicles when the wait started (vehicle_crashed)
    stations: frozenset[int] = frozenset()  # the company's stations when the wait started (station_first_vehicle)
    auto: bool = False
    ex_seq: int = 0  # the exchange record count when the wait started (offer_received, message_received)


@dataclass
class Observation:
    """What the coordinator reads after a step: the month, the cash of each company, and the
    GameScript events of the step (``{"t": "ev", "k": kind, ...}``)."""

    month: int | None = None
    cash: dict[int, int] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    market: dict[int, str] = field(default_factory=dict)  # company -> market wake reason at this tick


def fired(w: Waiter, obs: Observation) -> str | None:
    """The first wake condition of ``w`` that ``obs`` satisfies, or None."""
    for item in w.until:
        kind, _, value = item.partition(":")
        if kind == "cash_below":
            if w.company in obs.cash and obs.cash[w.company] < int(value):
                return kind
        elif kind == "new_month":
            if w.month is not None and obs.month is not None and obs.month != w.month:
                return kind
        else:
            for ev in obs.events:
                if ev.get("k") != kind:
                    continue
                if kind in ("industry_open", "industry_close"):
                    return kind
                if kind in ("company_bankrupt", "company_in_trouble") and ev.get("company") == w.company:
                    return kind
                if kind == "vehicle_crashed" and ev.get("vehicle") in w.vehicles:
                    return kind
                if kind == "station_first_vehicle" and ev.get("station") in w.stations:
                    return kind
    return None


class Barrier:
    def __init__(self, companies: Iterable[int], horizon_tick: int | None = None,
                 chunk_ticks: int = DEFAULT_CHUNK_DAYS * TICKS_PER_DAY,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.live: set[int] = set(companies)
        self.horizon_tick = horizon_tick
        self.chunk_ticks = chunk_ticks
        self.clock = clock
        self.waiting: dict[int, Waiter] = {}
        now = clock()
        self.turn_start: dict[int, float] = {c: now for c in self.live}

    def add(self, w: Waiter) -> None:
        if w.company not in self.live:
            raise ValueError(f"company {w.company} is not live")
        if w.company in self.waiting:
            raise ValueError(f"company {w.company} already waits")
        self.waiting[w.company] = w

    def submit(self, company: int) -> None:
        self.live.discard(company)
        self.waiting.pop(company, None)

    def at_horizon(self, now: int) -> bool:
        return self.horizon_tick is not None and now >= self.horizon_tick

    def ready(self, now: int) -> bool:
        """True when the world must step: every live company waits, and the horizon is not reached."""
        return bool(self.live) and self.live <= set(self.waiting) and not self.at_horizon(now)

    def next_ticks(self, now: int, cap: int | None = None) -> int:
        """Ticks to the next stop. ``cap``: a tick the world must stop at (a market deadline or quarter)."""
        end = min(w.target for w in self.waiting.values())
        if cap is not None and cap > now:
            end = min(end, cap)
        if any(w.until for w in self.waiting.values()):
            end = min(end, now + self.chunk_ticks)
        if self.horizon_tick is not None:
            end = min(end, self.horizon_tick)
        return max(1, end - now)

    def wake(self, now: int, obs: Observation | None = None) -> list[tuple[int, str]]:
        """Remove and return ``(company, reason)`` for each waiter that wakes at tick ``now``."""
        obs = obs or Observation()
        out: list[tuple[int, str]] = []
        horizon = self.at_horizon(now)
        for c in sorted(self.waiting):
            w = self.waiting[c]
            reason = "horizon" if horizon else "elapsed" if w.target <= now else fired(w, obs) or obs.market.get(c)
            if reason is not None:
                out.append((c, reason))
        t = self.clock()
        for c, _ in out:
            del self.waiting[c]
            self.turn_start[c] = t
        return out

    def overdue(self, budget_s: float | None) -> list[int]:
        """Live companies that do not wait and whose turn has lasted more than ``budget_s`` seconds."""
        if budget_s is None:
            return []
        t = self.clock()
        return sorted(c for c in self.live - set(self.waiting) if t - self.turn_start.get(c, t) > budget_s)
