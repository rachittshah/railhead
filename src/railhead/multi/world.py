"""SharedWorld: one World played by several companies, with the merged v3 log (docs/WAYBILL.md, section 4).

The GameScript acts and reads for one bound company. ``SharedWorld`` binds the company of each request
(``bind_company``) before it sends the request; the bound company is not in the checksum, so binding is
invisible to the replay. The caller (the coordinator) serialises all calls with one lock: the bridge
has one reply queue.

Every failure that leaves it unknown whether the engine applied a request raises ``WorldFault``, as in
:class:`railhead.world.World`; the owner then rebuilds the world from the log.
"""

from __future__ import annotations

from typing import Any

from railhead.actionlog import ActionLog
from railhead.bridge import BridgeError
from railhead.multi.barrier import Observation
from railhead.multi.log import LOG_V3, digests_v3
from railhead.world import World, WorldFault


def _month(ymd: str) -> int:
    return int(str(ymd).split("-")[1])


class SharedWorld:
    def __init__(self, world: World, companies: list[dict]) -> None:
        self.world = world
        self.wide_digests = False  # exchange rules 2: four notes per company record (digests_v3)
        self.companies = [dict(c) for c in companies]
        self.ids = [int(c["id"]) for c in companies]
        self.log: ActionLog | None = None
        self.bound: int | None = None  # unknown after load: bind before the first request
        self._refresh()

    def _refresh(self) -> None:
        st = self.world.state()
        self.ticks, self.date, self.ymd = int(st["ticks"]), int(st["date"]), str(st["ymd"])

    @property
    def month(self) -> int:
        return _month(self.ymd)

    # ------------------------------------------------------------------ log
    def attach_log(self, log: ActionLog, header: bool = True) -> None:
        self.log = log
        if header:
            log.write({"kind": "header", "v": LOG_V3, "companies": self.companies, "ticks": self.ticks,
                       "checksum": self.world.call("checksum")})

    def _write(self, rec: dict[str, Any]) -> dict[str, Any]:
        assert self.log is not None, "no log attached"
        return self.log.write(rec)

    # ------------------------------------------------------------------ requests
    def bind(self, company: int) -> None:
        if self.bound != company:
            self.bound = None
            try:
                self.world.call("bind_company", company=company)
            except Exception as e:
                raise WorldFault(f"bind_company {company}: {type(e).__name__}: {e}") from e
            self.bound = company

    def query(self, company: int, method: str, args: dict[str, Any] | None = None) -> Any:
        self.bind(company)
        return self.world.call_args(method, args or {})

    def act(self, company: int, method: str, args: dict[str, Any]) -> Any:
        """One player action of ``company``, logged. A GameScript refusal is logged and re-raised;
        any other failure raises WorldFault (see railhead.world.World.act)."""
        self.bind(company)
        rec = {"kind": "act", "ticks": self.ticks, "company": company, "m": method, "a": args}
        try:
            try:
                ret = self.world.call_args(method, args)
            except BridgeError as e:
                self._write(rec | {"ok": False, "code": e.code})
                raise
            self._write(rec | {"ok": True, "ret": ret})
            return ret
        except BridgeError:
            raise
        except Exception as e:
            raise WorldFault(f"{method}: {type(e).__name__}: {e}") from e

    def note(self, company: int, method: str, args: dict[str, Any]) -> None:
        """A meta decision of ``company``: its digest goes to the GameScript, then the record to the log."""
        try:
            for d in digests_v3(company, method, args, self.wide_digests):
                self.world.call_args("note", {"d": d})
            self._write({"kind": "meta", "ticks": self.ticks, "company": company, "m": method, "a": args})
        except Exception as e:
            raise WorldFault(f"note {method}: {type(e).__name__}: {e}") from e

    def wait_req(self, company: int, a: dict[str, Any], target: int, auto: bool = False) -> None:
        rec: dict[str, Any] = {"kind": "wait_req", "ticks": self.ticks, "company": company, "a": a, "target": target}
        if auto:
            rec["auto"] = True
        try:
            self._write(rec)
        except Exception as e:
            raise WorldFault(f"wait_req: {type(e).__name__}: {e}") from e

    # ------------------------------------------------------------------ the market
    def ex_record(self, company: int | None, x: dict[str, Any]) -> dict[str, Any]:
        """Log one exchange record. A company record also sends its digest to the GameScript (as a meta
        does), so a company record that is added, moved or changed changes every later step checksum."""
        try:
            if company is not None:
                for d in digests_v3(company, "ex", x, self.wide_digests):
                    self.world.call_args("note", {"d": d})
            return self._write({"kind": "ex", "ticks": self.ticks, "company": company, "x": x})
        except Exception as e:
            raise WorldFault(f"ex {x.get('op')}: {type(e).__name__}: {e}") from e

    def monitor_add(self, company: int, cargo: int, industry: int) -> int | None:
        """An engine cargo monitor of the deliveries of ``company`` (not in the checksum). None if the
        GameScript refuses it (for example, the industry closed)."""
        try:
            return int(self.world.call_args("monitor_add", {"kind": "industry_delivery", "cargo": int(cargo),
                                                            "target": int(industry), "company": int(company)}))
        except BridgeError:
            return None
        except Exception as e:
            raise WorldFault(f"monitor_add: {type(e).__name__}: {e}") from e

    def monitor_rows(self) -> list[dict]:
        try:
            return list(self.world.call("monitor_read"))
        except Exception as e:
            raise WorldFault(f"monitor_read: {type(e).__name__}: {e}") from e

    def industries(self) -> list[dict]:
        try:
            return list(self.world.call("industries"))
        except Exception as e:
            raise WorldFault(f"industries: {type(e).__name__}: {e}") from e

    # ------------------------------------------------------------------ the clock
    def advance(self, n: int) -> tuple[int, Observation]:
        """Step exactly ``n`` ticks (no wake conditions in the GameScript) and observe the result.
        Returns (start tick, observation). Not logged: the caller decides the wakers, then calls
        :meth:`log_step`. Any failure raises WorldFault."""
        t0 = self.ticks
        try:
            self.world.bridge.drain_events()  # events before the step are not part of it
            out = self.world.step(ticks=n)
            events = self.world.bridge.drain_events()
            self.ticks, self.date, self.ymd = int(out["ticks"]), int(out["date"]), str(out["ymd"])
            if self.ticks != t0 + n:
                raise RuntimeError(f"step of {n} ticks from {t0} ended at {self.ticks}")
            rows = self.world.call("companies")
        except Exception as e:
            raise WorldFault(f"step: {type(e).__name__}: {e}") from e
        self._rows = {int(r["id"]): r for r in rows}
        return t0, Observation(month=self.month, cash={c: int(r["money"]) for c, r in self._rows.items()},
                               events=events)

    def seal(self) -> dict:
        """The closing checkpoint (exchange rules 2): the checksum of the world as the log leaves it, so the
        records after the last step are bound too."""
        try:
            return self._write({"kind": "seal", "ticks": self.ticks, "checksum": self.world.call("checksum")})
        except Exception as e:
            raise WorldFault(f"seal record: {type(e).__name__}: {e}") from e

    def log_step(self, t0: int, wakers: list[tuple[int, str]]) -> dict:
        try:
            snap = {str(c): {"money": int(self._rows[c]["money"]), "loan": int(self._rows[c]["loan"])}
                    for c in self.ids if c in self._rows}
            return self._write({"kind": "step", "ticks": t0, "end_ticks": self.ticks,
                                "checksum": self.world.call("checksum"),
                                "wakers": [{"company": c, "reason": r} for c, r in wakers], "snap": snap})
        except Exception as e:
            raise WorldFault(f"step record: {type(e).__name__}: {e}") from e

    def net_worth(self) -> dict[int, int]:
        rows = {int(r["id"]): r for r in self.world.call("companies")}
        return {c: int(rows[c]["money"]) - int(rows[c]["loan"]) if c in rows else 0 for c in self.ids}

    def close(self) -> None:
        try:
            if self.log is not None:
                self.log.close()
        finally:
            self.world.close()


class WorldProbe:
    """The franchise probe (railhead.multi.regions.Probe) over a shared world: read-only queries for the
    company that acts. The coordinator and the replay send the same queries at the same tick."""

    def __init__(self, sw: SharedWorld, company: int) -> None:
        self.sw, self.company = sw, company
        self._towns: dict[int, tuple[int, int]] | None = None

    def tile(self, index: int) -> dict:
        try:
            return self.sw.query(self.company, "tile", {"tile": int(index), "radius": 0})
        except BridgeError:
            return {}

    def town(self, town_id: int) -> tuple[int, int] | None:
        if self._towns is None:
            self._towns = {int(t["id"]): (int(t["x"]), int(t["y"])) for t in self.sw.query(self.company, "towns")}
        return self._towns.get(int(town_id))
