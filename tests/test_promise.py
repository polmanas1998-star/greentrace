"""The analyst's promise and documents, judged with their own plots (kit 2.1, custom sources)."""
import json

import pytest
from fastapi.testclient import TestClient
from shapely import wkt

import verify
from app import plotcheck, userdocs
from app.api import app
from app.checks.landcover import CELLS_FILE
from app.rules import CONTRADICTED, PROVEN, UNVERIFIABLE
from app.web.features import map_engine
from app.world import geo

pytestmark = pytest.mark.skipif(not CELLS_FILE.exists(), reason="data/reference_cells.csv.gz not built")


def square(plot_id, lat, lon, area, supplier):
    ring = [[x, y] for x, y in wkt.loads(geo.square_wkt(lat, lon, area)).exterior.coords]
    return {"type": "Feature", "properties": {"plot_id": plot_id, "supplier": supplier},
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


P042 = square("P-042", 5.7300, -6.5220, 10.0, "Coop Nord")
CLEAR = square("CLEAR", 5.6600, -6.7600, 2.0, "Coop Sud")
FULL = "100% of our cocoa comes from deforestation-free farms since 2021."
PLAIN = "Our cocoa comes from deforestation-free farms since 2021."
REPORT = ("Sustainability report 2025.\n\n"
          "Not one hectare of forest was cleared in 2023 by our member farms.\n\n"
          "In 2022 we cleared 3 ha of forest on the Nord block to plant new cocoa trees.")


def run(features, promise, documents=(), batch_id="pr-test", model=None):
    return plotcheck.check({"type": "FeatureCollection", "features": list(features)}, batch_id,
                           map_engine(sample=True), year=2026, log=lambda record: None,
                           promise=promise, documents=list(documents), model=model)


def supplier_doc(text=REPORT):
    return {"name": "Coop Nord report 2025", "issuer": "supplier", "text": text}


# ---- rules alone (sample mode: no model) ---------------------------------------------------------------------

def test_the_supplier_admits_clearing_and_its_plot_shows_it():
    promise = run([P042, CLEAR], FULL, [supplier_doc()])["promise"]
    assert promise["verdict"] == CONTRADICTED
    assert {p["claim_part"]: p["verdict"] for p in promise["parts"]} == {
        "coverage": UNVERIFIABLE, "no_deforestation": CONTRADICTED, "scope": PROVEN}
    codes = {f["code"] for f in promise["findings"]}
    assert {"forest_loss_in_delivering_plot", "company_admission", "volume_not_checked"} <= codes
    admission = next(f for f in promise["findings"] if f["code"] == "company_admission")
    assert admission["refs"] == ["doc:coop_nord_report_2025:main:3"]  # paragraph 3, not the denial of paragraph 2


def test_a_denial_is_never_an_admission():
    promise = run([CLEAR], PLAIN, [supplier_doc("Not one hectare of forest was cleared in 2023.")])["promise"]
    assert promise["verdict"] == PROVEN and promise["findings"] == []
    assert promise["sentence"] == ("The promise holds on every point checked: no plot and no document "
                                   "stands against it.")


def test_100_percent_cannot_be_proven_without_volumes():
    promise = run([CLEAR], FULL)["promise"]
    assert promise["verdict"] == UNVERIFIABLE
    assert promise["sentence"] == ("The plot file holds no volumes: that every tonne comes from these plots "
                                   "is not checked.")


def test_a_third_party_naming_a_clear_supplier_is_a_conflict_not_a_contradiction():
    ngo = {"name": "NGO field note", "issuer": "third_party",
           "text": "Witnesses say Coop Sud cleared forest near the river in 2022."}
    promise = run([CLEAR], PLAIN, [ngo])["promise"]
    assert promise["verdict"] == UNVERIFIABLE
    (finding,) = promise["findings"]
    assert finding["code"] == "sources_in_conflict" and finding["refs"][0] == "supplier:Coop Sud"


def test_a_third_party_naming_an_already_contradicted_supplier_adds_no_conflict():
    ngo = {"name": "NGO", "issuer": "third_party", "text": "Coop Nord cleared forest in 2022."}
    promise = run([P042], PLAIN, [ngo])["promise"]
    assert "sources_in_conflict" not in {f["code"] for f in promise["findings"]}


def test_a_narrower_promise_and_a_vague_one():
    narrow = run([CLEAR], "Our certified cocoa is deforestation-free since 2021.")["promise"]
    assert narrow["verdict"] == UNVERIFIABLE and narrow["scope"] == "subset"
    vague = run([CLEAR], "We love the planet.")["promise"]
    assert (vague["status"], vague["verdict"]) == ("too_vague", None)


@pytest.mark.parametrize("promise, documents, message", [
    ("", [], "promise: the claim to check"),
    (PLAIN, [{"name": "x", "issuer": "ngo", "text": "t"}], "issuer must be 'supplier' or 'third_party'"),
    (PLAIN, [{"name": "x", "issuer": "supplier", "text": "  "}], "no text"),
    (PLAIN, [supplier_doc()] * 11, "at most 10"),
])
def test_bad_texts_are_refused_with_a_reason(promise, documents, message):
    with pytest.raises(userdocs.DocumentsRefused, match=message):
        run([CLEAR], promise, documents)


# ---- with a model: quotes checked word for word, roles enforced ------------------------------------------------

class FakeModel:
    """Answers the same reading three times: one true quote, one invented, one admission from a third party."""

    def __init__(self):
        self.calls = 0

    def complete_json(self, agent, system, blocks, schema):
        self.calls += 1
        answer = {"scope": "all", "scope_quote": None, "mentions": [], "admissions": [
            {"doc": "coop_nord_report_2025", "section": "main", "paragraph_no": 3, "year": 2022, "plots": [],
             "quote": "In 2022 we cleared 3 ha of forest on the Nord block"},
            {"doc": "coop_nord_report_2025", "section": "main", "paragraph_no": 2, "year": 2023, "plots": [],
             "quote": "we cleared 12 ha of forest in 2023"},
            {"doc": "ngo_note", "section": "main", "paragraph_no": 1, "year": 2022, "plots": [],
             "quote": "Coop Sud cleared forest"}]}
        return answer, {"provider": "fake", "model": "fake-nemotron"}


def test_the_model_reads_three_times_and_only_exact_supplier_quotes_survive():
    model = FakeModel()
    ngo = {"name": "NGO note", "issuer": "third_party", "text": "Coop Sud cleared forest in 2022."}
    promise = run([CLEAR], PLAIN, [supplier_doc(), ngo], model=model)["promise"]
    assert model.calls == 3
    (quote,) = promise["quotes"]
    assert quote["quote"] == "In 2022 we cleared 3 ha of forest on the Nord block" and quote["kind"] == "admission"
    assert promise["verdict"] == CONTRADICTED


# ---- the receipt ----------------------------------------------------------------------------------------------

def test_the_receipt_re_derives_the_promise_and_catches_a_changed_quote():
    model = FakeModel()
    run([P042, CLEAR], FULL, [supplier_doc()], batch_id="pr-receipt", model=model)
    receipt = json.loads(json.dumps(plotcheck.receipt(plotcheck.BATCHES["pr-receipt"])))
    result = verify.check(receipt)
    assert result["status"] == "VALID" and result["verdict"].endswith("promise CONTRADICTED")

    forged = json.loads(json.dumps(receipt))
    forged["promise"]["extracted"]["admissions"][0]["quote"] = "we cleared 50 ha"
    assert "quote: not found word for word in coop_nord_report_2025 main 3" in verify.check(forged)["problems"]

    erased = json.loads(json.dumps(receipt))
    erased["promise"]["extracted"]["admissions"] = []
    for row in erased["sources"][0]["rows"]:
        row["loss_year"] = 0
    problems = verify.check(erased)["problems"]
    assert any(p.startswith("promise: the rules give UNVERIFIABLE") for p in problems)


def test_the_route_takes_the_file_the_promise_and_the_documents(monkeypatch):
    monkeypatch.setenv("SAMPLE_MODE", "true")
    client = TestClient(app)
    body = {"geojson": {"type": "FeatureCollection", "features": [P042]}, "promise": FULL,
            "documents": [supplier_doc()]}
    out = client.post("/api/check-plots", json=body).json()
    assert out["promise"]["verdict"] == CONTRADICTED and out["summary"]["plots"][CONTRADICTED] == 1
    assert verify.check(client.get(out["exports"]["receipt"]).json())["status"] == "VALID"
    bad = {**body, "documents": [{"name": "x", "issuer": "ngo", "text": "t"}]}
    assert client.post("/api/check-plots", json=bad).status_code == 422
    assert client.post("/api/check-plots", json=body["geojson"]).json()["promise"] is None
