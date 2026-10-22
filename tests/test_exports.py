"""What the analyst takes away: plot file, supplier letters, findings table, evidence file."""
import csv
import io
import json
import re

import pytest

from app.explain.exports import NO_ISSUE, eudr_geojson, evidence_markdown, findings_csv
from app.memory.fallback import MemoryEngine
from app.pipeline import run_case
from app.rules import decide
from app.world.cases import CASES, build_case
from tests.test_rules import evidence

CLAIM = "100% of our cocoa comes from deforestation-free farms since 2021."
NEVER = re.compile(r"declarable|compliant|compliance with|due diligence statement|accus|illegal|fraud|violat", re.I)


def run(case):
    return run_case({"run_id": f"exp-{case:02d}", "input": {"claim": CLAIM},
                     "options": {"sample_mode": True, "case": case}},
                    stream=io.StringIO(), memory_engine=MemoryEngine())


def delivering(case):
    world = build_case(case)
    year = world["company"][0]["fiscal_year"]
    known = {p["plot_id"] for p in world["plots"]}
    return sorted({d["plot_id"] for d in world["deliveries"]
                   if d["plot_id"] in known and d["year"] == year and d["tonnes"] > 0})


# ---- the plot file ------------------------------------------------------------------------------

def test_case_2_keeps_every_delivering_plot_but_p014():
    export = eudr_geojson(evidence(2), decide(evidence(2)))
    features = export["plots_no_issue_found"]["features"]
    assert [f["properties"]["plot_id"] for f in features] == [p for p in delivering(2) if p != "P-014"]
    assert export["excluded"] == [{"plot": "P-014", "reason": "alert A-221 (2022-03-14, high) lies inside P-014, "
                                                             "which delivered in 2022 or later"}]
    assert {f["properties"]["status"] for f in features} == {NO_ISSUE}


def test_geometry_is_geojson_with_six_decimals():
    features = {f["properties"]["plot_id"]: f for f in
                eudr_geojson(evidence(1), decide(evidence(1)))["plots_no_issue_found"]["features"]}
    polygon, point = features["P-014"]["geometry"], features["P-006"]["geometry"]  # P-006 is point-declared
    assert polygon["type"] == "Polygon" and polygon["coordinates"][0][0] == polygon["coordinates"][0][-1]
    assert point["type"] == "Point" and len(point["coordinates"]) == 2
    numbers = re.findall(r"-?\d+\.\d+", json.dumps(polygon))
    assert numbers and all(len(n.split(".")[1]) <= 6 for n in numbers)


def test_no_plot_is_cleared_when_the_alerts_could_not_be_read():
    export = eudr_geojson(evidence(20), decide(evidence(20)))
    assert export["plots_no_issue_found"]["features"] == []
    assert [e["plot"] for e in export["excluded"]] == delivering(20)
    assert all(e["reason"].startswith("not checked for alerts") for e in export["excluded"])


def test_plots_the_company_admits_are_left_out():
    export = eudr_geojson(evidence(18), decide(evidence(18)))
    left_out = {e["plot"]: e["reason"] for e in export["excluded"]}
    assert {"P-005", "P-011", "P-017"} <= set(left_out)
    assert "named in the company's own report (rse_2025 annex" in left_out["P-005"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c.number:02d}_{c.slug}")
def test_no_export_ever_certifies_or_accuses(case):
    out = run(case.number)
    text = json.dumps(out["eudr_export"]) + json.dumps(out["supplier_letters"])
    assert not NEVER.search(text), NEVER.search(text)


# ---- supplier letters ---------------------------------------------------------------------------

def test_case_2_drafts_one_letter_to_the_supplier_of_p014():
    (letter,) = run(2)["supplier_letters"]
    assert (letter["supplier_id"], letter["name"]) == ("F-007", "Agnibi")
    text = letter["letter"]
    assert text.startswith("DRAFT prepared by GreenTrace") and "[date]" in text and "Dear Agnibi (F-007)" in text
    assert "our checks found" in text and "- Alert A-221 (2022-03-14, high) lies inside P-014" in text
    assert "- A field report on P-014" in text
    assert not re.search(r"\bhold\b", text)  # "hold its deliveries" is an internal step: never sent


def test_case_8_asks_the_untraced_supplier_for_its_plots():
    (letter,) = run(8)["supplier_letters"]
    assert letter["supplier_id"] == "F-031"
    assert "are not linked to any plot in our registry" in letter["letter"]
    assert "- Your plot registry" in letter["letter"]


def test_no_finding_no_letter():
    assert run(1)["supplier_letters"] == []


# ---- findings table and evidence file ------------------------------------------------------------

def test_findings_csv_opens_as_a_table():
    rows = list(csv.DictReader(io.StringIO(findings_csv(run(2)))))
    assert [r["row"] for r in rows] == ["part"] * 4 + ["finding"]
    finding = rows[-1]
    assert (finding["verdict"], finding["sources"], finding["refutations"]) == (
        "CONTRADICTED", "alert:A-221; plot:P-014", "R3 rejected; R4 rejected; R5 rejected")


def test_evidence_file_carries_the_flip_and_the_fingerprints():
    out = run(2)
    text = evidence_markdown(out)
    assert text.startswith("# GreenTrace evidence file: CONTRADICTED")
    assert "The claim would be PROVEN if alert A-221 were dated 2020-12-31." in text
    assert f"verdict_sha256: `{out['verdict_sha256']}`" in text and out["fingerprints"]["data_sha256"] in text
    assert "- alert:A-221 alert_date: 2022-03-14 -> 2020-12-31 gives PROVEN" in text
    assert evidence_markdown(out) == text


def test_the_run_output_carries_the_exports():
    out = run(2)
    assert set(out["eudr_export"]) == {"plots_no_issue_found", "excluded"}
    assert out["eudr_export"]["excluded"][0]["plot"] == "P-014"
    assert [letter["supplier_id"] for letter in out["supplier_letters"]] == ["F-007"]
