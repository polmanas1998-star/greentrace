"""The verdict receipt: re-derived offline, tampering caught, same case same seal."""
import hashlib
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import verify
from app import orchestrator
from app.api import app
from app.canonical import sha256_of
from app.memory.fallback import MemoryEngine
from app.pipeline import run_case
from app.receipt import build_receipt, package_zip
from app.world.cases import CASES

ROOT = Path(__file__).resolve().parents[1]
CLAIM = "100% of our cocoa comes from deforestation-free farms since 2021."


def finished(case=2, run_id="rc-02", tmp_path=None):
    run_case({"run_id": run_id, "input": {"claim": CLAIM}, "options": {"sample_mode": True, "case": case}},
             log_dir=tmp_path, stream=io.StringIO(), memory_engine=MemoryEngine())
    return orchestrator.RUNS[run_id]


def receipt_of(case=2, run_id="rc-02"):
    return json.loads(json.dumps(build_receipt(finished(case, run_id))))  # exactly what a file holds


def q4_row(receipt, alert_id):
    source = next(s for s in receipt["sources"] if s["query_id"] == "Q4" and s["agent"] == "Investigator")
    return next(row for row in source["rows"] if row["alert_id"] == alert_id)


# ---- the demo, as the video shows it ---------------------------------------------------------

def test_case_2_receipt_is_valid_and_carries_the_app_seal():
    run = finished()
    receipt = json.loads(json.dumps(build_receipt(run)))
    result = verify.check(receipt)
    assert (result["status"], result["verdict"], result["problems"]) == ("VALID", "CONTRADICTED", [])
    assert result["seal"] == receipt["verdict_sha256"] == run.output["verdict_sha256"]
    assert receipt["data_sha256"] == run.output["fingerprints"]["data_sha256"]


def test_redating_a221_in_the_q4_rows_is_tampered_and_recomputes_proven():
    receipt = receipt_of()
    q4_row(receipt, "A-221")["alert_date"] = "2019-03-14"
    result = verify.check(receipt)
    assert result["status"] == "TAMPERED"
    assert result["problems"][0].startswith("Q4 row ") and result["problems"][0].endswith("(alert A-221)")
    assert result["verdict"] == "PROVEN"  # exactly the flip the receipt announced


def test_a_forged_row_the_rules_cannot_read_is_tampered_not_a_crash():
    from scripts.tamper_bench import rehash
    receipt = receipt_of(run_id="rc-broken")
    row = next(r for s in receipt["sources"] for r in s["rows"] if isinstance(r.get("polygon_wkt"), str))
    row["polygon_wkt"] += " (amended)"
    for forged in (receipt, rehash(json.loads(json.dumps(receipt)))):  # with and without the hashes rewritten
        result = verify.check(forged)
        assert result["status"] == "TAMPERED"
        assert result["problems"][-1].startswith("rows: the rules cannot re-derive a verdict")


def test_two_runs_give_the_same_seal():
    first, second = receipt_of(run_id="rc-one"), receipt_of(run_id="rc-two")
    assert first["verdict_sha256"] == second["verdict_sha256"]
    assert first["data_sha256"] == second["data_sha256"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c.number:02d}_{c.slug}")
def test_every_trap_case_receipt_is_valid(case):
    result = verify.check(receipt_of(case.number, f"rc-all-{case.number:02d}"))
    assert (result["status"], result["problems"]) == ("VALID", [])


# ---- the ways a receipt can lie --------------------------------------------------------------

def test_rows_rehashed_to_match_still_show():
    receipt = receipt_of()
    source = next(s for s in receipt["sources"] if s["query_id"] == "Q4")
    row = q4_row(receipt, "A-221")
    row["alert_date"] = "2019-03-14"
    source["row_sha256"] = [sha256_of(r) for r in source["rows"]]
    source["rows_sha256"] = sha256_of(source["rows"])
    problems = verify.check(receipt)["problems"]
    assert problems[0] == "data_sha256: answers added, removed or re-hashed"
    assert any(p.startswith("verdict: the rules give PROVEN") for p in problems)


def test_a_quote_not_in_its_paragraph_is_caught():
    receipt = receipt_of()
    receipt["extracted"]["admissions"].append({"doc": "rse_2025", "section": "annex", "paragraph_no": 1,
                                               "quote": "We cleared forest in 2022.", "plots": [], "year": 2022})
    assert "quote: not found word for word in rse_2025 annex 1" in verify.check(receipt)["problems"]


def test_a_what_if_run_replays_its_change():
    finished(run_id="rc-wi")
    orchestrator.what_if("rc-wi", "alert:A-221", "alert_date", "2019-03-14")
    receipt = json.loads(json.dumps(build_receipt(orchestrator.RUNS["rc-wi"])))
    assert receipt["what_if"] == [{"entity": "alert:A-221", "field": "alert_date", "value": "2019-03-14"}]
    assert verify.check(receipt)["status"] == "VALID" and verify.check(receipt)["verdict"] == "PROVEN"


def test_findings_say_where_they_come_from():
    receipt = receipt_of()
    (finding,) = receipt["verdict"]["findings"]
    assert finding["code"] == "confirmed_alert_in_delivering_plot"
    rows = []
    for ref in finding["derived_from"]:  # "Q4#7 row 30": answer 7 of the run, its row 30
        answer, row = ref.split(" row ")
        source = receipt["sources"][int(answer.split("#")[1])]
        rows.append((source["query_id"], source["rows"][int(row)].get("alert_id") or source["rows"][int(row)]["plot_id"]))
    assert rows == [("Q4", "A-221"), ("Q3", "P-014")]


# ---- the command line, the package, the API --------------------------------------------------

def test_command_line_exit_codes(tmp_path):
    good, bad, garbage = tmp_path / "good.json", tmp_path / "bad.json", tmp_path / "garbage.json"
    receipt = receipt_of()
    good.write_text(json.dumps(receipt), encoding="utf-8")
    q4_row(receipt, "A-221")["alert_date"] = "2019-03-14"
    bad.write_text(json.dumps(receipt), encoding="utf-8")
    garbage.write_text("not a receipt", encoding="utf-8")
    runs = {name: subprocess.run([sys.executable, "verify.py", str(path)], cwd=ROOT, capture_output=True, text=True)
            for name, path in (("good", good), ("bad", bad), ("garbage", garbage))}
    assert runs["good"].returncode == 0 and runs["good"].stdout.startswith("VALID: CONTRADICTED re-derived offline")
    assert runs["bad"].returncode == 1 and runs["bad"].stdout.startswith("TAMPERED: Q4 row")
    assert "verdict recomputed on the data as found: PROVEN" in runs["bad"].stdout
    assert runs["garbage"].returncode == 2 and runs["garbage"].stdout.startswith("UNREADABLE")


def test_package_holds_every_file_and_a_true_manifest(tmp_path):
    run = finished(run_id="rc-pkg", tmp_path=tmp_path)
    archive = zipfile.ZipFile(io.BytesIO(package_zip(run, tmp_path)))
    assert sorted(archive.namelist()) == sorted([
        "evidence.md", "trace.jsonl", "findings.csv", "plots_no_issue_found.geojson", "supplier_letters.md",
        "receipt.json", "manifest.json"])
    manifest = json.loads(archive.read("manifest.json"))
    for entry in manifest["files"]:
        assert hashlib.sha256(archive.read(entry["name"])).hexdigest() == entry["sha256"]
    assert archive.read("trace.jsonl").decode().count("\n") > 10
    assert verify.check(json.loads(archive.read("receipt.json")))["status"] == "VALID"


def test_api_serves_the_receipt_and_the_package(monkeypatch):
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("WORLD_MODEL_URL", "http://127.0.0.1:9")
    client = TestClient(app)
    client.post("/run", json={"run_id": "rc-api", "input": {"claim": CLAIM}, "options": {"case": 2}})
    assert verify.check(client.get("/runs/rc-api/receipt").json())["status"] == "VALID"
    package = client.get("/runs/rc-api/package")
    assert package.status_code == 200 and package.headers["content-type"] == "application/zip"
    assert client.get("/runs/nope/receipt").status_code == 404
