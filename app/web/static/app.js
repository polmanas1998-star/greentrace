/* GreenTrace interface: home, live trace, evidence file. Plain JS, no build step, no network but this server.

Everything shown comes from the server: the trace lines (GET /runs/{id}/events), the run's output, and
/ui/setup. Text from a run is always set with textContent, never parsed as HTML: the reports a run reads
can hold anything (trap case 19 hides an instruction in an NGO text). */
(() => {
  "use strict";

  // What each claim part and each rule number means, in app/rules.py's words. The run's rules_version is
  // shown next to them in the evidence file.
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
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const state = { setup: null, run: null, filter: "all" };
  const $ = (id) => document.getElementById(id);

  // ---- small DOM helpers -----------------------------------------------------------------------

  function h(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (name === "class") node.className = value;
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

  const verdictTag = (verdict) => h("span", { class: "tag tag-verdict", "data-verdict": verdict, text: verdict });
  const chip = (text, kind) => h("span", { class: "chip" + (kind ? ` chip-${kind}` : ""), text });
  const words = (code) => String(code).replace(/_/g, " ");
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + "s"}`;
  const capital = (text) => (text ? text[0].toUpperCase() + text.slice(1) : "");
  const shortHash = (hash) => (hash ? `${hash.slice(0, 8)}…${hash.slice(-8)}` : "");

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

  // ---- routing: #/ home, #/runs/<id> live trace, #/runs/<id>/file evidence file ------------------

  const VIEWS = ["home", "live", "file", "missing"];

  function show(view) {
    for (const name of VIEWS) $(`view-${name}`).hidden = name !== view;
    const run = state.run;
    const onRun = view === "live" || view === "file";
    document.querySelector(".run-nav").hidden = !onRun;
    if (onRun && run) {
      for (const name of ["live", "file"]) {
        const link = $(`nav-${name}`);
        link.href = `#/runs/${encodeURIComponent(run.id)}` + (name === "file" ? "/file" : "");
        if (name === view) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
      }
    }
    renderMode(view);
    document.title = view === "file" && run && run.output && run.output.verdict
      ? `${capital(run.output.verdict.toLowerCase())} · Evidence file · GreenTrace`
      : view === "live" ? "Live trace · GreenTrace" : "GreenTrace";
  }

  function route(initial) {
    const [first, id, sub] = location.hash.replace(/^#\/?/, "").split("/");
    if (first === "runs" && id) {
      openRun(decodeURIComponent(id));
      if (sub === "file") {
        if (state.run.output) renderFile(state.run); else state.run.wantFile = true;
        show(state.run.output ? "file" : "live");
      } else {
        show("live");
      }
    } else {
      show("home");
    }
    if (!initial) $("main").focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }

  function renderMode(view) {
    const badge = $("mode-badge");
    const run = state.run;
    let text = "";
    if ((view === "live" || view === "file") && run && run.mode) {
      text = `${capital(run.mode)} mode` + (run.degraded ? " · degraded: rules only, no model" : "");
    } else if (view === "home" && state.setup) {
      text = state.setup.default_mode === "sample" ? "Sample mode · no network" : "Live mode";
    }
    badge.textContent = text;
    badge.hidden = !text;
  }

  // ---- home --------------------------------------------------------------------------------------

  async function loadSetup() {
    try {
      state.setup = await getJSON("/ui/setup");
    } catch (error) {
      $("demo-hint").textContent = `Could not read /ui/setup (${error.message}).`;
      return;
    }
    const setup = state.setup;
    $("demo-hint").textContent = setup.default_mode === "sample"
      ? `Sample mode: case ${setup.demo_case} of the bench, synthetic world, no network`
      : "Live mode: data through Zetaris, the model chain from the environment";
    $("bench-label").textContent = `Reliability bench · ${plural(setup.cases.length, "trap case")}`;
    const select = $("bench-case");
    select.replaceChildren(...setup.cases.map((c) =>
      h("option", { value: c.case, selected: c.case === setup.demo_case, text: `Case ${c.case} · ${c.change}` })));
    renderMode(VIEWS.find((name) => !$(`view-${name}`).hidden));
  }

  async function startRun(options, caseNo) {
    const claim = $("claim").value.trim();
    const error = $("claim-error");
    error.hidden = true;
    if (!claim) {
      error.textContent = "Write the promise to check first.";
      error.hidden = false;
      $("claim").focus();
      return;
    }
    const buttons = document.querySelectorAll("#view-home button[type=submit]");
    buttons.forEach((b) => { b.disabled = true; });
    try {
      const body = { input: { claim } };
      if (options) body.options = options;
      const { run_id: id } = await getJSON("/runs", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      remember(`run:${id}`, { claim, caseNo });
      state.run = newRun(id, { fresh: true });
      location.hash = `#/runs/${encodeURIComponent(id)}`;
    } catch (failure) {
      error.textContent = `The run did not start: ${failure.message}`;
      error.hidden = false;
    } finally {
      buttons.forEach((b) => { b.disabled = false; });
    }
  }

  // ---- a run: its trace lines, then its output -------------------------------------------------

  function newRun(id, { fresh }) {
    const meta = recall(`run:${id}`) || {};
    return {
      id, fresh, claim: meta.claim || "", caseNo: meta.caseNo || null,
      lines: [], pending: [], timer: null, source: null,
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
    run.source = null;
    run.timer = null;
  }

  function subscribe(run) {
    const source = new EventSource(`/runs/${encodeURIComponent(run.id)}/events`);
    run.source = source;
    source.addEventListener("trace", (event) => { run.pending.push(event.data); pump(run); });
    source.addEventListener("result", (event) => {
      source.close();
      run.output = JSON.parse(event.data);
      run.mode = run.output.mode;
      run.degraded = run.output.degraded;
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

  async function fromFinishedRun(run) {
    // No live stream for this id (a run made by POST /run, or a server restart): the output may still be here.
    try {
      run.output = await getJSON(`/runs/${encodeURIComponent(run.id)}`);
      run.mode = run.output.mode;
      run.degraded = run.output.degraded;
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

  // ---- live view ---------------------------------------------------------------------------------

  function resetLive(run) {
    $("trace").replaceChildren();
    $("live-result").hidden = true;
    $("live-error").hidden = true;
    $("live-title").textContent = run.claim ? `“${run.claim}”` : `Run ${run.id}`;
    setStatus("running", "Starting");
    $("live-lead").textContent = "Starting the run. Each line below is one step, as the agents write it to the trace.";
    renderSub(run);
    renderStats(run);
    $("trace-foot").textContent = `One line per step, as written to logs/${run.id}.jsonl. Times are the trace's own` +
      (run.fresh && !reducedMotion ? "; lines that arrive together are shown at reading pace." : ".");
  }

  function renderSub(run) {
    const bits = [`Run ${run.id}`];
    if (run.caseNo) bits.push(`bench case ${run.caseNo}`);
    if (run.mode) bits.push(`${run.mode} mode`);
    $("live-sub").textContent = bits.join(" · ");
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
      renderSub(run);
      renderMode("live");
    }
    if (step.action === "extract_claims" && !run.claim) {
      const promise = /^promise: (.*)$/.exec(step.input_summary || "");
      if (promise) {
        run.claim = promise[1];
        $("live-title").textContent = `“${run.claim}”`;
      }
    }
    $("trace").append(stepRow(step, run.lines[0]));
    renderStats(run);
    const elapsed = offset(step, run.lines[0]);
    setStatus("running", `Investigating · ${plural(run.lines.length, "step")}` + (elapsed === null ? "" : ` · ${clock(elapsed)}`));
    $("live-lead").textContent = progress(count(run.lines));
  }

  function offset(step, first) {
    const now = Date.parse(step.timestamp);
    const start = Date.parse(first && first.timestamp);
    return Number.isNaN(now) || Number.isNaN(start) ? null : Math.max(0, now - start);
  }

  function clock(ms) {
    const s = ms / 1000;
    if (s < 10) return `${s.toFixed(3)} s`;
    if (s < 60) return `${s.toFixed(1)} s`;
    return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  }

  const isWorldModel = (s) => String(s.model || "").startsWith("world-model");
  const isSponsor = (s) => ["query_zetaris", "model_call", "list_models", "provider_fallback"].includes(s.action) || isWorldModel(s);

  function stepRow(s, first) {
    const meta = [h("span", { class: "step-action", text: s.action })];
    if (s.target_agent) meta.push(h("span", { class: "step-arrow", text: `→ ${s.target_agent}` }));
    let text = s.output_summary || "";
    let flag = null;
    let key = false;
    let mono = false;

    if (s.action === "query_zetaris") {
      const query = /^(Q\d+)/.exec(s.input_summary || "");
      meta.push(chip(`${s.provider} · ${query ? query[1] : "query"} · ${plural(s.rows ?? 0, "row")}`, "data"));
      text = s.input_summary || text;
      mono = true;
    } else if (s.action === "model_call" || s.action === "list_models") {
      meta.push(chip(`${s.provider} · ${s.model || "no model"}`, "model"));
    } else if (isWorldModel(s)) {
      meta.push(chip(`World Model · ${s.provider} · ${s.model}`));
    } else if (s.provider) {
      meta.push(chip(s.provider));
    }

    if (s.action === "request_revision") {
      key = true;
      flag = "Sent back to the Planner";
    } else if (s.action === "revise_plan") {
      key = true;
      flag = "Plan revised";
    } else if (s.action === "refute_hypothesis") {
      const tested = /^[^:]*:\s*(R\d+)\s*'(.*)'$/.exec(s.input_summary || "");
      const result = /^(HOLDS|rejected):\s*(.*)$/.exec(s.output_summary || "");
      if (tested) meta.push(chip(tested[1]));
      if (result) {
        const holds = result[1] === "HOLDS";
        meta.push(h("span", { class: holds ? "chip chip-signal" : "chip", text: holds ? "HOLDS" : "rejected" }));
        key = holds;
        flag = holds ? "This doubt holds: the card is blocked" : null;
        text = tested ? `“${tested[2]}”: ${result[2]}` : result[2];
      }
    }
    if (s.status && s.status !== "success") meta.push(h("span", { class: "chip step-status", text: s.status }));
    if (s.retry_count) meta.push(chip(`retry ${s.retry_count}`));
    if (s.confidence !== null && s.confidence !== undefined) meta.push(chip(`confidence ${s.confidence}`));

    const since = offset(s, first);
    return h("li", {
      class: "step" + (key ? " step-key" : ""), "data-agent": s.agent_name, "data-status": s.status,
      "data-sponsor": isSponsor(s),
    },
    h("span", { class: "step-time", text: since === null ? "" : `+${clock(since)}` }),
    h("span", { class: "agent-dot", "data-agent": s.agent_name, "aria-hidden": "true" }),
    h("span", { class: "step-agent", text: s.agent_name }),
    h("div", { class: "step-body" },
      h("div", { class: "step-meta" }, meta),
      flag && h("span", { class: "step-flag", text: flag }),
      h("span", { class: "step-text" + (mono ? " mono" : ""), title: s.input_summary || null, text })));
  }

  function count(lines) {
    const c = { steps: lines.length, queries: 0, rows: 0, data: new Set(), models: 0, modelRoutes: new Set(),
      tested: 0, held: 0, sentBack: 0, memory: 0, memoryEngine: null };
    for (const s of lines) {
      if (s.action === "query_zetaris") { c.queries += 1; c.rows += s.rows || 0; c.data.add(s.provider); }
      if (s.action === "model_call") { c.models += 1; c.modelRoutes.add(s.provider); }
      if (s.action === "refute_hypothesis") { c.tested += 1; if (/^HOLDS\b/.test(s.output_summary || "")) c.held += 1; }
      if (s.action === "request_revision") c.sentBack += 1;
      if (isWorldModel(s)) { c.memoryEngine = s.model; if (s.action !== "connect_world_model") c.memory += 1; }
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
    renderSub(run);
    renderMode(document.querySelector(".view:not([hidden])")?.id.replace("view-", ""));
    if (run.error) {
      setStatus("failed", "Stopped");
      $("live-lead").textContent = "The run did not finish. Nothing below is a verdict.";
      $("live-error").textContent = `The run stopped: ${run.error}`;
      $("live-error").hidden = false;
      return;
    }
    const out = run.output;
    setStatus("done", `Finished · ${plural(run.lines.length, "step")}` +
      (typeof out.execution_time_seconds === "number" ? ` · ${out.execution_time_seconds} s` : ""));
    $("live-lead").textContent = out.summary || "";
    $("live-verdict").replaceChildren(out.verdict ? verdictTag(out.verdict)
      : h("span", { class: "tag tag-quiet", text: "TOO VAGUE" }));
    $("live-open-file").href = `#/runs/${encodeURIComponent(run.id)}/file`;
    $("live-result").hidden = false;
    if (!run.lines.length) {
      $("trace").replaceChildren(h("li", { class: "trace-empty",
        text: "This run was not started from this page, so its trace was not streamed here. " +
          `It is in ${out.log_file || "logs/"} on the server.` }));
    }
    renderFile(run);
    if (run.wantFile) {
      run.wantFile = false;
      show("file");
    }
  }

  // ---- evidence file -----------------------------------------------------------------------------

  function renderFile(run) {
    const out = run.output;
    if (!out) return;
    const vague = out.status === "too_vague";
    const verdict = out.verdict || null;

    const banner = $("file-banner");
    banner.hidden = !run.changes.length;
    if (run.changes.length) {
      banner.textContent = `This evidence file includes ${plural(run.changes.length, "change")} made with “Try to fool it”: ` +
        run.changes.map((w) => `${w.change.entity} ${w.change.field} = ${w.change.value}`).join("; ") +
        `. The run keeps ${run.changes.length === 1 ? "it" : "them"}; the seal below is for the changed evidence.`;
    }

    const tags = [verdict ? verdictTag(verdict) : h("span", { class: "tag tag-quiet", text: "TOO VAGUE" })];
    if (out.human_review) tags.push(h("span", { class: "tag tag-warn", text: "HUMAN REVIEW REQUIRED" }));
    tags.push(h("span", { class: "tag tag-quiet", text: out.mode === "sample" ? "Sample mode · synthetic world, no network" : `${capital(out.mode)} mode` }));
    if (out.degraded) tags.push(h("span", { class: "tag tag-warn", text: "Degraded · rules only, no model answered" }));
    const bench = run.caseNo && state.setup && state.setup.cases.find((c) => c.case === run.caseNo);
    if (bench && !run.changes.length) {
      tags.push(h("span", { class: "tag tag-quiet", text: `Bench case ${bench.case} · expected ${bench.expected}` +
        (verdict === bench.expected ? " · match" : " · miss") }));
    }
    $("verdict-tags").replaceChildren(...tags);

    const word = $("verdict-word");
    word.textContent = verdict ? `${capital(verdict.toLowerCase())}.` : "Too vague.";
    if (verdict) word.dataset.verdict = verdict; else delete word.dataset.verdict;
    $("file-summary").textContent = out.summary || "";
    const untested = out.untested_factors || [];
    $("file-untested").textContent = untested.length ? `Not tested: ${untested.join(" ")}` : "";
    $("file-untested").hidden = !untested.length;

    renderSeal(run);
    $("parts-section").hidden = vague;
    $("findings-section").hidden = vague;
    $("flip").hidden = vague;
    $("fool").hidden = vague;
    if (!vague) {
      renderParts(out);
      renderFindings(out);
      renderFlip(run);
      renderFool(run);
    }
    renderNotes(out);
    $("review-text").textContent = out.human_review || "Not stated in this output.";
    $("file-foot").replaceChildren(
      h("span", { text: `Every finding names the rows it rests on · trace in ${out.log_file || "logs/"}` }),
      h("span", { text: `Run ${out.run_id}` }));
    if (!$("view-file").hidden) show("file");
  }

  function renderSeal(run) {
    const out = run.output;
    const hash = out.verdict_sha256;
    $("seal-hash").textContent = hash ? shortHash(hash) : "no verdict to seal";
    $("seal-hash").title = hash || "";
    $("seal-copy").hidden = !hash;
    $("seal-note").textContent = "";
    const fp = out.fingerprints || {};
    const facts = [
      ["rules", fp.rules_version], ["run time", typeof out.execution_time_seconds === "number" ? `${out.execution_time_seconds} s` : null],
      ["input", shortHash(fp.input_sha256)], ["data", shortHash(fp.data_sha256)], ["rules file", shortHash(fp.rules_sha256)],
    ].filter(([, value]) => value);
    $("seal-facts").replaceChildren(...facts.flatMap(([label, value]) => [h("dt", { text: label }), h("dd", { text: value })]));
  }

  function renderParts(out) {
    const parts = out.parts || [];
    $("parts-title").textContent = `The claim, in ${plural(parts.length, "part")}`;
    $("parts").replaceChildren(...parts.map((p) => h("article", { class: "part" },
      h("div", { class: "part-head" }, h("span", { class: "part-name", text: p.claim_part }), verdictTag(p.verdict)),
      h("div", { class: "part-what", text: PARTS[p.claim_part] || words(p.claim_part) }),
      h("div", { class: "part-rule", text: `Rule ${p.rule} · ${RULES[p.rule] || "see app/rules.py"}` }),
      p.codes && p.codes.length ? h("div", { class: "part-codes" }, p.codes.map((code) => chip(words(code)))) : null)));
  }

  function checkedBy(list) {
    const names = { investigator: "Investigator", evaluator: "Evaluator", "evaluator:own_query": "Evaluator, with its own queries" };
    return (list || []).map((who) => names[who] || who).join(", then ");
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
          h("div", { class: "finding-head" }, h("span", { class: "part-name", text: f.claim_part }), verdictTag(f.verdict)),
          h("h3", { class: "finding-text", text: `${capital(f.finding)}.` }),
          h("p", { class: "finding-rule", text: `Rule ${f.rule} · ${RULES[f.rule] || "see app/rules.py"}` })),
        h("div", { class: "source" },
          h("h4", { text: "Where this comes from" }),
          h("div", { class: "source-block" }, h("span", { text: "Evidence" }),
            h("ul", { class: "source-list" }, (f.evidence || []).map((e) => h("li", {}, chip(`${e.source} · ${e.id}`, "data"))))),
          h("div", { class: "source-block" }, h("span", { text: "Checked by" }),
            h("div", { text: checkedBy(f.checked_by) || "not stated" })),
          h("div", { class: "source-block" }, h("span", { text: `Refutations tried · ${tried.length}` }),
            tried.length
              ? h("ul", { class: "refutations" }, tried.map((t) => h("li", {},
                h("span", { class: "ref-id", text: t.id }),
                h("span", { class: t.result === "held" ? "ref-held" : null, text: t.result }),
                h("span", { text: t.detail }))))
              : h("span", { class: "none", text: "None tried on this finding." }))));
    }));
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
    $("flip-tried").replaceChildren(...tried.map((t) => {
      const c = t.change;
      const from = Array.isArray(c.from) ? c.from.join(", ") : c.from;
      const to = Array.isArray(c.to) ? c.to.join(", ") : c.to;
      return h("li", {},
        h("span", { class: "mono", text: `${c.entity} ${c.field}` }),
        h("span", { text: `${from} → ${to}` }),
        verdictTag(t.verdict),
        editable(c.entity, c.field) ? h("button", {
          class: "btn btn-small", type: "button", text: "Try it",
          onclick: () => fillFool(c.entity, c.field, c.to),
        }) : null);
    }));
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
    $("fool-value").focus();
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
          : h("p", { class: "none", text: "None." })));
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
    button.disabled = true;
    try {
      const out = await getJSON(`/runs/${encodeURIComponent(run.id)}/what_if`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ entity, field, value }),
      });
      if (run !== state.run) return;
      run.output = out;
      run.changes.push(out.what_if);
      run.values[`${entity} ${field}`] = value;
      renderFile(run);
      $("fool-result").scrollIntoView({ block: "nearest", behavior: reducedMotion ? "auto" : "smooth" });
    } catch (failure) {
      error.textContent = `Not applied: ${failure.message}`;
      error.hidden = false;
    } finally {
      button.disabled = false;
    }
  }

  // ---- wiring ------------------------------------------------------------------------------------

  async function copySeal() {
    const hash = state.run && state.run.output && state.run.output.verdict_sha256;
    if (!hash) return;
    try {
      await navigator.clipboard.writeText(hash);
      $("seal-note").textContent = "Copied the full verdict_sha256.";
    } catch {
      const range = document.createRange();
      $("seal-hash").textContent = hash;
      range.selectNodeContents($("seal-hash"));
      getSelection().removeAllRanges();
      getSelection().addRange(range);
      $("seal-note").textContent = "Clipboard not available: the full hash is selected, copy it with Ctrl+C.";
    }
  }

  function downloadOutput() {
    const out = state.run && state.run.output;
    if (!out) return;
    const blob = new Blob([JSON.stringify(out, null, 2) + "\n"], { type: "application/json" });
    const link = h("a", { href: URL.createObjectURL(blob), download: `greentrace-${out.run_id}.json` });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  }

  function setFilter(filter) {
    state.filter = filter;
    $("trace").dataset.filter = filter;
    for (const button of document.querySelectorAll(".filters button")) {
      button.setAttribute("aria-pressed", String(button.dataset.filter === filter));
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("claim-form").addEventListener("submit", (event) => { event.preventDefault(); startRun(null, null); });
    $("bench-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const caseNo = Number($("bench-case").value);
      startRun({ sample_mode: true, case: caseNo }, caseNo);
    });
    for (const button of document.querySelectorAll(".filters button")) {
      button.addEventListener("click", () => setFilter(button.dataset.filter));
    }
    $("fool-form").addEventListener("submit", submitFool);
    $("fool-entity").addEventListener("change", () => renderFoolFields(state.run));
    $("fool-field").addEventListener("change", () => renderFoolValue(state.run));
    $("seal-copy").addEventListener("click", copySeal);
    $("download-json").addEventListener("click", downloadOutput);
    window.addEventListener("hashchange", () => route(false));
    loadSetup().then(() => { if (state.run && state.run.output) renderFile(state.run); });
    route(true);
  });
})();
