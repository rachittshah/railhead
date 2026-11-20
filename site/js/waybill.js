/* Waybill figures: hotspots (fig. 7), the life of C7 (fig. 8), pilot v3 obligations (fig. 9),
   the settlement explorer (fig. 10), instalments (fig. 11), conservation (fig. 12).
   Fig. 10 and 11 are line-for-line ports of src/railhead/exchange/settlement.py and exchange.py. */
(function () {
  "use strict";
  const RH = window.RH;
  const el = RH.el;

  /* ---------------------------------------------------------------- exact ports (integers) */
  const TICKS_PER_DAY = 74;
  const INSTALMENT_TICKS = 90 * TICKS_PER_DAY;
  const GRACE_TICKS = 30 * TICKS_PER_DAY;
  const fdiv = function (a, b) { return Math.floor(a / b); }; // Python // for the non-negative ints used here

  function outcomeFor(delivered, quantity) {
    if (delivered >= quantity) return "kept";
    return delivered === 0 ? "broken" : "partial";
  }
  function computeSupplySettlement(t, delivered) {
    const q = t.quantity, price = t.price, ppu = t.penalty_per_unit, bond = t.bond || 0;
    const counted = Math.min(delivered, q);
    const seller_payment = fdiv(price * counted, q);
    const penalty = ppu * (q - counted);
    const from_bond = Math.min(bond, penalty);
    return { delivered: delivered, quantity: q, outcome: outcomeFor(delivered, q), seller_payment: seller_payment,
      buyer_refund: price - seller_payment, penalty: penalty, penalty_from_bond: from_bond,
      penalty_from_balance: penalty - from_bond, bond_returned: bond - from_bond };
  }
  function dueTicks(ws, dl) {
    const out = [];
    for (let t = ws + INSTALMENT_TICKS; t < dl; t += INSTALMENT_TICKS) out.push(t);
    out.push(dl);
    return out;
  }
  function split(total, dues) {
    if (total <= 0 || !dues.length) return [];
    const each = fdiv(total, dues.length);
    const parts = dues.slice(0, -1).map(function (d) { return [d, each]; });
    parts.push([dues[dues.length - 1], total - each * (dues.length - 1)]);
    return parts.filter(function (p) { return p[1] > 0; });
  }
  function clipSchedule(schedule, lastDue, floor) {
    if (lastDue == null || !schedule.length) return schedule;
    const kept = schedule.filter(function (p) { return p[0] <= lastDue; }).map(function (p) { return p.slice(); });
    const late = schedule.reduce(function (s, p) { return s + (p[0] > lastDue ? p[1] : 0); }, 0);
    if (!kept.length) return [[Math.max(floor, lastDue), late]];
    if (late) kept[kept.length - 1][1] += late;
    return kept;
  }
  function instalments(fee, ws, dl, lastDue) { return clipSchedule(split(fee, dueTicks(ws, dl)), lastDue, ws); }
  // exposed so the site check can compare the port with the Python reference
  window.WaybillRules = { computeSupplySettlement: computeSupplySettlement, instalments: instalments, dueTicks: dueTicks, split: split, clipSchedule: clipSchedule };

  /* ---------------------------------------------------------------- sliders */
  function slider(host, o) {
    const w = el("div", { class: "sl" });
    const id = "sl-" + o.key;
    const lab = el("label", { for: id });
    lab.appendChild(el("span", null, o.label));
    const out = el("output", { for: id });
    lab.appendChild(out);
    const inp = el("input", { type: "range", id: id, min: o.min, max: o.max, step: o.step, value: o.value });
    w.appendChild(lab); w.appendChild(inp);
    if (o.hint) w.appendChild(el("span", { class: "hint" }, o.hint));
    host.appendChild(w);
    const sync = function () {
      out.textContent = o.fmt(+inp.value);
      inp.setAttribute("aria-valuetext", o.fmt(+inp.value));
      inp.style.setProperty("--fill", ((inp.value - o.min) / (o.max - o.min) * 100) + "%");
    };
    inp.addEventListener("input", function () { sync(); o.on(); });
    sync();
    return { get: function () { return +inp.value; }, input: inp, wrap: w, sync: sync };
  }

  /* ---------------------------------------------------------------- fig. 7 hotspots */
  const SPOTS = [
    [13.6, 7.4, "Four franchise regions", "Each company owns a region, a set of rectangles on the map. A build outside it is refused with ERR_FRANCHISE. A and B are two of the four here."],
    [54.5, 16.4, "The arm", "Cheap talk: payments are voluntary. The same seed is also played enforced, where the exchange collects what falls due."],
    [30.7, 7.9, "A real engine render", "With --engine-shots, the GUI build of OpenTTD draws a real close-up: A's site on B's land, contract C7."],
    [46.6, 55.6, "A licence", "A signed licence is a rectangle in the grantor's region, drawn in the licensee's colour. What the licensee builds there keeps working after the deadline."],
    [41.4, 44.4, "A contract arc", "A dashed arc runs from the licence back to the licensee. A payment on time, or a collection, pulses along it; a late payment pulses amber."],
    [35.4, 36.6, "The broken promise", "A missed a £4,000 instalment owed to B. A broken obligation flashes red and leaves this mark."],
    [81.3, 27.8, "The leaderboard", "The score of a company is its net worth after the run-off plus its ledger balance. In this frame all four are below zero."]
  ];
  function hotspots() {
    const host = document.getElementById("hotspots"); if (!host) return;
    const n = document.getElementById("hs-n"), t = document.getElementById("hs-t"), d = document.getElementById("hs-d");
    const btns = SPOTS.map(function (s, i) {
      const b = el("button", { type: "button", class: "hs", "aria-pressed": "false", "aria-label": (i + 1) + ": " + s[2] }, String(i + 1));
      b.style.left = s[0] + "%"; b.style.top = s[1] + "%";
      b.addEventListener("click", function () { pick(i); });
      host.appendChild(b); return b;
    });
    function pick(i) {
      btns.forEach(function (b, j) { b.setAttribute("aria-pressed", i === j ? "true" : "false"); });
      n.textContent = (i + 1) + " of " + SPOTS.length; t.textContent = SPOTS[i][2]; d.textContent = SPOTS[i][3];
    }
    pick(0);
  }

  /* ---------------------------------------------------------------- fig. 8 the life of C7 */
  const STEPS = [
    { h: "Offer", t: "A offers terms for a licence on B's land: a rectangle, a start, a deadline and a fee. The coordinator refuses a rectangle that is not inside the grantor's region.", msgs: [] },
    { h: "Signed", t: "B accepts. Accepting signs the contract, and nothing is paid at signing. The two agree in words how the instalments will be paid.", msgs: [
      ["s1 → s0", "B to A", "Accepted C7. Please pay the licence instalments through the exchange ledger as they fall due."],
      ["s0 → s1", "A to B", "Thanks B for accepting C7! I'll pay each instalment through the ledger when due."]] },
    { h: "The right starts", t: "From the start of the window, A can build inside the rectangle. The build right is the same in both arms. In the cinema, the OpenTTD close-up shows A's site on B's land.", msgs: [] },
    { h: "Due", t: "An instalment of £4,000 falls due at tick 26199. A is woken with the logged reason obligation_due and has a turn. In cheap talk, nothing happens unless it pays.", msgs: [], code: "rh pay to=B amount=4000 contract=C7" },
    { h: "Broken", t: "Thirty days after the due tick, the obligation settles from the payments tagged to it. There were none: it settles broken. A pays late, and says so.", msgs: [
      ["s0 → s1", "A to B", "sorry, I missed the instalment due at tick 26199 - just paid it (4000) late. Next ones will be on time."]] },
    { h: "Chased", t: "B, the payee, explains the ledger to the payer and asks for the rest. Under rules 1, a late tagged payment counted toward the next instalment.", msgs: [
      ["s1 → s0", "B to A", "The late C7 payment was applied to the next instalment; the earlier £4,000 is marked broken, so please send that amount as a separate ledger payment when convenient."]] },
    { h: "Paid late", t: "A pays the missed £4,000 as a plain ledger payment. All six broken instalments of that game were paid in the end, 37 to 240 days after they fell due.", msgs: [
      ["s0 → s1", "A to B", "paid 4000 for C7 instalment O25 which I missed (as a plain ledger payment)."]] }
  ];
  function promise() {
    const list = document.getElementById("promise-steps"), body = document.getElementById("promise-body");
    if (!list) return;
    const stamps = Array.from(document.querySelectorAll("#waybill-doc .stamp"));
    let cur = 0;
    const btns = STEPS.map(function (s, i) {
      const li = el("li");
      const b = el("button", { type: "button", role: "tab", "aria-selected": "false", id: "pstep-" + i, "aria-controls": "promise-body", tabindex: "-1" });
      b.appendChild(el("span", { class: "n" }, "0" + (i + 1)));
      b.appendChild(document.createTextNode(s.h));
      b.addEventListener("click", function () { go(i); });
      b.addEventListener("keydown", function (e) {
        if (e.key === "ArrowRight") { e.preventDefault(); go(Math.min(STEPS.length - 1, i + 1), true); }
        if (e.key === "ArrowLeft") { e.preventDefault(); go(Math.max(0, i - 1), true); }
      });
      li.appendChild(b); list.appendChild(li); return b;
    });
    function go(i, focus) {
      cur = i;
      btns.forEach(function (b, j) {
        b.setAttribute("aria-selected", j === i ? "true" : "false"); b.tabIndex = j === i ? 0 : -1;
        b.classList.toggle("done", j < i);
      });
      if (focus) btns[i].focus();
      body.setAttribute("aria-labelledby", "pstep-" + i);
      const s = STEPS[i];
      body.replaceChildren();
      body.appendChild(el("h3", null, s.h));
      body.appendChild(el("p", null, s.t));
      if (s.code) {
        const pre = el("pre", { class: "stop-code" }); pre.appendChild(el("code", null, s.code));
        body.appendChild(pre);
        body.appendChild(el("p", { class: "aside-note" }, "The command it would have taken; the agent did not run it in time."));
      }
      s.msgs.forEach(function (m) {
        const box = el("div", { class: "msg" });
        const h = el("p", { class: "msg-h" }); h.appendChild(el("b", null, m[0])); h.appendChild(document.createTextNode("  " + m[1]));
        box.appendChild(h); box.appendChild(el("p", { class: "msg-t" }, "“" + m[2] + "”"));
        body.appendChild(box);
      });
      stamps.forEach(function (st) { st.classList.toggle("on", i + 1 >= +st.dataset.from); });
      document.getElementById("promise-prev").disabled = i === 0;
      document.getElementById("promise-next").textContent = i === STEPS.length - 1 ? "Start again" : "Next step";
    }
    document.getElementById("promise-prev").addEventListener("click", function () { go(Math.max(0, cur - 1)); });
    document.getElementById("promise-next").addEventListener("click", function () { go(cur === STEPS.length - 1 ? 0 : cur + 1); });
    go(0);
  }

  /* ---------------------------------------------------------------- fig. 9 pilot v3 obligations */
  function obligations() {
    const host = document.getElementById("obl-chart"); if (!host) return;
    const rows = [
      { name: "enforced", sub: "8 obligations", segs: [["Collected", 8, "o-coll", "kept: collected by the exchange"]] },
      { name: "cheap_talk", sub: "30 obligations", segs: [["Kept", 21, "o-kept", "kept"], ["Broken", 6, "o-broken", "broken: not paid within the 30-day grace, all paid later"], ["Open", 3, "o-open", "still open at the horizon"]] }
    ];
    const max = 30;
    rows.forEach(function (r) {
      const row = el("div", { class: "obl-row" });
      const nm = el("div", { class: "obl-name" }); nm.appendChild(el("code", null, r.name)); nm.appendChild(el("small", null, r.sub));
      const bar = el("div", { class: "obl-bar", role: "img", "aria-label": r.name + ": " + r.segs.map(function (s) { return s[1] + " " + s[3]; }).join("; ") });
      const total = r.segs.reduce(function (a, s) { return a + s[1]; }, 0);
      bar.style.width = (total / max * 100) + "%";
      r.segs.forEach(function (s) {
        const seg = el("span", { class: "obl-seg " + s[2], title: s[1] + " " + s[3] });
        seg.style.flex = s[1] + " 1 0";
        seg.textContent = s[1] >= 5 ? s[1] + " " + s[0].toLowerCase() : String(s[1]);
        bar.appendChild(seg);
      });
      row.appendChild(nm); row.appendChild(bar); host.appendChild(row);
    });
    const lg = el("ul", { class: "obl-legend" });
    [["o-kept", "Kept, paid by the payer"], ["o-coll", "Kept, collected by the exchange"], ["o-broken", "Broken: not paid within 30 days"], ["o-open", "Open at the horizon"]].forEach(function (x) {
      const li = el("li"); li.appendChild(el("span", { class: "key obl-seg " + x[0] })); li.appendChild(document.createTextNode(x[1])); lg.appendChild(li);
    });
    host.appendChild(lg);
  }

  /* ---------------------------------------------------------------- fig. 10 settlement explorer */
  function settlement() {
    const sh = document.getElementById("settle-sliders"); if (!sh) return;
    const flow = document.getElementById("settle-flow"), sum = document.getElementById("settle-sum");
    let arm = "enforced";
    const gbp = RH.gbp;
    const s = {
      Q: slider(sh, { key: "q", label: "Quantity promised, Q", min: 1, max: 1000, step: 1, value: 500, fmt: function (v) { return RH.int(v) + " units"; }, on: draw }),
      D: slider(sh, { key: "d", label: "Delivered by the deadline, D", min: 0, max: 1200, step: 1, value: 380, fmt: function (v) { return RH.int(v) + " units"; }, on: draw }),
      P: slider(sh, { key: "p", label: "Price, P", min: 0, max: 200000, step: 1000, value: 60000, fmt: gbp, on: draw }),
      K: slider(sh, { key: "k", label: "Penalty per missing unit, K", min: 0, max: 400, step: 5, value: 120, fmt: gbp, on: draw }),
      B: slider(sh, { key: "b", label: "Bond, B", min: 0, max: 40000, step: 1000, value: 10000, fmt: gbp, on: draw, hint: "Escrowed from the seller at signing (enforced only)" })
    };
    RH.seg("settle-arm", function (v) { arm = v; s.B.wrap.style.opacity = v === "enforced" ? 1 : 0.45; draw(); });

    const NS = "http://www.w3.org/2000/svg";
    function svgEl(tag, attrs, text) { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (text != null) e.textContent = text; return e; }

    function draw() {
      const terms = { quantity: s.Q.get(), price: s.P.get(), penalty_per_unit: s.K.get(), bond: arm === "enforced" ? s.B.get() : 0 };
      const r = computeSupplySettlement(terms, s.D.get());
      const W = 640, H = 320;
      const svg = svgEl("svg", { viewBox: "0 0 " + W + " " + H, role: "presentation" });
      const N = { buyer: [70, 160], seller: [570, 160], escrow: [320, 56], bond: [320, 264] };
      const maxAmt = Math.max(1, terms.price, terms.bond, r.penalty);
      const width = function (a) { return a <= 0 ? 0 : 1.5 + 12 * Math.sqrt(a / maxAmt); };
      function arc(from, to, amount, label, cls, bend) {
        if (amount <= 0) return;
        const a = N[from], b = N[to];
        const mx = (a[0] + b[0]) / 2 + (bend[0] || 0), my = (a[1] + b[1]) / 2 + (bend[1] || 0);
        const sw = cls === "owed" ? 2 : width(amount);
        svg.appendChild(svgEl("path", { class: "arc " + (cls || ""), d: "M" + a[0] + "," + a[1] + " Q" + mx + "," + my + " " + b[0] + "," + b[1], "stroke-width": sw }));
        // label at the curve's midpoint, pushed off the stroke, away from the centre of the diagram
        const px = 0.25 * a[0] + 0.5 * mx + 0.25 * b[0], py = 0.25 * a[1] + 0.5 * my + 0.25 * b[1];
        let nx = -(b[1] - a[1]), ny = b[0] - a[0];
        const nl = Math.hypot(nx, ny) || 1; nx /= nl; ny /= nl;
        if ((px - W / 2) * nx + (py - H / 2) * ny < 0 || (Math.abs(py - H / 2) < 2 && ny > 0)) { nx = -nx; ny = -ny; }
        const off = sw / 2 + 8;
        const above = ny < 0;
        const lx = px + nx * off, ly = py + ny * off + (above ? -14 : 12);
        svg.appendChild(svgEl("text", { class: "arc-l", x: lx, y: ly, "text-anchor": "middle" }, gbp(amount)));
        svg.appendChild(svgEl("text", { class: "arc-n", x: lx, y: ly + 13, "text-anchor": "middle" }, label));
      }
      if (arm === "enforced") {
        arc("escrow", "seller", r.seller_payment, "payment", "", [60, -40]);
        arc("escrow", "buyer", r.buyer_refund, "refund", "", [-60, -40]);
        arc("bond", "buyer", r.penalty_from_bond, "penalty from bond", "", [-60, 40]);
        arc("bond", "seller", r.bond_returned, "bond returned", "", [60, 40]);
        arc("seller", "buyer", r.penalty_from_balance, "penalty from the seller\u2019s balance", "force", [0, 0]);
      } else {
        arc("buyer", "seller", r.seller_payment, "owed by the buyer", "owed", [0, -170]);
        arc("seller", "buyer", r.penalty, "penalty owed by the seller", "owed", [0, 170]);
      }
      Object.keys(N).forEach(function (k) {
        const p = N[k], g = svgEl("g", { class: "node" });
        const muted = arm === "cheap_talk" && (k === "escrow" || k === "bond");
        if (muted) return; // cheap talk escrows nothing: no escrow, no bond
        const label = { buyer: "Buyer", seller: "Seller", escrow: "Escrow", bond: "Bond" }[k];
        const sub = { buyer: arm === "enforced" ? "escrowed " + gbp(terms.price) : "no escrow", seller: arm === "enforced" ? "bonded " + gbp(terms.bond) : "no bond",
          escrow: muted ? "not used" : "held " + gbp(terms.price), bond: muted ? "not used" : "held " + gbp(terms.bond) }[k];
        g.appendChild(svgEl("rect", { x: p[0] - 58, y: p[1] - 24, width: 116, height: 48, rx: 4, opacity: muted ? 0.4 : 1 }));
        g.appendChild(svgEl("text", { x: p[0], y: p[1] - 3, "text-anchor": "middle", opacity: muted ? 0.45 : 1 }, label));
        g.appendChild(svgEl("text", { class: "sub", x: p[0], y: p[1] + 13, "text-anchor": "middle", opacity: muted ? 0.6 : 1 }, sub));
        svg.appendChild(g);
      });
      if (arm === "cheap_talk") svg.appendChild(svgEl("text", { class: "phase-l", x: W / 2, y: H / 2 + 4, "text-anchor": "middle" }, "Nothing moves. The same numbers become obligations."));
      flow.replaceChildren(svg);

      sum.replaceChildren();
      const cell = function (k, v, cls) { const d = el("div"); d.appendChild(el("dt", null, k)); const dd = el("dd", { class: cls || "" }, v); d.appendChild(dd); sum.appendChild(d); return dd; };
      const oc = cell("Outcome", r.outcome, "outcome oc-" + r.outcome);
      oc.setAttribute("aria-label", "Outcome: " + r.outcome);
      cell("Counted delivery", RH.int(Math.min(r.delivered, r.quantity)) + " of " + RH.int(r.quantity));
      cell("Penalty", gbp(r.penalty));
      if (arm === "enforced") {
        cell("Seller receives", gbp(r.seller_payment));
        cell("Buyer refund", gbp(r.buyer_refund));
        cell("Penalty from the seller's balance", gbp(r.penalty_from_balance));
      } else {
        cell("Buyer owes the seller", gbp(r.seller_payment));
        cell("Seller owes the buyer", gbp(r.penalty));
        cell("Moved by the exchange", gbp(0));
      }
    }
    draw();
  }

  /* ---------------------------------------------------------------- fig. 11 instalments */
  function instalmentsFig() {
    const sh = document.getElementById("inst-sliders"); if (!sh) return;
    const chart = document.getElementById("inst-chart"), out = document.getElementById("inst-sum");
    const fee = slider(sh, { key: "fee", label: "Licence fee", min: 0, max: 100000, step: 1000, value: 20000, fmt: RH.gbp, on: draw });
    const len = slider(sh, { key: "len", label: "Window, start to deadline", min: 30, max: 1095, step: 1, value: 365, fmt: function (v) { return v + " days"; }, on: draw });
    const chkWrap = el("label", { class: "check" });
    const chk = el("input", { type: "checkbox" });
    chkWrap.appendChild(chk); chkWrap.appendChild(document.createTextNode("Fold at the horizon (rules 2)"));
    sh.appendChild(chkWrap);
    const hz = slider(sh, { key: "hz", label: "Horizon, days after the start", min: 31, max: 1095, step: 1, value: 300, fmt: function (v) { return v + " days"; }, on: draw });
    hz.wrap.style.display = "none";
    chk.addEventListener("change", function () { hz.wrap.style.display = chk.checked ? "" : "none"; draw(); });

    const NS = "http://www.w3.org/2000/svg";
    function svgEl(tag, attrs, text) { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (text != null) e.textContent = text; return e; }

    function draw() {
      const F = fee.get(), L = len.get(), ws = 0, dl = L * TICKS_PER_DAY;
      const lastDue = chk.checked ? hz.get() * TICKS_PER_DAY - GRACE_TICKS : null;
      const raw = split(F, dueTicks(ws, dl));
      const sched = instalments(F, ws, dl, lastDue);
      const W = Math.max(320, chart.clientWidth || 600), H = 250;
      const svg = svgEl("svg", { viewBox: "0 0 " + W + " " + H, width: W, height: H, role: "img",
        "aria-label": sched.length ? sched.length + " instalments: " + sched.map(function (p) { return RH.gbp(p[1]) + " on day " + p[0] / TICKS_PER_DAY; }).join(", ") : "No instalments" });
      const padL = 8, padR = 8, tlY = 30;
      const span = Math.max(L, chk.checked ? hz.get() : 0);
      const tx = function (dayN) { return padL + (W - padL - padR) * dayN / span; };
      // timeline
      svg.appendChild(svgEl("rect", { class: "win", x: tx(0), y: tlY - 10, width: tx(L) - tx(0), height: 20 }));
      svg.appendChild(svgEl("line", { x1: tx(0), x2: tx(span), y1: tlY, y2: tlY, stroke: "#3a3e37" }));
      svg.appendChild(svgEl("text", { class: "day", x: tx(0), y: tlY - 16 }, "day 0, the right starts"));
      svg.appendChild(svgEl("text", { class: "day", x: tx(L), y: tlY - 16, "text-anchor": L > span * 0.7 ? "end" : "start" }, "deadline, day " + L));
      if (lastDue != null) {
        const ld = Math.max(0, lastDue / TICKS_PER_DAY);
        svg.appendChild(svgEl("line", { class: "hz", x1: tx(ld), x2: tx(ld), y1: tlY - 12, y2: tlY + 12 }));
        svg.appendChild(svgEl("text", { class: "hz-l", x: tx(ld) < 150 ? tx(ld) + 6 : tx(ld) - 6, y: tlY + 24, "text-anchor": tx(ld) < 150 ? "start" : "end" }, "horizon − 30 days"));
      }
      const n = sched.length;
      const colW = n ? Math.min(64, (W - padL - padR) / n) : 0;
      const maxA = n ? Math.max.apply(null, sched.map(function (p) { return p[1]; })) : 1;
      const base = H - 30, top = 92;
      const x0 = padL + ((W - padL - padR) - colW * n) / 2;
      sched.forEach(function (p, i) {
        const dN = p[0] / TICKS_PER_DAY;
        const cx = x0 + colW * i + colW / 2;
        const bw = Math.min(24, colW - 6);
        const h = Math.max(2, (base - top) * p[1] / maxA);
        const folded = lastDue != null && raw.length !== sched.length && i === n - 1;
        svg.appendChild(svgEl("line", { x1: tx(dN), y1: tlY + 4, x2: cx, y2: base - h - 18, stroke: "#3a3e37" }));
        svg.appendChild(svgEl("circle", { cx: tx(dN), cy: tlY, r: 3.5, fill: "#d8ae66", stroke: "#1c1e1b", "stroke-width": 2 }));
        const r = 4, x1 = cx - bw / 2, y1 = base - h;
        svg.appendChild(svgEl("path", { class: "bar" + (i === n - 1 && n > 1 ? " last" : "") + (folded ? " folded" : ""),
          d: "M" + x1 + "," + base + "V" + (y1 + r) + "Q" + x1 + "," + y1 + " " + (x1 + r) + "," + y1 + "H" + (x1 + bw - r) + "Q" + (x1 + bw) + "," + y1 + " " + (x1 + bw) + "," + (y1 + r) + "V" + base + "Z" }));
        if (colW >= 34 || i === n - 1) svg.appendChild(svgEl("text", { class: "val", x: cx, y: y1 - 6, "text-anchor": "middle" }, RH.gbpShort(p[1])));
        if (colW >= 30 || i === n - 1 || i === 0) svg.appendChild(svgEl("text", { class: "day", x: cx, y: base + 16, "text-anchor": "middle" }, "d" + (Number.isInteger(dN) ? dN : dN.toFixed(1))));
      });
      svg.appendChild(svgEl("line", { x1: padL, x2: W - padR, y1: base, y2: base, stroke: "#3a3e37" }));
      chart.replaceChildren(svg);
      const total = sched.reduce(function (a, p) { return a + p[1]; }, 0);
      out.replaceChildren();
      if (!n) { out.textContent = "A fee of £0 creates no obligation."; return; }
      const b = function (t) { return el("b", null, t); };
      out.appendChild(b(n + (n === 1 ? " instalment" : " instalments")));
      out.appendChild(document.createTextNode(n > 1 && sched[0][1] === sched[n - 1][1] ? " of " : ", "));
      out.appendChild(b(n > 1 && sched[0][1] === sched[n - 1][1] ? RH.gbp(sched[0][1]) : sched.map(function (p) { return RH.gbp(p[1]); }).join(" + ")));
      out.appendChild(document.createTextNode(" on days " + sched.map(function (p) { const d = p[0] / TICKS_PER_DAY; return Number.isInteger(d) ? d : d.toFixed(1); }).join(", ") + ". They sum to "));
      out.appendChild(b(RH.gbp(total)));
      out.appendChild(document.createTextNode(total === F ? ", the fee (law L5)." : "."));
      if (lastDue != null && raw.length !== n) out.appendChild(document.createTextNode(" " + (raw.length - n) + " later instalment" + (raw.length - n === 1 ? " was" : "s were") + " folded into the last one in time."));
    }
    draw();
    let lw = 0;
    new ResizeObserver(function () { if (chart.clientWidth !== lw) { lw = chart.clientWidth; requestAnimationFrame(draw); } }).observe(chart);
  }

  /* ---------------------------------------------------------------- fig. 12 conservation */
  function conservation() {
    const host = document.getElementById("cons-chart"); if (!host) return;
    const names = ["A", "B", "C", "D", "escrow:C9", "exchange"];
    const bal = names.map(function () { return 0; });
    let lastMsg = "No transfers yet. Every account starts at £0.", lastPair = null, timer = 0, left = 0;
    const NS = "http://www.w3.org/2000/svg";
    function svgEl(tag, attrs, text) { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (text != null) e.textContent = text; return e; }
    let seed = 7;
    function rnd() { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; }
    function step() {
      const a = Math.floor(rnd() * names.length);
      let b = Math.floor(rnd() * (names.length - 1)); if (b >= a) b++;
      const amt = (1 + Math.floor(rnd() * 20)) * 1000;
      bal[a] -= amt; bal[b] += amt;
      lastPair = [a, b];
      lastMsg = names[a] + " → " + names[b] + "  " + RH.gbp(amt);
      draw();
    }
    function draw() {
      const W = Math.max(320, host.clientWidth - 48 || 600), H = 230;
      const svg = svgEl("svg", { viewBox: "0 0 " + W + " " + H, width: W, height: H, role: "img",
        "aria-label": "Balances: " + names.map(function (n, i) { return n + " " + RH.gbp(bal[i]); }).join(", ") + ". Sum: £0." });
      const sumW = W < 480 ? 0 : 150;
      const colW = (W - sumW) / names.length, mid = 110;
      const maxAbs = Math.max(20000, Math.max.apply(null, bal.map(Math.abs)));
      const k = 80 / maxAbs;
      svg.appendChild(svgEl("line", { class: "zero", x1: 0, x2: W - sumW - 10, y1: mid, y2: mid }));
      names.forEach(function (n, i) {
        const cx = colW * i + colW / 2, v = bal[i], h = Math.abs(v) * k, bw = Math.min(24, colW - 14);
        if (v !== 0) svg.appendChild(svgEl("rect", { class: v > 0 ? "bar-pos" : "bar-neg", x: cx - bw / 2, y: v > 0 ? mid - h : mid, width: bw, height: Math.max(1, h), rx: 3 }));
        svg.appendChild(svgEl("text", { class: "acct-l", x: cx, y: H - 8, "text-anchor": "middle" }, W < 480 && n.length > 4 ? n.replace("escrow:", "esc:").replace("exchange", "exch.") : n));
        svg.appendChild(svgEl("text", { class: "acct-v", x: cx, y: v >= 0 ? mid - h - 7 : mid + h + 15, "text-anchor": "middle" }, RH.gbpShort(v)));
      });
      if (lastPair) {
        const ax = colW * lastPair[0] + colW / 2, bx = colW * lastPair[1] + colW / 2;
        svg.appendChild(svgEl("path", { class: "xfer", d: "M" + ax + ",14 Q" + (ax + bx) / 2 + ",-6 " + bx + ",14", "marker-end": "" }));
        svg.appendChild(svgEl("circle", { cx: bx, cy: 14, r: 3, fill: "#eeede7" }));
      }
      const total = bal.reduce(function (a, b) { return a + b; }, 0);
      if (sumW) {
        svg.appendChild(svgEl("text", { class: "sum-l", x: W - sumW + 16, y: mid - 20 }, "Sum of all balances"));
        svg.appendChild(svgEl("text", { class: "sum-v", x: W - sumW + 16, y: mid + 8 }, RH.gbp(total)));
        svg.appendChild(svgEl("text", { class: "log", x: W - sumW + 16, y: mid + 32 }, lastPair ? lastMsg : ""));
      }
      host.replaceChildren(svg);
      if (!sumW) host.appendChild(el("p", { class: "aside-note" }, "Sum of all balances: " + RH.gbp(total) + (lastPair ? ". Last: " + lastMsg : "")));
    }
    document.getElementById("cons-step").addEventListener("click", step);
    const runBtn = document.getElementById("cons-run");
    runBtn.addEventListener("click", function () {
      if (timer) { clearInterval(timer); timer = 0; runBtn.setAttribute("aria-pressed", "false"); runBtn.textContent = "Run 40"; return; }
      left = 40; runBtn.setAttribute("aria-pressed", "true"); runBtn.textContent = "Stop";
      timer = setInterval(function () {
        step(); left--;
        if (left <= 0) { clearInterval(timer); timer = 0; runBtn.setAttribute("aria-pressed", "false"); runBtn.textContent = "Run 40"; }
      }, RH.reduced.matches ? 400 : 140);
    });
    for (let i = 0; i < 9; i++) step(); // start from a ledger with some history, drawn once
    let lw = 0;
    new ResizeObserver(function () { if (host.clientWidth !== lw) { lw = host.clientWidth; requestAnimationFrame(draw); } }).observe(host);
  }

  hotspots(); promise(); obligations(); settlement(); instalmentsFig(); conservation();
})();
