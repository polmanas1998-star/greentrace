/* GreenTrace interface: check a claim, live trace, evidence files, reliability tests.
Plain JS, no build step, no network but this server.

Everything shown comes from the server: /ui/setup, /api/plot-map, the trace lines (GET /runs/{id}/events),
the run's output, /ui/runs and /ui/bench/{case}. Text from a run is always set with textContent, never parsed
as HTML: the reports a run reads can hold anything (trap case 19 hides an instruction in an NGO text). */
(() => {
  "use strict";

  // What each claim part and each rule number means, in app/rules.py's words. The run's rules_version is
  // printed at the foot of its evidence file.
  const PARTS = {
    coverage: "Every tonne of the year is traced to a plot",
    no_deforestation: "No forest lost after 31 December 2020 on a plot that delivers",
    plausibility: "The declared plots could have produced the volume",
    scope: "The report promises what the claim says",
  };
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

  const state = { setup: null, run: null, filter: "all", maps: {}, mapPlot: null, bench: null };
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

  const verdictTag = (verdict) => h("span", { class: `tag tag-${verdict}`, text: verdict });
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
  // #/ check a claim · #/runs/<id> live trace · #/runs/<id>/file evidence file · #/files · #/bench

  const VIEWS = ["home", "live", "file", "files", "bench", "missing"];
  const TAB_OF = { home: "check", live: "check", file: "files", files: "files", bench: "bench" };

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
    const verdict = run && run.output && run.output.verdict;
    document.title = {
      file: verdict ? `${capital(verdict.toLowerCase())} · Evidence file · GreenTrace` : "Evidence file · GreenTrace",
      live: "Live trace · GreenTrace", files: "Evidence files · GreenTrace", bench: "Reliability tests · GreenTrace",
    }[view] || "GreenTrace";
  }

  function route(initial) {
    const [first, id, sub] = location.hash.replace(/^#\/?/, "").split("/");
    if (first === "runs" && id) {
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
  }

  function renderMode(view) {
    const badge = $("mode-badge");
    const run = state.run;
    let text = "";
    if ((view === "live" || view === "file") && run && run.mode) {
      text = `${capital(run.mode)} mode` + (run.degraded ? " · degraded: rules only, no model" : "");
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
      $("demo-sub").textContent = `Could not read /ui/setup (${error.message})`;
      return;
    }
    const setup = state.setup;
    $("demo-sub").textContent = setup.default_mode === "sample"
      ? `Cocoa importer · synthetic supply chain, case ${setup.demo_case}`
      : "Cocoa importer · data through Zetaris";
    $("examples").replaceChildren(...setup.examples.map((example) => h("button", {
      class: "btn", type: "button", title: `Case ${example.case}: ${example.change}. Sample mode, rules alone.`,
      text: example.label, onclick: () => startRun($("claim").value, { sample_mode: true, case: example.case }, example.case),
    })));
    if (!setup.plot_input) {
      for (const id of ["plot-upload", "plot-draw"]) $(id).title = "Coming: the agents do not read a plot from this form yet";
    }
    renderMode(currentView());
    const plots = setup.map_plots.map((p) => p.id);
    $("map-switch").replaceChildren(...plots.map((id) => h("button", {
      type: "button", "aria-pressed": "false", "data-plot": id, text: id, onclick: () => loadMap(id),
    })));
    loadMap(plots[0]);
  }

  async function startRun(claimText, options, caseNo) {
    const claim = String(claimText || "").trim();
    const error = $("claim-error");
    error.hidden = true;
    if (!claim) {
      error.textContent = "Write the claim to check first.";
      error.hidden = false;
      $("claim").focus();
      return;
    }
    const buttons = document.querySelectorAll("#view-home button:not([disabled])");
    buttons.forEach((b) => b.setAttribute("aria-busy", "true"));
    try {
      const body = { input: { claim } };
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
    state.mapPlot = plotId;
    for (const button of $("map-switch").querySelectorAll("button")) {
      button.setAttribute("aria-pressed", String(button.dataset.plot === plotId));
    }
    try {
      state.maps[plotId] = state.maps[plotId] || await getJSON(`/api/plot-map?plot=${encodeURIComponent(plotId)}`);
    } catch (error) {
      $("map-foot").replaceChildren(h("p", { class: "error", text: `No map: ${error.message}` }));
      return;
    }
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
      s("g", { class: "m-vertex" }, data.plot.vertices.map(([lat, lon]) => s("circle", { cx: x(lon).toFixed(1), cy: y(lat).toFixed(1), r: 5 }))));

    const scale = h("div", { class: "map-scale", "aria-hidden": "true" },
      h("span", { class: "map-scale-bar", style: `width: ${(100 / w.width_m) * 100}cqw` }), "100 m");
    const north = h("div", { class: "map-north", "aria-hidden": "true" },
      s("svg", { viewBox: "0 0 16 20", width: 16, height: 20 }, s("path", { d: "M8 1 L14 15 L8 12 L2 15 Z" })), "N");
    const image = data.image ? h("img", { src: data.image, alt: `Sentinel-2 image of the area around ${data.plot.id}` }) : null;
    $("map").replaceChildren($("map-switch"), ...[image, svg, scale, north].filter(Boolean));
    $("map-switch").setAttribute("aria-label", `Plot shown: ${data.plot.id}`);

    $("map-title").textContent = data.place;
    $("map-coords").textContent = `${data.plot.lat.toFixed(4)}° N · ${Math.abs(data.plot.lon).toFixed(4)}° W`;
    const number = (label, value, cls) => h("div", {}, h("dt", { text: label }), h("dd", { class: cls || null, text: value }));
    $("map-numbers").replaceChildren(
      number("Outline", `${m.outline_ha.toFixed(1)} ha`),
      number("Year of loss · Hansen", m.main_loss_year ? String(m.main_loss_year) : "none"),
      number("Lost after 2020 · on EU forest", `${m.loss_after_2020_ha.toFixed(2)} ha`, m.loss_after_2020_ha > 0 ? "loss" : null));
    const swatch = (cls, text) => h("span", {}, h("i", { class: `swatch ${cls}` }), text);
    const forestLine = m.forest_2020_ha > 0
      ? `EU 2020 forest inside the outline: ${m.forest_2020_ha.toFixed(2)} ha.`
      : "No EU 2020 forest inside this outline: the loss drawn here does not count as deforestation after 2020 on this map.";
    $("map-foot").replaceChildren(
      h("div", { class: "legend" },
        swatch("sw-forest", "EU forest 2020"), swatch("sw-loss", "Lost after 2020, on EU forest"),
        swatch("sw-loss-other", "Lost after 2020, elsewhere"), swatch("sw-loss-old", "Lost before 2021"),
        swatch("sw-outline", "Outline")),
      h("span", { text: `${forestLine} Outline: a ${data.plot.declared_area_ha.toFixed(1)} ha square around the declared point. ` +
        "Measured offline by app/checks/landcover.py from 30 m cells; not an input of the demo run." +
        (data.image ? " Imagery: Sentinel-2, contains modified Copernicus Sentinel data." : "") }));
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
    $("live-error").hidden = true;
    setTitles(run);
    setStatus("running", "Starting");
    $("live-lead").textContent = "Starting the run. Each line below is one step, as the agents write it to the trace.";
    renderLiveParts(run);
    renderStats(run);
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
    const elapsed = offset(step, run.lines[0]);
    setStatus("running", `Investigating · ${plural(run.lines.length, "step")}` + (elapsed === null ? "" : ` · ${clock(elapsed)}`));
    $("live-lead").textContent = progress(count(run.lines));
  }

  function renderLiveParts(run) {
    const parts = run.output && run.output.parts;
    const card = (name, state) => h("div", { class: "part-card" },
      h("div", { class: "part-card-head" }, h("span", { class: "part-id", text: name }), state),
      h("div", { class: "part-text", text: PARTS[name] || words(name) }));
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

  function settle(run) {
    if (run !== state.run) return;
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
    $("live-verdict").replaceChildren(out.verdict ? verdictTag(out.verdict) : h("span", { class: "tag tag-quiet", text: "TOO VAGUE" }));
    $("live-open-file").href = runHref(run.id, "file");
    $("live-result").hidden = false;
    renderLiveParts(run);
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
    $("file-summary").textContent = out.summary || "";
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
    $("seal").hidden = !hash;
    $("seal").replaceChildren("sealed · ", h("b", { text: sealOf(hash) }));
    $("seal-note").textContent = "";

    for (const id of ["flip", "ask", "fool"]) $(id).hidden = vague;
    if (vague) {
      $("findings").replaceChildren(h("p", { class: "none", text: "No finding: the claim names nothing the data can check." }));
      $("part-rows").replaceChildren();
    } else {
      renderFindings(out);
      renderPartRows(out);
      renderFlip(run);
      renderAsk(out);
      renderFool(run);
    }
    renderNotes(out);
    const fp = out.fingerprints || {};
    $("file-foot").replaceChildren(
      h("span", {}, "Every finding names the rows it rests on. Files are dated and sealed",
        hash ? h("span", {}, " · ", h("b", { text: sealOf(hash) })) : null),
      h("span", { text: [fp.rules_version && `rules ${fp.rules_version}`,
        typeof out.execution_time_seconds === "number" && `run ${out.execution_time_seconds} s`,
        out.log_file && `trace ${out.log_file}`].filter(Boolean).join(" · ") }),
      h("a", { href: "#/files", text: "All evidence files" }));
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
      return h("article", { class: "finding" },
        h("div", { class: "finding-what" },
          h("div", { class: "finding-head" }, h("span", { class: "part-id", text: f.claim_part }), verdictTag(f.verdict),
            h("span", { class: "rule-ref", text: `Rule ${f.rule}` })),
          h("h3", { class: "finding-text", text: `${capital(f.finding)}.` }),
          h("p", { class: "small muted", text: `Rule ${f.rule}: ${RULES[f.rule] || "see app/rules.py"}.` })),
        h("div", { class: "source" },
          h("span", { class: "eyebrow", text: "Where this comes from" }),
          h("div", { class: "source-block" }, h("span", { class: "sub", text: "Evidence" }),
            h("ul", { class: "evidence-list" }, (f.evidence || []).map((e) => h("li", {}, chip(`${e.source} · ${e.id}`, "data"))))),
          h("div", { class: "source-block" }, h("span", { class: "sub", text: `Refutations tried · ${tried.length}` }),
            tried.length
              ? h("ul", { class: "refutations" }, tried.map((t) => h("li", {},
                h("span", { class: "ref-id", text: t.id }),
                h("span", { class: t.result === "held" ? "ref-held" : null, text: t.result }),
                h("span", { text: t.detail }))))
              : h("span", { class: "none", text: "None tried on this finding." }))),
        h("div", { class: "checked", text: checkedBy(f.checked_by) || "Who checked it is not stated in this output." }));
    }));
  }

  function renderPartRows(out) {
    const withFinding = new Set((out.findings || []).map((f) => f.claim_part));
    $("part-rows").replaceChildren(...(out.parts || []).filter((p) => !withFinding.has(p.claim_part)).map((p) =>
      h("article", { class: "part-row" },
        h("span", { class: "part-id", text: p.claim_part }), verdictTag(p.verdict),
        h("span", { class: "part-text", text: PARTS[p.claim_part] || words(p.claim_part) }),
        h("span", { class: "rule-ref", text: `rule ${p.rule} · ${RULES[p.rule] || ""}` }))));
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
      h("div", {}, h("span", { class: "label", text: `Facts invalidated in the World Model · ${facts.length}` }),
        facts.length ? h("ul", {}, shown.map((id) => h("li", { class: "mono", text: id })),
          facts.length > shown.length ? h("li", { text: `and ${facts.length - shown.length} more` }) : null)
          : h("p", { class: "none", text: "None: no fact of this run was built on that value." })),
      h("div", {}, h("span", { class: "label", text: `Cards checked again by the Evaluator · ${cards.length}` }),
        cards.length ? h("ul", {}, cards.map((id) => h("li", { class: "mono", text: id })))
          : h("p", { class: "none", text: "None: no card rests on that value any more." })));
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
    let runs;
    try {
      runs = await getJSON("/ui/runs");
    } catch (error) {
      list.replaceChildren(h("li", { class: "error", text: `Could not list the runs: ${error.message}` }));
      return;
    }
    if (!runs.length) {
      list.replaceChildren(h("li", { class: "none" }, "No run yet in this server's memory. ", h("a", { href: "#/", text: "Check a claim" }), "."));
      return;
    }
    list.replaceChildren(...runs.map((r) => {
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

  // ---- wiring ------------------------------------------------------------------------------------

  function setFilter(filter) {
    state.filter = filter;
    $("trace").dataset.filter = filter;
    for (const button of document.querySelectorAll(".filters button")) {
      button.setAttribute("aria-pressed", String(button.dataset.filter === filter));
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("run-demo").addEventListener("click", () => startRun(state.setup ? state.setup.default_claim : $("claim").defaultValue, null, null));
    $("claim-form").addEventListener("submit", (event) => { event.preventDefault(); startRun($("claim").value, null, null); });
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
    window.addEventListener("hashchange", () => route(false));
    loadSetup().then(() => {
      if (state.run && state.run.output) renderFile(state.run);
      if (currentView() === "bench") renderBench();
    });
    route(true);
  });
})();
