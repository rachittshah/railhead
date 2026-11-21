// Shared by every scene: the 1920x1080 canvas (times ?scale=), the site's tokens (site/DESIGN.md),
// easing, type helpers, the persistent rail along the bottom, and the motlab page contract.
const q = new URLSearchParams(location.search), S = parseFloat(q.get('scale') || '1'), W = 1920, H = 1080;
const cv = document.getElementById('c'); cv.width = W * S; cv.height = H * S;
const ctx = cv.getContext('2d');

const C = {
  ballast: '#141513', sleeper: '#1C1E1B', sleeper2: '#242722', plate: '#1A1A19', hairline: '#2E312C', hairline2: '#3A3E37',
  chalk: '#EEEDE7', gravel: '#A8AB9F', dim: '#8D9085', copper: '#D98A4E', signal: '#D8AE66',
  paper: '#E9E4D6', ink: '#1E1F1C', ink2: '#5B5A52', open: '#E0645C',
  opus: '#5b93dc', astra: '#d9733f', sol: '#2fa07a', other: '#5E6159',
};
const MODEL = { 'claude-opus-5-5': C.opus, 'gpt-6-astra': C.astra, 'gpt-6-sol': C.sol };
const modelColor = p => MODEL[p] || C.other;
const SANS = '"General Sans"', MONO = '"IBM Plex Mono"';
const FONTS = [`400 40px ${SANS}`, `500 40px ${SANS}`, `600 40px ${SANS}`, `italic 400 40px ${SANS}`, `400 40px ${MONO}`, `500 40px ${MONO}`];

// Scene lengths in order. The rail at the bottom reads its position from this table, so it runs on across every cut.
const SCENES = [3.3, 5.4, 5.6, 4.2, 3.2, 4.2, 4.6, 5.1];
const ALL = SCENES.reduce((a, b) => a + b, 0);
const JUNCTION = 4;   // index of the first Waybill scene: the rail's accent turns from copper to signal there

const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const prog = (t, a, b) => clamp((t - a) / (b - a));
const lerp = (a, b, k) => a + (b - a) * k;
const outCubic = x => 1 - Math.pow(1 - clamp(x), 3);
const outExpo = x => (x = clamp(x)) >= 1 ? 1 : 1 - Math.pow(2, -10 * x);
const inOutCubic = x => (x = clamp(x)) < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
const inOutSine = x => -(Math.cos(Math.PI * clamp(x)) - 1) / 2;
const mix = (a, b, k) => {   // hex colours
  const p = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const x = p(a), y = p(b);
  return `rgb(${x.map((v, i) => Math.round(lerp(v, y[i], k))).join(',')})`;
};

// Text. Tracking is given in em and set in px, so display type sits at the site's -0.045em.
function font(size, weight = 500, family = SANS, italic = false) {
  ctx.font = `${italic ? 'italic ' : ''}${weight} ${size}px ${family}`;
}
function text(str, x, y, o = {}) {
  const size = o.size || 40;
  font(size, o.weight || 500, o.mono ? MONO : SANS, o.italic);
  ctx.letterSpacing = `${(o.track ?? (o.mono ? 0 : size >= 60 ? -0.045 : -0.01)) * size}px`;
  ctx.textAlign = o.align || 'left'; ctx.textBaseline = o.baseline || 'alphabetic';
  ctx.fillStyle = o.color || C.chalk;
  ctx.fillText(str, x, y);
  const w = ctx.measureText(str).width;
  ctx.letterSpacing = '0px';
  return w;
}
function measure(str, o = {}) {
  const size = o.size || 40;
  font(size, o.weight || 500, o.mono ? MONO : SANS, o.italic);
  ctx.letterSpacing = `${(o.track ?? (o.mono ? 0 : size >= 60 ? -0.045 : -0.01)) * size}px`;
  const w = ctx.measureText(str).width; ctx.letterSpacing = '0px'; return w;
}
// Wrap into lines no wider than maxW.
function wrap(str, maxW, o) {
  const out = []; let line = '';
  for (const w of str.split(' ')) {
    const tryL = line ? line + ' ' + w : w;
    if (line && measure(tryL, o) > maxW) { out.push(line); line = w; } else line = tryL;
  }
  if (line) out.push(line);
  return out;
}
// Words arrive one at a time: each rises 18 px while it sharpens from a blur. k runs 0..1 over the whole line.
// `out` (0..1) takes the line away the same way.
function words(str, x, y, o, t, t0, out = 0) {
  const ws = str.split(' '), stagger = o.stagger ?? 0.06, dur = o.dur ?? 0.6;
  const space = measure(' ', o); let cx = x;
  if (o.align === 'center') cx = x - measure(str, o) / 2;
  if (o.align === 'right') cx = x - measure(str, o);
  ws.forEach((w, i) => {
    const k = outCubic(prog(t, t0 + i * stagger, t0 + i * stagger + dur)) * (1 - inOutSine(out));
    if (k <= 0) { cx += measure(w, o) + space; return; }
    ctx.save();
    ctx.globalAlpha *= k;
    const b = (1 - k) * 8;
    if (b > 0.3) ctx.filter = `blur(${b * S}px)`;
    const ww = text(w, cx, y + (1 - k) * 18, { ...o, align: 'left' });
    ctx.restore();
    cx += ww + space;
  });
}
// Several lines as one block, word by word across the block.
function block(lines, x, y, lh, o, t, t0, out = 0) {
  let i0 = 0;
  lines.forEach((l, li) => {
    words(l, x, y + li * lh, o, t, t0 + i0 * (o.stagger ?? 0.06), out);
    i0 += l.split(' ').length;
  });
}

function fill(c) { ctx.fillStyle = c; ctx.fillRect(0, 0, W, H); }
function line(x1, y1, x2, y2, c, w = 2) { ctx.strokeStyle = c; ctx.lineWidth = w; ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke(); }
function dot(x, y, r, c) { ctx.fillStyle = c; ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill(); }

// The rail: one line along the bottom of every scene, a station at each cut, a signal dot at the current time.
const RAIL = { x0: 120, x1: 1800, y: 1032 };
function railX(gt) { return lerp(RAIL.x0, RAIL.x1, clamp(gt / ALL)); }
function sceneStart(i) { return SCENES.slice(0, i).reduce((a, b) => a + b, 0); }
function rail(idx, t, o = {}) {
  const gt = sceneStart(idx) + t, y = o.y ?? RAIL.y, x1 = o.x1 ?? RAIL.x1, alpha = o.alpha ?? 1;
  const jx = railX(sceneStart(JUNCTION));
  // the accent turns at the junction, over the first 0.5 s of the Waybill scene
  const turn = outCubic(prog(gt, sceneStart(JUNCTION), sceneStart(JUNCTION) + 0.5));
  const accent = mix(C.copper, C.signal, turn);
  ctx.save(); ctx.globalAlpha = alpha;
  line(RAIL.x0, y, x1, y, C.hairline, 2);
  const hx = Math.min(railX(gt), x1);
  line(RAIL.x0, y, Math.min(hx, jx), y, C.copper, 2);
  if (hx > jx) line(jx, y, hx, y, C.signal, 2);
  for (let i = 0; i <= SCENES.length; i++) {           // stations
    const sx = railX(sceneStart(i)); if (sx > x1 + 0.5) continue;
    const passed = sx <= hx + 0.5;
    ctx.fillStyle = passed ? (i >= JUNCTION ? C.signal : C.copper) : C.hairline2;
    ctx.fillRect(Math.round(sx) - 1, y - 7, 2, 14);
  }
  dot(hx, y, 6, accent);
  ctx.restore();
}

const loadImage = src => new Promise((ok, no) => { const im = new Image(); im.onload = () => ok(im); im.onerror = () => no(new Error('image ' + src)); im.src = src; });

// The page contract. `prep` is an async function that loads what the scene needs.
function scene(total, draw, prep = async () => {}) {
  window.TOTAL = total;
  window.ready = (async () => {
    await Promise.all(FONTS.map(f => document.fonts.load(f).then(faces => { if (!faces.length) throw new Error('font not loaded: ' + f); })));
    await prep();
  })();
  window.renderAt = async t => {
    await window.ready;
    ctx.setTransform(S, 0, 0, S, 0, 0); ctx.globalAlpha = 1; ctx.filter = 'none';
    await draw(t);
  };
  if (q.has('t')) window.ready.then(() => window.renderAt(parseFloat(q.get('t'))));
}
