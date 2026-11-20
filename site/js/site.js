/* Page behaviour: the contents rail, the map tabs, copy buttons. */
(function () {
  "use strict";
  const RH = window.RH;

  /* contents rail: mark the section in view, and which volume the reader is in */
  const links = Array.from(document.querySelectorAll(".rail .rail-list a"));
  const targets = links.map(function (a) { return document.querySelector(a.getAttribute("href")); }).filter(Boolean);
  const vols = Array.from(document.querySelectorAll(".rail-vol"));
  function onScroll() {
    const y = window.innerHeight * 0.3;
    let cur = targets[0];
    targets.forEach(function (t) { if (t.getBoundingClientRect().top <= y) cur = t; });
    links.forEach(function (a) { a.setAttribute("aria-current", a.getAttribute("href") === "#" + cur.id ? "true" : "false"); });
    const w = document.getElementById("waybill");
    const inW = w && w.getBoundingClientRect().top <= y && !(document.getElementById("run").getBoundingClientRect().top <= y);
    const inR = !inW && document.getElementById("railhead").getBoundingClientRect().top <= y && !(w.getBoundingClientRect().top <= y);
    document.body.classList.toggle("in-waybill", !!inW);
    vols.forEach(function (v) { v.classList.toggle("is-here", (v.dataset.vol === "waybill" && inW) || (v.dataset.vol === "railhead" && inR)); });
  }
  let ticking = false;
  window.addEventListener("scroll", function () { if (!ticking) { ticking = true; requestAnimationFrame(function () { ticking = false; onScroll(); }); } }, { passive: true });
  onScroll();

  /* small screens: the same contents in a disclosure in the top bar */
  const mt = document.getElementById("m-toc"), mtn = document.getElementById("m-toc-nav");
  const railNav = document.querySelector(".rail nav");
  if (mt && mtn && railNav) {
    Array.from(railNav.children).forEach(function (n) { if (!n.classList.contains("rail-title")) mtn.appendChild(n.cloneNode(true)); });
    mtn.addEventListener("click", function (e) { if (e.target.closest("a")) mt.open = false; });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && mt.open) { mt.open = false; mt.querySelector("summary").focus(); } });
  }

  /* map tabs */
  const MAPS = {
    "claude-opus-5-5": {
      rank: 1, worth: 45758664, alt: "Route map of claude-opus-5-5: 26 long air legs that cross the whole map.",
      facts: "An air network. 142 vehicles, all aircraft; 52 stations; 26 air legs. 37 minutes of wall-clock time, 209 model requests, about $15 of API-equivalent tokens.",
      story: ["Opus borrowed the full £500,000, put every pound of income into large airports and Bakewell LB-8 airliners, and repaid the loan before the end of 1961. Pay per passenger grows with distance and a town makes only so many passengers, so it opened 26 routes between towns 330 to 700 tiles apart, and nothing shorter.",
        "It also forked the world twice to test a “wait until full” loading order. Over 180 simulated days the variant earned about £384k less, because the planes sat idle at the gate. Opus threw the idea away and never touched the live company with it."],
      quote: "Building an air route also takes about 10 calls, versus dozens for rail.", cite: "claude-opus-5-5, from its test-flight notes"
    },
    "gpt-6-astra": {
      rank: 2, worth: 27935307, alt: "Route map of gpt-6-astra: long air legs and a rail feeder.",
      facts: "Air with rail feeders. 45 vehicles (43 air, 1 rail, 1 road); 34 stations; 22 air legs, 1 rail, 1 road.",
      story: ["Second place after only 298 actions, the most money per command of any player. The OpenAI models that won also went to the sky: the top five companies all had an airline at their core."],
      quote: "My strategy was to fund long-distance passenger flights early, repay the £500,000 loan, and reinvest in airports across the map. I added dedicated mail flights, an eight-wagon coal train, and a local bus service.", cite: "gpt-6-astra"
    },
    "claude-sonnet-5-5": {
      rank: 7, worth: 3930521, alt: "Route map of claude-sonnet-5-5: road and rail lines.",
      facts: "Road and rail. 112 vehicles (101 road, 11 rail); 29 stations; 6 road legs, 10 rail legs.",
      story: ["The busiest player of all, with 1,021 actions. It started with coal trucks, then moved to rail in 1964 when it found that one 240-ton coal train earned about £14k a trip. Its final report has the best confession in the dataset."],
      quote: "I forgot to start the first 6 cloned trucks and lost about 4 months of their income. A missing road piece stranded all the Fluwood trucks for about 7 months before I found it. I built one junction with the wrong track piece, and the Sludstone train circled for months.", cite: "claude-sonnet-5-5, final report"
    },
    "claude-haiku-5-5": {
      rank: 8, worth: 2757221, alt: "Route map of claude-haiku-5-5: 11 short road legs.",
      facts: "A road network. 249 vehicles, all road; 26 stations; 11 road legs.",
      story: ["Haiku read the same map and saw coal. It ran trucks from eleven mines to power stations, built every road by hand (with bridges where lakes were in the way), and paid for each new route out of profit. It bought 275 road vehicles, the biggest fleet in the arena."],
      quote: "Trucks were the only vehicle type that paid back well here. Rail, air, and water were not used.", cite: "claude-haiku-5-5"
    },
    "claude-fable-5-1": {
      rank: 6, worth: 9837139, alt: "Route map of claude-fable-5-1 in 1963: 13 rail lines and 2 air legs.",
      facts: "The network that ran itself. On 1963-02-15: 35 vehicles (29 rail, 6 air); 32 stations; 13 rail legs, 2 air legs.",
      story: ["Fable played like an engineer: 13 rail lines, 56 locomotives and wagons, two long air routes, 566 moves. Then, in February 1963, its Claude session ran out of usage, 79 minutes and $35.58 of API-equivalent tokens in. Its net worth at that moment was −£279,327.",
        "For seven more game years and the run-off nobody ran the company. The trains kept running. Sixth place, ahead of Sonnet and Haiku, who stayed for all ten years."],
      quote: "The environment does not reward being present. It rewards building something that can run without you.", cite: "The run-off rule, working as designed", narration: true
    }
  };
  const tabs = Array.from(document.querySelectorAll("#map-tabs [role=tab]"));
  const panel = document.getElementById("map-panel");
  function showMap(i, focus) {
    tabs.forEach(function (t, j) { t.setAttribute("aria-selected", i === j ? "true" : "false"); t.tabIndex = i === j ? 0 : -1; });
    if (focus) tabs[i].focus();
    const id = tabs[i].dataset.m, m = MAPS[id];
    panel.setAttribute("aria-labelledby", tabs[i].id);
    const img = document.getElementById("map-img");
    img.src = "img/map-" + id + ".webp"; img.alt = m.alt;
    document.getElementById("map-model").textContent = id;
    document.getElementById("map-worth").textContent = RH.gbp(m.worth);
    document.getElementById("map-rank").textContent = RH.ordinal(m.rank) + " of 11, net worth after year 11";
    document.getElementById("map-facts").textContent = m.facts;
    const st = document.getElementById("map-story"); st.replaceChildren();
    m.story.forEach(function (p) { st.appendChild(RH.el("p", null, p)); });
    const q = document.getElementById("map-quote"); q.replaceChildren();
    q.appendChild(document.createTextNode(m.narration ? m.quote : "“" + m.quote + "”"));
    q.appendChild(RH.el("cite", null, m.cite));
    q.style.fontStyle = m.narration ? "normal" : "";
  }
  tabs.forEach(function (t, i) {
    t.addEventListener("click", function () { showMap(i); });
    t.addEventListener("keydown", function (e) {
      if (e.key === "ArrowRight") { e.preventDefault(); showMap((i + 1) % tabs.length, true); }
      if (e.key === "ArrowLeft") { e.preventDefault(); showMap((i - 1 + tabs.length) % tabs.length, true); }
    });
  });
  if (tabs.length) showMap(0);
  // warm the other maps once the section is near
  const mapsSec = document.getElementById("maps");
  if (mapsSec && "IntersectionObserver" in window) {
    const io = new IntersectionObserver(function (es) {
      if (es.some(function (e) { return e.isIntersecting; })) {
        Object.keys(MAPS).forEach(function (id) { const im = new Image(); im.src = "img/map-" + id + ".webp"; });
        io.disconnect();
      }
    }, { rootMargin: "400px" });
    io.observe(mapsSec);
  }

  /* copy buttons */
  document.querySelectorAll(".copy").forEach(function (b) {
    b.addEventListener("click", function () {
      const src = b.dataset.copyFrom ? document.getElementById(b.dataset.copyFrom).textContent : b.dataset.copy;
      const done = function () { const t = b.textContent; b.textContent = "Copied"; b.classList.add("done"); setTimeout(function () { b.textContent = t === "Copied" ? "Copy" : t; b.classList.remove("done"); }, 1600); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(src).then(done, function () { fallback(src); done(); });
      else { fallback(src); done(); }
    });
  });
  function fallback(text) {
    const ta = document.createElement("textarea"); ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "absolute"; ta.style.left = "-9999px";
    document.body.appendChild(ta); ta.select(); try { document.execCommand("copy"); } catch (e) { /* ignore */ } ta.remove();
  }
})();
