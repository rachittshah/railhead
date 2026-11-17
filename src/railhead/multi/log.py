"""Merged multi-company action log (log v3) and its ordering rules (docs/WAYBILL.md, section 2).

JSONL, one record per line, written by the coordinator only:
  {"kind":"header","v":3,"companies":[{"id":C,"name":N,"slot":S},...],"ticks":T,"checksum":H}
  {"kind":"act","seq":n,"ticks":T,"company":C,"m":"road_line","a":{...},"ok":true,"ret":{...}|"code":"ERR_.."}
  {"kind":"meta","seq":n,"ticks":T,"company":C,"m":"submit","a":{...}}
  {"kind":"wait_req","seq":n,"ticks":T,"company":C,"a":{"days":30,"until":[...]},"target":T2[,"auto":true]}
  {"kind":"step","seq":n,"ticks":T0,"end_ticks":T1,"checksum":H,"wakers":[{"company":C,"reason":R},...]}

Only ``step`` records move the clock. ``wait_req`` records and ``wakers`` have no effect on the world:
the replay checks them for structure only (:class:`LogState`). They let a reader rebuild who waited for
what, and they make the lockstep rule checkable: a step happens only when every live company waits.

This module is pure Python (no engine): the replay and the coordinator recovery both use
:class:`LogState`, and the unit tests drive it directly.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from railhead.actions import ActionArgError, check_until

LOG_V3 = 3
TICKS_PER_DAY = 74
AUTO_WAIT_DAYS = 7  # the wait that the coordinator records for an agent that runs out of turn time
WAKE_REASONS_FIXED = {"elapsed", "horizon"}


class LogRejected(ValueError):
    """The log breaks a v3 ordering rule: no honest coordinator writes it."""


def meta_digest_v3(company: int, m: str, a: dict[str, Any]) -> int:
    """31-bit nonzero digest of one meta decision of one company. The company is in the digest, so a
    ``submit`` moved to another company changes every later step checksum."""
    blob = json.dumps({"c": company, "m": m, "a": a}, separators=(",", ":"), sort_keys=True).encode()
    return int.from_bytes(hashlib.sha256(blob).digest()[:4], "little") & 0x7FFFFFFF or 1


def _int(v: object, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise LogRejected(f"{what} must be an integer, got {v!r}")
    return v


def wait_args(a: object) -> tuple[int, list[str] | None]:
    """(length in ticks, canonical until) of the arguments of one wait request, or LogRejected.
    ``a`` has exactly one of ``days`` or ``ticks`` (a positive integer) and an optional ``until``."""
    if not isinstance(a, dict):
        raise LogRejected("wait arguments are not an object")
    unknown = set(a) - {"days", "ticks", "until"}
    if unknown:
        raise LogRejected(f"wait: unknown argument(s) {sorted(unknown)}")
    if ("days" in a) == ("ticks" in a):
        raise LogRejected("wait needs exactly one of days or ticks")
    n = _int(a["days"] if "days" in a else a["ticks"], "wait length")
    if n < 1:
        raise LogRejected("wait length must be 1 or more")
    try:
        until = check_until(a.get("until"))
    except ActionArgError as e:
        raise LogRejected(str(e)) from None
    return (n * TICKS_PER_DAY if "days" in a else n), until


def wait_target(now: int, a: dict, horizon_tick: int | None) -> int:
    """The tick at which a wait that starts at ``now`` ends, capped at the horizon."""
    n, _ = wait_args(a)
    end = now + n
    return end if horizon_tick is None else min(end, horizon_tick)


def check_companies(header: dict) -> dict[int, dict]:
    comps = header.get("companies")
    if not isinstance(comps, list) or not comps:
        raise LogRejected("v3 header has no companies")
    out: dict[int, dict] = {}
    slots: set[int] = set()
    for c in comps:
        if not isinstance(c, dict):
            raise LogRejected("a header company is not an object")
        cid, slot = _int(c.get("id"), "company id"), _int(c.get("slot"), "company slot")
        if cid in out or slot in slots:
            raise LogRejected(f"company {cid} or slot {slot} is listed twice")
        if not isinstance(c.get("name"), str):
            raise LogRejected(f"company {cid} has no name")
        out[cid] = dict(c)
        slots.add(slot)
    return out


class LogState:
    """The ordering rules of a v3 log, as a state machine. ``check(record)`` raises :class:`LogRejected`
    for a record that breaks a rule, else it applies the record (a ``step`` moves ``now``).

    Rules (docs/WAYBILL.md, section 2.3):

    * ``seq`` is contiguous from 1 (the header is line 0); every record is at the current tick ``now``.
    * ``act``, ``meta`` and ``wait_req`` name a company of the header that is not waiting and has not
      submitted. ``act`` and ``wait_req`` are refused at or after the horizon.
    * ``wait_req``: valid arguments, and ``target`` is ``now`` plus the length, capped at the horizon.
      The company then waits until a ``step`` names it in ``wakers``.
    * ``step``: ``end_ticks > ticks``; a checksum; every live company (not submitted) waits;
      ``end_ticks`` is not after the earliest target and not after the horizon; ``wakers`` are waiting
      companies, each once; a waiter whose target is reached is a waker with reason ``elapsed``; a waker
      whose target is not reached has a reason from its own ``until`` list; at the horizon every waiter
      wakes with reason ``horizon``.
    """

    def __init__(self, header: dict, horizon_tick: int | None = None) -> None:
        if header.get("kind") != "header":
            raise LogRejected("the first record of the log is not a header")
        if header.get("v") != LOG_V3 or isinstance(header.get("v"), bool):
            raise LogRejected(f"not a v3 log (v={header.get('v')!r})")
        self.companies = check_companies(header)
        self.now = _int(header.get("ticks"), "header ticks")
        self.horizon_tick = horizon_tick
        self.waiting: dict[int, dict] = {}  # company -> {"target", "until", "ticks", "auto"}
        self.submitted: set[int] = set()
        self.expect_seq = 1

    @property
    def live(self) -> set[int]:
        return set(self.companies) - self.submitted

    def _company(self, r: dict, seq: int) -> int:
        c = r.get("company")
        if isinstance(c, bool) or not isinstance(c, int) or c not in self.companies:
            raise LogRejected(f"seq={seq}: company {c!r} is not in the header")
        if c in self.waiting:
            raise LogRejected(f"seq={seq}: company {c} is waiting and cannot {r.get('kind')}")
        if c in self.submitted:
            raise LogRejected(f"seq={seq}: company {c} has submitted")
        return c

    def _before_horizon(self, seq: int) -> None:
        if self.horizon_tick is not None and self.now >= self.horizon_tick:
            raise LogRejected(f"seq={seq}: at or after the horizon (tick {self.horizon_tick})")

    def check(self, r: dict) -> None:
        kind = r.get("kind")
        seq = r.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq != self.expect_seq:
            raise LogRejected(f"record seq={seq!r} is out of sequence (expected {self.expect_seq})")
        if kind == "header":
            raise LogRejected(f"second header at seq={seq}")
        if _int(r.get("ticks"), f"ticks of seq={seq}") != self.now:
            raise LogRejected(f"record seq={seq} at tick {r.get('ticks')}, the clock is at {self.now}")
        if kind == "act":
            self._company(r, seq)
            self._before_horizon(seq)
            if not isinstance(r.get("m"), str) or not isinstance(r.get("a", {}), dict):
                raise LogRejected(f"act seq={seq}: no method or arguments")
        elif kind == "meta":
            c = self._company(r, seq)
            if not isinstance(r.get("m"), str) or not isinstance(r.get("a", {}), dict):
                raise LogRejected(f"meta seq={seq}: no decision or arguments")
            if r["m"] == "submit":
                self.submitted.add(c)
        elif kind == "wait_req":
            c = self._company(r, seq)
            self._before_horizon(seq)
            _, until = wait_args(r.get("a"))
            target = _int(r.get("target"), f"target of seq={seq}")
            expect = wait_target(self.now, r["a"], self.horizon_tick)
            if self.horizon_tick is not None and target != expect:
                raise LogRejected(f"wait_req seq={seq}: target {target}, expected {expect}")
            if not self.now < target <= expect:
                raise LogRejected(f"wait_req seq={seq}: target {target} is not in ({self.now}, {expect}]")
            if "auto" in r and not isinstance(r["auto"], bool):
                raise LogRejected(f"wait_req seq={seq}: auto must be true or false")
            self.waiting[c] = {"target": target, "until": until or [], "ticks": self.now,
                               "auto": bool(r.get("auto"))}
        elif kind == "step":
            self._step(r, seq)
        else:
            raise LogRejected(f"unknown record kind {kind!r}")
        self.expect_seq = seq + 1

    def _step(self, r: dict, seq: int) -> None:
        end = _int(r.get("end_ticks"), f"end_ticks of seq={seq}")
        if end <= self.now:
            raise LogRejected(f"step seq={seq} does not advance time")
        ck = r.get("checksum")
        if not isinstance(ck, dict) or "h" not in ck:
            raise LogRejected(f"step seq={seq} has no checksum")
        live = self.live
        if not live:
            raise LogRejected(f"step seq={seq}: no live company")
        idle = sorted(live - set(self.waiting))
        if idle:
            raise LogRejected(f"step seq={seq}: companies {idle} are not waiting (lockstep)")
        earliest = min(w["target"] for w in self.waiting.values())
        if end > earliest:
            raise LogRejected(f"step seq={seq} ends at {end}, after the earliest wait target {earliest}")
        if self.horizon_tick is not None and end > self.horizon_tick:
            raise LogRejected(f"step seq={seq} ends at {end}, past the horizon (tick {self.horizon_tick})")
        at_horizon = self.horizon_tick is not None and end >= self.horizon_tick
        wakers = r.get("wakers")
        if not isinstance(wakers, list):
            raise LogRejected(f"step seq={seq}: wakers is not a list")
        seen: dict[int, str] = {}
        for w in wakers:
            if not isinstance(w, dict):
                raise LogRejected(f"step seq={seq}: a waker is not an object")
            c, reason = w.get("company"), w.get("reason")
            if isinstance(c, bool) or not isinstance(c, int) or c not in self.waiting or c in seen:
                raise LogRejected(f"step seq={seq}: waker {c!r} is not a waiting company (or is listed twice)")
            if not isinstance(reason, str):
                raise LogRejected(f"step seq={seq}: waker {c} has no reason")
            wt = self.waiting[c]
            if at_horizon:
                ok = reason == "horizon"
            elif wt["target"] <= end:
                ok = reason == "elapsed"
            else:
                ok = reason in {u.partition(":")[0] for u in wt["until"]}
            if not ok:
                raise LogRejected(f"step seq={seq}: company {c} cannot wake with reason {reason!r}")
            seen[c] = reason
        for c, wt in self.waiting.items():
            if (at_horizon or wt["target"] <= end) and c not in seen:
                raise LogRejected(f"step seq={seq}: company {c} reached its wait target but did not wake")
        for c in seen:
            del self.waiting[c]
        self.now = end
