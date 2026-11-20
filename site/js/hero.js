/* The hero: the Arena 2 timelapse with a panel synced to the video clock.
   Video time -> game date follows the renderer's own schedule (src/railhead/timelapse.py: render_theme):
   30 fps, a 1 s hold on the start, motion linear in ticks for the middle frames, a 4 s hold on the end. */
(function () {
  "use strict";
  const RH = window.RH;
  const FPS = 30, SECONDS = 60, HOLD_START = FPS, HOLD_END = 4 * FPS;
  const N = SECONDS * FPS, MOTION = N - HOLD_START - HOLD_END;
  const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

  const video = document.getElementById("hero-video");
  if (!video) return;
  const $ = function (id) { return document.getElementById(id); };
  const scrub = $("hero-scrub"), pct = $("hero-pct"), pauseBtn = $("hero-pause"), againBtn = $("hero-again"), bigPlay = $("hero-bigplay");
  const quote = $("rp-quote"), kind = $("rp-kind"), when = $("rp-when");
  const phases = Array.from(document.querySelectorAll(".phases li"));

  let S, H, E, curve, events, lastEvent = -1, raf = 0;

  function frac(t) { // video seconds -> 0..1 of the decade plus run-off
    const i = Math.floor(t * FPS + 1e-6);
    if (i < HOLD_START) return 0;
    return Math.min(1, (i - HOLD_START) / (MOTION - 1));
  }
  function timeOf(ms) { // game date (ms) -> video seconds, the inverse of frac
    const f = Math.max(0, Math.min(1, (ms - S) / (E - S)));
    return (HOLD_START + f * (MOTION - 1)) / FPS;
  }

  function sampleAt(ms) {
    if (ms < curve[0].t) return null;
    let i = 0;
    while (i < curve.length - 1 && curve[i + 1].t <= ms) i++;
    const a = curve[i], b = curve[Math.min(i + 1, curve.length - 1)];
    const k = b.t === a.t ? 0 : (ms - a.t) / (b.t - a.t);
    const lerp = function (key) { return a[key] + (b[key] - a[key]) * k; };
    // money interpolates (as in the video); counts step at each sample
    return { nw: lerp("net_worth"), loan: a.loan, veh: a.vehicles, sta: a.stations, acts: a.acts };
  }

  function render(t) {
    if (!curve) return;
    const ms = S + frac(t) * (E - S);
    const d = new Date(ms);
    $("rp-month").textContent = MONTHS[d.getUTCMonth()];
    $("rp-year").textContent = d.getUTCFullYear();
    const runoff = ms >= H;
    const year = Math.min(10, Math.floor((ms - S) / (365.25 * RH.DAY)) + 1);
    $("rp-phase").textContent = runoff ? "Run-off: no more decisions" : "Mandate, year " + year + " of 10";
    $("rp-ledger").classList.toggle("is-runoff", runoff); // counts freeze at the last sample, the horizon
    const s = sampleAt(ms);
    $("rp-nw").textContent = s ? RH.gbp(s.nw) : "—";
    $("rp-loan").textContent = s ? RH.gbp(s.loan) : "—";
    $("rp-veh").textContent = s ? RH.int(s.veh) : "—";
    $("rp-sta").textContent = s ? RH.int(s.sta) : "—";
    $("rp-act").textContent = s ? RH.int(s.acts) : "—";

    let ev = 0;
    for (let i = 0; i < events.length; i++) if (ms >= events[i].from) ev = i;
    if (ev !== lastEvent) { lastEvent = ev; showEvent(events[ev]); }

    const p = Math.round(frac(t) * 100);
    pct.textContent = p + "%";
    scrub.value = t.toFixed(2);
    scrub.style.setProperty("--fill", (t / SECONDS * 100) + "%");
    scrub.setAttribute("aria-valuetext", MONTHS[d.getUTCMonth()] + " " + d.getUTCFullYear());
    const ph = ms >= H ? 2 : (d.getUTCFullYear() >= 1963 ? 1 : 0);
    phases.forEach(function (li, i) { li.classList.toggle("is-on", i === ph); });
  }

  function showEvent(e) {
    const apply = function () {
      kind.textContent = e.kind;
      quote.textContent = e.text;
      quote.classList.toggle("is-narration", !e.quote);
      when.textContent = e.when;
      quote.style.opacity = 1;
    };
    if (RH.reduced.matches) { apply(); return; }
    quote.style.opacity = 0;
    setTimeout(apply, 180);
  }

  function loop() {
    render(video.currentTime);
    if (!video.paused && !video.ended) raf = requestAnimationFrame(loop);
  }
  function setPaused(p) {
    pauseBtn.textContent = p ? "Play" : "Pause";
    pauseBtn.setAttribute("aria-pressed", p ? "true" : "false");
  }
  function play() {
    const pr = video.play();
    if (pr && pr.catch) pr.catch(function () { if (video.paused) { bigPlay.hidden = false; setPaused(true); } });
  }

  video.addEventListener("play", function () { bigPlay.hidden = true; setPaused(false); cancelAnimationFrame(raf); raf = requestAnimationFrame(loop); });
  video.addEventListener("pause", function () { setPaused(true); render(video.currentTime); });
  video.addEventListener("ended", function () { setPaused(true); render(SECONDS); });
  video.addEventListener("seeked", function () { render(video.currentTime); });
  video.addEventListener("error", disable, true);
  const src = video.querySelector("source");
  if (src) src.addEventListener("error", disable);
  function disable() { // no playable video: keep the poster and the final ledger
    bigPlay.hidden = true; scrub.disabled = true; pauseBtn.disabled = true; againBtn.disabled = true;
    document.querySelector(".replay-controls").classList.add("is-off");
  }

  scrub.addEventListener("input", function () { video.currentTime = +scrub.value; render(+scrub.value); });
  pauseBtn.addEventListener("click", function () { if (video.paused || video.ended) { if (video.ended) video.currentTime = 0; play(); } else video.pause(); });
  againBtn.addEventListener("click", function () { video.currentTime = 0; render(0); play(); });
  bigPlay.addEventListener("click", play);
  phases.forEach(function (li) {
    li.tabIndex = 0;
    li.setAttribute("role", "button");
    const go = function () { const t = +li.dataset.t; video.currentTime = t; render(t); };
    li.addEventListener("click", go);
    li.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
  });

  /* enlarge: the same timelapse at full size in a modal dialog; time carries over both ways */
  const dlg = $("viewer"), big = $("viewer-video"), enlargeBtn = $("hero-enlarge");
  let opener = null, wasPlaying = false;
  function openViewer(from) {
    if (!dlg || typeof dlg.showModal !== "function") { window.location.href = video.currentSrc || "assets/video/arena2-timelapse-dark.mp4"; return; }
    opener = from || enlargeBtn;
    wasPlaying = !video.paused && !video.ended;
    video.pause();
    if (!big.getAttribute("src")) { big.preload = "auto"; big.setAttribute("src", video.currentSrc || video.querySelector("source").getAttribute("src")); big.load(); }
    const t = video.currentTime;
    const seek = function () { try { big.currentTime = t; } catch (e) { /* not ready */ } };
    if (big.readyState >= 1) seek(); else big.addEventListener("loadedmetadata", seek, { once: true });
    dlg.showModal();
    $("viewer-close").focus();
    if (wasPlaying && !RH.reduced.matches) { const pr = big.play(); if (pr && pr.catch) pr.catch(function () {}); }
  }
  if (dlg) {
    enlargeBtn.addEventListener("click", function () { openViewer(enlargeBtn); });
    video.addEventListener("click", function () { openViewer(video); });
    video.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openViewer(video); } });
    $("viewer-close").addEventListener("click", function () { dlg.close(); });
    dlg.addEventListener("click", function (e) { if (e.target === dlg) dlg.close(); }); // backdrop
    dlg.addEventListener("close", function () {
      const resume = !big.paused && !big.ended;
      big.pause();
      if (big.readyState >= 1) { video.currentTime = big.currentTime; render(big.currentTime); }
      if (resume) play();
      if (opener) opener.focus();
    });
  }

  RH.json("arena2.json").then(function (d) {
    const res = {};
    d.results.forEach(function (r) { res[r.player] = r; });
    const opus = d.curves["claude-opus-5-5"];
    H = RH.day(res["claude-opus-5-5"].reached);           // the horizon, 1970-01-29
    S = H - 3650 * RH.DAY;                                  // horizon_days = 3650
    E = H + 365 * RH.DAY;                                   // runoff_days = 365
    curve = opus.map(function (p) { return Object.assign({ t: RH.day(p.date) }, p); });
    const last = curve[curve.length - 1];
    // the verified final value after the run-off; counts freeze at the last sample
    curve.push(Object.assign({}, last, { t: E, date: null, net_worth: res["claude-opus-5-5"].net_worth, money: res["claude-opus-5-5"].net_worth, loan: 0 }));

    const D = function (s) { return RH.day(s); };
    events = [
      { from: S, quote: true, kind: "In its own words", when: "Market research, before it spent a pound",
        text: "Test flights showed planes cover about 8.5 tiles a day, and one full 200-passenger flight of ~340 tiles earned about £28k against a £37k plane." },
      { from: D("1960-11-01"), quote: false, kind: "From the story", when: "1960 to 1961",
        text: "It borrowed the full £500,000 for large airports and airliners. Pay per passenger grows with distance, so it opened 26 routes between towns 330 to 700 tiles apart." },
      { from: D("1962-02-27"), quote: false, kind: "From the story", when: "1962",
        text: "The loan is repaid. By 1962 the airline made £7.05 million a year." },
      { from: D("1963-02-15"), quote: false, kind: "Elsewhere on the map", when: "February 1963, claude-fable-5-1",
        text: "Claude Fable 5.1's session ran out of usage, with its net worth at \u2212£279,327. Fable never came back. Its trains kept running." },
      { from: D("1964-06-01"), quote: false, kind: "From the story", when: "Through the decade",
        text: "As the fleet aged, Opus built statues in the eight busiest towns, ran one round of large advertising campaigns and added aircraft on thin routes. Six planes crashed." },
      { from: H, quote: true, kind: "In its own words", when: "At submission, its forecast for the run-off",
        text: "It is still earning about £4.2M a year, so it should finish the 365-day run-off somewhere around £44-45M." },
      { from: E, quote: false, kind: "Verified", when: "The replay, after the run-off year",
        text: "The replay said £45,758,664." }
    ];
    phases[1].dataset.t = timeOf(D("1963-01-01")).toFixed(2);
    phases[2].dataset.t = timeOf(H).toFixed(2);
    lastEvent = -1;
    // until the video plays, the panel matches the poster: the last frame, January 1971
    render(video.currentTime > 0 ? video.currentTime : SECONDS);
    lastEvent = -1;

    const auto = !RH.reduced.matches;
    if (!auto) { bigPlay.hidden = false; setPaused(true); return; }
    // play while the replay is on screen; pause when it scrolls away
    let userPaused = false;
    pauseBtn.addEventListener("click", function () { userPaused = video.paused; });
    const io = new IntersectionObserver(function (es) {
      es.forEach(function (e) {
        if (e.isIntersecting && video.paused && !video.ended && !userPaused && !(dlg && dlg.open)) play();
        else if (!e.isIntersecting && !video.paused) video.pause();
      });
    }, { threshold: 0.35 });
    io.observe(video);
  }).catch(function (err) { console.warn(err); });
})();
