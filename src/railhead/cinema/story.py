"""Cinema: what each frame shows, as pure functions of the samples (no engine, no matplotlib).

* :func:`events_of` turns the replay's exchange state into timed events at their exact ticks: licences
  signed and ended, offers, amendments, instalments paid on time / late / collected by the exchange,
  broken and partial obligations, payments that name no obligation (a late payment with a memo), tender
  awards and their settlements, and messages.
* :func:`licences_of` gives each signed licence with its rectangle (clipped to the grantor's region) and
  the interval in which it is in force; :func:`contract_layer` says, for a frame, which licences glow,
  which arcs carry pulses and which carry scars.
* :func:`warp_schedule` maps video frames to ticks: uniform game time, slower around key moments, faster
  in the run-off. :func:`captions_of` writes the short captions; :func:`camera_at` eases a slow zoom.
* :func:`excerpt` makes a short, public-safe quote of an agent's message (or nothing).
"""

from __future__ import annotations

import bisect
import math
import re
from dataclasses import dataclass, field

TICKS_PER_DAY = 74
EXCHANGE = "exchange"

PULSE_KINDS = ("paid", "collected", "late", "broken", "partial", "plain_late")


@dataclass(frozen=True)
class Event:
    t: int
    kind: str  # see events_of
    a: str | None = None  # the company that acts or pays (exchange id: "0", "1", ...)
    b: str | None = None  # the other party
    ref: str = ""  # contract, obligation or tender id
    amount: int = 0
    text: str = ""  # message text or payment memo (raw: use excerpt())
    contract: str = ""


@dataclass
class Licence:
    cid: str
    grantor: str
    grantee: str
    rect: list[int]  # x0, y0, x1, y1, tiles, both corners included
    start: int  # signed tick
    end: int  # first tick no longer in force (deadline or earlier settlement)
    fee: int
    amended: list[int] = field(default_factory=list)


def _settle_tick(c: dict) -> int | None:
    ticks = [e["tick"] for e in c.get("events") or [] if e.get("event") == "settle"]
    return ticks[-1] if ticks else None


def clip_rect(rect: list[int], regions: list[list[int]]) -> list[int]:
    """The part of ``rect`` inside the union's bounding box of ``regions`` (the grantor's land); ``rect``
    if there are no regions."""
    if not regions:
        return list(rect)
    best, area = list(rect), -1
    for r in regions:
        x0, y0 = max(rect[0], r[0]), max(rect[1], r[1])
        x1, y1 = min(rect[2], r[2]), min(rect[3], r[3])
        if x0 <= x1 and y0 <= y1 and (x1 - x0 + 1) * (y1 - y0 + 1) > area:
            best, area = [x0, y0, x1, y1], (x1 - x0 + 1) * (y1 - y0 + 1)
    return best


def licences_of(state: dict, region_rects=None) -> list[Licence]:
    """Signed licences. ``region_rects(slot_or_id: str, tick) -> rects`` clips each to its grantor's region."""
    out = []
    for c in (state.get("contracts") or {}).values():
        if c.get("kind") != "licence" or c.get("signed_tick") is None:
            continue
        hist = c.get("terms_history") or [{"terms": c["terms"]}]
        terms = hist[-1]["terms"]
        start = int(c["signed_tick"])
        settled = _settle_tick(c)
        end = int(terms["deadline"]) if settled is None else min(int(terms["deadline"]), settled)
        rect = list(terms["rect"])
        if region_rects is not None:
            rect = clip_rect(rect, region_rects(str(terms["grantor"]), start))
        amended = [e["tick"] for e in c.get("events") or [] if e.get("event") == "amend_sign"]
        out.append(Licence(c["id"], str(terms["grantor"]), str(terms["grantee"]), rect, start, max(end, start + 1),
                           int(terms.get("fee", 0)), amended))
    return sorted(out, key=lambda lc: (lc.start, lc.cid))


def events_of(state: dict) -> list[Event]:
    """Every event of the exchange state, sorted by tick (stable)."""
    ev: list[Event] = []
    contracts = state.get("contracts") or {}
    for c in contracts.values():
        t = c.get("terms") or {}
        if c.get("tender_id") is None:
            ev.append(Event(int(c["created_tick"]), "offer", str(c["offerer"]), c.get("to"), c["id"],
                            int(t.get("fee") or t.get("price") or 0), contract=c["id"]))
        if c.get("kind") == "licence" and c.get("signed_tick") is not None:
            ev.append(Event(int(c["signed_tick"]), "licence_signed", str(t["grantee"]), str(t["grantor"]), c["id"],
                            int(t.get("fee", 0)), contract=c["id"]))
            for e in c.get("events") or []:
                if e.get("event") == "amend_sign":
                    ev.append(Event(int(e["tick"]), "amended", str(t["grantee"]), str(t["grantor"]), c["id"],
                                    int(t.get("fee", 0)), contract=c["id"]))
        if c.get("tender_id") is not None and c.get("signed_tick") is not None:
            ev.append(Event(int(c["signed_tick"]), "tender_won", str(t["seller"]), EXCHANGE, c["id"],
                            int(t.get("price") or 0), contract=c["id"]))
            st = _settle_tick(c)
            if st is not None and c.get("outcome"):
                ev.append(Event(st, f"tender_{c['outcome']}", str(t["seller"]), EXCHANGE, c["id"], contract=c["id"]))
    tagged: dict[tuple, int] = {}
    for o in (state.get("obligations") or {}).values():
        if o.get("status") == "replaced":
            continue
        payer, payee, cid = str(o["payer"]), str(o["payee"]), o["contract_id"]
        for tick, amount in o.get("payments") or []:
            kind = "paid" if tick < o["settle_tick"] else "late"
            ev.append(Event(int(tick), kind, payer, payee, o["id"], int(amount), contract=cid))
            key = (payer, payee, int(tick), int(amount))
            tagged[key] = tagged.get(key, 0) + 1
        if o.get("collected"):
            ev.append(Event(int(o["settled_tick"]), "collected", payer, payee, o["id"], int(o["collected"]),
                            contract=cid))
        if o.get("status") in ("broken", "partial"):
            ev.append(Event(int(o["settled_tick"]), o["status"], payer, payee, o["id"],
                            int(o["amount"]) - int(o.get("paid", 0)), contract=cid))
    broken_pairs = {(e.a, e.b): e.t for e in sorted(ev, key=lambda e: -e.t) if e.kind in ("broken", "partial")}
    for h in ((state.get("ledger") or {}).get("history") or []):
        if h.get("reason") != "pay":
            continue
        key = (str(h["src"]), str(h["dst"]), int(h["tick"]), int(h["amount"]))
        if tagged.get(key):
            tagged[key] -= 1
            continue
        first_break = broken_pairs.get((key[0], key[1]))
        kind = "plain_late" if first_break is not None and first_break <= key[2] else "plain"
        ev.append(Event(key[2], kind, key[0], key[1], "", key[3], text=str(h.get("ref") or "")))
    for m in state.get("messages") or []:
        ev.append(Event(int(m["tick"]), "message", str(m["from"]), str(m["to"]), str(m.get("seq", "")),
                        text=str(m["text"])))
    order = {k: i for i, k in enumerate(("offer", "message", "licence_signed", "amended", "tender_won", "paid",
                                         "collected", "plain", "late", "plain_late", "partial", "broken"))}
    return sorted(ev, key=lambda e: (e.t, order.get(e.kind, 99)))


# ============================================================================================ excerpts
_UNSAFE = re.compile(r"(/(?:Users|home|private|tmp|var)/|https?://|www\.|@|sk-[A-Za-z0-9]|\\|`|claude\.ai)",
                     re.IGNORECASE)


def excerpt(text: str, limit: int = 96) -> str | None:
    """A short public-safe quote: the first sentence (or clause), whitespace collapsed, at most ``limit``
    characters, cut at a word with an ellipsis. None if the text holds a path, a link, an address, a key,
    code or a character outside Latin-1 punctuation (the HUD font may lack it)."""
    if not text or _UNSAFE.search(text):
        return None
    t = " ".join(str(text).split())
    t = re.sub(r"^(Company [A-H]|[A-H])\s*[:,\-]\s*", "", t)  # a signature the HUD already shows
    t = re.sub(r"\s*\b(due )?(at )?tick \d+", "", t)  # engine ticks mean nothing to a viewer
    if any(ord(ch) > 0x2122 for ch in t):
        return None
    m = re.match(r"(.+?[.!?])(\s|$)", t)
    s = m.group(1) if m and len(m.group(1)) >= 24 else t
    if len(s) > limit:
        cut = s[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:-")
        s = cut + "…"
    return s.replace("$", "＄") if "$" in s else s


# ============================================================================================ timing
def smoothstep(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def ease_in_out(x: float) -> float:
    """Cubic ease, 0 -> 1."""
    x = min(1.0, max(0.0, x))
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def warp_schedule(n_frames: int, t0: int, t1: int, horizon: int, key_ticks: list[int], hold_start: int,
                  hold_end: int, runoff_weight: float = 0.35, slow: float = 2.6, slow_days: float = 20.0) -> list[float]:
    """Tick shown in each frame. Video time per game day is the weight of that day: 1 in play,
    ``runoff_weight`` after ``horizon``, up to ``1 + slow`` within ``slow_days`` of a key tick (a smooth
    bump). The schedule is monotone, starts at ``t0`` and ends at ``t1``, with still holds at both ends."""
    motion = n_frames - hold_start - hold_end
    if motion < 2 or t1 <= t0:
        raise ValueError("not enough frames or no time span")
    days = max(1, math.ceil((t1 - t0) / TICKS_PER_DAY))
    keys = sorted(key_ticks)
    w = []
    for d in range(days):
        t = t0 + (d + 0.5) * TICKS_PER_DAY
        base = 1.0 if t <= horizon else runoff_weight
        i = bisect.bisect_left(keys, t)
        near = min((abs(t - keys[j]) / TICKS_PER_DAY for j in (i - 1, i) if 0 <= j < len(keys)), default=1e9)
        bump = slow * (1 - smoothstep(near / slow_days)) if near < slow_days else 0.0
        w.append(base + bump)
    cum = [0.0]
    for x in w:
        cum.append(cum[-1] + x)
    total = cum[-1]
    out = []
    for k in range(motion):
        target = total * k / (motion - 1)
        j = min(days - 1, max(0, bisect.bisect_right(cum, target) - 1))
        frac = (target - cum[j]) / w[j] if w[j] else 0.0
        out.append(min(float(t1), t0 + (j + frac) * TICKS_PER_DAY))
    out[-1] = float(t1)
    return [float(t0)] * hold_start + out + [float(t1)] * hold_end


def frames_of(events: list[Event], sched: list[float]) -> list[int]:
    """The first frame that shows each event's tick (the last frame if it is past the end)."""
    return [min(bisect.bisect_left(sched, e.t), len(sched) - 1) for e in events]


# ============================================================================================ contract layer
@dataclass
class Pulse:
    licence: str | None  # licence id whose arc carries it, or None (a spark between regions)
    kind: str
    a: str
    b: str
    progress: float  # 0..1 along the arc, from payer to payee
    amount: int


@dataclass
class Layer:
    glowing: list[tuple[Licence, float]]  # licence, intensity 0..1 (a flash when signed, fades after its end)
    pulses: list[Pulse]
    scars: dict[str, list[str]]  # licence id -> scar kinds ("broken", "partial"), in order
    flashes: list[tuple[str, str, float]]  # (licence id, kind, strength 0..1): the red flash of a break
    sparks: list[tuple[str, str, float, str]]  # (from, to, progress, kind) for messages and offers
    tenders: list[tuple[Event, float, str | None]]  # (award, age 0..1 of its flash, outcome or None)


def licence_for(lics: list[Licence], e: Event) -> Licence | None:
    """The licence whose arc an obligation event belongs to: the contract it names, else the latest one
    between the two parties signed by the event's tick."""
    for lc in lics:
        if e.contract and lc.cid == e.contract:
            return lc
    pair = [lc for lc in lics if {lc.grantor, lc.grantee} == {e.a, e.b} and lc.start <= e.t]
    return pair[-1] if pair else None


def contract_layer(frame: int, now: float, lics: list[Licence], events: list[Event], ev_frames: list[int],
                   fps: int = 30, pulse_s: float = 1.1, flash_s: float = 1.4, spark_s: float = 0.8,
                   fade_s: float = 1.0, ghost: float = 0.22, spark_frames: dict[int, int] | None = None) -> Layer:
    """What the contract layer draws in ``frame`` (showing tick ``now``). Pulses, flashes and sparks are
    timed in video frames from their event's first frame, so a fast or slow stretch of game time does
    not change how long they last. A licence that ended stays as a faint ghost arc (``ghost``) so its
    scars stay visible."""
    glowing = []
    for lc in lics:
        if now < lc.start:
            continue
        fi = _frame_at(events, ev_frames, "licence_signed", lc.cid)
        age = (frame - fi) / fps if fi is not None else 99.0
        flash = 1.0 + 0.8 * max(0.0, 1 - age / flash_s)
        if now < lc.end:
            glowing.append((lc, flash))
        else:
            fe = _first_frame_after(ev_frames, events, lc.end)
            out_age = (frame - fe) / fps if fe is not None else 99.0
            glowing.append((lc, max(ghost, 1 - out_age / fade_s * (1 - ghost))))
    pulses, scars, flashes, sparks, tenders = [], {}, [], [], []
    for e, f in zip(events, ev_frames, strict=True):
        if f > frame:
            break
        age = (frame - f) / fps
        if e.kind in ("paid", "collected", "late", "plain_late", "broken", "partial"):
            lc = licence_for(lics, e)
            if e.kind in ("broken", "partial"):
                if lc is not None:
                    scars.setdefault(lc.cid, []).append(e.kind)
                if age < flash_s:
                    flashes.append((lc.cid if lc else "", e.kind, 1 - age / flash_s))
            elif age < pulse_s:
                pulses.append(Pulse(lc.cid if lc else None, e.kind, e.a or "", e.b or "", age / pulse_s, e.amount))
        elif e.kind in ("message", "offer"):
            dur = spark_s * (1.6 if e.kind == "offer" else 1.0)
            if age < dur and e.a and e.b:
                sparks.append((e.a, e.b, age / dur, e.kind))
        elif e.kind == "tender_won":
            outcome = next((x.kind[len("tender_"):] for x, fx in zip(events, ev_frames, strict=True)
                            if x.kind.startswith("tender_") and x.kind != "tender_won" and x.ref == e.ref
                            and fx <= frame), None)
            tenders.append((e, min(1.0, age / flash_s), outcome))
    return Layer(glowing, pulses, scars, flashes, sparks, tenders)


def _frame_at(events: list[Event], frames: list[int], kind: str, ref: str) -> int | None:
    for e, f in zip(events, frames, strict=True):
        if e.kind == kind and e.ref == ref:
            return f
    return None


def _first_frame_after(frames: list[int], events: list[Event], t: int) -> int | None:
    for e, f in zip(events, frames, strict=True):
        if e.t >= t:
            return f
    return None


# ============================================================================================ captions
@dataclass
class Caption:
    frame: int
    frames: int
    title: str
    body: str = ""
    quote: str | None = None
    who: str | None = None  # the company letter of the quote
    focus: tuple[float, float] | None = None  # tile x, y of the moment (for the camera)
    kind: str = ""


def _gbp(v: int) -> str:
    return f"£{v:,}"


def _quote_near(events: list[Event], t: int, a: str, b: str, before_days: int, after_days: int,
                prefer: str | None = None) -> tuple[str, str] | None:
    """A short safe excerpt of a message between ``a`` and ``b`` near tick ``t``: from ``prefer`` first,
    the nearest in time first."""
    lo, hi = t - before_days * TICKS_PER_DAY, t + after_days * TICKS_PER_DAY
    cands = [e for e in events if e.kind == "message" and lo <= e.t <= hi and {e.a, e.b} == {a, b}]
    cands.sort(key=lambda e: (e.a != prefer if prefer else 0, abs(e.t - t)))
    found = [(q, e.a or "") for e in cands if (q := excerpt(e.text))]
    good = [x for x in found if len(x[0]) >= 30 and sum(ch.isdigit() for ch in x[0]) <= 6]
    return (good or found or [None])[0]


def captions_of(events: list[Event], ev_frames: list[int], lics: list[Licence], name: dict[str, str],
                horizon: int, sched: list[float], fps: int = 30, quotes: bool = True, hold_s: float = 3.6,
                arm: str = "") -> list[Caption]:
    """Short captions for the key moments: the first licence signed, the first instalment paid (or
    collected), the first renegotiation, the first late payment, the first broken promise, the first
    tender won, the end of decisions (the horizon). ``name`` maps an exchange id to a company letter.
    Captions never overlap: a later one waits for the earlier one to finish (and is dropped if that
    pushes it more than 4 seconds after its moment)."""
    lic = {lc.cid: lc for lc in lics}
    want: list[tuple[int, Caption]] = []
    seen: set[str] = set()

    def centre(lc: Licence | None) -> tuple[float, float] | None:
        if lc is None:
            return None
        return ((lc.rect[0] + lc.rect[2]) / 2, (lc.rect[1] + lc.rect[3]) / 2)

    def n(x: str | None) -> str:
        return name.get(str(x), str(x))

    for e, f in zip(events, ev_frames, strict=True):
        if e.kind == "licence_signed" and "signed" not in seen:
            seen.add("signed")
            lc = lic.get(e.ref)
            fee = f"for {_gbp(e.amount)} in quarterly instalments" if e.amount else "for free"
            q = _quote_near(events, e.t, e.a, e.b, 60, 5) if quotes else None
            want.append((f, Caption(f, 0, "First contract signed",
                                    f"{n(e.b)} licenses {n(e.a)} to build inside {n(e.b)}'s region, {fee}.",
                                    *(q or (None, None)), focus=centre(lc), kind=e.kind)))
        elif e.kind in ("paid", "collected") and e.b != EXCHANGE and "paid" not in seen:
            seen.add("paid")
            if e.kind == "collected":
                body = f"The exchange collects {n(e.a)}'s {_gbp(e.amount)} instalment to {n(e.b)} automatically."
                title = "Enforced: payment collected"
            else:
                body = f"{n(e.a)} pays {n(e.b)} {_gbp(e.amount)} on time. Nothing forced it to."
                title = "A promise kept"
            want.append((f, Caption(f, 0, title, body, focus=centre(licence_for(lics, e)), kind=e.kind)))
        elif e.kind == "amended" and "amended" not in seen:
            seen.add("amended")
            q = _quote_near(events, e.t, e.a, e.b, 10, 2) if quotes else None
            want.append((f, Caption(f, 0, "Renegotiated", f"{n(e.a)} and {n(e.b)} sign new terms for {e.ref}.",
                                    *(q or (None, None)), focus=centre(lic.get(e.ref)), kind=e.kind)))
        elif e.kind in ("broken", "partial") and e.b != EXCHANGE and "broken" not in seen:
            seen.add("broken")
            q = _quote_near(events, e.t, e.a, e.b, 2, 120, prefer=e.a) if quotes else None
            what = "misses" if e.kind == "broken" else "short-pays"
            want.append((f, Caption(f, 0, "First broken promise",
                                    f"{n(e.a)} {what} a {_gbp(e.amount)} instalment owed to {n(e.b)}.",
                                    *(q or (None, None)), focus=centre(licence_for(lics, e)), kind=e.kind)))
        elif e.kind in ("late", "plain_late") and "late" not in seen:
            seen.add("late")
            want.append((f, Caption(f, 0, "Paid late", f"{n(e.a)} pays {n(e.b)} {_gbp(e.amount)} after the deadline.",
                                    focus=centre(licence_for(lics, e)), kind=e.kind)))
        elif e.kind == "tender_won" and "tender" not in seen:
            seen.add("tender")
            want.append((f, Caption(f, 0, "Tender won", f"{n(e.a)} wins exchange tender {e.ref} for "
                                    f"{_gbp(e.amount)}.", kind=e.kind)))
    hf = min(bisect.bisect_left(sched, horizon), len(sched) - 1)
    if hf < len(sched) - 1:
        want.append((hf, Caption(hf, 0, "Decisions end", "The agents stop. For one more year the networks run "
                                 "on their own; the score is net worth plus the ledger.", kind="horizon")))
    want.sort(key=lambda x: x[0])
    out: list[Caption] = []
    free = 0
    hold = round(hold_s * fps)
    for f, c in want:
        start = max(f, free)
        if start - f > 4 * fps or start >= len(sched) - 1:
            continue
        c.frame, c.frames = start, min(hold, len(sched) - start)
        out.append(c)
        free = start + c.frames + round(0.3 * fps)
    return out


def caption_at(caps: list[Caption], frame: int) -> tuple[Caption, float] | None:
    """The caption on screen and its opacity (eased in and out over 0.3 of a second at 30 fps)."""
    for c in caps:
        if c.frame <= frame < c.frame + c.frames:
            k = frame - c.frame
            fade = 9
            return c, min(1.0, (k + 1) / fade, (c.frames - k) / fade)
    return None


# ============================================================================================ camera
def camera_at(frame: int, caps: list[Caption], size: tuple[int, int], zoom: float = 1.45,
              ease_frames: int = 30) -> tuple[float, float, float]:
    """(centre x, centre y, zoom) in tiles: the whole map, easing to ``zoom`` on a caption's focus while the
    caption shows, then back. The view never leaves the map."""
    sx, sy = size
    cx, cy, z = sx / 2, sy / 2, 1.0
    for c in caps:
        if c.focus is None:
            continue
        if c.frame - ease_frames <= frame < c.frame + c.frames + ease_frames:
            if frame < c.frame:
                k = ease_in_out((frame - (c.frame - ease_frames)) / ease_frames)
            elif frame >= c.frame + c.frames:
                k = 1 - ease_in_out((frame - (c.frame + c.frames)) / ease_frames)
            else:
                k = 1.0
            z = 1 + (zoom - 1) * k
            fx, fy = c.focus
            cx, cy = sx / 2 + (fx - sx / 2) * k, sy / 2 + (fy - sy / 2) * k
            break
    half_x, half_y = sx / 2 / z, sy / 2 / z
    cx = min(max(cx, half_x), sx - half_x)
    cy = min(max(cy, half_y), sy - half_y)
    return cx, cy, z


# ============================================================================================ geometry
def bezier(p0: tuple[float, float], p1: tuple[float, float], bend: float = 0.22, n: int = 24) -> list[tuple[float, float]]:
    """A quadratic arc from ``p0`` to ``p1``, bowed to the left of the direction by ``bend`` x length."""
    (x0, y0), (x1, y1) = p0, p1
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    dx, dy = x1 - x0, y1 - y0
    cx, cy = mx - dy * bend, my + dx * bend
    pts = []
    for i in range(n + 1):
        u = i / n
        pts.append(((1 - u) ** 2 * x0 + 2 * (1 - u) * u * cx + u * u * x1,
                    (1 - u) ** 2 * y0 + 2 * (1 - u) * u * cy + u * u * y1))
    return pts


def point_on(pts: list[tuple[float, float]], u: float) -> tuple[float, float]:
    """The point at fraction ``u`` of the polyline's vertices (uniform in the curve parameter)."""
    u = min(1.0, max(0.0, u)) * (len(pts) - 1)
    i = min(int(u), len(pts) - 2)
    f = u - i
    return pts[i][0] + (pts[i + 1][0] - pts[i][0]) * f, pts[i][1] + (pts[i + 1][1] - pts[i][1]) * f


def region_centre(rects: list[list[int]]) -> tuple[float, float]:
    """Area-weighted centre of a region's rectangles (tiles)."""
    tot = sx = sy = 0.0
    for x0, y0, x1, y1 in rects:
        a = (x1 - x0 + 1) * (y1 - y0 + 1)
        tot += a
        sx += a * (x0 + x1) / 2
        sy += a * (y0 + y1) / 2
    return (sx / tot, sy / tot) if tot else (0.0, 0.0)


def value_at(ts: list[int], vs: list[float], t: float) -> float:
    """Linear interpolation, held flat outside the samples (railhead.timelapse.money_at)."""
    from railhead.timelapse import money_at

    return money_at(ts, vs, t)


def counters_at(samples: list[dict], t: float) -> dict:
    """The exchange counters of the last sample at or before ``t`` (a step function, never ahead)."""
    from railhead.timelapse import at_or_before

    ts = [s["t"] for s in samples]
    return samples[at_or_before(ts, t)].get("ex") or {}
