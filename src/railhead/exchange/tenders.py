"""World tenders: deterministic generation from an industry snapshot, and the sealed-bid auction.

A candidate is (destination industry I, cargo C) where I accepts C and some industry in a DIFFERENT region
produces C. ``distance`` is the Manhattan tile distance from I to the nearest such producer. Each quarter
:func:`generate_tenders` picks up to ``per_quarter`` candidates and sizes them with :class:`TenderConfig`.

Randomness comes only from :func:`draw` (SHA-256 over the seed and labels), so the result is the same on
every platform and Python version. Industries are sorted by id first; input order does not matter.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any

from .errors import ExchangeError
from .ledger import is_int

TICKS_PER_DAY = 74


@dataclass(frozen=True)
class Industry:
    id: int
    type: Any
    produces: tuple[int, ...]
    accepts: tuple[int, ...]
    tile: tuple[int, int]          # (x, y)
    region: int                    # region slot

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Industry:
        try:
            ind = cls(id=d["id"], type=d["type"], produces=tuple(d["produces"]), accepts=tuple(d["accepts"]),
                      tile=(d["tile"][0], d["tile"][1]), region=d["region"])
        except (KeyError, TypeError, IndexError) as e:
            raise ExchangeError("bad_snapshot", f"industry {d!r}: {e}") from None
        ints = [ind.id, ind.region, *ind.produces, *ind.accepts, *ind.tile]
        if not all(is_int(x) for x in ints):
            raise ExchangeError("bad_snapshot", f"industry {d!r}: ids, cargos, tile and region must be ints")
        return ind


@dataclass(frozen=True)
class TenderConfig:
    """Sizing formula (all integer):

    - ``quantity = max(1, (quantity_base + quantity_per_tile * distance) * (100 + j) // 100)``,
      j drawn uniformly from ``[-quantity_jitter_pct, +quantity_jitter_pct]``
    - ``max_price = quantity * (payout_base_per_unit + payout_per_unit_per_tile * distance)``
    - ``penalty_per_unit = max_price * penalty_bp // 10000 // quantity``
    - bids close ``bid_ticks`` after opening; delivery window = [bid close, bid close + ``window_ticks``]
    """
    per_quarter: int = 3
    quantity_base: int = 200
    quantity_per_tile: int = 0
    quantity_jitter_pct: int = 25
    payout_base_per_unit: int = 40
    payout_per_unit_per_tile: int = 2
    penalty_bp: int = 5000
    bid_ticks: int = 7 * TICKS_PER_DAY
    window_ticks: int = 90 * TICKS_PER_DAY

    def __post_init__(self) -> None:
        for k, v in asdict(self).items():
            if not is_int(v) or v < 0:
                raise ExchangeError("bad_config", f"TenderConfig.{k} must be a non-negative int: {v!r}")
        if self.window_ticks < 1:
            raise ExchangeError("bad_config", "TenderConfig.window_ticks must be >= 1")


def draw(n: int, *labels: Any) -> int:
    """A deterministic integer in [0, n) from the labels (seed, quarter, purpose, ...)."""
    if n <= 0:
        raise ValueError("n must be positive")
    h = hashlib.sha256("\x1f".join(map(str, labels)).encode()).digest()
    return int.from_bytes(h, "big") % n


def candidates(industries: list[dict[str, Any]] | list[Industry]) -> list[dict[str, int]]:
    inds = sorted((i if isinstance(i, Industry) else Industry.from_dict(i) for i in industries), key=lambda i: i.id)
    if len({i.id for i in inds}) != len(inds):
        raise ExchangeError("bad_snapshot", "duplicate industry id")
    out = []
    for dest in inds:
        for cargo in sorted(set(dest.accepts)):
            best = None
            for p in inds:
                if p.region != dest.region and cargo in p.produces:
                    d = abs(p.tile[0] - dest.tile[0]) + abs(p.tile[1] - dest.tile[1])
                    if best is None or (d, p.id) < best:
                        best = (d, p.id)
            if best is not None:
                out.append({"industry": dest.id, "cargo": cargo, "distance": best[0], "source_industry": best[1],
                            "region": dest.region})
    return out


def generate_tenders(industries: list[dict[str, Any]], *, seed: int, quarter: int, tick: int,
                     config: TenderConfig | None = None, first_index: int = 1) -> list[dict[str, Any]]:
    """Tenders for one quarter. Ids are ``T<n>`` from ``first_index`` upward."""
    cfg = config or TenderConfig()
    pool = candidates(industries)
    picked = []
    for i in range(min(cfg.per_quarter, len(pool))):
        picked.append(pool.pop(draw(len(pool), seed, quarter, "pick", i)))
    out = []
    for n, c in enumerate(picked):
        j = draw(2 * cfg.quantity_jitter_pct + 1, seed, quarter, "qty", n) - cfg.quantity_jitter_pct
        qty = max(1, (cfg.quantity_base + cfg.quantity_per_tile * c["distance"]) * (100 + j) // 100)
        max_price = qty * (cfg.payout_base_per_unit + cfg.payout_per_unit_per_tile * c["distance"])
        bid_close = tick + cfg.bid_ticks
        out.append({
            "tender_id": f"T{first_index + n}", "quarter": quarter, **c, "quantity": qty, "max_price": max_price,
            "penalty_per_unit": max_price * cfg.penalty_bp // 10000 // qty, "open_tick": tick,
            "bid_close": bid_close, "window_start": bid_close, "deadline": bid_close + cfg.window_ticks,
        })
    return out


def auction(bids: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Lowest price wins; ties go to the earliest bid tick, then the lowest company id. Log order inside one
    tick does not count, so agents that act in the same lockstep tick are equal."""
    if not bids:
        return None
    return min(bids, key=lambda b: (b["price"], b["tick"], b["company"]))
