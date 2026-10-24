/* GreenTrace interface: check your plots, their results and evidence files, the agents' live trace, reliability tests.
Plain JS, no build step, no network but this server.

Everything shown comes from the server: /ui/setup, /api/plot-map, POST /api/check-plots and /ui/check-plots/...,
the trace lines (GET /runs/{id}/events), the run's output, /ui/runs and /ui/bench/{case}. Text from a run is always set with textContent, never parsed
as HTML: the reports a run reads can hold anything (trap case 19 hides an instruction in an NGO text). */
(() => {
  "use strict";

  // What each claim part means: in plain words first, then in app/rules.py's words (shown under "Technical
  // details", with the rule numbers). The run's rules_version is printed at the foot of its evidence file.
  const PARTS = {
    coverage: "Every tonne is traced back to a farm",
    no_deforestation: "No forest cut after 2020 on a farm that sells to the company",
    plausibility: "The farms could really grow that much cocoa",
    scope: "The company's own report promises the same",
  };
  const PARTS_EXACT = {
    coverage: "Every tonne of the year is traced to a plot",
    no_deforestation: "No forest lost after 31 December 2020 on a plot that delivers",
    plausibility: "The declared plots could have produced the volume",
    scope: "The report promises what the claim says",
  };
  // The five agents of app/agents, and what each one does, in a sentence.
  const AGENTS = [
    ["Extractor", "Reads the promise and the company's reports"],
    ["Planner", "Decides what to check"],
    ["Investigator", "Pulls satellite alerts, maps and deliveries"],
    ["Evaluator", "Tries to break every finding"],
    ["Writer", "Applies the fixed rules and writes the evidence file"],
  ];
  const RULES = {
    1: "a source did not answer, or a datum is invalid",
    2: "direct evidence that held, after the cut-off",
    3: "a gap or a doubt the data cannot settle",
    4: "nothing found against it",
  };
  const PACE_MS = 70; // reading pace for lines that arrive all at once (sample mode runs in well under a second)
  const MAP_W = 960; // the map's SVG frame, 3:2 like the window the server cuts
  const MAP_H = 640;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const state = { setup: null, run: null, filter: "all", maps: {}, mapPlot: null, bench: null, geo: null,
    // the judge's plot: corners drawn on the map (editable), or an uploaded GeoJSON that falls outside the frame
    draw: { active: false, points: [], feature: null, source: null, measure: null, problem: null, timer: null, asked: 0 },
    lens: {},  // the Satellite Lens result of each scene, kept for this page
    batch: null, plotId: null,  // the plot check on screen: { id, output, view, details, meta }, and its plot
    where: null,  // the farm and alert behind the verdict of the evidence file on screen
    tour: null };  // the guided tour, while it is open: { step, runId }
  const $ = (id) => document.getElementById(id);
  const SVG = "http://www.w3.org/2000/svg";

  // ---- small helpers ---------------------------------------------------------------------------

  function h(tag, attrs, ...children) {
    const node = document.createElement(tag);
    return fill(node, attrs, children);
  }
  function s(tag, attrs, ...children) {
    const node = document.createElementNS(SVG, tag);
    return fill(node, attrs, children);
  }
  function fill(node, attrs, children) {
    for (const [name, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (name === "class") node.setAttribute("class", value);
      else if (name === "text") node.textContent = value;
      else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
      else node.setAttribute(name, value === true ? "" : String(value));
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  const VERDICT_WORDS = { NO_ISSUE: "NO ISSUE FOUND" };  // a plot check's verdict when no rule found anything
  const verdictTag = (verdict) => h("span", { class: `tag tag-${verdict}`, text: VERDICT_WORDS[verdict] || verdict });
  const chip = (text, kind) => h("span", { class: "chip" + (kind ? ` chip-${kind}` : ""), text });
  const words = (code) => String(code).replace(/_/g, " ");
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + "s"}`;
  const capital = (text) => (text ? text[0].toUpperCase() + text.slice(1) : "");
  const sealOf = (hash) => (hash ? `${hash.slice(0, 6)}…${hash.slice(-4)}` : "");
  const shortHash = (hash) => (hash ? `${hash.slice(0, 8)}…${hash.slice(-8)}` : "");
  const runHref = (id, view) => `#/runs/${encodeURIComponent(id)}` + (view === "file" ? "/file" : "");

  function remember(key, value) {
    try { sessionStorage.setItem(key, JSON.stringify(value)); } catch { /* storage off: only the bench label is lost */ }
  }
  function recall(key) {
    try { return JSON.parse(sessionStorage.getItem(key) || "null"); } catch { return null; }
  }

  async function getJSON(url, options) {
    const response = await fetch(url, options);
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || body);
      throw new Error(`${response.status}: ${detail}`);
    }
    return body;
  }
  const postJSON = (url, body) => getJSON(url, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}),
  });

  // ---- routing ---------------------------------------------------------------------------------
  // #/ check plots · #/check/<batch> its results · #/check/<batch>/<plot> one plot's file
  // #/runs/<id> the agents' live trace · #/runs/<id>/file their evidence file · #/files · #/bench

  const VIEWS = ["home", "check", "plot", "live", "file", "files", "bench", "missing"];
  const TAB_OF = { home: "check", check: "check", plot: "check", live: "check", file: "files", files: "files", bench: "bench" };
  const checkHref = (batchId, plotId) => `#/check/${encodeURIComponent(batchId)}` + (plotId ? `/${encodeURIComponent(plotId)}` : "");

  function currentView() {
    return VIEWS.find((name) => !$(`view-${name}`).hidden) || "home";
  }

  function show(view) {
    for (const name of VIEWS) $(`view-${name}`).hidden = name !== view;
    const run = state.run;
    for (const tab of ["check", "files", "bench"]) {
      const link = $(`tab-${tab}`);
      if (TAB_OF[view] === tab) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
    }
    // "Evidence files" opens the evidence file of the run in hand, or the list when there is none yet.
    $("tab-files").href = run && !run.error && !(run.output && run.output.status !== "success") ? runHref(run.id, "file") : "#/files";
    if (run) {
      for (const link of document.querySelectorAll(".seg-live")) link.href = runHref(run.id, "live");
      for (const link of document.querySelectorAll(".seg-file")) link.href = runHref(run.id, "file");
      for (const link of document.querySelectorAll(".seg a")) {
        const current = link.classList.contains(view === "file" ? "seg-file" : "seg-live");
        if (current) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
      }
    }
    renderMode(view);
    if (view === "file") drawWhere();
    const verdict = run && run.output && run.output.verdict;
    document.title = {
      check: "Your plots · GreenTrace", plot: state.plotId ? `${state.plotId} · GreenTrace` : "Plot · GreenTrace",
      file: verdict ? `${capital(verdict.toLowerCase())} · Evidence file · GreenTrace` : "Evidence file · GreenTrace",
      live: "Live trace · GreenTrace", files: "Evidence files · GreenTrace", bench: "Reliability tests · GreenTrace",
    }[view] || "GreenTrace";
  }

  function route(initial) {
    const [first, id, sub] = location.hash.replace(/^#\/?/, "").split("/");
    if (first === "check" && id) {
      openBatch(decodeURIComponent(id), sub ? decodeURIComponent(sub) : null);
    } else if (first === "runs" && id) {
      openRun(decodeURIComponent(id));
      const run = state.run;
      if (sub === "file") {
        if (run.output) { renderFile(run); show("file"); } else { run.wantFile = true; show("live"); }
      } else {
        show("live");
      }
    } else if (first === "files") {
      show("files");
      loadFiles();
    } else if (first === "bench") {
      show("bench");
      renderBench();
    } else {
      show("home");
    }
    if (!initial) {
      $("main").focus({ preventScroll: true });
      window.scrollTo(0, 0);
    }
    if (state.tour) {
      const [kind, id] = location.hash.replace(/^#\/?/, "").split("/");
      const ours = kind === undefined || kind === "" || (kind === "check" && decodeURIComponent(id) === state.tour.batchId);
      if (!ours) closeTour();
      else tourRefresh(true);
    }
  }

  function renderMode(view) {
    const badge = $("mode-badge");
    const run = state.run;
    let text = "";
    if ((view === "live" || view === "file") && run && run.mode) {
      text = `${capital(run.mode)} mode` + (run.degraded ? " · degraded" : "");
      badge.title = run.degraded ? "Degraded: no model answered, the agents used their rule fallbacks" : "";
    } else if (state.setup) {
      text = state.setup.default_mode === "sample" ? "Sample mode · no network" : "Live mode";
    }
    badge.textContent = text;
    badge.hidden = !text;
  }

  // ---- check a claim ---------------------------------------------------------------------------

  async function loadSetup() {
    try {
      state.setup = await getJSON("/ui/setup");
    } catch (error) {
      dropStatus(`Could not read /ui/setup (${error.message})`, true);
      return;
    }
    const setup = state.setup;
    $("default-claim").textContent = setup.default_claim;
    const promise = recall("promise");
    if (promise) { $("claim").value = promise; $("promise").open = true; }
    if (setup.default_mode === "sample") {
      $("lens-run").textContent = "Show the code's grid";
      $("lens-intro").textContent = "Sample mode: the AI lens needs a model, so no image is sent. The code's 3 x 3 grid " +
        "of Hansen loss after 2020 is shown on the Sentinel-2 scenes; with a model, Nemotron Vision compares 2020 and today " +
        "cell by cell and the code checks each answer.";
    }
    renderMode(currentView());
    const plots = setup.map_plots.map((p) => p.id);
    $("map-switch").replaceChildren(...plots.map((id) => h("button", {
      type: "button", "aria-pressed": "false", "data-plot": id, text: id, onclick: () => loadMap(id),
    })));
    loadMap(plots[0]);
  }

  async function startRun(claimText, options, caseNo, plot, errorBox, busy) {
    const claim = String(claimText || "").trim();
    const error = errorBox || $("why-error");
    error.hidden = true;
    const buttons = busy ? [busy] : [];
    buttons.forEach((b) => b.setAttribute("aria-busy", "true"));
    try {
      const body = { input: plot ? { claim, plot } : { claim } };
      if (options) body.options = options;
      const { run_id: id } = await postJSON("/runs", body);
      remember(`run:${id}`, { claim, caseNo });
      if (state.run) closeRun(state.run);
      state.run = newRun(id, { fresh: true });
      location.hash = runHref(id, "live");
    } catch (failure) {
      error.textContent = `The run did not start: ${failure.message}`;
      error.hidden = false;
    } finally {
      buttons.forEach((b) => b.removeAttribute("aria-busy"));
    }
  }

  // ---- the map: real cells from data/reference_cells.csv.gz, drawn as they are -----------------

  async function loadMap(plotId) {
    if (state.mapPlot && state.mapPlot !== plotId && state.draw.points.length) {
      clearPlot();  // the corners belong to the other frame
      $("draw-status").textContent = "Drawing cleared: it was on the other map.";
    }
    state.mapPlot = plotId;
    for (const button of $("map-switch").querySelectorAll("button")) {
      button.setAttribute("aria-pressed", String(button.dataset.plot === plotId));
    }
    if (!state.maps[plotId]) {
      $("map").setAttribute("aria-busy", "true");
      $("map-coords").textContent = "";
      $("map-numbers").replaceChildren(...["Outline", "Year of loss · Hansen", "Lost after 2020 · on EU forest"].map((label) =>
        h("div", {}, h("dt", { text: label }), h("dd", { class: "pending", text: "…" }))));
      $("map-foot").replaceChildren(h("span", { text: `Reading the 30 m cells around ${plotId} from data/reference_cells.csv.gz…` }));
    }
    try {
      state.maps[plotId] = state.maps[plotId] || await getJSON(`/api/plot-map?plot=${encodeURIComponent(plotId)}`);
    } catch (error) {
      $("map").removeAttribute("aria-busy");
      $("map-foot").replaceChildren(h("p", { class: "error", text: `The map could not be read: ${error.message}` }));
      return;
    }
    $("map").removeAttribute("aria-busy");
    if (state.mapPlot === plotId) renderMap(state.maps[plotId]);
  }

  function renderMap(data) {
    const w = data.window;
    const g = data.grid;
    const m = data.measure;
    const x = (lon) => ((lon - w.lon_min) / (w.lon_max - w.lon_min)) * MAP_W;
    const y = (lat) => ((w.lat_max - lat) / (w.lat_max - w.lat_min)) * MAP_H;
    const half = g.cell_deg / 2;
    const kindOf = ([, , forest, year]) => year > g.cutoff_year
      ? (forest >= g.forest_from ? "loss" : "loss-other")
      : year > 0 ? "loss-old" : "forest";

    // One path per kind; cells next to each other on a row are merged into one rectangle.
    const paths = { forest: [], "loss-old": [], "loss-other": [], loss: [] };
    const rows = new Map();
    for (const cell of data.cells) {
      const key = cell[0].toFixed(6);
      if (!rows.has(key)) rows.set(key, []);
      rows.get(key).push(cell);
    }
    for (const cells of rows.values()) {
      cells.sort((a, b) => a[1] - b[1]);
      let start = 0;
      for (let i = 1; i <= cells.length; i += 1) {
        const joined = i < cells.length && kindOf(cells[i]) === kindOf(cells[start])
          && Math.abs(cells[i][1] - cells[i - 1][1] - g.cell_deg) < g.cell_deg / 10;
        if (joined) continue;
        const [lat, lonFirst] = cells[start];
        const lonLast = cells[i - 1][1];
        const x0 = x(lonFirst - half);
        const y0 = y(lat + half);
        paths[kindOf(cells[start])].push(
          `M${x0.toFixed(1)} ${y0.toFixed(1)}H${x(lonLast + half).toFixed(1)}V${y(lat - half).toFixed(1)}H${x0.toFixed(1)}Z`);
        start = i;
      }
    }

    const points = data.plot.vertices.map(([lat, lon]) => `${x(lon).toFixed(1)},${y(lat).toFixed(1)}`).join(" ");
    const label = `${data.plot.id}: outline of ${m.outline_ha.toFixed(1)} ha with ${m.forest_2020_ha.toFixed(2)} ha of EU 2020 ` +
      `forest inside, ${m.loss_after_2020_ha.toFixed(2)} ha of it lost after 2020` +
      (m.main_loss_year ? `; most tree cover lost in ${m.main_loss_year}` : "");
    const svg = s("svg", { viewBox: `0 0 ${MAP_W} ${MAP_H}`, role: "img", "aria-label": label, class: data.image ? "has-image" : null },
      s("g", { "shape-rendering": "crispEdges" },
        ["forest", "loss-old", "loss-other", "loss"].map((kind) => s("path", { class: `m-${kind}`, d: paths[kind].join("") }))),
      s("polygon", { points, class: "m-halo" }),
      s("polygon", { points, class: "m-outline" }),
      s("g", { class: "m-vertex" }, data.plot.vertices.map(([lat, lon]) => s("circle", { cx: x(lon).toFixed(1), cy: y(lat).toFixed(1), r: 5 }))),
      s("g", { id: "draw-layer" }));
    svg.addEventListener("click", addPoint);
    svg.addEventListener("keydown", drawKeys);
    state.geo = { data, x, y, svg };

    const scale = h("div", { class: "map-scale", "aria-hidden": "true" },
      h("span", { class: "map-scale-bar", style: `width: ${(100 / w.width_m) * 100}cqw` }), "100 m");
    const north = h("div", { class: "map-north", "aria-hidden": "true" },
      s("svg", { viewBox: "0 0 16 20", width: 16, height: 20 }, s("path", { d: "M8 1 L14 15 L8 12 L2 15 Z" })), "N");
    const image = data.image ? h("img", { src: data.image, alt: `Sentinel-2 image of the area around ${data.plot.id}` }) : null;
    $("map").replaceChildren($("map-switch"), ...[image, svg, scale, north].filter(Boolean));
    $("map-switch").setAttribute("aria-label", `Plot shown: ${data.plot.id}`);
    if (image) {
      image.style.opacity = String(state.imageOpacity ?? 1);
      $("map").append(h("label", { class: "map-imagery" }, "Imagery",
        h("input", { type: "range", min: 0, max: 100, value: Math.round((state.imageOpacity ?? 1) * 100), "aria-label": "Imagery opacity",
          oninput: (e) => { state.imageOpacity = e.target.value / 100; image.style.opacity = String(state.imageOpacity); } })));
    }

    $("map").classList.toggle("drawing", state.draw.active);
    svg.setAttribute("tabindex", state.draw.active ? "0" : "-1");
    $("map-title").textContent = data.place;
    $("map-coords").textContent = `${data.plot.lat.toFixed(4)}° N · ${Math.abs(data.plot.lon).toFixed(4)}° W`;
    renderNumbers(m);
    const swatch = (cls, text) => h("span", {}, h("i", { class: `swatch ${cls}` }), text);
    const forestLine = m.forest_2020_ha > 0
      ? `EU 2020 forest inside the outline: ${m.forest_2020_ha.toFixed(2)} ha.`
      : "No EU 2020 forest inside this outline: the loss drawn here does not count as deforestation after 2020 on this map.";
    $("map-foot").replaceChildren(
      h("div", { class: "legend" },
        swatch("sw-forest", "EU forest 2020"), swatch("sw-loss", "Lost after 2020, on EU forest"),
        swatch("sw-loss-other", "Lost after 2020, elsewhere"), swatch("sw-loss-old", "Lost before 2021"),
        swatch("sw-outline", "Outline")),
      h("span", { id: "map-note", text: `${forestLine} Outline: a ${data.plot.declared_area_ha.toFixed(1)} ha square around the declared point. ` +
        "Measured offline by app/checks/landcover.py from 30 m cells; not an input of the demo run." +
        (data.image ? " Imagery: Sentinel-2, contains modified Copernicus Sentinel data." : "") }));
    renderDrawing();
    renderLens();
  }

  function renderNumbers(m, whose) {
    const number = (label, value, cls) => h("div", {}, h("dt", { text: label }), h("dd", { class: cls || null, text: value }));
    if (!m.covered) {
      $("map-numbers").replaceChildren(number(whose ? `${whose} · outline` : "Outline", `${m.outline_ha.toFixed(2)} ha`),
        number("Year of loss · Hansen", "no data"), number("Lost after 2020 · on EU forest", "no data"));
      return;
    }
    $("map-numbers").replaceChildren(
      number(whose ? `${whose} · outline` : "Outline", `${m.outline_ha.toFixed(whose ? 2 : 1)} ha`),
      number("Year of loss · Hansen", m.main_loss_year ? String(m.main_loss_year) : "none"),
      number("Lost after 2020 · on EU forest", `${m.loss_after_2020_ha.toFixed(2)} ha`, m.loss_after_2020_ha > 0 ? "loss" : null));
  }

  // ---- the judge's plot: drawn on the map, or uploaded as GeoJSON ------------------------------

  const MIN_HA = 0.05;   // app/world/drawn.py: MIN_HA, MAX_HA, MAX_VERTICES (the server checks again)
  const MAX_HA = 500;
  const MAX_CORNERS = 499;

  function setDrawing(on) {
    const d = state.draw;
    if (on && d.feature) clearPlot();  // an uploaded plot outside the frame cannot be edited: start afresh
    d.active = on;
    $("plot-draw").setAttribute("aria-pressed", String(on));
    $("plot-draw").textContent = on ? "Stop drawing" : "or draw one on the map";
    $("map").classList.toggle("drawing", on);
    if (state.geo) {
      state.geo.svg.setAttribute("tabindex", on ? "0" : "-1");
      if (on) state.geo.svg.setAttribute("aria-description", "Click to add a corner; Backspace undoes the last one, Escape stops drawing");
    }
    if (on) {
      $("map").scrollIntoView({ block: "nearest", behavior: reducedMotion ? "auto" : "smooth" });
      if (state.geo) state.geo.svg.focus({ preventScroll: true });
    }
    renderDrawing();
  }

  function toLatLon(event) {
    const { svg, data } = state.geo;
    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    const at = point.matrixTransform(svg.getScreenCTM().inverse());
    const w = data.window;
    return [w.lat_max - (at.y / MAP_H) * (w.lat_max - w.lat_min), w.lon_min + (at.x / MAP_W) * (w.lon_max - w.lon_min)];
  }

  function addPoint(event) {
    const d = state.draw;
    if (!d.active || !state.geo) return;
    if (d.points.length >= MAX_CORNERS) return;
    const [lat, lon] = toLatLon(event);
    d.points.push([Number(lat.toFixed(6)), Number(lon.toFixed(6))]);
    d.source = "drawn";
    plotChanged();
  }

  function drawKeys(event) {
    if (!state.draw.active) return;
    if (event.key === "Backspace" || event.key === "Delete") { event.preventDefault(); undoPoint(); }
    if (event.key === "Escape") { event.preventDefault(); setDrawing(false); }
  }

  function undoPoint() {
    state.draw.points.pop();
    if (!state.draw.points.length) state.draw.source = null;
    plotChanged();
  }

  function clearPlot() {
    const d = state.draw;
    Object.assign(d, { points: [], feature: null, source: null, measure: null, problem: null });
    plotChanged();
  }

  function localMetres(points) {
    const lat0 = points.reduce((sum, p) => sum + p[0], 0) / points.length;
    const sx = 111320 * Math.cos((lat0 * Math.PI) / 180);  // app/world/geo.py, the same flat-earth scale
    return points.map(([lat, lon]) => [lon * sx, lat * 110574]);
  }

  function areaHa(points) {
    if (points.length < 3) return 0;
    const m = localMetres(points);
    let twice = 0;
    for (let i = 0; i < m.length; i += 1) {
      const [x1, y1] = m[i];
      const [x2, y2] = m[(i + 1) % m.length];
      twice += x1 * y2 - x2 * y1;
    }
    return Math.abs(twice) / 2 / 10000;
  }

  function crossesItself(points) {
    const m = localMetres(points);
    const n = m.length;
    const side = (a, b, c) => Math.sign((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]));
    for (let i = 0; i < n; i += 1) {
      for (let j = i + 1; j < n; j += 1) {
        if (j === i + 1 || (i === 0 && j === n - 1)) continue;  // neighbours share a corner
        const [a, b, c, e] = [m[i], m[(i + 1) % n], m[j], m[(j + 1) % n]];
        if (side(a, b, c) * side(a, b, e) < 0 && side(c, e, a) * side(c, e, b) < 0) return true;
      }
    }
    return false;
  }

  function plotFeature() {
    const d = state.draw;
    if (d.feature) return d.feature;
    if (d.points.length < 3) return null;
    const ring = d.points.map(([lat, lon]) => [lon, lat]);
    ring.push(ring[0]);
    return { type: "Feature", properties: { plot_id: state.setup ? drawnId() : "P-900", source: "drawn on the GreenTrace map" },
      geometry: { type: "Polygon", coordinates: [ring] } };
  }

  const drawnId = () => (state.geo && state.geo.data.drawn_plot_id) || "P-900";

  function plotProblem() {
    const d = state.draw;
    if (d.problem) return d.problem;
    if (d.feature) return null;
    if (d.points.length < 3) return null;
    if (crossesItself(d.points)) return "The outline crosses itself: undo the last corner or start again.";
    const area = areaHa(d.points);
    if (area < MIN_HA || area > MAX_HA) return `${area.toFixed(2)} ha: a plot is between ${MIN_HA} and ${MAX_HA} ha.`;
    return null;
  }

  function plotChanged() {
    const d = state.draw;
    d.measure = null;
    if (!d.feature) d.problem = null;
    renderDrawing();
    clearTimeout(d.timer);
    if (plotFeature() && !plotProblem()) d.timer = setTimeout(measurePlot, 300);
  }

  async function measurePlot() {
    const d = state.draw;
    const feature = plotFeature();
    if (!feature) return;
    const asked = (d.asked += 1);
    try {
      const measured = await postJSON("/api/plot-measure", { plot: feature });
      if (asked !== d.asked) return;  // the plot changed meanwhile: a newer answer is coming
      d.measure = measured;
      d.problem = null;
    } catch (error) {
      if (asked !== d.asked) return;
      d.problem = error.message.replace(/^422: (input\.plot: )?/, "");
    }
    renderDrawing();
  }

  function renderDrawing() {
    const d = state.draw;
    const feature = plotFeature();
    const problem = plotProblem();
    const hasPlot = Boolean(d.points.length || d.feature);
    $("map-tools").hidden = !(d.active || hasPlot);
    $("draw-undo").disabled = !d.points.length;
    $("draw-clear").disabled = !hasPlot;
    $("draw-check").disabled = !feature || Boolean(problem) || !d.measure;
    $("map").classList.toggle("has-plot", hasPlot);

    // the outline on the map
    if (state.geo) {
      const { x, y, data } = state.geo;
      const layer = state.geo.svg.querySelector("#draw-layer");
      const pts = d.points.map(([lat, lon]) => `${x(lon).toFixed(1)},${y(lat).toFixed(1)}`).join(" ");
      const shapes = [];
      if (d.points.length >= 3) shapes.push(s("polygon", { points: pts, class: "d-area" + (problem ? " d-bad" : "") }));
      else if (d.points.length === 2) shapes.push(s("polyline", { points: pts, class: "d-line" }));
      d.points.forEach(([lat, lon], i) => shapes.push(s("circle", { cx: x(lon).toFixed(1), cy: y(lat).toFixed(1), r: i === 0 ? 7 : 5, class: "d-corner" })));
      const geom = d.feature && d.feature.geometry;
      if (geom && geom.type === "Point") {
        const [lon, lat] = geom.coordinates;
        const w = data.window;
        if (lat >= w.lat_min && lat <= w.lat_max && lon >= w.lon_min && lon <= w.lon_max) {
          const r = (Math.sqrt((d.feature.properties.area_ha * 10000) / Math.PI) / w.width_m) * MAP_W;
          shapes.push(s("circle", { cx: x(lon).toFixed(1), cy: y(lat).toFixed(1), r: r.toFixed(1), class: "d-area" }));
        }
      }
      layer.replaceChildren(...shapes);
    }

    // what it says, in words
    let status;
    if (d.feature && problem) {
      status = "";  // the reason, below, says why the file is refused
    } else if (d.feature) {
      const g = d.feature.geometry;
      status = `Uploaded ${g.type === "Point" ? `point of ${d.feature.properties.area_ha} ha` : "polygon"}, outside this map's frame.`;
    } else if (!d.points.length) {
      status = d.active ? "Click the map to place the plot's corners, in order. Three corners or more make a plot." : "";
    } else if (d.points.length < 3) {
      status = `${plural(d.points.length, "corner")} · place at least 3.`;
    } else {
      status = `${plural(d.points.length, "corner")} · ${areaHa(d.points).toFixed(2)} ha`;
    }
    if (problem) status = d.feature || (!d.points.length && d.source === null)
      ? `File refused: ${problem}` : `${status} ${problem}`;
    else if (feature && !d.measure) status += " · measuring on the 30 m cells…";
    else if (d.measure && !d.measure.measure.covered) status += ` · ${d.measure.measure.reason}: the run will say so.`;
    $("draw-status").textContent = status;

    // the numbers under the map: the judge's plot once measured, else the reference plot
    const note = $("map-note");
    if (note && !note.dataset.reference) note.dataset.reference = note.textContent;
    if (d.measure) {
      const m = d.measure.measure;
      renderNumbers(m, d.measure.plot.plot_id);
      if (note) note.textContent = !m.covered ? `${d.measure.plot.plot_id}: ${m.reason}.`
        : `${d.measure.plot.plot_id}, ${d.source === "uploaded" ? "uploaded" : "drawn"} by you: EU 2020 forest inside ${m.forest_2020_ha.toFixed(2)} ha. ` +
          "Measured offline by app/checks/landcover.py from the same 30 m cells the run reads with Q10.";
    } else if (state.geo && !hasPlot) {
      renderNumbers(state.geo.data.measure);
      if (note) note.textContent = note.dataset.reference;
    }

  }

  function checkPlot() {
    const d = state.draw;
    const feature = plotFeature();
    if (!feature || plotProblem() || !d.measure) return;
    setDrawing(false);
    checkFile({ type: "FeatureCollection", features: [feature] }, `Plot ${feature.properties.plot_id}, drawn on the map`);
  }

  // ---- the analyst's own plot file: read here, checked by POST /api/check-plots ------------------

  function dropStatus(text, isError) {
    const box = $("drop-status");
    box.hidden = !text;
    box.textContent = text || "";
    box.classList.toggle("is-error", Boolean(isError));
  }

  async function uploadPlots(file) {
    let geojson;
    try {
      geojson = JSON.parse(await file.text());
    } catch (error) {
      dropStatus(`${file.name} is not a GeoJSON file: ${error.message}.`, true);
      return;
    }
    checkFile(geojson, file.name);
  }

  const loadExample = () => checkFileQuiet();

  async function checkFile(geojson, name, quiet) {
    const count = geojson && Array.isArray(geojson.features) ? geojson.features.length : 0;
    dropStatus(count ? `Checking ${plural(count, "plot")} on the maps…` : "Checking the file…");
    const buttons = ["plots-upload", "load-example", "draw-check"].map($);
    buttons.forEach((b) => b.setAttribute("aria-busy", "true"));
    try {
      const out = await postJSON("/api/check-plots", { geojson });
      remember(`batch:${out.batch_id}`, { name });
      state.batch = null;
      dropStatus("");
      location.hash = checkHref(out.batch_id);
      return out;
    } catch (error) {
      dropStatus(`The file was not checked. ${error.message.replace(/^422: /, "")}`, true);
      if (quiet) throw error;
      return null;
    } finally {
      buttons.forEach((b) => b.removeAttribute("aria-busy"));
    }
  }

  // ---- the results of a plot check: every plot by risk, on a map, and each plot's file ------------

  const WORD_OF = { CONTRADICTED: "Contradicted.", UNVERIFIABLE: "Unverifiable.", NO_ISSUE: "No issue found." };
  const ha = (value) => (value >= 1 ? `${value.toFixed(1)} ha` : `${value.toFixed(2)} ha`);

  async function openBatch(batchId, plotId) {
    state.plotId = plotId;
    await state.setupReady;
    if (!state.batch || state.batch.id !== batchId) {
      state.batch = { id: batchId, output: null, view: null, details: {}, meta: recall(`batch:${batchId}`) || {} };
      $("check-title").textContent = "Reading the results…";
      show(plotId ? "plot" : "check");
      try {
        const id = encodeURIComponent(batchId);
        const [output, view] = await Promise.all([getJSON(`/api/check-plots/${id}`), getJSON(`/ui/check-plots/${id}/view`)]);
        if (!state.batch || state.batch.id !== batchId) return;
        Object.assign(state.batch, { output, view });
      } catch {
        state.batch = null;
        $("missing-text").textContent = `No plot check ${batchId} in this server's memory. Checks are kept in memory only, ` +
          "the last 50: upload the file again.";
        show("missing");
        return;
      }
    }
    if (!state.batch.output) return;  // still loading: the first call draws it
    renderCheck();
    if (plotId) await renderPlot(plotId);
    if (state.plotId !== plotId) return;
    show(plotId ? "plot" : "check");
    // the maps are drawn at their real width, known once the view shows
    if (plotId) renderPlotMap(state.batch.details[plotId], state.batch.output.plots.find((r) => r.plot_id === plotId));
    else renderBatchMap();
    tourRefresh(true);
  }

  function plainSummary(out) {
    const c = out.summary.plots;
    const total = out.plots.length;
    const parts = [];
    if (c.CONTRADICTED) parts.push(`${c.CONTRADICTED} ${c.CONTRADICTED === 1 ? "shows" : "show"} forest lost after 2020`);
    if (c.UNVERIFIABLE) parts.push(`${c.UNVERIFIABLE} cannot be checked yet`);
    if (c.NO_ISSUE) parts.push(`${c.NO_ISSUE} ${c.NO_ISSUE === 1 ? "shows" : "show"} no issue`);
    let text = `Of your ${plural(total, "plot")}, ${parts.length > 1 ? parts.slice(0, -1).join(", ") + " and " + parts.at(-1) : parts[0]}.`;
    const top = out.plots[0];
    if (top && top.verdict !== "NO_ISSUE") text += ` Start with ${top.plot_id}` + (top.supplier && top.supplier !== "not given" ? `, from ${top.supplier}.` : ".");
    return text;
  }

  function lossCell(r) {
    const m = r.measure;
    if (!m || !m.covered) return h("td", { class: "num muted", text: "not measured" });
    const years = Object.keys(m.forest_loss_ha_by_year || {}).filter((y) => Number(y) > 2020);
    return h("td", { class: "num" + (m.loss_after_2020_ha > 0 ? " loss" : "") },
      ha(m.loss_after_2020_ha), years.length ? h("span", { class: "sub", text: years.length > 1 ? `${years[0]}–${years.at(-1)}` : years[0] }) : null);
  }

  function renderCheck() {
    const b = state.batch;
    const out = b.output;
    const total = out.plots.length;
    const sample = state.setup && state.setup.default_mode === "sample";
    $("check-title").textContent = b.meta.name || `Plot check ${b.id}`;
    $("check-sub").textContent = [`${plural(total, "plot")} checked`, out.summary.refused && `${out.summary.refused} refused`,
      `counted as producing up to ${out.producing_until}`,
      sample ? "sample mode: maps of the Soubré area only, no network" : "live mode"].filter(Boolean).join(" · ");
    $("export-geojson").href = out.exports["plots_no_issue_found.geojson"];
    $("export-csv").href = out.exports["findings.csv"];
    $("export-package").href = out.exports.package;
    $("export-geojson").hidden = !out.summary.plots.NO_ISSUE;
    $("check-plain").textContent = plainSummary(out);
    $("check-counts").replaceChildren(...["CONTRADICTED", "UNVERIFIABLE", "NO_ISSUE"].map((v) =>
      h("div", { "data-verdict": v }, h("dt", {}, verdictTag(v)), h("dd", { text: String(out.summary.plots[v]) }))));

    const notes = [...out.errors.map((e) => `Feature ${e.index}${e.id ? ` (${e.id})` : ""} was not checked: ${e.error}`), ...out.warnings];
    $("check-refused").hidden = !notes.length;
    $("check-refused").replaceChildren(h("b", { text: out.errors.length ? "Not everything in the file could be read" : "Notes on the file" }),
      h("ul", {}, notes.map((n) => h("li", { text: n }))));

    $("plots-rows").replaceChildren(...out.plots.map((r) => {
      const href = checkHref(b.id, r.plot_id);
      return h("tr", { "data-plot": r.plot_id, "data-verdict": r.verdict, onclick: (e) => { if (!e.target.closest("a")) location.hash = href; },
        onmouseenter: () => highlight(r.plot_id, true), onmouseleave: () => highlight(r.plot_id, false) },
      h("td", { class: "num muted", text: String(r.risk_rank) }),
      h("td", {}, h("a", { href, class: "plot-link", text: r.plot_id, onfocus: () => highlight(r.plot_id, true), onblur: () => highlight(r.plot_id, false) }),
        h("span", { class: "sub", text: r.supplier })),
      h("td", {}, verdictTag(r.verdict)),
      h("td", { class: "plain-cell", text: b.view.plain[r.plot_id] || r.sentence }),
      h("td", { class: "num", text: r.area_ha === null ? "no area" : ha(r.area_ha) }),
      lossCell(r));
    }));

    $("supplier-list").replaceChildren(...out.suppliers.map((s) => h("li", { title: s.why },
      h("span", { class: "supplier-rank", text: String(s.rank) }),
      h("span", { class: "supplier-who" }, h("b", { text: s.supplier }),
        h("span", { class: "sub", text: `${plural(s.plots, "plot")}` + (s.CONTRADICTED ? ` · ${ha(s.forest_lost_after_2020_ha)} of 2020 forest lost` : "") })),
      h("span", { class: "supplier-tags" }, ["CONTRADICTED", "UNVERIFIABLE", "NO_ISSUE"].filter((v) => s[v]).map((v) =>
        h("span", { class: `tag tag-${v}`, title: `${s[v]} ${VERDICT_WORDS[v] || v}`, text: `${s[v]} ${v === "NO_ISSUE" ? "clear" : v.slice(0, 5).toLowerCase() + "."}` }))))));

    const sources = (out.plots.find((r) => r.evidence) || {}).evidence;
    const fact = (label, value) => h("div", {}, h("dt", { text: label }), h("dd", { class: "mono", text: value }));
    $("check-tech-body").replaceChildren(
      h("dl", { class: "prints" }, fact("seal", out.seal), fact("rules_version", out.rules_version), fact("batch", out.batch_id),
        fact("trace", out.log_file), fact("maps", sources ? sources.maps : "no plot was measured")),
      h("p", { class: "small" }, "Receipt: ", h("a", { href: out.exports.receipt, download: `${out.batch_id}_receipt.json`, text: "receipt.json" }),
        ". ", h("span", { class: "mono", text: "python verify.py receipt.json" }), " re-derives every verdict offline from the same cells."));
    renderBatchMap();
  }

  function highlight(plotId, on) {
    const shape = $("batch-map").querySelector(`[data-plot="${CSS.escape(plotId)}"]`);
    if (shape) shape.classList.toggle("hl", on);
    const row = $("plots-rows").querySelector(`tr[data-plot="${CSS.escape(plotId)}"]`);
    if (row) row.classList.toggle("hl", on);
  }

  function projector(points, W, H, pad) {
    // A flat projection of the points' box onto W x H, north up, same scale both ways (app/world/geo.py).
    const lats = points.map((p) => p[0]);
    const lons = points.map((p) => p[1]);
    const lat0 = (Math.min(...lats) + Math.max(...lats)) / 2;
    const lon0 = (Math.min(...lons) + Math.max(...lons)) / 2;
    const sx = 111320 * Math.cos((lat0 * Math.PI) / 180);
    const wide = Math.max(200, (Math.max(...lons) - Math.min(...lons)) * sx);
    const tall = Math.max(200, (Math.max(...lats) - Math.min(...lats)) * 110574);
    const k = Math.min((W - 2 * pad) / wide, (H - 2 * pad) / tall);  // pixels per metre
    return { k, x: (lon) => W / 2 + (lon - lon0) * sx * k, y: (lat) => H / 2 - (lat - lat0) * 110574 * k };
  }

  function scaleBar(k, H) {
    const metres = [50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000, 200000].find((m) => m * k >= 60) || 200000;
    const label = metres >= 1000 ? `${metres / 1000} km` : `${metres} m`;
    return s("g", { class: "w-scale" }, s("path", { d: `M16 ${H - 20}V${H - 14}H${16 + metres * k}V${H - 20}` }),
      s("text", { x: 24 + metres * k, y: H - 14, text: label }));
  }

  function renderBatchMap() {
    const b = state.batch;
    const box = $("batch-map");
    const W = Math.max(280, Math.round(box.clientWidth) || 560);
    const H = Math.round(W * 0.75);
    // The map frames the main group of plots; a plot more than FAR_KM from their median is named, not drawn.
    const FAR_KM = 50;
    const median = (values) => [...values].sort((a, c) => a - c)[Math.floor(values.length / 2)];
    const mid = [median(b.view.shapes.map((sh) => sh.lat)), median(b.view.shapes.map((sh) => sh.lon))];
    const km = (sh) => Math.hypot((sh.lat - mid[0]) * 110.574, (sh.lon - mid[1]) * 111.32 * Math.cos((mid[0] * Math.PI) / 180));
    const shapes = b.view.shapes.filter((sh) => km(sh) <= FAR_KM);
    const far = b.view.shapes.filter((sh) => km(sh) > FAR_KM);
    const points = shapes.flatMap((sh) => (sh.rings ? sh.rings.flat() : [[sh.lat, sh.lon]]));
    if (!points.length) { box.replaceChildren(); return; }
    const p = projector(points, W, H, 40);
    const marks = shapes.map((sh) => {
      const go = () => { location.hash = checkHref(b.id, sh.plot_id); };
      const attrs = { class: `bm bm-${sh.verdict}`, "data-plot": sh.plot_id, onclick: go,
        onmouseenter: () => highlight(sh.plot_id, true), onmouseleave: () => highlight(sh.plot_id, false) };
      const rings = sh.rings || [];
      const size = rings.length
        ? Math.max(...rings.flat().map(([lat]) => p.y(lat))) - Math.min(...rings.flat().map(([lat]) => p.y(lat)))
        : 2 * (sh.radius_m || 0) * p.k;
      const title = s("title", { text: `${sh.plot_id}: ${VERDICT_WORDS[sh.verdict] || sh.verdict}` });
      if (size < 12) return s("circle", { ...attrs, cx: p.x(sh.lon).toFixed(1), cy: p.y(sh.lat).toFixed(1), r: 7 }, title);
      if (!rings.length) return s("circle", { ...attrs, cx: p.x(sh.lon).toFixed(1), cy: p.y(sh.lat).toFixed(1), r: (sh.radius_m * p.k).toFixed(1), "stroke-dasharray": "6 4" }, title);
      return s("g", attrs, title, rings.map((ring) => s("polygon", { points: ring.map(([lat, lon]) => `${p.x(lon).toFixed(1)},${p.y(lat).toFixed(1)}`).join(" ") })));
    });
    const placed = [];  // a label is written only where it does not cover another one
    const labels = shapes.flatMap((sh) => {
      const width = sh.plot_id.length * 7.5;
      const right = p.x(sh.lon) + 12 + width <= W - 4;  // right of the mark, or left of it at the frame's edge
      const x = right ? p.x(sh.lon) + 12 : p.x(sh.lon) - 12 - width;
      const y = p.y(sh.lat) + 4;
      if (x < 4 || placed.some((q) => Math.abs(q.y - y) < 16 && x < q.x + q.width && q.x < x + width)) return [];
      placed.push({ x, y, width });
      return [s("text", { class: "w-label", x: x.toFixed(1), y: y.toFixed(1), text: sh.plot_id })];
    });
    const said = `Map of ${plural(shapes.length, "plot")}, coloured by verdict: ` +
      ["CONTRADICTED", "UNVERIFIABLE", "NO_ISSUE"].map((v) => `${shapes.filter((sh) => sh.verdict === v).length} ${VERDICT_WORDS[v] || v}`).join(", ");
    box.replaceChildren(s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": said }, marks, labels, scaleBar(p.k, H)));
    const spanKm = (Math.max(W, H) / p.k) / 1000;
    $("batch-map-sub").textContent = `Outlines from your file · north up · about ${spanKm < 10 ? spanKm.toFixed(1) : Math.round(spanKm)} km across` +
      (far.length ? ` · not drawn, more than ${FAR_KM} km away: ${far.map((sh) => sh.plot_id).join(", ")}` : "");
  }

  function plotFeatureOf(detail, result) {
    const properties = { plot_id: detail.plot_id, supplier: result.supplier };
    if (detail.rings) {
      return { type: "Feature", properties, geometry: detail.rings.length === 1
        ? { type: "Polygon", coordinates: [detail.rings[0].map(([lat, lon]) => [lon, lat])] }
        : { type: "MultiPolygon", coordinates: detail.rings.map((ring) => [ring.map(([lat, lon]) => [lon, lat])]) } };
    }
    return { type: "Feature", properties: { ...properties, area_ha: result.area_ha }, geometry: { type: "Point", coordinates: [detail.lon, detail.lat] } };
  }

  async function renderPlot(plotId) {
    const b = state.batch;
    const out = b.output;
    const index = out.plots.findIndex((r) => r.plot_id === plotId);
    if (index < 0) {
      $("missing-text").textContent = `No plot ${plotId} in this check.`;
      show("missing");
      return;
    }
    const r = out.plots[index];
    let detail = b.details[plotId];
    if (!detail) {
      try {
        detail = b.details[plotId] = await getJSON(`/ui/check-plots/${encodeURIComponent(b.id)}/${encodeURIComponent(plotId)}`);
      } catch (error) {
        $("missing-text").textContent = `This plot could not be read: ${error.message}`;
        show("missing");
        return;
      }
    }
    if (state.plotId !== plotId) return;
    const prev = out.plots[index - 1];
    const next = out.plots[index + 1];
    $("plot-back").href = checkHref(b.id);
    $("plot-prev").href = prev ? checkHref(b.id, prev.plot_id) : "#";
    $("plot-next").href = next ? checkHref(b.id, next.plot_id) : "#";
    $("plot-prev").hidden = !prev;
    $("plot-next").hidden = !next;
    $("plot-title").textContent = `${r.plot_id}` + (r.supplier && r.supplier !== "not given" ? ` · ${r.supplier}` : "");
    $("plot-sub").textContent = [`risk ${r.risk_rank} of ${out.plots.length}`, r.area_ha === null ? "no area given" : `${r.area_ha} ha`,
      r.shape, b.meta.name].filter(Boolean).join(" · ");
    for (const id of ["plot-verdict-label", "plot-verdict-word"]) $(id).className = `${id === "plot-verdict-word" ? "verdict-word" : "verdict-label"} v-${r.verdict}`;
    $("plot-verdict-word").textContent = WORD_OF[r.verdict] || r.verdict;
    $("plot-plain").textContent = detail.plain;
    $("plot-rules-words").textContent = `The rules' own words: ${r.sentence}`;
    $("plot-receipt").href = out.exports.receipt;
    $("plot-receipt").setAttribute("download", `${out.batch_id}_receipt.json`);
    $("plot-package").href = out.exports.package;
    renderPlotMap(detail, r);
    const m = r.measure;
    $("plot-timeline").replaceChildren(...(m ? [timelineCard({ plot_id: r.plot_id, reference_maps: m, delivered_tonnes_by_year: {},
      producing_until: out.producing_until }, "2020-12-31")] : []));
    const fact = (label, value) => h("div", {}, h("dt", { text: label }), h("dd", { class: "mono", text: value }));
    const own = Object.entries(r.properties || {});
    $("plot-tech-sub").textContent = `rule ${r.rule} · ${r.code || "no finding"} · ${r.evidence ? `Q10 answer ${r.evidence.answer}` : "no map query"}`;
    $("plot-tech-body").replaceChildren(
      h("dl", { class: "prints" }, fact("rule", `${r.rule}: ${RULES[r.rule] || ""}`), fact("code", r.code || "none"),
        fact("query", r.evidence ? `${r.evidence.query}, answer ${r.evidence.answer} of the receipt` : "none"),
        fact("cells", m ? String(m.cells) : "none"), fact("maps", r.evidence ? r.evidence.maps : "none"),
        fact("tree cover lost by year", m && Object.keys(m.loss_ha_by_year || {}).length
          ? Object.entries(m.loss_ha_by_year).map(([y, v]) => `${y}: ${v}`).join(" · ") : "none"),
        fact("seal of the check", out.seal)),
      own.length ? h("div", {}, h("span", { class: "label", text: "Your properties, kept as given" }),
        h("dl", { class: "prints" }, own.map(([k, v]) => fact(k, typeof v === "string" ? v : JSON.stringify(v))))) : null);
    const claim = $("claim").value.trim() || state.setup.default_claim;
    $("why-claim").textContent = `The promise they check: “${claim}”` + ($("claim").value.trim() ? "" : " (yours can be pasted on the first page)");
    $("why-note").textContent = state.setup.default_mode === "sample"
      ? "Sample mode: the agents check this plot as one more plot of the example supply chain (synthetic deliveries), with the rules alone."
      : "Live mode: the agents read the company's data through Zetaris and ask Nemotron.";
    $("why-error").hidden = true;
    $("why-run").onclick = () => {
      remember("promise", $("claim").value.trim());
      startRun(claim, state.setup.default_mode === "sample" ? { case: state.setup.drawn_plot_world } : null, null,
        plotFeatureOf(detail, r), $("why-error"), $("why-run"));
    };
  }

  function renderPlotMap(detail, r) {
    const box = $("plot-map");
    const W = Math.max(280, Math.round(box.clientWidth) || 640);
    const w = detail.window;
    const sx = 111320 * Math.cos((((w.lat_min + w.lat_max) / 2) * Math.PI) / 180);
    const H = Math.round(Math.min(W * 0.6, (W * ((w.lat_max - w.lat_min) * 110574)) / ((w.lon_max - w.lon_min) * sx)));
    const p = projector([[w.lat_min, w.lon_min], [w.lat_max, w.lon_max]], W, H, 0);
    const g = detail.grid;
    const half = g.cell_deg / 2;
    const kindOf = ([, , forest, year]) => year > g.cutoff_year ? (forest >= g.forest_from ? "loss" : "loss-other")
      : year > 0 ? "loss-old" : forest >= g.forest_from ? "forest" : null;
    const paths = { forest: [], "loss-old": [], "loss-other": [], loss: [] };
    for (const cell of detail.cells) {
      const kind = kindOf(cell);
      if (!kind) continue;
      const [lat, lon] = cell;
      const x0 = p.x(lon - half);
      const y0 = p.y(lat + half);
      paths[kind].push(`M${x0.toFixed(1)} ${y0.toFixed(1)}H${p.x(lon + half).toFixed(1)}V${p.y(lat - half).toFixed(1)}H${x0.toFixed(1)}Z`);
    }
    const outline = detail.rings
      ? detail.rings.map((ring) => {
        const pts = ring.map(([lat, lon]) => `${p.x(lon).toFixed(1)},${p.y(lat).toFixed(1)}`).join(" ");
        return [s("polygon", { points: pts, class: "m-halo" }), s("polygon", { points: pts, class: "m-outline" })];
      }).flat()
      : detail.radius_m ? [s("circle", { cx: p.x(detail.lon), cy: p.y(detail.lat), r: (detail.radius_m * p.k).toFixed(1), class: "w-disk" })]
        : [s("circle", { cx: p.x(detail.lon), cy: p.y(detail.lat), r: 5, class: "w-point" })];
    const m = r.measure;
    const said = `${r.plot_id}: ` + (m && m.covered ? `${ha(m.forest_2020_ha)} of EU 2020 forest inside, ${ha(m.loss_after_2020_ha)} of it lost after 2020`
      : "no map cells measured here");
    box.replaceChildren(s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": said },
      s("g", { "shape-rendering": "crispEdges" }, ["forest", "loss-old", "loss-other", "loss"].map((kind) => s("path", { class: `m-${kind}`, d: paths[kind].join("") }))),
      outline, scaleBar(p.k, H)));
    const swatch = (cls, text) => h("span", {}, h("i", { class: `swatch ${cls}` }), text);
    $("plot-map-legend").replaceChildren(swatch("sw-forest", "EU forest 2020"), swatch("sw-loss", "Lost after 2020, on EU forest"),
      swatch("sw-loss-other", "Lost after 2020, elsewhere"), swatch("sw-loss-old", "Lost before 2021"), swatch("sw-outline", "Your outline"));
    $("plot-map-sub").textContent = m && m.covered
      ? `${ha(m.forest_2020_ha)} of EU 2020 forest inside · ${ha(m.loss_after_2020_ha)} of it lost after 2020`
      : (m && m.reason) || "not measured";
    $("plot-map-note").textContent = detail.cells.length
      ? `${plural(detail.cells.length, "cell")} of 30 m, as the check read them (query Q10): EU JRC GFC2020 v4 for the forest in 2020, Hansen/UMD v1.12 for the year of loss. North is up.`
      : `No map cell was read here${m && m.reason ? `: ${m.reason}` : ""}. Only your outline is drawn.`;
  }

  // ---- the Satellite Lens: the model looks at the scene, the code checks each cell ---------------

  const SAID = { forest_lost: "forest lost", forest_kept: "forest kept", no_forest_in_2020: "no forest in 2020",
    cannot_see: "cannot see" };

  function lensStatus(text) {
    $("lens-status").hidden = !text;
    $("lens-status").textContent = text || "";
  }

  async function runLens() {
    const button = $("lens-run");
    const d = state.draw;
    // An uploaded plot outside the frames is asked about by its own place; otherwise the scene on screen.
    const ask = d.feature && d.measure ? { lat: d.measure.plot.lat, lon: d.measure.plot.lon } : { plot: state.mapPlot };
    const sample = state.setup && state.setup.default_mode === "sample";
    button.setAttribute("aria-busy", "true");
    lensStatus(sample ? "Reading the Hansen cells under the grid…" : "Sending the scene to Nemotron Vision…");
    try {
      let job = await postJSON("/api/lens", ask);
      if (job.status === "no_scene") {
        lensStatus(`${capital(job.message)}: no Sentinel-2 frame covers this plot.`);
        return;
      }
      const started = Date.now();
      while (job.status === "running" || job.status === "queued") {
        const waited = Math.round((Date.now() - started) / 1000);
        if (!sample) lensStatus(job.status === "queued" ? `Waiting for the lens already running · ${waited} s (one call at a time).`
          : `Nemotron Vision is comparing 2020 and today · ${waited} s (90 s at most, then one retry).`);
        await new Promise((resolve) => setTimeout(resolve, sample ? 250 : 1000));
        job = await getJSON(`/api/lens/${encodeURIComponent(job.key)}`);
      }
      if (job.status === "failed") {
        lensStatus(`The lens stopped: ${job.error}`);
        return;
      }
      state.lens[job.result.plot_id] = job.result;
      if (job.result.plot_id !== state.mapPlot) await loadMap(job.result.plot_id);
      lensStatus("");
      renderLens();
    } catch (error) {
      lensStatus(`The lens could not run: ${error.message}`);
    } finally {
      button.removeAttribute("aria-busy");
    }
  }

  const SHORT = { CONFIRMED: "right", REJECTED: "wrong", "MISSED BY AI": "missed", "NOT SEEN": "not seen", LOSS: "loss" };

  function lensFrame(src, alt, caption, cells, status, labelled) {
    const grid = h("div", { class: "lens-grid", "aria-hidden": "true" }, cells.map((c) => {
      const st = status(c);
      return h("div", { class: "lens-cell", "data-status": st },
        h("span", { class: "lens-id", text: c.cell }),
        labelled && st ? h("span", { class: "lens-said", text: SHORT[st] || st }) : null);
    }));
    return h("figure", { class: "lens-date" },
      h("figcaption", { class: "sub", text: caption }),
      h("div", { class: "lens-frame" },
        src ? h("img", { src, alt }) : h("p", { class: "lens-missing", text: "2020 scene not fetched yet" }), grid));
  }

  function renderLens() {
    const result = state.lens[state.mapPlot];
    for (const id of ["lens-score", "lens-table", "lens-foot", "lens-pair"]) $(id).hidden = !result;
    if (!result) return;
    const ai = result.ai;
    const lost = result.cells.filter((c) => c.lost).length;
    const status = (c) => (ai ? c.status : c.lost ? "LOSS" : null);
    const before = result.before;
    $("lens-pair").replaceChildren(
      lensFrame(result.images.before, `Sentinel-2 image of ${result.plot_id}'s frame on ${before ? before.date : ""}`,
        before ? `Before · ${before.date}` : "Before · 2019-2020 dry season", result.cells, status, false),
      lensFrame(result.images.after, `Sentinel-2 image of ${result.plot_id}'s frame on ${result.scene.date}`,
        `After · ${result.scene.date}`, result.cells, status, true),
      h("div", { class: "legend lens-legend" }, (ai
        ? [["CONFIRMED", "right: confirmed by the maps"], ["REJECTED", "wrong: no loss on the maps"],
          ["MISSED BY AI", "missed: a loss the AI did not see"], ["NOT SEEN", "not seen: counts neither way"]]
        : [["LOSS", `loss after 2020 of ${result.loss_from_ha} ha or more (Hansen)`]]).map(([st, text]) =>
        h("span", { "data-status": st }, h("i", { class: "swatch lens-swatch" }), text))));
    $("lens-score").replaceChildren(...(ai
      ? [h("b", { text: `AI right ${result.score.right} / seen ${result.score.seen} · missed ${result.score.missed} · wrong ${result.score.wrong}` }),
        h("span", { text: `${result.method === "cells" ? "one question per cell, its 2020 | today pair enlarged" : "one image of the whole grid"}` +
          `${result.fallback ? ` (${result.fallback})` : ""}; answered by ${result.model} through ${result.provider}, ` +
          `asked ${result.requested_model}; ${result.score.of - result.score.seen} cell(s) it could not see count neither way` })]
      : [h("b", { text: "Code grid" }),
        h("span", { text: `${capital(result.message)}. ${lost} of 9 cells lost ${result.loss_from_ha} ha or more after 2020 on the Hansen map.` })]));
    $("lens-rows").replaceChildren(...result.cells.map((c) => h("tr", { "data-status": status(c) },
      h("td", { text: c.cell }),
      h("td", { text: ai ? (c.label ? `${SAID[c.label]} · ${c.confidence.toFixed(2)}` : "no answer") : "not asked" }),
      h("td", { class: "mono", text: `${c.loss_after_2020_ha.toFixed(2)} ha` }),
      h("td", {}, h("span", { class: "lens-verdict", text: ai ? c.status : c.lost ? "LOSS AFTER 2020" : "no loss" })))));
    const sc = result.scene || {};
    $("lens-foot").textContent = [
      before ? `Before ${before.scene_id}` : "Before: not fetched",
      `after ${sc.scene_id || "?"}`, sc.licence,
      `a cell counts as lost from ${result.loss_from_ha} ha of Hansen loss after 2020`,
      `trace logs/${result.run_id}.jsonl`, ai ? null : "no model answer is ever replayed from a file",
    ].filter(Boolean).join(" · ");
  }

  // ---- a run: its trace lines, then its output -------------------------------------------------

  function newRun(id, { fresh }) {
    const meta = recall(`run:${id}`) || {};
    return {
      id, fresh, claim: meta.claim || "", caseNo: meta.caseNo || null,
      lines: [], pending: [], timer: null, source: null, parts: [],
      output: null, error: null, mode: null, degraded: null, changes: [], wantFile: false,
      entities: new Set(), values: {}, // what "try to fool it" can change, and the value each one has now
    };
  }

  function openRun(id) {
    if (state.run && state.run.id === id && (state.run.source || state.run.output || state.run.error)) return;
    if (state.run && state.run.id !== id) closeRun(state.run);
    if (!state.run || state.run.id !== id) state.run = newRun(id, { fresh: false });
    resetLive(state.run);
    subscribe(state.run);
  }

  function closeRun(run) {
    if (run.source) run.source.close();
    clearTimeout(run.timer);
    run.timer = null;
  }

  function subscribe(run) {
    const source = new EventSource(`/runs/${encodeURIComponent(run.id)}/events`);
    run.source = source;
    source.addEventListener("trace", (event) => { run.pending.push(event.data); pump(run); });
    source.addEventListener("result", (event) => {
      source.close();
      takeOutput(run, JSON.parse(event.data));
      pump(run);
    });
    source.addEventListener("failed", (event) => {
      source.close();
      run.error = JSON.parse(event.data).error;
      pump(run);
    });
    source.onerror = () => {
      if (source.readyState !== EventSource.CLOSED || run.output || run.error) return; // EventSource retries by itself
      fromFinishedRun(run);
    };
  }

  function takeOutput(run, output) {
    run.output = output;
    run.mode = output.mode;
    run.degraded = output.degraded;
  }

  async function fromFinishedRun(run) {
    // No live stream for this id (a bench run, a run made by POST /run, or a server restart): the output may be here.
    try {
      takeOutput(run, await getJSON(`/runs/${encodeURIComponent(run.id)}`));
      settle(run);
    } catch {
      if (run !== state.run) return;
      $("missing-text").textContent = `No run ${run.id} in this server's memory. Runs are kept in memory only, ` +
        "the last 50; their trace stays in logs/ on the server.";
      show("missing");
    }
  }

  function pump(run) {
    if (run.timer || run !== state.run) return;
    const paced = run.fresh && !reducedMotion;
    const next = () => {
      run.timer = null;
      if (run !== state.run) return;
      if (!paced) {
        while (run.pending.length) addLine(run, run.pending.shift());
      } else if (run.pending.length) {
        addLine(run, run.pending.shift());
        run.timer = setTimeout(next, PACE_MS);
        return;
      }
      if (run.output || run.error) settle(run);
    };
    next();
  }

  // ---- live trace ------------------------------------------------------------------------------

  function resetLive(run) {
    $("trace").replaceChildren();
    $("live-result").hidden = true;
    $("live-lead").hidden = false;
    $("live-error").hidden = true;
    setTitles(run);
    setStatus("running", "Starting");
    $("live-lead").textContent = "Starting the run. Each line below is one step, as the agents write it to the trace.";
    renderLiveParts(run);
    renderStats(run);
    renderAgents(run);
    $("trace-foot").textContent = `One line per step, as written to logs/${run.id}.jsonl. Times are the trace's own` +
      (run.fresh && !reducedMotion ? "; lines that arrive together are shown at reading pace." : ".");
  }

  function setTitles(run) {
    const title = run.claim ? `“${run.claim}”` : `Run ${run.id}`;
    $("live-title").textContent = title;
    $("file-title").textContent = title;
    const bits = [`Run ${run.id}`];
    if (run.caseNo) bits.push(`bench case ${run.caseNo}`);
    if (run.mode) bits.push(run.mode === "sample" ? "sample mode, synthetic company" : `${run.mode} mode`);
    if (run.degraded) bits.push("degraded: rules only");
    $("live-sub").textContent = bits.join(" · ");
    $("file-sub").textContent = bits.join(" · ");
  }

  function setStatus(kind, text) {
    $("live-status").dataset.state = kind;
    $("live-status-text").textContent = text;
  }

  function addLine(run, raw) {
    let step;
    try {
      step = JSON.parse(raw);
    } catch {
      step = { agent_name: "?", action: "unreadable line", output_summary: raw, status: "error" };
    }
    run.lines.push(step);
    if (step.action === "receive_case") {
      const said = /^data (\w+)/.exec(step.output_summary || "");
      if (said && !run.mode) run.mode = said[1];
      if (/model chain rules$/.test(step.output_summary || "") && run.degraded === null) run.degraded = true;
      setTitles(run);
      renderMode("live");
    }
    if (step.action === "extract_claims") {
      const promise = /^promise: (.*)$/.exec(step.input_summary || "");
      if (promise && !run.claim) { run.claim = promise[1]; setTitles(run); }
      const parts = /checkable parts: ([\w, ]+)/.exec(step.output_summary || "");
      if (parts) { run.parts = parts[1].split(",").map((p) => p.trim()).filter(Boolean); renderLiveParts(run); }
    }
    $("trace").append(stepRow(step, run.lines[0]));
    renderStats(run);
    renderAgents(run);
    const elapsed = offset(step, run.lines[0]);
    setStatus("running", `Investigating · ${plural(run.lines.length, "step")}` + (elapsed === null ? "" : ` · ${clock(elapsed)}`));
    $("live-lead").textContent = progress(count(run.lines));
    if (state.tour) tourRefresh();
  }

  function renderLiveParts(run) {
    const parts = run.settled && run.output && run.output.parts;  // once every line is on screen
    const card = (name, state) => h("div", { class: "part-card" },
      h("div", { class: "part-card-head" }, h("span", { class: "part-text", text: PARTS[name] || words(name) }), state),
      h("span", { class: "part-id", text: name }));
    let cards;
    if (parts && parts.length) {
      cards = parts.map((p) => card(p.claim_part, verdictTag(p.verdict)));
    } else {
      cards = run.parts.map((name) => card(name, h("span", { class: "part-state", text: "Checking" })));
    }
    $("parts-live-title").textContent = parts ? `The claim, in ${plural(parts.length, "part")}` : "The claim, part by part";
    $("parts-live-title").hidden = !cards.length;
    $("live-parts").replaceChildren(...cards);
  }

  function offset(step, first) {
    const now = Date.parse(step.timestamp);
    const start = Date.parse(first && first.timestamp);
    return Number.isNaN(now) || Number.isNaN(start) ? null : Math.max(0, now - start);
  }

  function clock(ms) {
    const sec = ms / 1000;
    if (sec < 10) return `${sec.toFixed(3)} s`;
    if (sec < 60) return `${sec.toFixed(1)} s`;
    return `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, "0")}`;
  }

  const isWorldModel = (step) => String(step.model || "").startsWith("world-model");
  const isSponsor = (step) => ["query_zetaris", "model_call", "list_models", "provider_fallback"].includes(step.action)
    || isWorldModel(step);

  function stepRow(step, first) {
    const meta = [h("span", { class: "step-action", text: step.action })];
    if (step.target_agent) meta.push(h("span", { class: "step-arrow", text: `→ ${step.target_agent}` }));
    let text = step.output_summary || "";
    let flag = null;
    let key = false;
    let mono = false;

    if (step.action === "query_zetaris") {
      const query = /^(Q\d+)/.exec(step.input_summary || "");
      meta.push(chip(`${step.provider} · ${query ? query[1] : "query"} · ${plural(step.rows ?? 0, "row")}`, "data"));
      text = step.input_summary || text;
      mono = true;
    } else if (step.action === "model_call" || step.action === "list_models") {
      meta.push(chip(`${step.provider} · ${step.model || "no model"}`, "model"));
    } else if (isWorldModel(step)) {
      meta.push(chip(`World Model · ${step.provider} · ${step.model}`));
    } else if (step.provider) {
      meta.push(chip(step.provider));
    }

    if (step.action === "request_revision") {
      key = true;
      flag = "Sent back to the Planner";
    } else if (step.action === "revise_plan") {
      key = true;
      flag = "Plan revised";
    } else if (step.action === "refute_hypothesis") {
      const tested = /^[^:]*:\s*(R\d+)\s*'(.*)'$/.exec(step.input_summary || "");
      const result = /^(HOLDS|rejected):\s*(.*)$/.exec(step.output_summary || "");
      if (tested) meta.push(chip(tested[1]));
      if (result) {
        const holds = result[1] === "HOLDS";
        meta.push(h("span", { class: holds ? "chip chip-signal" : "chip", text: holds ? "HOLDS" : "rejected" }));
        key = holds;
        flag = holds ? "This doubt holds: the card is blocked" : null;
        text = tested ? `“${tested[2]}”: ${result[2]}` : result[2];
      }
    }
    if (step.status && step.status !== "success") meta.push(h("span", { class: "chip step-status", text: step.status }));
    if (step.retry_count) meta.push(chip(`retry ${step.retry_count}`));
    if (step.confidence !== null && step.confidence !== undefined) meta.push(chip(`confidence ${step.confidence}`));

    const since = offset(step, first);
    return h("li", {
      class: "step" + (key ? " step-key" : ""), "data-agent": step.agent_name, "data-status": step.status,
      "data-sponsor": isSponsor(step),
    },
    h("span", { class: "step-time", text: since === null ? "" : `+${clock(since)}` }),
    h("span", { class: "agent-dot", "data-agent": step.agent_name, "aria-hidden": "true" }),
    h("span", { class: "step-agent", text: step.agent_name }),
    h("div", { class: "step-body" },
      h("div", { class: "step-meta" }, meta),
      flag && h("span", { class: "step-flag", text: flag }),
      h("span", { class: "step-text" + (mono ? " mono" : ""), title: step.input_summary || null, text })));
  }

  function count(lines) {
    const c = { steps: lines.length, queries: 0, rows: 0, data: new Set(), models: 0, modelRoutes: new Set(),
      tested: 0, held: 0, sentBack: 0, memory: 0, memoryEngine: null };
    for (const step of lines) {
      if (step.action === "query_zetaris") { c.queries += 1; c.rows += step.rows || 0; c.data.add(step.provider); }
      if (step.action === "model_call") { c.models += 1; c.modelRoutes.add(step.provider); }
      if (step.action === "refute_hypothesis") { c.tested += 1; if (/^HOLDS\b/.test(step.output_summary || "")) c.held += 1; }
      if (step.action === "request_revision") c.sentBack += 1;
      if (isWorldModel(step)) { c.memoryEngine = step.model; if (step.action !== "connect_world_model") c.memory += 1; }
    }
    return c;
  }

  function progress(c) {
    let text = `${plural(c.steps, "step")} so far.`;
    if (c.sentBack) text += ` The Evaluator sent the plan back to the Planner ${c.sentBack === 1 ? "once" : c.sentBack + " times"}.`;
    if (c.tested) text += ` ${plural(c.tested, "refutation")} tested by its own queries, ${c.held} held.`;
    return text + " The rules give the verdict at the end.";
  }

  function renderStats(run) {
    const c = count(run.lines);
    const stat = (label, value, note) => h("div", {}, h("dt", { text: label }),
      h("dd", {}, String(value), note && h("span", { class: "stat-note", text: note })));
    const modelNote = c.models ? [...c.modelRoutes].join(", ") : run.degraded ? "rules only" : null;
    const memoryNote = c.memoryEngine === "world-model-fallback" ? "in-memory fallback"
      : c.memoryEngine ? "Meterless reference" : null;
    $("live-stats").replaceChildren(
      stat("Data queries", c.queries, c.data.size ? `via ${[...c.data].join(", ")}` : null),
      stat("Rows read", c.rows),
      stat("Model calls", c.models, modelNote),
      stat("Refutations tested", c.tested, c.tested ? `${c.held} held` : null),
      stat("Plan sent back", c.sentBack, "by the Evaluator"),
      stat("World Model writes", c.memory, memoryNote),
    );
  }

  function renderAgents(run) {
    const finished = Boolean(run.settled);
    const last = run.lines.length ? run.lines[run.lines.length - 1].agent_name : null;
    const c = count(run.lines);
    const did = (name, mine) => {
      const acts = (action) => mine.filter((step) => step.action === action).length;
      if (name === "Extractor" && run.parts.length) return `Found ${plural(run.parts.length, "part")} of the promise that data can check`;
      if (name === "Planner" && acts("revise_plan")) return `Planned the checks, then revised the plan when the Evaluator sent it back`;
      if (name === "Investigator" && acts("query_zetaris")) return `${plural(acts("query_zetaris"), "look-up")} in the data` + (finished ? "" : " so far");
      if (name === "Evaluator" && c.tested) return `${plural(c.tested, "attempt")} to break a finding, ${c.held} held` +
        (c.sentBack ? `; sent the plan back ${c.sentBack === 1 ? "once" : c.sentBack + " times"}` : "");
      if (name === "Writer" && acts("apply_verdict_rules")) return "Took the verdict from the fixed rules, then wrote the file";
      return mine.length ? plural(mine.length, "step") : "";
    };
    $("agent-list").replaceChildren(...AGENTS.map(([name, role]) => {
      const mine = run.lines.filter((step) => step.agent_name === name);
      const st = !mine.length ? (finished ? "idle" : "waiting") : finished ? "done" : last === name ? "working" : "paused";
      const label = { idle: "no step", waiting: "waiting", working: "working", paused: "standing by", done: "done" }[st];
      return h("li", { class: "agent", "data-state": st },
        h("span", { class: "agent-dot", "data-agent": name, "aria-hidden": "true" }),
        h("span", { class: "agent-who" }, h("b", { text: name }), h("span", { text: role })),
        h("span", { class: "agent-did", text: did(name, mine) }),
        h("span", { class: "agent-state", text: label }));
    }));
    $("trace-tech-sub").textContent = `full trace of ${plural(run.lines.length, "step")}, counters, World Model`;
  }

  async function loadPlain(run) {
    // The sentence is built on the server from the run's own findings (app/web/plain.py), after every change.
    const key = `${run.id}:${run.output && run.output.verdict_sha256}`;
    if (run.plainKey === key) return;
    run.plainKey = key;
    let text;
    try {
      text = (await getJSON(`/ui/runs/${encodeURIComponent(run.id)}/plain`)).sentence;
    } catch {
      text = run.output.summary || "";  // a run this server no longer keeps: the Writer's own summary
    }
    if (run.plainKey !== key) return;
    run.plain = text;
    if (run === state.run) {
      $("live-plain").textContent = text;
      $("file-plain").textContent = text;
      tourRefresh();
    }
  }

  function settle(run) {
    if (run !== state.run) return;
    run.settled = true;
    setTitles(run);
    if (run.error) {
      setStatus("failed", "Stopped");
      $("live-lead").textContent = "The run did not finish. Nothing below is a verdict.";
      $("live-error").textContent = `The run stopped: ${run.error}`;
      $("live-error").hidden = false;
      show(currentView());
      return;
    }
    const out = run.output;
    setStatus("done", `Finished · ${plural(run.lines.length, "step")}` +
      (typeof out.execution_time_seconds === "number" ? ` · ${out.execution_time_seconds} s` : ""));
    $("live-lead").textContent = out.summary || "";
    $("live-lead").hidden = true;
    $("live-verdict").replaceChildren(out.verdict ? verdictTag(out.verdict) : h("span", { class: "tag tag-quiet", text: "TOO VAGUE" }));
    $("live-plain").textContent = run.plain || "";
    $("live-open-file").href = runHref(run.id, "file");
    $("live-result").hidden = false;
    renderLiveParts(run);
    renderAgents(run);
    loadPlain(run);
    if (!run.lines.length) {
      $("trace").replaceChildren(h("li", { class: "trace-empty",
        text: "This run was not started from this page, so its trace was not streamed here. " +
          `It is in ${out.log_file || "logs/"} on the server.` }));
    }
    renderFile(run);
    if (run.wantFile) {
      run.wantFile = false;
      show("file");
    } else {
      show(currentView());
    }
    tourRefresh();
  }

  // ---- evidence file ---------------------------------------------------------------------------

  function renderFile(run) {
    const out = run.output;
    if (!out) return;
    const vague = out.status !== "success";
    const verdict = out.verdict || null;
    setTitles(run);

    const banner = $("file-banner");
    banner.hidden = !run.changes.length;
    if (run.changes.length) {
      banner.textContent = `This evidence file includes ${plural(run.changes.length, "change")} made with “Try to fool it”: ` +
        run.changes.map((w) => `${w.change.entity} ${w.change.field} = ${w.change.value}`).join("; ") +
        `. The run keeps ${run.changes.length === 1 ? "it" : "them"}; the seal and the audit package are for the changed evidence.`;
    }

    const label = $("verdict-label");
    label.className = "verdict-label" + (verdict ? ` v-${verdict}` : "");
    label.textContent = out.human_review ? "Verdict · human review required" : "Verdict";
    const word = $("verdict-word");
    word.className = "verdict-word" + (verdict ? ` v-${verdict}` : "");
    word.textContent = verdict ? `${capital(verdict.toLowerCase())}.` : "Too vague.";
    $("file-plain").textContent = run.plain || "";
    $("file-summary").textContent = out.summary ? `The Writer's summary: ${out.summary}` : "";
    loadPlain(run);
    const untested = out.untested_factors || [];
    $("file-untested").textContent = untested.length ? `Not tested: ${untested.join(" ")}` : "";
    $("file-untested").hidden = !untested.length;

    const tags = [h("span", { class: "tag tag-quiet", text: out.mode === "sample" ? "Sample mode · synthetic world, no network" : `${capital(out.mode)} mode` })];
    if (out.degraded) tags.push(h("span", { class: "tag tag-warn", text: "Degraded · rules only, no model answered" }));
    if (out.human_review) tags.push(h("span", { class: "tag tag-warn", text: `Human review · ${out.human_review}` }));
    const bench = run.caseNo && state.setup && state.setup.cases.find((c) => c.case === run.caseNo);
    if (bench && !run.changes.length) {
      tags.push(h("span", { class: "tag tag-quiet", text: `Bench case ${bench.case} · expected ${bench.expected}` +
        (verdict === bench.expected ? " · match" : " · miss") }));
    }
    $("verdict-tags").replaceChildren(...tags);

    const hash = out.verdict_sha256;
    $("download-package").hidden = vague;
    $("download-package").href = `/runs/${encodeURIComponent(run.id)}/package`;
    $("open-letters").hidden = !(out.supplier_letters && out.supplier_letters.length);
    $("seal-tech").hidden = !hash;
    $("seal").replaceChildren("sealed · ", h("b", { text: sealOf(hash) }));
    $("seal-note").textContent = "";
    const prints = out.fingerprints || {};
    const print = (label, value) => h("div", {}, h("dt", { text: label }), h("dd", { class: "mono", title: value, text: value }));
    $("prints").replaceChildren(...[["verdict_sha256", shortHash(hash)], ["input_sha256", shortHash(prints.input_sha256)],
      ["data_sha256", shortHash(prints.data_sha256)], ["rules_sha256", shortHash(prints.rules_sha256)],
      ["rules_version", prints.rules_version]].filter(([, v]) => v).map(([k, v]) => print(k, v)));

    for (const id of ["flip", "ask", "fool"]) $(id).hidden = vague;
    if (vague) {
      $("findings").replaceChildren(h("p", { class: "none", text: "No finding: the claim names nothing the data can check." }));
      $("part-rows").replaceChildren();
      $("where").hidden = true;
    } else {
      renderFindings(out);
      renderWhere(run);
      renderTimelines(run);
      renderPartRows(out);
      renderFlip(run);
      renderAsk(out);
      renderFool(run);
    }
    renderNotes(out);
    renderFileTech(run);
    const fp = out.fingerprints || {};
    $("file-foot").replaceChildren(
      h("span", {}, "Every finding names the rows it rests on. Files are dated and sealed",
        hash ? h("span", {}, " · ", h("b", { text: sealOf(hash) })) : null),
      h("span", { text: [fp.rules_version && `rules ${fp.rules_version}`,
        typeof out.execution_time_seconds === "number" && `run ${out.execution_time_seconds} s`,
        out.log_file && `trace ${out.log_file}`].filter(Boolean).join(" · ") }),
      h("a", { href: "#/files", text: "All evidence files" }));
  }

  async function renderTimelines(run) {
    const box = $("timelines");
    const key = `${run.id}:${run.output.verdict_sha256}`;
    if (box.dataset.key === key) return;
    box.dataset.key = key;
    box.replaceChildren();
    let data;
    try {
      data = await getJSON(`/runs/${encodeURIComponent(run.id)}/timeline`);
    } catch {
      return;  // a run this server no longer keeps: the evidence file still stands without its chart
    }
    if (box.dataset.key !== key) return;
    box.replaceChildren(...data.plots.filter((p) => p.reference_maps).map((p) => timelineCard(p, data.cutoff)));
  }

  function timelineCard(plot, cutoff) {
    const maps = plot.reference_maps;
    const head = h("div", { class: "timeline-head" },
      h("h3", { class: "h-card", text: "When the forest went, year by year" }),
      h("span", { class: "sub", text: `${plot.plot_id} · tree cover lost inside the outline · Hansen/UMD, real` }));
    if (!maps.covered) {
      return h("article", { class: "timeline" }, head, h("p", { class: "none", text: `${plot.plot_id}: ${maps.reason}.` }));
    }
    const cut = Number(cutoff.slice(0, 4));
    const all = maps.loss_ha_by_year || {};
    const forest = maps.forest_loss_ha_by_year || {};
    const delivered = plot.delivered_tonnes_by_year || {};
    const years = [...Object.keys(all), ...Object.keys(delivered)].map(Number);
    const first = Math.min(cut - 6, ...years);
    const last = Math.max(cut + 4, ...years);
    const top = Math.max(...Object.values(all), 0.01);
    const tallest = Object.keys(all).reduce((a, b) => (all[b] > (all[a] || 0) ? b : a), Object.keys(all)[0]);
    const columns = [];
    for (let year = first; year <= last; year += 1) {
      const total = all[year] || 0;
      const onForest = forest[year] || 0;
      const label = `${year}: ${total.toFixed(2)} ha of tree cover lost` + (onForest ? `, ${onForest.toFixed(2)} ha of it on EU 2020 forest` : "") +
        (delivered[year] ? `; delivered ${delivered[year]} t` : "");
      columns.push(h("div", { class: "tl-col" + (year > cut ? " tl-after" : ""), title: label },
        h("span", { class: "tl-value", text: String(year) === String(tallest) && total ? `${total.toFixed(2)} ha` : "" }),
        h("span", { class: "tl-bars", "aria-hidden": "true" },
          h("i", { class: "tl-other", style: `height: ${((total - onForest) / top) * 100}%` }),
          h("i", { class: "tl-forest", style: `height: ${(onForest / top) * 100}%` })),
        h("span", { class: "tl-year", text: String(year) }),
        h("i", { class: "tl-delivered" + (delivered[year] ? " on" : ""), "aria-hidden": "true" })));
    }
    const deliveredYears = Object.keys(delivered);
    return h("article", { class: "timeline", "aria-label": `Loss by year inside ${plot.plot_id}` },
      head,
      h("div", { class: "tl-chart", style: `--cut: ${((cut + 1 - first) / (last - first + 1)) * 100}%` },
        h("span", { class: "tl-cut", text: "after the cut-off · 31 Dec 2020" }), columns),
      h("ul", { class: "sr-only" }, columns.map((c) => h("li", { text: c.title }))),
      h("div", { class: "legend" },
        h("span", {}, h("i", { class: "swatch sw-loss" }), "Lost on EU 2020 forest"),
        h("span", {}, h("i", { class: "swatch sw-loss-other" }), "Other tree cover lost"),
        deliveredYears.length || !plot.producing_until ? h("span", {}, h("i", { class: "swatch sw-forest" }), deliveredYears.length
          ? `${plot.plot_id} delivered ${deliveredYears[0]}–${deliveredYears.at(-1)} (${plot.declared_by === "analyst" ? "declared with the drawn plot" : "registry"})`
          : "No delivery recorded") : h("span", { text: `Every plot of the file counts as producing up to ${plot.producing_until}` })),
      h("p", { class: "small muted", text: `EU 2020 forest inside the outline: ${maps.forest_2020_ha.toFixed(2)} ha; ` +
        `${maps.loss_after_2020_ha.toFixed(2)} ha of it lost after the cut-off.` }));
  }

  function checkedBy(list) {
    const names = { investigator: "the Investigator", evaluator: "the Evaluator", "evaluator:own_query": "the Evaluator, with its own queries" };
    const who = (list || []).map((name) => names[name] || name);
    return who.length ? `Checked by ${who.join(", then ")}.` : "";
  }

  function renderFindings(out) {
    const findings = out.findings || [];
    $("findings-title").textContent = `Findings · ${findings.length}`;
    if (!findings.length) {
      $("findings").replaceChildren(h("p", { class: "none", text: "No finding: the rules found nothing against the claim." }));
      return;
    }
    $("findings").replaceChildren(...findings.map((f) => {
      const tried = f.refutations_tried || [];
      const held = tried.filter((t) => t.result === "held").length;
      return h("article", { class: "finding" },
        h("div", { class: "finding-what" },
          h("div", { class: "finding-head" }, verdictTag(f.verdict), h("span", { class: "finding-part", text: PARTS[f.claim_part] || words(f.claim_part) })),
          h("h3", { class: "finding-text", text: `${capital(f.finding)}.` })),
        h("div", { class: "source" },
          h("div", { class: "source-block" }, h("span", { class: "eyebrow", text: "Sources" }),
            h("ul", { class: "evidence-list" }, (f.evidence || []).map((e) => h("li", {}, chip(`${e.source} · ${e.id}`, "data"))))),
          h("div", { class: "source-block" }, h("span", { class: "eyebrow", text: `Checks the AI tried to break it with · ${tried.length}` }),
            tried.length
              ? h("ul", { class: "checks" }, tried.map((t) => h("li", {},
                h("span", { class: t.result === "held" ? "check-held" : "check-result", text: t.result === "held" ? "the doubt held" : "did not break it" }),
                h("span", { text: t.detail }))))
              : h("span", { class: "none", text: "None tried on this finding." }),
            tried.length ? h("span", { class: "small muted", text: held ? `${plural(held, "doubt")} held: read the trace before relying on it.`
              : "None of them broke it." }) : null)),
        h("div", { class: "checked", text: checkedBy(f.checked_by) || "Who checked it is not stated in this output." }),
        h("details", { class: "tech finding-tech" },
          h("summary", {}, h("span", { text: "Technical details" }), h("span", { class: "sub", text: `rule ${f.rule} · ${f.claim_part}` + (tried.length ? ` · ${tried.map((t) => t.id).join(" ")}` : "") })),
          h("p", { class: "small", text: `Rule ${f.rule}: ${RULES[f.rule] || "see app/rules.py"}. Claim part ${f.claim_part}: ${PARTS_EXACT[f.claim_part] || words(f.claim_part)}.` }),
          tried.length ? h("ul", { class: "refutations" }, tried.map((t) => h("li", {},
            h("span", { class: "ref-id", text: t.id }),
            h("span", { class: t.result === "held" ? "ref-held" : null, text: t.result }),
            h("span", { text: t.detail })))) : null,
          h("p", { class: "small muted", text: `checked_by: ${(f.checked_by || []).join(", ") || "not stated"}` })));
    }));
  }

  function renderPartRows(out) {
    const withFinding = new Set((out.findings || []).map((f) => f.claim_part));
    $("part-rows").replaceChildren(...(out.parts || []).filter((p) => !withFinding.has(p.claim_part)).map((p) =>
      h("article", { class: "part-row" },
        verdictTag(p.verdict),
        h("span", { class: "part-text", text: PARTS[p.claim_part] || words(p.claim_part) }),
        h("span", { class: "part-id", text: p.claim_part }))));
  }

  function renderFileTech(run) {
    const out = run.output;
    const row = (cells) => h("tr", {}, cells.map((c) => h("td", {}, c)));
    const parts = (out.parts || []).map((p) => row([h("span", { class: "mono", text: p.claim_part }), verdictTag(p.verdict),
      `rule ${p.rule}: ${RULES[p.rule] || ""}`, h("span", { class: "mono", text: (p.codes || []).join(", ") || "none" })]));
    const files = [out.log_file && `trace ${out.log_file}`, typeof out.execution_time_seconds === "number" && `run ${out.execution_time_seconds} s`,
      out.fingerprints && out.fingerprints.rules_version && `rules ${out.fingerprints.rules_version}`, `run id ${run.id}`].filter(Boolean);
    $("file-tech-body").replaceChildren(
      parts.length ? h("div", { class: "table-wrap" }, h("table", { class: "tech-table" },
        h("caption", { class: "sr-only", text: "Each claim part, its verdict, the rule that decided it and the finding codes" }),
        h("thead", {}, h("tr", {}, ["Claim part", "Verdict", "Rule", "Codes"].map((name) => h("th", { scope: "col", text: name })))),
        h("tbody", {}, parts))) : null,
      h("p", { class: "small muted", text: "Rules 1 to 4 are fixed code (app/rules.py): the model proposes, the code decides. " + files.join(" · ") }));
  }

  // ---- where it happened: the farm and the alert behind the verdict, on a small map -------------

  async function renderWhere(run) {
    const box = $("where");
    const key = `${run.id}:${run.output.verdict_sha256}`;
    if (box.dataset.key === key) return;
    box.dataset.key = key;
    box.hidden = true;
    state.where = null;
    delete $("where-map").dataset.width;
    let data;
    try {
      data = await getJSON(`/ui/runs/${encodeURIComponent(run.id)}/where`);
    } catch {
      return;  // a run this server no longer keeps: the findings still name their rows
    }
    if (box.dataset.key !== key || !data.plot) { tourRefresh(); return; }
    state.where = data;
    box.hidden = false;
    drawWhere();
    tourRefresh();
  }

  function drawWhere() {
    // Drawn at the map's real width, so the labels keep their size: again when the view shows or the window resizes.
    const data = state.where;
    const width = Math.round($("where-map").clientWidth);
    if (!data || $("where").hidden || !width || $("where-map").dataset.width === String(width)) return;
    $("where-map").dataset.width = String(width);
    const W = Math.max(280, width);
    const H = Math.round(W * (W < 560 ? 0.66 : 0.4));
    const p = data.plot;
    const a = data.alert;
    const lat0 = p.lat;
    const sx = 111320 * Math.cos((lat0 * Math.PI) / 180);  // app/world/geo.py, the same flat-earth scale
    const toM = ([lat, lon]) => [(lon - p.lon) * sx, (lat - lat0) * 110574];
    const radius = Math.sqrt((p.area_ha * 10000) / Math.PI);  // a point plot: a disk of its declared area
    const pts = (p.ring || []).map(toM);
    const extent = [...pts, a ? toM([a.lat, a.lon]) : [0, 0], [radius, radius], [-radius, -radius]];
    const half = Math.max(100, ...extent.map(([x, y]) => Math.max(Math.abs(x) * (H / W), Math.abs(y)))) * 1.3;
    const k = H / 2 / half;  // pixels per metre
    const X = (x) => W / 2 + x * k;
    const Y = (y) => H / 2 - y * k;
    const labelAt = (x, y, words) => {  // one line beside the dot, on the side with room; two lines when narrow
      const room = (text, right) => (right ? W - 8 - (x + 18) : x - 18 - 8) >= text.length * 7.5;
      const line = words.join(" · ");
      const right = room(line, true) || !room(line, false) ? x < W / 2 || room(line, true) : false;
      const lines = room(line, right) ? [line] : words;
      return lines.map((text, i) => ({ x: (right ? x + 18 : x - 18).toFixed(1), y: (y + i * 16).toFixed(1),
        "text-anchor": right ? "start" : "end", text }));
    };
    const bar = [50, 100, 200, 500, 1000].find((m) => m * k >= 60) || 1000;
    const shapes = [];
    if (pts.length) {
      const d = pts.map(([x, y]) => `${X(x).toFixed(1)},${Y(y).toFixed(1)}`).join(" ");
      shapes.push(s("polygon", { points: d, class: "m-halo" }), s("polygon", { points: d, class: "m-outline" }));
    } else {
      shapes.push(s("circle", { cx: W / 2, cy: H / 2, r: (radius * k).toFixed(1), class: "w-disk" }),
        s("circle", { cx: W / 2, cy: H / 2, r: 4, class: "w-point" }));
    }
    const top = pts.length ? Math.min(...pts.map(([, y]) => Y(y))) : H / 2 - radius * k;
    shapes.push(s("text", { x: W / 2, y: Math.max(20, top - 12), class: "w-label", "text-anchor": "middle", text: p.ring ? p.plot_id : `${p.plot_id} · declared by a point` }));
    if (a) {
      const [ax, ay] = toM([a.lat, a.lon]);
      shapes.push(s("circle", { cx: X(ax).toFixed(1), cy: Y(ay).toFixed(1), r: 12, class: "w-alert-ring" }),
        s("circle", { cx: X(ax).toFixed(1), cy: Y(ay).toFixed(1), r: 6, class: "w-alert" }),
        ...labelAt(X(ax), Y(ay) + 5, [`alert ${a.alert_id}`, a.alert_date]).map((at) => s("text", { ...at, class: "w-label w-label-alert" })));
    }
    shapes.push(s("g", { class: "w-scale" }, s("path", { d: `M16 ${H - 20}V${H - 14}H${16 + bar * k}V${H - 20}` }),
      s("text", { x: 24 + bar * k, y: H - 14, text: `${bar} m` })));
    const said = `${p.plot_id}, ${p.area_ha} ha, ${p.ring ? "mapped as a polygon" : "declared by a point"}` +
      (a ? `; satellite alert ${a.alert_id} of ${a.alert_date}, ${a.confidence} confidence, ${pts.length ? "inside the outline" : "near the point"}` : "");
    $("where-map").replaceChildren(s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": said }, shapes));
    $("where-sub").textContent = `${p.plot_id} · ${p.area_ha} ha` + (a ? ` · alert ${a.alert_id}, ${a.confidence} confidence` : "");
    $("where-note").textContent = "Drawn from the rows this run read: the farm's outline from the plot registry" +
      (a ? ", the alert's position from the satellite alerts" : "") + ". North is up.";
  }

  function renderNotes(out) {
    const note = (title, items, empty) => h("section", { class: "note" },
      h("h3", { class: "h-card", text: title }),
      items && items.length ? h("ul", {}, items.map((item) => h("li", { text: item }))) : h("p", { class: "none", text: empty }));
    const notes = [];
    if (out.untested_factors) notes.push(note("Not tested", out.untested_factors, "Nothing listed."));
    if (out.recommended_actions) notes.push(note("Recommended actions", out.recommended_actions, "None."));
    if (out.limitations) notes.push(note("Limits", out.limitations, "None listed."));
    $("notes").replaceChildren(...notes);
  }

  function renderAsk(out) {
    const actions = out.recommended_actions || [];
    $("ask").hidden = !actions.length;
    $("ask-text").textContent = actions[0] || "";
    $("ask-note").textContent = actions.length > 1 ? `First of ${actions.length} recommended actions, in the Writer's order.`
      : "The one recommended action of this evidence file.";
  }

  // ---- the flip and "try to fool it" -------------------------------------------------------------

  function editable(entity, field) {
    const kind = String(entity).split(":")[0];
    return Boolean(state.setup && state.setup.what_if[kind] && state.setup.what_if[kind][field]);
  }

  function renderFlip(run) {
    const out = run.output;
    const tried = out.flip_tried || [];
    $("flip-sentence").textContent = out.flip || "No flip in this output.";
    $("flip-how").textContent = `Computed by code from the same evidence and rules: ${plural(tried.length, "single change")} tried, no model.`;
    $("flip-tech-sub").textContent = `${plural(tried.length, "single change")} tried`;
    const flip = tried.find((t) => t.verdict !== out.verdict && editable(t.change.entity, t.change.field));
    const val = (value) => (Array.isArray(value) ? value.join(", ") : value);
    $("flip-tried").replaceChildren(...tried.map((t) => h("li", {},
      h("span", { class: "mono", text: `${t.change.entity} ${t.change.field}` }),
      h("span", { text: `${val(t.change.from)} → ${val(t.change.to)}` }),
      verdictTag(t.verdict))));
    const old = $("flip").querySelector(".flip-try");
    if (old) old.remove();
    if (flip) {
      $("flip").append(h("button", {
        class: "flip-try", type: "button", text: `Try it: ${flip.change.field} → ${val(flip.change.to)}`,
        onclick: () => fillFool(flip.change.entity, flip.change.field, flip.change.to),
      }));
    }
  }

  function foolEntities(out) {
    const ids = [];
    for (const f of out.findings || []) for (const e of f.evidence || []) ids.push(`${e.source}:${e.id}`);
    for (const t of out.flip_tried || []) ids.push(t.change.entity);
    const kinds = (state.setup && state.setup.what_if) || {};
    return [...new Set(ids)].filter((id) => kinds[id.split(":")[0]]);
  }

  function currentValue(run, entity, field) {
    const key = `${entity} ${field}`;
    if (key in run.values) return run.values[key];
    const known = (run.output.flip_tried || []).find((t) => t.change.entity === entity && t.change.field === field);
    return known ? known.change.from : "";
  }

  function renderFool(run) {
    // A change can clear the findings (a flip to PROVEN): keep offering every entity this run has named.
    for (const id of foolEntities(run.output)) run.entities.add(id);
    const entities = [...run.entities];
    renderFoolResult(run);
    $("fool-form").hidden = !entities.length;
    $("fool-empty").hidden = Boolean(entities.length);
    $("fool-error").hidden = true;
    if (!entities.length) return;
    const select = $("fool-entity");
    const keep = select.value;
    select.replaceChildren(...entities.map((id) => h("option", { value: id, text: id.replace(":", " · ") })));
    if (entities.includes(keep)) select.value = keep;
    renderFoolFields(run);
  }

  function renderFoolFields(run, preset) {
    const entity = $("fool-entity").value;
    const kind = entity.split(":")[0];
    const fields = Object.keys(state.setup.what_if[kind] || {});
    const select = $("fool-field");
    const keep = preset ? preset.field : select.value;
    select.replaceChildren(...fields.map((name) => h("option", { value: name, text: name })));
    if (fields.includes(keep)) select.value = keep;
    renderFoolValue(run, preset ? preset.value : undefined);
  }

  function renderFoolValue(run, value) {
    const entity = $("fool-entity").value;
    const field = $("fool-field").value;
    const control = state.setup.what_if[entity.split(":")[0]][field];
    const start = value !== undefined ? value : currentValue(run, entity, field);
    let input;
    if (control.type === "choice") {
      input = h("select", { id: "fool-value", name: "value" },
        control.choices.map((choice) => h("option", { value: choice, selected: choice === start, text: choice })));
    } else if (control.type === "number") {
      input = h("input", { id: "fool-value", name: "value", type: "number", step: "any", min: "0", required: true,
        inputmode: "decimal", value: start === "" ? null : start });
    } else {
      input = h("input", { id: "fool-value", name: "value", type: "text", required: true, inputmode: "numeric",
        pattern: "\\d{4}-\\d{2}-\\d{2}", placeholder: "YYYY-MM-DD", autocomplete: "off", value: start === "" ? null : start });
    }
    $("fool-value-slot").replaceChildren(input);
  }

  function fillFool(entity, field, value) {
    $("fool-entity").value = entity;
    renderFoolFields(state.run, { field, value });
    $("fool").scrollIntoView({ block: "nearest", behavior: reducedMotion ? "auto" : "smooth" });
    $("fool-value").focus({ preventScroll: true });
  }

  function renderFoolResult(run) {
    const box = $("fool-result");
    const last = run.changes[run.changes.length - 1];
    box.hidden = !last;
    if (!last) return;
    const facts = last.invalidated_facts || [];
    const cards = last.rechecked_cards || [];
    const shown = facts.slice(0, 8);
    box.replaceChildren(
      h("span", { class: "eyebrow", text: `Last change · ${last.change.entity} ${last.change.field} = ${last.change.value}` }),
      h("div", { class: "before-after", "aria-label": `Verdict before ${last.verdict_before}, after ${last.verdict_after}` },
        h("span", { class: "label", text: "Verdict" }), verdictTag(last.verdict_before),
        h("span", { "aria-hidden": "true", text: "→" }), verdictTag(last.verdict_after)),
      h("p", { class: "small", text: last.verdict_before === last.verdict_after
        ? `The verdict stays ${last.verdict_after}: this change does not touch what decided it.`
        : `The verdict changes from ${last.verdict_before} to ${last.verdict_after}: the evidence file above now shows why.` }),
      h("details", { class: "tech" },
        h("summary", {}, h("span", { text: "Technical details" }), h("span", { class: "sub", text: `${plural(facts.length, "fact")} invalidated · ${plural(cards.length, "card")} checked again` })),
        h("div", {}, h("span", { class: "label", text: `Facts invalidated in the World Model · ${facts.length}` }),
          facts.length ? h("ul", {}, shown.map((id) => h("li", { class: "mono", text: id })),
            facts.length > shown.length ? h("li", { text: `and ${facts.length - shown.length} more` }) : null)
            : h("p", { class: "none", text: "None: no fact of this run was built on that value." })),
        h("div", {}, h("span", { class: "label", text: `Cards checked again by the Evaluator · ${cards.length}` }),
          cards.length ? h("ul", {}, cards.map((id) => h("li", { class: "mono", text: id })))
            : h("p", { class: "none", text: "None: no card rests on that value any more." }))));
  }

  async function submitFool(event) {
    event.preventDefault();
    const run = state.run;
    const error = $("fool-error");
    error.hidden = true;
    const entity = $("fool-entity").value;
    const field = $("fool-field").value;
    const control = state.setup.what_if[entity.split(":")[0]][field];
    const raw = $("fool-value").value.trim();
    if (control.type === "number" && (raw === "" || Number.isNaN(Number(raw)))) {
      error.textContent = `${field} takes a number.`;
      error.hidden = false;
      return;
    }
    const value = control.type === "number" ? Number(raw) : raw;
    const button = $("fool-form").querySelector("button[type=submit]");
    button.setAttribute("aria-busy", "true");
    button.textContent = "Re-running…";
    try {
      const out = await postJSON(`/runs/${encodeURIComponent(run.id)}/what_if`, { entity, field, value });
      if (run !== state.run) return;
      run.output = out;
      run.changes.push(out.what_if);
      run.values[`${entity} ${field}`] = value;
      renderFile(run);
      show("file");
      $("fool-result").scrollIntoView({ block: "nearest", behavior: reducedMotion ? "auto" : "smooth" });
    } catch (failure) {
      error.textContent = `Not applied: ${failure.message}`;
      error.hidden = false;
    } finally {
      button.removeAttribute("aria-busy");
      button.textContent = "Re-run what depends on it";
    }
  }

  // ---- seal and supplier letters ---------------------------------------------------------------

  async function copySeal() {
    const hash = state.run && state.run.output && state.run.output.verdict_sha256;
    if (!hash) return;
    try {
      await navigator.clipboard.writeText(hash);
      $("seal-note").textContent = "Full verdict_sha256 copied.";
    } catch {
      $("seal-note").textContent = `Clipboard not available. verdict_sha256: ${hash}`;
    }
  }

  function openLetters() {
    const letters = (state.run && state.run.output && state.run.output.supplier_letters) || [];
    $("letters-body").replaceChildren(...letters.map((letter) => {
      const note = h("span", { class: "small muted", "aria-live": "polite" });
      return h("section", {},
        h("pre", { class: "letter", text: letter.letter }),
        h("div", { class: "letter-tools" },
          h("button", { class: "btn", type: "button", text: `Copy the letter to ${letter.name} (${letter.supplier_id})`,
            onclick: async () => {
              try { await navigator.clipboard.writeText(letter.letter); note.textContent = "Copied."; } catch { note.textContent = "Clipboard not available: select the text above."; }
            } }), note));
    }));
    $("letters").showModal();
  }

  // ---- evidence files: the runs this server keeps ------------------------------------------------

  async function loadFiles() {
    const list = $("file-list");
    list.setAttribute("aria-busy", "true");
    list.replaceChildren(h("li", { class: "none", text: "Reading the runs this server keeps…" }));
    let runs;
    let checks;
    try {
      [checks, runs] = await Promise.all([getJSON("/ui/check-plots"), getJSON("/ui/runs")]);
    } catch (error) {
      list.replaceChildren(h("li", { class: "error", text: `Could not list the runs: ${error.message}` }));
      return;
    } finally {
      list.removeAttribute("aria-busy");
    }
    if (!runs.length && !checks.length) {
      list.replaceChildren(h("li", { class: "none" }, "Nothing checked yet in this server's memory. ", h("a", { href: "#/", text: "Check your plots" }), "."));
      return;
    }
    const plotChecks = checks.map((c) => {
      const name = (recall(`batch:${c.batch_id}`) || {}).name;
      const counts = c.summary.plots;
      const worst = ["CONTRADICTED", "UNVERIFIABLE", "NO_ISSUE"].find((v) => counts[v]);
      return h("li", {}, h("a", { href: checkHref(c.batch_id) },
        worst ? verdictTag(worst) : h("span", { class: "tag tag-quiet", text: "NO PLOT" }),
        h("span", { class: "stack-8" }, h("span", { class: "file-claim", text: name || `Plot check of ${plural(c.plots, "plot")}` }),
          h("span", { class: "sub", text: [c.batch_id, plural(c.plots, "plot"), `${counts.CONTRADICTED} contradicted`,
            `${counts.UNVERIFIABLE} unverifiable`, `${counts.NO_ISSUE} clear`].join(" · ") })),
        h("span", { class: "sub", text: "Results →" })));
    });
    list.replaceChildren(...plotChecks, ...runs.map((r) => {
      const status = r.verdict ? verdictTag(r.verdict) : h("span", { class: "tag tag-quiet", text: (r.status || "").replace("_", " ").toUpperCase() });
      const bits = [r.run_id, r.mode && `${r.mode} mode`, r.degraded && "degraded", r.changed && "changed with try to fool it"];
      return h("li", {}, h("a", { href: runHref(r.run_id, r.verdict ? "file" : "live") },
        status,
        h("span", { class: "stack-8" }, h("span", { class: "file-claim", text: r.claim ? `“${r.claim}”` : "Claim not kept for this run" }),
          h("span", { class: "sub", text: bits.filter(Boolean).join(" · ") })),
        h("span", { class: "sub", text: r.verdict ? "Evidence file →" : "Live trace →" })));
    }));
  }

  // ---- reliability tests -------------------------------------------------------------------------

  function renderBench() {
    if (!state.setup) return;
    const cases = state.setup.cases;
    $("bench-title").textContent = `${cases.length} trap cases, known answers.`;
    $("bench-run").textContent = `Run all ${cases.length}`;
    if (state.bench) return; // keep the last results on screen
    $("bench-rows").replaceChildren(...cases.map((c) => h("tr", { id: `bench-${c.case}` },
      h("td", { class: "num", text: String(c.case).padStart(2, "0") }),
      h("td", { text: c.change }),
      h("td", {}, verdictTag(c.expected)),
      h("td", { class: "got", text: "not run" }), h("td", { class: "ok" }), h("td", { class: "seal-cell" }), h("td", { class: "open" }))));
  }

  async function runBench() {
    const cases = state.setup.cases;
    const button = $("bench-run");
    button.setAttribute("aria-busy", "true");
    button.disabled = true;
    state.bench = [];
    renderBenchRowsEmpty(cases);
    for (const c of cases) {
      const row = $(`bench-${c.case}`);
      row.dataset.state = "running";
      row.querySelector(".got").textContent = "running";
      try {
        const r = await postJSON(`/ui/bench/${c.case}`);
        state.bench.push(r);
        row.dataset.state = "done";
        row.querySelector(".got").replaceChildren(r.got ? verdictTag(r.got) : h("span", { class: "tag tag-quiet", text: r.status }));
        row.querySelector(".ok").replaceChildren(h("span", { class: r.ok ? "match" : "miss", text: r.ok ? "match" : "miss" }));
        row.querySelector(".seal-cell").textContent = sealOf(r.verdict_sha256);
        row.querySelector(".open").replaceChildren(h("a", { href: runHref(r.run_id, "file"), text: "Evidence file" }));
      } catch (error) {
        row.dataset.state = "done";
        row.querySelector(".got").textContent = `error: ${error.message}`;
      }
      const ok = state.bench.filter((r) => r.ok).length;
      $("bench-score").textContent = `${ok}/${state.bench.length}`;
      $("bench-score-note").textContent = state.bench.length < cases.length
        ? `Running case ${c.case} of ${cases.length}…` : "";
    }
    const seconds = state.bench.reduce((sum, r) => sum + (r.execution_time_seconds || 0), 0);
    const degraded = state.bench.every((r) => r.degraded);
    $("bench-score-note").textContent = `${plural(state.bench.length, "run")} on this server just now, ${seconds.toFixed(2)} s of run time` +
      (degraded ? ", every one degraded: rules alone, no model." : ".");
    button.removeAttribute("aria-busy");
    button.disabled = false;
  }

  function renderBenchRowsEmpty(cases) {
    for (const c of cases) {
      const row = $(`bench-${c.case}`);
      delete row.dataset.state;
      row.querySelector(".got").textContent = "not run";
      for (const cls of [".ok", ".seal-cell", ".open"]) row.querySelector(cls).replaceChildren();
    }
    $("bench-score").textContent = "not run yet";
  }

  // ---- the guided tour: five bubbles on the example plot file ----------------------------------------
  // Nothing in it is a number: each bubble points at what the check itself shows on screen.

  const TOUR = [
    { view: "home", target: "drop", title: "Your plots",
      text: "Start with the GeoJSON of your production plots, the file the EUDR already asks for. For the tour, " +
        "GreenTrace checks its example file: real places, example ids and suppliers." },
    { view: "check", target: "plots-table", title: "Every plot, by risk",
      text: "Each plot gets a verdict from fixed rules and one sentence in plain words. The riskiest come first; " +
        "click one to open its file." },
    { view: "check", target: "batch-map-card", title: "All of them on the map",
      text: "Coloured by verdict. Your suppliers are ranked the same way, so you know whom to call first." },
    { view: "plot", target: "plot-plain-box", title: "One plot, in plain words",
      text: "Then the evidence: the map cells inside the outline, the loss year by year, and a receipt that " +
        "re-derives the verdict offline." },
    { view: "plot", target: "why", title: "Why? Ask the agents",
      text: "Five AI agents re-check the plot, try to break the finding and write an evidence file in which you can " +
        "try to fool them." },
  ];

  function startTour() {
    if (!state.setup) return;
    state.tour = { step: 0, batchId: null, plotId: null };
    if (currentView() !== "home") location.hash = "#/";
    else tourRefresh(true);
  }

  async function tourGo(delta) {
    const tour = state.tour;
    if (!tour) return;
    const next = tour.step + delta;
    if (next >= TOUR.length) { closeTour(); return; }
    if (next < 0) return;
    if (!tour.batchId && next > 0) {  // the tour's plots: the example file, checked for real
      $("tour-next").setAttribute("aria-busy", "true");
      const out = await checkFileQuiet();
      $("tour-next").removeAttribute("aria-busy");
      if (!out || state.tour !== tour) return;
      Object.assign(tour, { batchId: out.batch_id, plotId: out.plots[0].plot_id });
    }
    tour.step = next;
    const want = TOUR[next].view;
    const href = want === "home" ? "#/" : checkHref(tour.batchId, want === "plot" ? tour.plotId : null);
    if (location.hash !== href) location.hash = href;  // route() refreshes the bubble
    else tourRefresh(true);
  }

  async function checkFileQuiet() {
    try {
      return await checkFile(await getJSON("/static/example_plots.geojson"), "Example data · real places near Soubré, example ids and suppliers", true);
    } catch (error) {
      dropStatus(`The example file could not be read: ${error.message}`, true);
      return null;
    }
  }

  function tourRefresh(focus) {
    const tour = state.tour;
    const bubble = $("tour");
    document.querySelectorAll(".tour-on").forEach((node) => node.classList.remove("tour-on"));
    if (!tour) { bubble.hidden = true; return; }
    const step = TOUR[tour.step];
    const loaded = step.view === "home" || (state.batch && state.batch.id === tour.batchId && state.batch.output
      && (step.view === "check" || state.plotId === tour.plotId));
    if (currentView() !== step.view || !loaded) { bubble.hidden = true; return; }
    const target = $(step.target);
    $("tour-count").textContent = `${tour.step + 1} of ${TOUR.length}`;
    $("tour-title").textContent = step.title;
    $("tour-text").textContent = step.text;
    $("tour-back").disabled = tour.step === 0;
    $("tour-next").disabled = false;
    $("tour-next").textContent = tour.step === TOUR.length - 1 ? "Done" : "Next";
    const wasHidden = bubble.hidden;
    bubble.hidden = false;
    target.classList.add("tour-on");
    const scrollTo = placeTour(target);
    const shown = `${tour.step}:${target.id}`;
    if (tour.shown !== shown || wasHidden) {  // scroll only when the bubble moves to something new
      tour.shown = shown;
      window.scrollTo({ top: scrollTo, behavior: reducedMotion ? "auto" : "smooth" });
    }
    if (focus || wasHidden) $("tour-title").focus({ preventScroll: true });
  }

  function placeTour(target) {
    // In page coordinates, so the bubble stays put while the page scrolls. Returns where to scroll to.
    const bubble = $("tour");
    const box = target.getBoundingClientRect();
    const top = box.top + window.scrollY;
    const bottom = box.bottom + window.scrollY;
    const width = bubble.offsetWidth;
    const height = bubble.offsetHeight;
    const view = window.innerHeight;
    const page = document.documentElement.clientWidth;
    const left = Math.min(Math.max(16, box.left), page - width - 16);
    let at;
    let scroll;
    if (bottom - top + 12 + height <= view - 32) {  // the target and the bubble under it fit in the window
      at = bottom + 12;
      scroll = top - (view - (bottom - top + 12 + height)) / 2;
    } else {  // a tall target: the bubble sits over its top edge
      at = top + 16;
      scroll = top - 72;
    }
    bubble.style.left = `${Math.round(left + window.scrollX)}px`;
    bubble.style.top = `${Math.round(at)}px`;
    return Math.max(0, Math.round(scroll));
  }

  function closeTour() {
    const had = Boolean(state.tour);
    state.tour = null;
    $("tour").hidden = true;
    document.querySelectorAll(".tour-on").forEach((node) => node.classList.remove("tour-on"));
    if (had) $("main").focus({ preventScroll: true });
  }

  // ---- wiring ------------------------------------------------------------------------------------

  function setFilter(filter) {
    state.filter = filter;
    $("trace").dataset.filter = filter;
    for (const button of document.querySelectorAll(".filters button")) {
      button.setAttribute("aria-pressed", String(button.dataset.filter === filter));
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("plots-upload").addEventListener("click", () => $("plots-file").click());
    $("plots-file").addEventListener("change", (event) => {
      const [file] = event.target.files;
      if (file) uploadPlots(file);
      event.target.value = "";  // the same file can be chosen again after a fix
    });
    // Drop the file anywhere on the first page; the drop zone says it is ready to take it.
    const home = $("view-home");
    home.addEventListener("dragover", (event) => { event.preventDefault(); $("drop").classList.add("over"); });
    home.addEventListener("dragleave", (event) => { if (!home.contains(event.relatedTarget)) $("drop").classList.remove("over"); });
    home.addEventListener("drop", (event) => {
      event.preventDefault();
      $("drop").classList.remove("over");
      const [file] = event.dataTransfer.files;
      if (file) uploadPlots(file);
    });
    $("load-example").addEventListener("click", loadExample);
    $("claim").addEventListener("change", () => remember("promise", $("claim").value.trim()));
    for (const button of document.querySelectorAll(".filters button")) {
      button.addEventListener("click", () => setFilter(button.dataset.filter));
    }
    $("fool-form").addEventListener("submit", submitFool);
    $("fool-entity").addEventListener("change", () => renderFoolFields(state.run));
    $("fool-field").addEventListener("change", () => renderFoolValue(state.run));
    $("seal").addEventListener("click", copySeal);
    $("open-letters").addEventListener("click", openLetters);
    $("letters-close").addEventListener("click", () => $("letters").close());
    $("bench-run").addEventListener("click", runBench);
    $("plot-draw").addEventListener("click", () => setDrawing(!state.draw.active));
    $("lens-run").addEventListener("click", runLens);
    $("draw-undo").addEventListener("click", undoPoint);
    $("draw-clear").addEventListener("click", clearPlot);
    $("draw-check").addEventListener("click", checkPlot);
    $("tour-start").addEventListener("click", startTour);
    $("tour-next").addEventListener("click", () => tourGo(1));
    $("tour-back").addEventListener("click", () => tourGo(-1));
    $("tour-close").addEventListener("click", closeTour);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && state.tour && !$("letters").open) { event.preventDefault(); closeTour(); }
    });
    window.addEventListener("resize", () => {
      drawWhere();
      const b = state.batch;
      if (b && b.output && currentView() === "check") renderBatchMap();
      if (b && b.output && currentView() === "plot" && b.details[state.plotId]) {
        renderPlotMap(b.details[state.plotId], b.output.plots.find((r) => r.plot_id === state.plotId));
      }
      if (state.tour && !$("tour").hidden) tourRefresh();
    });
    window.addEventListener("hashchange", () => route(false));
    state.setupReady = loadSetup();
    state.setupReady.then(() => {
      if (state.run && state.run.output) renderFile(state.run);
      if (currentView() === "bench") renderBench();
    });
    route(true);
  });
})();
