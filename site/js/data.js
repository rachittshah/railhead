/* Shared helpers: data loading, formatting, the model palette. Every number shown on the page is read
   from assets/data/*.json (copies of docs/data) or written in index.html from the repository docs. */
(function () {
  "use strict";
  const cache = new Map();
  function json(name) {
    if (!cache.has(name)) {
      cache.set(name, fetch("assets/data/" + name).then(function (r) {
        if (!r.ok) throw new Error(name + ": HTTP " + r.status);
        return r.json();
      }));
    }
    return cache.get(name);
  }

  // Validated with the dataviz validator on #141513 and #1c1e1b, --pairs all (see DESIGN.md).
  const COLOR = { "claude-opus-5-5": "#5b93dc", "gpt-6-astra": "#d9733f", "gpt-6-sol": "#2fa07a" };
  const OTHER = "#5e6159";
  const color = function (p) { return COLOR[p] || OTHER; };

  const MINUS = "−";
  function gbp(n) {
    const v = Math.round(n);
    return (v < 0 ? MINUS : "") + "£" + Math.abs(v).toLocaleString("en-GB");
  }
  function gbpShort(n) {
    const a = Math.abs(n), s = n < 0 ? MINUS : "";
    if (a >= 1e6) return s + "£" + (a / 1e6).toFixed(a >= 1e7 ? 1 : 2).replace(/\.0+$/, "") + "M";
    if (a >= 1e3) return s + "£" + Math.round(a / 1e3) + "k";
    return s + "£" + Math.round(a);
  }
  function int(n) { return Math.round(n).toLocaleString("en-GB"); }
  function day(s) { const d = new Date(s + "T00:00:00Z"); return d.getTime(); }
  const DAY = 86400000;
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  function ordinal(n) { const s = ["th", "st", "nd", "rd"], v = n % 100; return n + (s[(v - 20) % 10] || s[v] || s[0]); }
  function el(tag, attrs, text) {
    const e = document.createElement(tag);
    if (attrs) for (const k in attrs) { if (k === "class") e.className = attrs[k]; else e.setAttribute(k, attrs[k]); }
    if (text != null) e.textContent = text;
    return e;
  }
  window.RH = { json: json, color: color, COLOR: COLOR, OTHER: OTHER, gbp: gbp, gbpShort: gbpShort, int: int,
    day: day, DAY: DAY, reduced: reduced, ordinal: ordinal, el: el, MINUS: MINUS };
})();
