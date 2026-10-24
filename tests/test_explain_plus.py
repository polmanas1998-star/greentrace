"""Explain-plus: evidence graph, World Model diff, Deep Trace, geo-fence check, Contradiction Radar, letters.

Every feature is computed from a finished run (sample mode, the in-memory World Model, rules only), so
every test here is deterministic. The model is a fake client, in these tests only.
"""
import dataclasses
import io
import json
import math
from collections import Counter
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from shapely import segmentize
from shapely.geometry import box

from app import orchestrator
from app.agents import planner, writer
from app.api import app
from app.checks import geofence
from app.checks.landcover import CELLS_FILE, NO_DATA
from app.explain import deep_trace, exports, graph, letters_i18n, radar, world_diff
from app.memory.fallback import MemoryEngine
from app.pipeline import run_case
from app.receipt import decision_for, verdict_seal
from app.rules import CONTRADICTED, NO_EU_FOREST_MAP, PROVEN, TREE_COVER_NOT_FOREST, UNVERIFIABLE, decide
from app.world.cases import CASES, build_case
from tests.test_rules import evidence

ROOT = Path(__file__).resolve().parents[1]
CLAIM = "100% of our cocoa comes from deforestation-free farms since 2021."
VERDICT_SECTIONS = ("verdict", "finding", "flip", "untested", "action")


def investigate(run_id, case=2, log_dir=None, **inputs):
    run_case({"run_id": run_id, "input": {"claim": CLAIM, **inputs}, "options": {"sample_mode": True, "case": case}},
             stream=io.StringIO(), memory_engine=MemoryEngine(), log_dir=log_dir)
    return orchestrator.RUNS[run_id]


def example_05(run_id):
    request = json.loads((ROOT / "examples" / "05_real_plot_contradicted.json").read_text(encoding="utf-8"))
    request.update(run_id=run_id, options={**request["options"], "sample_mode": True})
    run_case(request, stream=io.StringIO(), memory_engine=MemoryEngine())
    return orchestrator.RUNS[run_id]


@pytest.fixture(scope="module")
def all_cases():
    return {case.number: investigate(f"xp-case-{case.number:02d}", case.number) for case in CASES}


# ---- 4. geo-fence consistency ---------------------------------------------------------------------------

def _band_ha(lat1, lat2, dlon):
    """Exact WGS84 area between two parallels (authalic latitude formula), in hectares."""
    a, f = 6_378_137.0, 1 / 298.257223563
    e2 = f * (2 - f)
    e = math.sqrt(e2)

    def q(lat):
        s = math.sin(math.radians(lat))
        return (1 - e2) * (s / (1 - e2 * s * s) - math.log((1 - e * s) / (1 + e * s)) / (2 * e))
    return a * a / 2 * (q(lat2) - q(lat1)) * math.radians(dlon) / 10_000


@pytest.mark.parametrize("lat", [0, 6, 60])
def test_the_projection_keeps_areas(lat):
    cell = segmentize(box(-6.5, lat, -5.5, lat + 1), 0.01)
    assert geofence.outline_area_ha(cell.wkt) == pytest.approx(_band_ha(lat, lat + 1, 1), rel=1e-5)


def test_a_registry_plot_that_matches_its_outline():
    plot = next(p for p in build_case(2)["plots"] if p["plot_id"] == "P-014")
    result = geofence.registry_plot(plot)
    assert (result["status"], result["matches"], result["finding"]) == ("checked", True, None)
    assert result["outline_ha"] == pytest.approx(3.2, rel=0.01)


def test_a_declared_area_far_from_the_outline_is_a_constat():
    plot = dict(next(p for p in build_case(2)["plots"] if p["plot_id"] == "P-014"), area_ha=9.0)
    result = geofence.registry_plot(plot)
    assert (result["status"], result["matches"]) == ("checked", False)
    assert result["finding"] == "declared area does not match the outline" == geofence.FINDING
    assert result["difference_share"] > geofence.AREA_MISMATCH_SHARE


def test_the_threshold_is_a_named_setting(monkeypatch):
    plot = dict(next(p for p in build_case(2)["plots"] if p["plot_id"] == "P-014"), area_ha=3.6)  # 11 % apart
    assert geofence.registry_plot(plot)["matches"] is False
    monkeypatch.setattr(geofence, "AREA_MISMATCH_SHARE", 0.15)
    assert geofence.registry_plot(plot)["matches"] is True


@pytest.mark.parametrize("plot, reason", [
    ({"plot_id": "P-033", "area_ha": 2.0, "polygon_wkt": ""}, "declared by a point"),
    ({"plot_id": "P-041", "area_ha": 3.0, "polygon_wkt": "POLYGON((0 0, 0 1, 1 1, 0 0))", "declared_by": "analyst"},
     "drawn by the analyst"),
    ({"plot_id": "P-050", "area_ha": None, "polygon_wkt": "POLYGON((0 0, 0 0.01, 0.01 0.01, 0 0))"},
     "no declared area"),
    ({"plot_id": "P-051", "area_ha": "big", "polygon_wkt": "POLYGON((0 0, 0 0.01, 0.01 0.01, 0 0))"},
     "not a positive number"),
])
def test_what_cannot_be_compared_says_why(plot, reason):
    result = geofence.registry_plot(plot)
    assert result["status"] == "not_checked" and reason in result["reason"] and result["finding"] is None


def test_the_constat_is_in_every_investigation_and_changes_no_verdict(all_cases):
    for number, run in all_cases.items():
        constat = run.output["geofence"]
        assert constat["decides_verdict"] is False and constat["findings"] == [], number  # the registry matches
        decision = decision_for(run.evidence, [c["id"] for c in run.cards if c["id"] not in run.reviews])
        assert (run.output["verdict"], run.output["verdict_sha256"]) == (decision["verdict"], verdict_seal(decision))
        assert not any("outline" in f["finding"] for f in run.output["findings"])


def test_a_what_if_on_the_area_raises_the_constat_and_keeps_the_verdict():
    run = investigate("xp-geo-area")
    seal = run.output["verdict_sha256"]
    out = orchestrator.what_if("xp-geo-area", "plot:P-014", "area_ha", 9.0)
    assert [f["plot_id"] for f in out["geofence"]["findings"]] == ["P-014"]
    assert (out["verdict"], out["verdict_sha256"]) == (CONTRADICTED, seal)


def test_check_plots_carries_the_constat_per_plot(monkeypatch):
    monkeypatch.setenv("SAMPLE_MODE", "true")
    ring = [[-6.4650, 6.8270], [-6.4630, 6.8270], [-6.4630, 6.8290], [-6.4650, 6.8290], [-6.4650, 6.8270]]
    features = [{"type": "Feature", "properties": {"plot_id": pid, **props},
                 "geometry": {"type": "Polygon", "coordinates": [ring]}}
                for pid, props in (("declared-9", {"Area": 9.0}), ("declared-ok", {"area_ha": 4.9}), ("silent", {}))]
    out = TestClient(app).post("/api/check-plots", json={"type": "FeatureCollection", "features": features}).json()
    by_id = {p["plot_id"]: p["geofence"] for p in out["plots"]}
    assert by_id["declared-9"]["finding"] == geofence.FINDING
    assert by_id["declared-ok"]["matches"] is True
    assert by_id["silent"]["status"] == "not_checked"


# ---- 3. Deep Trace ---------------------------------------------------------------------------------------

def test_every_sentence_of_the_demo_carries_its_rows():
    out = deep_trace.sentences(investigate("xp-dt-2"))
    first = out["sentences"][0]
    assert first["section"] == "verdict" and first["text"].startswith("The claim does not hold: alert A-221")
    rows = {(e["query_id"], e["agent"]): e for e in first["evidence"]}
    q4 = rows[("Q4", "Investigator")]
    assert q4["engine"] == "sqlite" and q4["sql"].startswith("SELECT alert_id")
    assert q4["rows"] == [r for r in q4["rows"] if r["alert_id"] == "A-221"] and q4["row_count"] == 1
    assert [r["plot_id"] for r in rows[("Q3", "Investigator")]["rows"]] == ["P-014"]
    assert all(e["row_count"] == len(e["rows"]) for e in first["evidence"])
    flip = next(s for s in out["sentences"] if s["section"] == "flip")
    assert {r["alert_id"] for e in flip["evidence"] for r in e["rows"]} == {"A-221"}


def test_no_sentence_of_the_verdict_is_unsupported_in_the_21_cases(all_cases):
    for number, run in all_cases.items():
        out = deep_trace.sentences(run)
        for sentence in out["sentences"]:
            if sentence["section"] in VERDICT_SECTIONS:
                assert sentence["evidence"] and not sentence["unsupported"], (number, sentence["text"])
            else:  # limitations and the human-review line: general statements, flagged as such
                assert sentence["evidence"] == [] and sentence["unsupported"] is True
        assert out["unsupported"] == sum(1 for s in out["sentences"] if s["section"] not in VERDICT_SECTIONS)


def test_a_source_that_never_answered_shows_its_failed_attempt(tmp_path):
    out = deep_trace.sentences(investigate("xp-dt-20", 20, log_dir=tmp_path))
    (attempt,) = out["sentences"][0]["evidence"]
    assert (attempt["query_id"], attempt["status"], attempt["rows"]) == ("Q4", "failed", [])
    assert attempt["reason"] == "source 'alerts' did not answer" and attempt["attempts"] == 2
    assert attempt["sql"].startswith("SELECT alert_id, lat, lon, alert_date, confidence FROM alerts WHERE lat BETWEEN")


def test_an_empty_answer_is_evidence_too():
    out = deep_trace.sentences(investigate("xp-dt-21", 21))
    (answer,) = out["sentences"][0]["evidence"]
    assert (answer["query_id"], answer["row_count"], answer["rows"]) == ("Q4", 0, [])


@pytest.mark.skipif(not CELLS_FILE.exists(), reason="data/reference_cells.csv.gz not built")
def test_a_drawn_plot_rests_on_its_declaration_and_the_cells():
    finding = next(s for s in deep_trace.sentences(example_05("xp-dt-05"))["sentences"] if s["section"] == "finding")
    queries = [e["query_id"] for e in finding["evidence"]]
    assert queries[0] == deep_trace.ANALYST and "Q10" in queries
    cells = next(e for e in finding["evidence"] if e["query_id"] == "Q10")
    assert cells["row_count"] > 0 and len(cells["rows"]) == min(cells["row_count"], deep_trace.MAX_ROWS)


def test_rows_are_capped_but_counted(monkeypatch):
    monkeypatch.setattr(deep_trace, "MAX_ROWS", 2)
    verdict = deep_trace.sentences(investigate("xp-dt-8", 8))["sentences"][0]
    q6 = next(e for e in verdict["evidence"] if e["query_id"] == "Q6")
    assert len(q6["rows"]) == 2 and q6["row_count"] > 2 and q6["truncated"] is True


def test_sentences_are_cut_on_full_stops_not_on_decimals():
    assert deep_trace.split("P-014 covers 3.2 ha. A satellite alert is not legal proof.") == [
        "P-014 covers 3.2 ha.", "A satellite alert is not legal proof."]


# ---- 1. evidence dependency graph -------------------------------------------------------------------------

def _edges(g):
    return {(e["from"], e["relation"], e["to"], e["via"]) for e in g["edges"]}


def test_the_demo_graph_goes_from_sentence_to_rows():
    run = investigate("xp-g-2")
    g = graph.graph(run)
    ids = {n["id"] for n in g["nodes"]}
    assert g["verdict_node"] == "verdict" and all(e["from"] in ids and e["to"] in ids for e in g["edges"])
    assert {n["kind"] for n in g["nodes"]} <= {"fact", "source", "query", "rule", "sentence", "verdict"}
    assert {e["relation"] for e in g["edges"]} <= {"supports", "refutes", "derived_from", "cites"}
    edges = _edges(g)
    assert ("fact:finding:E-01", "derived_from", "fact:alert:A-221", "world_model") in edges
    assert ("fact:finding:E-01", "derived_from", "fact:plot:P-014", "world_model") in edges
    assert ("fact:finding:E-01", "supports", "rule:2", "rules") in edges
    assert ("rule:2", "supports", "verdict", "rules") in edges
    assert ("rule:R5@E-01", "supports", "fact:finding:E-01", "evaluator") in edges
    assert ("sentence:1", "cites", "verdict", "writer") in edges
    alert = next(n for n in g["nodes"] if n["id"] == "fact:alert:A-221")
    assert (alert["source"], alert["query_id"], alert["label"]) == ("alerts", "Q4", "Alert A-221, 2022-03-14, high confidence")
    q4 = next(e["to"] for e in g["edges"] if e["from"] == "fact:alert:A-221" and e["via"] == "rows")
    assert (q4, "derived_from", "source:alerts", "queries") in edges


def test_world_model_edges_are_the_world_models_depends_on():
    run = investigate("xp-g-wm")
    (card,) = run.cards
    (fact,) = [f for f in run.ctx.memory.view("facts", "alert:eudr:A-221") if f["id"] == card["fact"]]
    from_model = {e["to"] for e in graph.graph(run)["edges"] if e["via"] == "world_model"}
    assert from_model == {"fact:" + d.replace(":eudr:", ":") for d in fact["depends_on"]}


def test_a_held_refutation_refutes_the_card():
    run = investigate("xp-g-held")
    card_id = run.cards[0]["id"]
    review = run.reviews[card_id]
    run.reviews[card_id] = {**review, "tried": [dict(review["tried"][0], holds=True)] + review["tried"][1:]}
    edges = _edges(graph.graph(run))
    assert (f"rule:{review['tried'][0]['id']}@{card_id}", "refutes", f"fact:finding:{card_id}", "evaluator") in edges


def test_a_proven_graph_rests_on_every_answer_of_the_plan(all_cases):
    g = graph.graph(all_cases[1])
    assert not [n for n in g["nodes"] if n["id"].startswith("fact:")]
    edges = _edges(g)
    assert ("rule:4", "supports", "verdict", "rules") in edges
    supporting = {e[0] for e in edges if e[1:] == ("supports", "rule:4", "rules")}
    assert {n["query_id"] for n in g["nodes"] if n["id"] in supporting} >= {"Q1", "Q2", "Q3", "Q4", "Q6"}


def test_same_run_same_graph():
    first, second = graph.graph(investigate("xp-g-same")), graph.graph(investigate("xp-g-same"))
    assert first == second


# ---- 2. memory diff ------------------------------------------------------------------------------------------

def test_no_what_if_no_diff():
    out = world_diff.world_diff(investigate("xp-wd-none"))
    assert out["status"] == "no_what_if" and out["changed"] == out["invalidated"] == out["superseded"] == []


def test_the_alert_before_the_cutoff_invalidates_the_card_and_flips():
    investigate("xp-wd-2019")
    what_if = orchestrator.what_if("xp-wd-2019", "alert:A-221", "alert_date", "2019-03-14")["what_if"]
    out = world_diff.world_diff(orchestrator.RUNS["xp-wd-2019"])
    assert out["changed"] == [{"fact": "alert:A-221.alert_date", "before": "2022-03-14", "after": "2019-03-14",
                               "via": "what_if"}]
    assert out["invalidated"] == [{"fact": "alert:A-221.confirmed_alert_in_delivering_plot",
                                   "because_of": "alert:A-221.alert_date",
                                   "world_model_id": what_if["invalidated_facts"][0]}]
    assert [i["world_model_id"] for i in out["invalidated"]] == what_if["invalidated_facts"]
    assert out["superseded"] == []
    assert out["verdict"] == {"before": CONTRADICTED, "after": PROVEN}


def test_a_card_rewritten_after_the_what_if_supersedes_the_old_one():
    investigate("xp-wd-2023")
    orchestrator.what_if("xp-wd-2023", "alert:A-221", "alert_date", "2023-07-01")
    out = world_diff.world_diff(orchestrator.RUNS["xp-wd-2023"])
    (superseded,) = out["superseded"]
    assert superseded["fact"] == "alert:A-221.confirmed_alert_in_delivering_plot"
    assert superseded["by"] == "alert:A-221.confirmed_alert_in_delivering_plot@v2"
    assert "same priority, the newer fact wins" in superseded["reason"] and "2023-07-01" in superseded["reason"]
    assert {"fact": "alert:A-221.date", "before": "2022-03-14", "after": "2023-07-01",
            "via": "world_model"} in out["changed"]
    assert out["verdict"] == {"before": CONTRADICTED, "after": CONTRADICTED}


def test_chained_what_ifs_diff_the_last_one():
    investigate("xp-wd-chain")
    orchestrator.what_if("xp-wd-chain", "alert:A-221", "alert_date", "2019-03-14")
    orchestrator.what_if("xp-wd-chain", "alert:A-221", "alert_date", "2022-05-01")
    out = world_diff.world_diff(orchestrator.RUNS["xp-wd-chain"])
    assert out["changed"][0] == {"fact": "alert:A-221.alert_date", "before": "2019-03-14", "after": "2022-05-01",
                                 "via": "what_if"}
    assert out["verdict"] == {"before": PROVEN, "after": CONTRADICTED}
    assert out["world_model"]["what_ifs"] == 2


# ---- 5. Contradiction Radar ---------------------------------------------------------------------------------

HYPOTHESES = [h for t in ("coverage", "no_deforestation") for h in planner.CATALOGUE[t]]


def test_the_demo_field_report_would_flip_by_flip_py():
    out = radar.radar(investigate("xp-r-2"))
    (doc,) = out["missing_documents"]
    assert (doc["document"], doc["kind"], doc["would_flip"]) == ("a field report on P-014", "document", True)
    assert {"basis": "flip", "outcome": "alert:A-221 alert_date: 2022-03-14 -> 2020-12-31",
            "verdict": PROVEN} in doc["tried"]
    assert [c["id"] for c in out["checkable_now"]] == ["H1", "H2", "H3", "H4", "H5"]
    assert out["needs_document"] == out["out_of_scope"] == []


def test_an_untraced_supplier_needs_its_plot_registry(all_cases):
    out = radar.radar(all_cases[8])
    (doc,) = out["missing_documents"]
    assert doc["document"] == "the plot registry of F-031" and doc["would_flip"] is True
    assert any(t["basis"] == "flip" and t["outcome"].startswith("supplier:F-031 untraced_tonnes") for t in doc["tried"])
    (open_,) = out["needs_document"]
    assert (open_["id"], open_["needs"]) == ("H1", ["the plot registry of F-031"])


def test_a_document_that_cannot_flip_is_said_so():
    e = evidence(2)
    second = next(a for a in build_case(19)["alerts"] if a["alert_id"] == "A-225")  # a second contradiction
    e = dataclasses.replace(e, alerts=e.alerts + [second])
    out = radar.build(CLAIM, e, HYPOTHESES)
    assert out["verdict"] == CONTRADICTED
    assert [(m["document"], m["would_flip"]) for m in out["missing_documents"]] == [
        ("a field report on P-014", False), ("a field report on P-030", False)]


def test_an_unconfirmed_alert_could_flip_both_ways(all_cases):
    (doc,) = radar.radar(all_cases[14])["missing_documents"]
    assert doc["would_flip"] is True and doc["verdict_if"] == [CONTRADICTED, PROVEN]
    assert any(t["basis"] == "confirmed" and t["verdict"] == CONTRADICTED for t in doc["tried"])


def test_documents_the_extractor_read_are_sorted_in_three():
    claim = CLAIM.rstrip(".") + ", and our farmers earn a living income."
    mention = radar.build(claim, evidence(12), HYPOTHESES)
    (ngo,) = [c for c in mention["needs_document"] if c["id"] == "D-M1"]
    assert ngo["needs"] == ["the plot records of F-007 for the year the NGO cites"]
    (outside,) = mention["out_of_scope"]
    assert outside["text"] == "our farmers earn a living income" and outside["status"] == "out_of_scope"
    admission = radar.build(CLAIM, evidence(18), HYPOTHESES)
    (said,) = [c for c in admission["checkable_now"] if c["id"] == "D-A1"]
    assert said["verdict"] == CONTRADICTED and "P-005, P-011, P-017" in said["text"]
    scope = radar.build(CLAIM, evidence(17), HYPOTHESES)
    assert [c["id"] for c in scope["needs_document"]] == ["H4", "D-S1"]
    old = evidence(12, dataclasses.replace(evidence(12).extracted, mentions=(
        {"doc": "ngo_2025", "paragraph_no": 9, "supplier_name": "Agnibi", "year": 2019},)))
    (before,) = [c for c in radar.build(CLAIM, old, HYPOTHESES)["out_of_scope"] if c["id"] == "D-M1"]
    assert "after 2020-12-31" in before["reason"]


def test_same_evidence_same_radar():
    assert radar.build(CLAIM, evidence(10), HYPOTHESES) == radar.build(CLAIM, evidence(10), HYPOTHESES)


# ---- 6. supplier letters in four languages ------------------------------------------------------------------

def _all_letters(all_cases):
    letters = [(l["letter"], tuple(s["name"] for s in run.evidence.suppliers))
               for run in all_cases.values() for l in run.output["supplier_letters"]]
    e = evidence(12)
    cards = [{"id": f"E-{n:02d}", **f} for n, f in enumerate(decide(e)["findings"], start=1)]
    output = {"findings": [writer._finding(c, None) for c in cards],
              "recommended_actions": writer._filled(writer.ACTIONS, cards)}
    letters += [(l["letter"], tuple(s["name"] for s in e.suppliers)) for l in exports.supplier_letters(e, output)]
    return letters


@pytest.mark.parametrize("lang", ["fr", "es", "pt"])
def test_every_letter_keeps_its_ids_dates_numbers_and_names(all_cases, lang):
    letters = _all_letters(all_cases)
    assert len(letters) >= 10
    for english, names in letters:
        translated, untranslated = letters_i18n.from_templates(english, lang)
        assert untranslated == [] and translated != english
        assert letters_i18n.protected(translated, names) == letters_i18n.protected(english, names)
        assert letters_i18n.check(english, translated, names) is None


def _letter(points, requests=()):
    return exports._letter("F-007", "Agnibi", 2025, points, list(requests))


RARE_POINTS = [
    "0.31 ha of 2020 forest lost inside P-041 in 2022, below 0.5 ha and 10 % of the plot while it delivered: "
    "confirm in the field",
    f"P-041: 1.20 ha of tree cover lost in 2021, 2023; {TREE_COVER_NOT_FOREST}",
    f"P-041: 0.80 ha of tree cover lost in 2024 (Hansen/UMD); {NO_EU_FOREST_MAP}",
    "P-041: the reference maps did not answer",
    f"P-041: {NO_DATA}",
    "3.42 ha that the EU map counted as forest in 2020 were lost inside P-042 in 2021, 2022 (Hansen/UMD), while it "
    "kept delivering (threshold 0.50 ha: 0.5 ha or 10 % of the plot)",
]


@pytest.mark.parametrize("lang", ["fr", "es", "pt"])
def test_the_rarer_findings_have_their_templates(lang):
    english = _letter(RARE_POINTS, ["A field report on P-041."])
    translated, untranslated = letters_i18n.from_templates(english, lang)
    assert untranslated == []
    assert letters_i18n.protected(translated) == letters_i18n.protected(english)


def test_a_sentence_no_template_knows_stays_in_english_and_is_listed():
    english = _letter(["P-014 was visited by a unicorn in 2023"])
    translated, untranslated = letters_i18n.from_templates(english, "fr")
    assert untranslated == ["P-014 was visited by a unicorn in 2023."]
    assert "- P-014 was visited by a unicorn in 2023." in translated and "Madame, Monsieur" in translated


class FakeModel:
    """Stands in for app.adapters.llm.ModelGate in these tests: one canned answer per call."""

    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def complete_json(self, agent, system, blocks, schema):
        self.calls.append((agent, system, blocks))
        return self.answer(blocks[0][1]), {"provider": "nvidia", "model": "fake-nemotron", "fallbacks": []}


def test_a_faithful_model_translation_is_kept():
    english = _letter(["Alert A-221 (2022-03-14, high) lies inside P-014, which delivered in 2022 or later"],
                      ["A field report on P-014."])
    model = FakeModel(lambda text: {"letter": letters_i18n.from_templates(text, "fr")[0].replace("PROJET", "Projet")})
    out = letters_i18n.translate(english, "fr", model, ("Agnibi",))
    assert (out["translated_by"], out["model"], out["model_rejected"]) == ("model", "fake-nemotron", None)
    assert out["letter"].startswith("Projet") and model.calls[0][0] == "Writer" and "French" in model.calls[0][1]


@pytest.mark.parametrize("damage, problem", [
    (lambda t: t.replace("2022-03-14", "14/03/2022"), "2022-03-14"),
    (lambda t: t.replace("P-014", "P-14"), "P-014"),
    (lambda t: t.replace("Agnibi (F-007)", "el proveedor (F-007)"), "Agnibi"),
    (lambda t: t + "\n2.4 t", "added"),  # a number the English letter does not have
    (None, "untranslated"),
])
def test_a_model_that_changes_a_token_is_replaced_by_the_templates(damage, problem):
    english = _letter(["Alert A-221 (2022-03-14, high) lies inside P-014, which delivered in 2022 or later"])
    good = letters_i18n.from_templates(english, "es")[0]
    model = FakeModel(lambda text: {"letter": damage(good) if damage else text})
    out = letters_i18n.translate(english, "es", model, ("Agnibi",))
    assert out["translated_by"] == "templates" and problem in out["model_rejected"]
    assert out["letter"] == good


def test_no_model_answer_means_templates():
    english = _letter(["P-021 covers 6.0 ha but is declared by a point"], ["The polygon of P-021."])
    out = letters_i18n.translate(english, "pt", FakeModel(lambda text: None))
    assert out["translated_by"] == "templates" and "no model answer" in out["model_rejected"]
    assert "A parcela P-021 abrange 6.0 ha mas está declarada por um ponto." in out["letter"]


# ---- the routes ---------------------------------------------------------------------------------------------

def test_the_routes_round_trip(monkeypatch):
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("WORLD_MODEL_URL", "http://127.0.0.1:9")  # nothing listens: in-memory fallback, fast
    client = TestClient(app)
    assert client.post("/run", json={"run_id": "xp-api", "input": {"claim": CLAIM},
                                     "options": {"case": 2}}).json()["verdict"] == CONTRADICTED
    assert client.get("/runs/xp-api/graph").json()["verdict_node"] == "verdict"
    assert client.get("/runs/xp-api/sentences").json()["sentences"][0]["evidence"]
    assert client.get("/runs/xp-api/radar").json()["missing_documents"][0]["would_flip"] is True
    assert client.get("/runs/xp-api/world_diff").json()["status"] == "no_what_if"
    letter = client.get("/runs/xp-api/letter", params={"lang": "fr"}).json()
    (fr,) = letter["letters"]
    assert (letter["translated_by"], fr["supplier_id"]) == ("templates", "F-007")
    assert "2022-03-14" in fr["letter"] and "Un rapport de terrain sur la parcelle P-014." in fr["letter"]
    assert client.get("/runs/xp-api/letter", params={"lang": "en"}).json()["letters"][0]["letter"] == fr["letter_en"]
    assert client.get("/runs/xp-api/letter", params={"lang": "de"}).status_code == 422
    assert client.get("/runs/xp-api/letter", params={"lang": "fr", "supplier": "F-999"}).status_code == 404
    client.post("/runs/xp-api/what_if", json={"entity": "alert:A-221", "field": "alert_date", "value": "2019-03-14"})
    assert client.get("/runs/xp-api/world_diff").json()["verdict"] == {"before": CONTRADICTED, "after": PROVEN}
    for path in ("graph", "world_diff", "sentences", "radar", "letter"):
        assert client.get(f"/runs/never-ran/{path}").status_code == 404
