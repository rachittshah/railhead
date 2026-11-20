/* Railhead figures: Arena 2 lines (fig. 2), the fleet against net worth (fig. 4), seed games (fig. 5). */
(function () {
  "use strict";
  const RH = window.RH;
  const d3 = window.d3;
  if (!d3) return;

  function tipEl(host) {
    let t = host.querySelector(".tip");
    if (!t) { t = RH.el("div", { class: "tip", role: "presentation" }); host.appendChild(t); }
    return t;
  }
  function placeTip(tip, host, x, y) {
    const w = host.clientWidth, tw = tip.offsetWidth, th = tip.offsetHeight;
    let left = x + 16; if (left + tw > w) left = x - tw - 16; if (left < 0) left = 0;
    let top = y - th / 2; if (top < 0) top = 0;
    tip.style.left = left + "px"; tip.style.top = top + "px";
  }
  function tipRow(color, name, value, sel) {
    const r = RH.el("div", { class: "tip-row" + (sel ? " sel" : "") });
    const k = RH.el("span", { class: "key" }); k.style.background = color;
    r.appendChild(k); r.appendChild(RH.el("span", { class: "n" }, name)); r.appendChild(RH.el("span", { class: "v" }, value));
    return r;
  }

  /* ------------------------------------------------------------------ Fig. 2: Arena 2 */
  function arena(d) {
    const host = document.getElementById("arena-chart");
    const legendEl = document.getElementById("arena-legend");
    const players = d.results.filter(function (r) { return r.status === "verified"; })
      .sort(function (a, b) { return b.net_worth - a.net_worth; });
    const ref = {};
    d.results.forEach(function (r) { ref[r.player] = r; });
    const H = RH.day(ref["claude-opus-5-5"].reached), S = H - 3650 * RH.DAY, E = H + 365 * RH.DAY;
    const state = { k: "net_worth", scale: "linear", sel: null };
    const COUNT = { vehicles: 1, stations: 1 };
    const LABEL = { net_worth: "Net worth", money: "Cash", vehicles: "Vehicles", stations: "Stations" };

    function series(p, k) {
      const pts = d.curves[p.player].map(function (c) { return { t: RH.day(c.date), v: c[k], est: false }; });
      let tail = [];
      if (k === "net_worth") {
        const last = pts[pts.length - 1];
        tail = [{ t: last.t, v: last.v, est: true }];
        if (last.t < H) tail.push({ t: H, v: p.net_worth_horizon, est: true });
        tail.push({ t: E, v: p.net_worth, est: true });
      }
      return { p: p, pts: pts, tail: tail };
    }
    const asinh = function (v) { return Math.asinh(v / 1e5); };

    function valueAt(s, t) {
      const all = s.pts.concat(s.tail.slice(1));
      if (t < all[0].t || t > all[all.length - 1].t) return null;
      let i = 0; while (i < all.length - 1 && all[i + 1].t <= t) i++;
      const a = all[i], b = all[Math.min(i + 1, all.length - 1)];
      if (COUNT[state.k] || b.t === a.t) return { v: a.v, est: a.est && i > 0 };
      const k = (t - a.t) / (b.t - a.t);
      return { v: a.v + (b.v - a.v) * k, est: b.est };
    }
    const fmt = function (v) { return COUNT[state.k] ? RH.int(v) : RH.gbpShort(v); };
    const fmtFull = function (v) { return COUNT[state.k] ? RH.int(v) : RH.gbp(v); };

    // legend
    players.forEach(function (p) {
      const li = RH.el("li");
      const b = RH.el("button", { type: "button", "aria-pressed": "false", "data-p": p.player });
      const k = RH.el("span", { class: "key" }); k.style.background = RH.color(p.player);
      b.appendChild(k); b.appendChild(RH.el("span", null, p.player)); b.appendChild(RH.el("span", { class: "v" }, RH.gbpShort(p.net_worth)));
      b.addEventListener("click", function () { state.sel = state.sel === p.player ? null : p.player; draw(); });
      li.appendChild(b); legendEl.appendChild(li);
    });

    const tip = tipEl(host);
    let lastW = 0;

    function draw() {
      const W = host.clientWidth; if (!W) return;
      lastW = W;
      const small = W < 560;
      const m = { t: 14, r: small ? 82 : 150, b: 30, l: small ? 46 : 58 };
      const Hh = small ? 300 : 420;
      const iw = W - m.l - m.r, ih = Hh - m.t - m.b;
      const k = state.k, logy = state.scale === "asinh" && !COUNT[k];
      const S2 = players.map(function (p) { return series(p, k); });
      const vals = [];
      S2.forEach(function (s) { s.pts.concat(s.tail).forEach(function (q) { vals.push(q.v); }); });
      const tf = logy ? asinh : function (v) { return v; };
      let lo = d3.min(vals), hi = d3.max(vals);
      const x = d3.scaleUtc().domain([S, E]).range([0, iw]);
      const y = d3.scaleLinear().domain([Math.min(0, tf(lo)), tf(hi)]).range([ih, 0]).nice();
      const Y = function (v) { return y(tf(v)); };

      d3.select(host).select("svg").remove();
      const svg = d3.select(host).insert("svg", ".tip").attr("width", W).attr("height", Hh).attr("viewBox", [0, 0, W, Hh]);
      const g = svg.append("g").attr("transform", "translate(" + m.l + "," + m.t + ")");

      g.append("rect").attr("class", "runoff").attr("x", x(H)).attr("y", 0).attr("width", x(E) - x(H)).attr("height", ih);
      g.append("text").attr("class", "runoff-l").attr("x", x(H) + 6).attr("y", ih - 8).text("Run-off");

      // y grid + ticks
      let ticks;
      if (logy) {
        const cand = [-1e6, -1e5, 0, 1e5, 1e6, 1e7, 4e7];
        ticks = cand.filter(function (v) { return tf(v) >= y.domain()[0] && tf(v) <= y.domain()[1]; });
      } else ticks = y.ticks(small ? 4 : 5).map(function (v) { return v; });
      const gy = g.append("g").attr("class", "grid");
      ticks.forEach(function (tv) {
        const yy = logy ? Y(tv) : y(tv);
        gy.append("line").attr("x1", 0).attr("x2", iw).attr("y1", yy).attr("y2", yy).attr("class", tv === 0 ? "baseline" : null);
        g.append("text").attr("class", "tick-l").attr("x", -10).attr("y", yy).attr("dy", "0.32em").attr("text-anchor", "end")
          .text(COUNT[k] ? RH.int(tv) : RH.gbpShort(tv));
      });
      const xt = x.ticks(small ? d3.utcYear.every(2) : d3.utcYear.every(1));
      g.append("g").attr("class", "axis").attr("transform", "translate(0," + (ih + 18) + ")")
        .selectAll("text").data(xt).join("text").attr("x", function (t) { return x(t); }).attr("text-anchor", "middle")
        .text(function (t) { return t.getUTCFullYear(); });

      if (logy && k === "net_worth") {
        [["Scripted truck route", ref.baseline.net_worth], ["No play", ref.idle.net_worth]].forEach(function (rr, i) {
          const yy = Y(rr[1]);
          g.append("line").attr("class", "refline").attr("x1", 0).attr("x2", x(E)).attr("y1", yy).attr("y2", yy);
          g.append("text").attr("class", "ref-l").attr("x", 4).attr("y", yy + (i ? 14 : -6)).text(rr[0] + ", after the run-off: " + RH.gbpShort(rr[1]));
        });
      }

      const line = d3.line().x(function (q) { return x(q.t); }).y(function (q) { return Y(q.v); });
      if (COUNT[k]) line.curve(d3.curveStepAfter);
      // draw grey first, then coloured, then selected on top
      const order = S2.slice().sort(function (a, b) {
        const w = function (s) { return s.p.player === state.sel ? 2 : (RH.COLOR[s.p.player] ? 1 : 0); };
        return w(a) - w(b);
      });
      const gs = g.append("g");
      order.forEach(function (s) {
        const sg = gs.append("g").attr("class", "series" + (state.sel ? (s.p.player === state.sel ? " is-sel" : " is-dim") : ""));
        sg.append("path").attr("d", line(s.pts)).attr("stroke", RH.color(s.p.player));
        if (s.tail.length) sg.append("path").attr("class", "ro").attr("d", line(s.tail)).attr("stroke", RH.color(s.p.player));
      });

      // end labels: coloured series + the selected one, with leader lines when they crowd
      const labelled = S2.filter(function (s) { return RH.COLOR[s.p.player] || s.p.player === state.sel; });
      const ends = labelled.map(function (s) {
        const lastP = s.tail.length ? s.tail[s.tail.length - 1] : s.pts[s.pts.length - 1];
        return { s: s, x: x(lastP.t), y: Y(lastP.v), v: lastP.v, ly: Y(lastP.v) };
      }).sort(function (a, b) { return a.ly - b.ly; });
      const gap = small ? 26 : 30;
      for (let i = 1; i < ends.length; i++) if (ends[i].ly - ends[i - 1].ly < gap) ends[i].ly = ends[i - 1].ly + gap;
      const over = ends.length ? ends[ends.length - 1].ly - ih : 0;
      if (over > 0) ends.forEach(function (e) { e.ly -= over; });
      const gl = g.append("g");
      ends.forEach(function (e) {
        const lx = iw + 14;
        gl.append("path").attr("class", "lead").attr("fill", "none").attr("d", "M" + (e.x + 4) + "," + e.y + "L" + (lx - 4) + "," + e.ly);
        gl.append("circle").attr("cx", e.x).attr("cy", e.y).attr("r", 4).attr("fill", e.s.p.player === state.sel ? "#eeede7" : RH.color(e.s.p.player)).attr("class", "hover-dot");
        gl.append("text").attr("class", "end-l").attr("x", lx).attr("y", e.ly - 2).text(small ? e.s.p.player.replace("claude-", "").replace("-5-5", "") : e.s.p.player);
        gl.append("text").attr("class", "end-v").attr("x", lx).attr("y", e.ly + 12).text(fmt(e.v));
      });

      // hover layer
      const xh = g.append("line").attr("class", "xhair").attr("y1", 0).attr("y2", ih).style("opacity", 0);
      const ov = g.append("rect").attr("width", iw).attr("height", ih).attr("fill", "transparent").attr("tabindex", 0)
        .attr("aria-label", "Chart area: use the left and right arrow keys to read values by month");
      let kt = null;
      function show(t) {
        t = Math.max(S, Math.min(E, t));
        const mo = new Date(t); mo.setUTCDate(1); const tt = Math.max(S, mo.getTime());
        xh.attr("x1", x(tt)).attr("x2", x(tt)).style("opacity", 1);
        tip.replaceChildren();
        const head = new Date(tt).toLocaleDateString("en-GB", { month: "long", year: "numeric", timeZone: "UTC" });
        tip.appendChild(RH.el("p", { class: "tip-h" }, head + (tt >= H ? ", run-off" : "") + ", " + LABEL[k].toLowerCase()));
        const rows = S2.map(function (s) { return { s: s, r: valueAt(s, tt) }; }).filter(function (o) { return o.r; })
          .sort(function (a, b) { return b.r.v - a.r.v; });
        rows.forEach(function (o) {
          tip.appendChild(tipRow(o.s.p.player === state.sel ? "#eeede7" : RH.color(o.s.p.player), o.s.p.player,
            (o.r.est ? "≈ " : "") + fmt(o.r.v), o.s.p.player === state.sel));
        });
        if (!rows.length) tip.appendChild(RH.el("p", { class: "tip-h" }, "No company has a sample yet."));
        tip.classList.add("on");
        placeTip(tip, host, m.l + x(tt), m.t + ih / 2);
        kt = tt;
      }
      function hide() { xh.style("opacity", 0); tip.classList.remove("on"); }
      ov.on("pointermove", function (ev) { show(x.invert(d3.pointer(ev)[0]).getTime()); })
        .on("pointerleave", hide).on("blur", hide)
        .on("focus", function () { show(kt || H); })
        .on("keydown", function (ev) {
          if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
          ev.preventDefault();
          const dd = new Date(kt || H); dd.setUTCMonth(dd.getUTCMonth() + (ev.key === "ArrowRight" ? 3 : -3)); show(dd.getTime());
        });

      legendEl.querySelectorAll("button").forEach(function (b) { b.setAttribute("aria-pressed", b.dataset.p === state.sel ? "true" : "false"); });
      const scaleSeg = document.getElementById("arena-scale");
      scaleSeg.querySelectorAll("button").forEach(function (b) { b.disabled = !!COUNT[k]; });
      scaleSeg.style.opacity = COUNT[k] ? 0.4 : 1;
      scaleSeg.title = COUNT[k] ? "The log-like scale applies to money" : "";
    }

    seg("arena-metric", function (v) { state.k = v; draw(); });
    seg("arena-scale", function (v) { state.scale = v; draw(); });
    draw();
    new ResizeObserver(function () { if (host.clientWidth !== lastW) requestAnimationFrame(draw); }).observe(host);

    // data table
    const table = document.getElementById("arena-table");
    const head = ["", "Model", "Net worth after year 11", "At the horizon", "Gain / baseline gain", "Actions", "Replay"];
    const thead = table.createTHead().insertRow();
    head.forEach(function (h, i) { const th = RH.el("th", { scope: "col", class: i >= 2 && i <= 5 ? "num" : "" }, h); thead.appendChild(th); });
    const tb = table.createTBody();
    const rows = players.concat([ref.baseline, ref.idle]);
    rows.forEach(function (r, i) {
      const tr = tb.insertRow();
      const isRef = r.status === "certificate";
      if (isRef) tr.className = "ref";
      const name = r.player === "baseline" ? "Scripted truck route (baseline)" : r.player === "idle" ? "No play" : r.player;
      const cells = [isRef ? "" : String(i + 1), name, RH.gbp(r.net_worth),
        r.net_worth_horizon != null ? RH.gbp(r.net_worth_horizon) : "–",
        r.gain_x_baseline.toFixed(1), r.actions != null ? RH.int(r.actions) : (r.player === "idle" ? "0" : "–"),
        isRef ? "certificate" : (r.valid ? "valid, " + r.divergences + " divergences" : "not valid")];
      cells.forEach(function (c, j) { tr.appendChild(RH.el(j === 1 ? "th" : "td", { class: j >= 2 && j <= 5 ? "num" : "", scope: j === 1 ? "row" : null }, c)); });
    });
  }

  function seg(id, cb) {
    const g = document.getElementById(id); if (!g) return;
    const bs = Array.from(g.querySelectorAll("button"));
    function pick(b) {
      bs.forEach(function (x) { x.setAttribute("aria-checked", x === b ? "true" : "false"); x.tabIndex = x === b ? 0 : -1; });
      cb(b.dataset.k);
    }
    bs.forEach(function (b, i) {
      b.tabIndex = b.getAttribute("aria-checked") === "true" ? 0 : -1;
      b.addEventListener("click", function () { pick(b); });
      b.addEventListener("keydown", function (e) {
        const dir = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
        if (!dir) return;
        e.preventDefault();
        let j = i; do { j = (j + dir + bs.length) % bs.length; } while (bs[j].disabled && j !== i);
        bs[j].focus(); pick(bs[j]);
      });
    });
  }
  RH.seg = seg;

  /* ------------------------------------------------------------------ Fig. 4: fleet against net worth */
  function fleet(d) {
    const host = document.getElementById("fleet-chart");
    const players = d.results.filter(function (r) { return r.status === "verified"; }).sort(function (a, b) { return b.net_worth - a.net_worth; });
    const maxV = d3.max(players, function (p) { const f = d.fleet_mix[p.player]; return f.road + f.rail + f.air + f.water; });
    const maxN = d3.max(players, function (p) { return p.net_worth; });
    host.appendChild(RH.el("div", { class: "fleet-h" }, "Model"));
    host.appendChild(RH.el("div", { class: "fleet-h" }, "Vehicles bought"));
    host.appendChild(RH.el("div", { class: "fleet-h" }, "Net worth after year 11"));
    players.forEach(function (p) {
      const f = d.fleet_mix[p.player], total = f.road + f.rail + f.air + f.water;
      const row = RH.el("div", { class: "fleet-row" + (p.player === "claude-opus-5-5" || p.player === "claude-haiku-5-5" ? " hl" : "") });
      const nm = RH.el("div", { class: "fleet-name" });
      const k = RH.el("span", { class: "key" }); k.style.background = RH.color(p.player);
      nm.appendChild(k); nm.appendChild(RH.el("span", null, p.player.replace(/^claude-/, "")));
      const vb = RH.el("div", { class: "fleet-bar" });
      const bars = RH.el("div", { class: "bars" });
      const parts = [["air", f.air], ["rail", f.rail], ["road", f.road], ["water", f.water]].filter(function (x) { return x[1] > 0; });
      bars.style.width = (total / maxV * 62) + "%";
      parts.forEach(function (x, i) {
        const b = RH.el("span", { class: "b" }); b.style.flex = x[1] + " 1 0";
        b.style.opacity = [0.75, 0.5, 0.32][i] || 0.3;
        b.title = x[1] + " " + x[0];
        bars.appendChild(b);
      });
      vb.appendChild(bars);
      const lab = RH.el("span", { class: "lab" }, RH.int(total) + " ");
      lab.appendChild(RH.el("span", { class: "muted" }, parts.map(function (x) { return x[1] + " " + x[0]; }).join(", ")));
      vb.appendChild(lab);
      vb.setAttribute("aria-label", p.player + ": " + total + " vehicles bought: " + parts.map(function (x) { return x[1] + " " + x[0]; }).join(", "));
      const nb = RH.el("div", { class: "fleet-bar" });
      const nbar = RH.el("div", { class: "bars" });
      const nbb = RH.el("span", { class: "b nw" }); nbb.style.width = "100%";
      nbar.style.width = Math.max(0, p.net_worth / maxN * 62) + "%";
      if (p.net_worth > 0) nbar.appendChild(nbb);
      nb.appendChild(nbar);
      nb.appendChild(RH.el("span", { class: "lab" }, RH.gbpShort(p.net_worth)));
      row.appendChild(nm); row.appendChild(vb); row.appendChild(nb);
      host.appendChild(row);
    });
  }

  /* ------------------------------------------------------------------ Fig. 5: seed games */
  function seeds(sg, st, a2) {
    const host = document.getElementById("seed-chart");
    const P = ["claude-opus-5-5", "gpt-6-astra", "gpt-6-sol"];
    const leg = document.getElementById("seed-legend");
    P.forEach(function (p) {
      const li = RH.el("li"); const k = RH.el("span", { class: "key" }); k.style.background = RH.color(p);
      li.appendChild(k); li.appendChild(document.createTextNode(p)); leg.appendChild(li);
    });
    const ring = RH.el("li"); ring.appendChild(RH.el("span", { class: "ring" })); ring.appendChild(document.createTextNode("log ends early")); leg.appendChild(ring);

    const rows = sg.maps.map(function (m) {
      const cells = {};
      m.results.forEach(function (r) { cells[r.player] = { nw: r.net_worth, end: r.end, days: r.days_played }; });
      return { map: m.task, cells: cells };
    });
    const a2r = {}; a2.results.forEach(function (r) { a2r[r.player] = r; });
    const arenaRow = { map: "arena-dev-0100", arena: true, cells: {} };
    P.forEach(function (p) { arenaRow.cells[p] = { nw: a2r[p].net_worth, end: "submitted", days: 3650 }; });
    rows.push(arenaRow);
    const finished = function (c) { return c.end === "submitted" || c.end === "horizon"; };
    const tip = tipEl(host);
    let lastW = 0;

    function draw() {
      const W = host.clientWidth; if (!W) return; lastW = W;
      const small = W < 560;
      const m = { t: 10, r: 16, b: 30, l: small ? 92 : 128 };
      const rh = small ? 26 : 30;
      const Hh = m.t + m.b + rh * rows.length + 12;
      const iw = W - m.l - m.r;
      const x = d3.scaleLog().domain([1e6, 2e8]).range([0, iw]);
      d3.select(host).select("svg").remove();
      const svg = d3.select(host).insert("svg", ".tip").attr("width", W).attr("height", Hh).attr("viewBox", [0, 0, W, Hh]);
      const g = svg.append("g").attr("transform", "translate(" + m.l + "," + m.t + ")");
      const ticks = [1e6, 3e6, 1e7, 3e7, 1e8];
      const ih = rh * rows.length + 12;
      ticks.forEach(function (t) {
        g.append("line").attr("x1", x(t)).attr("x2", x(t)).attr("y1", 0).attr("y2", ih).attr("stroke", "#2e312c");
        g.append("text").attr("class", "tick-l").attr("x", x(t)).attr("y", ih + 18).attr("text-anchor", "middle").text(RH.gbpShort(t));
      });
      rows.forEach(function (r, i) {
        const yy = i * rh + rh / 2 + (r.arena ? 12 : 0);
        if (r.arena) g.append("line").attr("x1", -m.l + 4).attr("x2", iw).attr("y1", yy - rh / 2 - 6).attr("y2", yy - rh / 2 - 6).attr("stroke", "#3a3e37");
        g.append("text").attr("class", "tick-l").attr("x", -12).attr("y", yy).attr("dy", "0.32em").attr("text-anchor", "end")
          .text(r.arena ? "Arena 2" : (small ? r.map.replace("arena-train-", "map ") : r.map));
        const present = P.filter(function (p) { return r.cells[p]; });
        const xs = present.map(function (p) { return x(r.cells[p].nw); });
        if (xs.length > 1) g.append("line").attr("x1", d3.min(xs)).attr("x2", d3.max(xs)).attr("y1", yy).attr("y2", yy).attr("stroke", "#3a3e37");
        present.slice().reverse().forEach(function (p) {
          const c = r.cells[p], cx = x(c.nw), fin = finished(c);
          const dot = g.append("circle").attr("cx", cx).attr("cy", yy).attr("r", fin ? 5 : 4.2)
            .attr("fill", fin ? RH.color(p) : "#141513").attr("stroke", fin ? "#141513" : RH.color(p)).attr("stroke-width", fin ? 2 : 2);
          const label = p + ", " + (r.arena ? "Arena 2" : r.map) + ": " + RH.gbp(c.nw) + (fin ? ", " + c.end : ", log ends on day " + RH.int(c.days));
          const hit = g.append("circle").attr("cx", cx).attr("cy", yy).attr("r", 12).attr("fill", "transparent").attr("tabindex", 0)
            .attr("role", "img").attr("aria-label", label);
          const on = function () {
            dot.attr("r", fin ? 6.5 : 5.5);
            tip.replaceChildren();
            tip.appendChild(RH.el("p", { class: "tip-h" }, r.arena ? "arena-dev-0100 (Arena 2)" : r.map));
            tip.appendChild(tipRow(RH.color(p), p, RH.gbp(c.nw)));
            tip.appendChild(RH.el("p", { class: "tip-h" }, fin ? (c.end === "submitted" ? "Submitted" : "Reached the horizon") : "Log ends on day " + RH.int(c.days) + "; the company ran on"));
            tip.classList.add("on"); placeTip(tip, host, m.l + cx, m.t + yy);
          };
          const off = function () { dot.attr("r", fin ? 5 : 4.2); tip.classList.remove("on"); };
          hit.on("pointerenter", on).on("pointerleave", off).on("focus", on).on("blur", off);
        });
      });
    }
    draw();
    new ResizeObserver(function () { if (host.clientWidth !== lastW) requestAnimationFrame(draw); }).observe(host);

    // paired intervals, 10 seed maps
    const ph = document.getElementById("seed-pairs");
    const maxHi = d3.max(st.pairs, function (q) { return q.net_worth.hi; });
    st.pairs.forEach(function (q) {
      const c = RH.el("div", { class: "pair" });
      const h = RH.el("p", { class: "pair-h" });
      [q.a, q.b].forEach(function (p, i) {
        const k = RH.el("span", { class: "key" }); k.style.background = RH.color(p); h.appendChild(k);
        h.appendChild(document.createTextNode(p + (i === 0 ? " minus" : "")));
      });
      c.appendChild(h);
      c.appendChild(RH.el("p", { class: "pair-v" }, "+" + RH.gbpShort(q.net_worth.mean)));
      c.appendChild(RH.el("p", { class: "pair-ci" }, "95% interval " + RH.gbpShort(q.net_worth.lo) + " to " + RH.gbpShort(q.net_worth.hi)));
      const sv = d3.select(c).append("svg").attr("viewBox", "0 0 200 26").attr("preserveAspectRatio", "none").attr("aria-hidden", "true");
      const sx = d3.scaleLinear().domain([0, maxHi]).range([4, 196]);
      sv.append("line").attr("x1", sx(0)).attr("x2", sx(0)).attr("y1", 4).attr("y2", 22).attr("stroke", "#5e6159");
      sv.append("line").attr("x1", sx(q.net_worth.lo)).attr("x2", sx(q.net_worth.hi)).attr("y1", 13).attr("y2", 13).attr("stroke", "#a8ab9f").attr("stroke-width", 2).attr("stroke-linecap", "round");
      sv.append("circle").attr("cx", sx(q.net_worth.mean)).attr("cy", 13).attr("r", 4).attr("fill", "#eeede7").attr("stroke", "#141513").attr("stroke-width", 2);
      c.appendChild(RH.el("p", { class: "pair-w" }, q.wins_a + "–" + q.wins_b + " on " + q.n + " shared maps, sign test p = " + (q.sign_p >= 0.01 ? String(+q.sign_p.toPrecision(3)) : q.sign_p.toFixed(3))));
      ph.appendChild(c);
    });

    // table
    const table = document.getElementById("seed-table");
    const tr0 = table.createTHead().insertRow();
    ["Map"].concat(P).forEach(function (h, i) { tr0.appendChild(RH.el("th", { scope: "col", class: i ? "num" : "" }, h)); });
    const tb = table.createTBody();
    rows.forEach(function (r) {
      const tr = tb.insertRow();
      tr.appendChild(RH.el("th", { scope: "row" }, r.arena ? "arena-dev-0100 (Arena 2)" : r.map));
      P.forEach(function (p) {
        const c = r.cells[p];
        tr.appendChild(RH.el("td", { class: "num" }, c ? RH.gbp(c.nw) + (finished(c) ? "" : " (log ends day " + RH.int(c.days) + ")") : "not played"));
      });
    });
  }

  Promise.all([RH.json("arena2.json"), RH.json("seedgames.json"), RH.json("seedgames-stats.json")]).then(function (a) {
    arena(a[0]); fleet(a[0]); seeds(a[1], a[2], a[0]);
  }).catch(function (e) { console.warn(e); });
})();
