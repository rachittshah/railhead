"""Cinema: real OpenTTD close-ups of key sites, rendered by the engine with its own graphics (OpenGFX).

The dedicated server cannot draw: it runs with the null blitter (screen depth 0), so ``screenshot normal``,
``big`` and ``giant`` over the admin port log "Screenshot failed!" (only ``minimap`` works, because it
paints its own 32-bit buffer). The null video driver does not help either: it forces the null blitter and
never drains the queue in which the console's screenshot requests wait. So a close-up needs the GUI build
of the engine with a real video driver:

1. **Saves (in the sample stage).** At a few ticks (:func:`save_plan`: 60 days after the first licence is
   signed, the first broken obligation, and the horizon) the sampler asks the replay's own engine for a
   savegame (``World.save``: a synchronous ``save`` over the admin port). A save only reads the world; the
   hook-equality integration test replays with and without saves and compares every checksum.
2. **Shots (in the render stage, ``--engine-shots``).** For each save, the GUI binary loads it in a private
   instance directory, with ``scripts/game_start.scr`` = ``screenshot giant``: the whole map at the default
   zoom, with no window in it (about 16000 x 8000 pixels for a 256 x 256 map, 4 seconds). A small window
   opens and is closed when the PNG is complete. Each site is a crop of it around the site's tile
   (:func:`iso_px`). The save is never written back. (``scrollto`` + ``screenshot normal`` would avoid the
   giant image, but the loaded game resets the view after ``game_start.scr`` runs, so every shot showed the
   saved view position.)

The GUI binary: ``$RAILHEAD_OPENTTD_GUI``, else ``<repo>/.cache/openttd-src/build-gui/openttd``. It needs a
display (macOS: the cocoa driver).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

TICKS_PER_DAY = 74
SHOT_W, SHOT_H = 960, 540


def save_plan(records: list[dict]) -> dict[str, int]:
    """Ticks at which the sampler saves the world, from the merged log: ``licence`` (60 days after the first
    licence is accepted, so the licensee has built on the site) and ``broken`` (the first broken or partial
    obligation). The horizon save is made by the sampler itself."""
    kinds: dict[str, str] = {}
    plan: dict[str, int] = {}
    for r in records:
        if r.get("kind") != "ex":
            continue
        x = r.get("x") or {}
        op = x.get("op")
        if op == "offer":
            kinds[str(x.get("contract_id"))] = str(x.get("kind", "supply"))
        elif op == "accept" and kinds.get(str(x.get("contract_id"))) == "licence" and "licence" not in plan:
            plan["licence"] = int(r["ticks"]) + 60 * TICKS_PER_DAY
        elif op == "obligation_settle" and x.get("outcome") in ("broken", "partial") and "broken" not in plan:
            plan["broken"] = int(r["ticks"])
    return plan


def sites(doc: dict) -> list[dict]:
    """The close-ups to take from the saves of one sample file: [{name, save, tile, contract, label}]."""
    from railhead.cinema import story

    ex = doc.get("exchange") or {}
    size_x = int(doc["base"]["size_x"])
    lics = story.licences_of(ex)
    saves = doc.get("saves") or {}
    out = []

    def tile(lc, save: str) -> int:
        """The licensee's stations on the site at the save tick (their centre), else the site's centre."""
        t = saves.get(save, 0)
        ss = [x for x in doc.get("samples") or [] if x["t"] <= t]
        st = ss[-1]["co"].get(str(lc.grantee), {}).get("st", []) if ss else []
        r = lc.rect
        inside = [(x, y) for _sid, x, y in st if r[0] <= x <= r[2] and r[1] <= y <= r[3]]
        if inside:
            x, y = round(sum(p[0] for p in inside) / len(inside)), round(sum(p[1] for p in inside) / len(inside))
        else:
            x, y = (r[0] + r[2]) // 2, (r[1] + r[3]) // 2
        return y * size_x + x

    if "licence" in saves and lics:
        lc = lics[0]
        out.append({"name": f"licence-{lc.cid}", "save": "licence", "tile": tile(lc, "licence"), "contract": lc.cid,
                    "grantor": lc.grantor, "grantee": lc.grantee, "kind": "licence_signed"})
    if "broken" in saves:
        ev = [e for e in story.events_of(ex) if e.kind in ("broken", "partial") and e.b != story.EXCHANGE]
        lc = story.licence_for(lics, ev[0]) if ev else None
        if lc is not None:
            out.append({"name": f"broken-{lc.cid}", "save": "broken", "tile": tile(lc, "broken"), "contract": lc.cid,
                        "grantor": lc.grantor, "grantee": lc.grantee, "kind": "broken"})
    if "horizon" in saves:
        for lc in sorted(lics, key=lambda lc: -(lc.end - lc.start))[:3]:
            out.append({"name": f"horizon-{lc.cid}", "save": "horizon", "tile": tile(lc, "horizon"), "contract": lc.cid,
                        "grantor": lc.grantor, "grantee": lc.grantee, "kind": "horizon"})
    return out


def gui_binary() -> Path | None:
    from railhead.engine import REPO_DIR

    for c in (os.environ.get("RAILHEAD_OPENTTD_GUI"), REPO_DIR / ".cache/openttd-src/build-gui/openttd"):
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            return Path(c)
    return None


def iso_px(tx: float, ty: float, width: int, height: int, size_x: int, size_y: int) -> tuple[int, int]:
    """Tile -> pixel of a ``screenshot giant`` image at the default zoom (32 x 16 pixels per tile; north at
    the top). The image is a little taller than the flat map (``height - (size_x + size_y - 2) * 16`` pixels
    of headroom for hills at the top); a tile's own height lifts it by up to that much, so crops are
    taken with a margin."""
    x0 = (size_x - 1) * 32  # the north tile's column: tile (size_x - 1, 0) is at the left edge
    top = height - (size_x + size_y - 2) * 16
    return round((ty - tx) * 32 + x0 + (width - (size_x + size_y - 2) * 32) / 2), round((tx + ty) * 16 + top)


def crop_at(img, tx: float, ty: float, size_x: int, size_y: int, w: int = SHOT_W, h: int = SHOT_H):
    """A ``w`` x ``h`` window of a giant screenshot centred on tile (tx, ty), clamped to the image."""
    H, W = img.shape[:2]
    x, y = iso_px(tx, ty, W, H, size_x, size_y)
    left = min(max(0, x - w // 2), max(0, W - w))
    top = min(max(0, y - h // 2), max(0, H - h))
    return img[top:top + h, left:left + w]


def shoot(binary: Path, save: Path, out_png: Path, work: Path, timeout_s: float = 120.0) -> Path:
    """The whole map of ``save`` as the engine draws it (``screenshot giant``) -> ``out_png``."""
    shutil.rmtree(work, ignore_errors=True)
    for sub in ("scripts", "screenshot", "save"):
        (work / sub).mkdir(parents=True, exist_ok=True)
    name = "rh_cinema_shot"
    (work / "scripts" / "game_start.scr").write_text(f"screenshot giant {name}\n")
    (work / "openttd.cfg").write_text(
        "[misc]\nfullscreen = false\nscreenshot_format = png\n"
        "resolution = 640,360\n[gui]\nautosave_interval = 0\nshow_finances = false\n"
        "[network]\nclient_name = railhead-cinema\n")
    shot = work / "screenshot" / f"{name}.png"
    driver = "cocoa" if sys.platform == "darwin" else "sdl"
    proc = subprocess.Popen([str(binary), "-x", "-X", "-c", str(work / "openttd.cfg"), "-v", driver,
                             "-r", "640x360", "-b", "32bpp-optimized", "-s", "null", "-m", "null",
                             "-g", str(save)], cwd=work, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + timeout_s
        last = -1
        while time.monotonic() < deadline:
            if shot.exists():
                size = shot.stat().st_size
                if size > 0 and size == last:  # written completely
                    break
                last = size
            if proc.poll() is not None and not shot.exists():
                raise RuntimeError(f"the GUI engine exited ({proc.returncode}) without a screenshot")
            time.sleep(0.3)
        else:
            raise RuntimeError(f"no screenshot from the GUI engine within {timeout_s} s")
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(shot, out_png)
    shutil.rmtree(work, ignore_errors=True)
    return out_png


def make_shots(out: Path, arm: str) -> list[dict]:
    """Take every close-up of one sampled arm; write ``<out>/shots/<arm>/<name>.png`` and ``shots.json``.
    Cached: a shot whose PNG exists is not taken again."""
    out = Path(out)
    doc = json.loads((out / "samples" / f"{arm}.json").read_text())
    binary = gui_binary()
    if binary is None:
        raise FileNotFoundError("no GUI OpenTTD build (set RAILHEAD_OPENTTD_GUI); engine shots need it")
    import matplotlib.image as mpimg

    sdir = out / "shots" / arm
    sx, sy = int(doc["base"]["size_x"]), int(doc["base"]["size_y"])
    index = []
    giants: dict[str, object] = {}
    for s in sites(doc):
        png = sdir / f"{s['name']}.png"
        if not png.exists():
            if s["save"] not in giants:
                g = shoot(binary, out / "saves" / arm / f"{s['save']}.sav", sdir / f"giant-{s['save']}.png",
                          out / "shots" / f"work-{arm}")
                giants[s["save"]] = mpimg.imread(g)[..., :3]
                g.unlink()  # tens of MB: only the crops are kept
            ty, tx = divmod(int(s["tile"]), sx)
            mpimg.imsave(png, crop_at(giants[s["save"]], tx, ty, sx, sy))
        index.append(s | {"file": png.name, "tick": doc["saves"][s["save"]]})
    (sdir / "shots.json").write_text(json.dumps(index, indent=1) + "\n")
    return index


def load_shots(out: Path, arm: str) -> list[dict]:
    f = Path(out) / "shots" / arm / "shots.json"
    return json.loads(f.read_text()) if f.exists() else []
