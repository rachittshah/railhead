// Shared by scenes 2 and 3: where the timelapse sits, and the brief beside it.
// CROP is the eleven route maps inside the 1920x1080 timelapse frame (measured on arena2-timelapse-end-dark.png:
// tiles start at x 20, 343, 665, 988 and y 134, 459, 785, 269 px square, model names 40 px above each).
const CROP = { x: 8, y: 86, w: 1262, h: 974 };
const PLATE = { x: 96, y: 80, w: 1110, h: 1110 * 974 / 1262 };
const PK = PLATE.w / CROP.w;
const toPlate = (x, y) => [PLATE.x + (x - CROP.x) * PK, PLATE.y + (y - CROP.y) * PK];
const TILE = { xs: [20, 343, 665, 988], ys: [134, 459, 785], size: 269 };
const tileSrc = n => ({ x: TILE.xs[(n - 1) % 4], y: TILE.ys[Math.floor((n - 1) / 4)] });
// Only the eleven map squares, not the timelapse's own labels (model names, money, legend).
// Laid out on an even 4 x 3 grid on screen, in the timelapse's order (richest first).
const GRID = { x: 96, y: 95, sz: 270, gap: 18 };
const tilePos = n => [GRID.x + ((n - 1) % 4) * (GRID.sz + GRID.gap), GRID.y + Math.floor((n - 1) / 4) * (GRID.sz + GRID.gap)];
// The timelapse burns a caption into tile 6 (claude-fable-5-1) from clip second 15.56 on. It is covered with the same
// strip from the clip at 15.5 s (assets/fable-caption-patch.png: same terrain and routes, no caption).
const PATCH = { n: 6, x: 16, y: 238, w: 236, h: 30, from: 15.5 };
let patchImg = null;
const loadPatch = async () => { patchImg = await loadImage('../assets/fable-caption-patch.png'); };
function patchTile(n, x, y, sz) {
  if (n !== PATCH.n || !patchImg) return;
  const k = sz / TILE.size;
  ctx.drawImage(patchImg, x + PATCH.x * k, y + PATCH.y * k, PATCH.w * k, PATCH.h * k);
}
function drawTiles(img, skip = [], patch = true) {
  for (let n = 1; n <= 11; n++) {
    if (skip.includes(n)) continue;
    const s = tileSrc(n), [x, y] = tilePos(n);
    ctx.drawImage(img, s.x, s.y, TILE.size, TILE.size, x, y, GRID.sz, GRID.sz);
    if (patch) patchTile(n, x, y, GRID.sz);
  }
}

// The right column: the year read off the clip time, and three short cards (README, chapter 1 and 'What this is').
const BX = 1300;
function brief(t, clipT, B, out) {
  const a = 1 - inOutSine(out);
  if (a <= 0) return;
  ctx.save(); ctx.globalAlpha = a;
  const year = Math.min(1971, 1960 + Math.floor(Math.max(0, clipT - 1) / 5));
  const runoff = clipT - 1 >= 50;
  text('Eleven AIs, one map', BX, 128, { size: 28, mono: true, color: C.gravel });
  text(String(year), BX - 8, 290, { size: 172, mono: true, weight: 500, color: C.chalk, track: -0.02 });
  text(runoff ? 'running on its own' : 'the AI decides', BX, 340, { size: 28, mono: true, color: runoff ? C.copper : C.gravel });
  const L = [
    ['£500,000 to borrow.', B.l1],
    ['Ten years.', B.l2],
    ['No help.', B.l3],
  ];
  L.forEach(([h, t0], i) => words(h, BX, 560 + i * 104, { size: 54, weight: 500 }, t, t0));
  ctx.restore();
}
