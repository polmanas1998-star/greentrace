"""Satellite Lens: the model looks, the code checks each cell, the verdict never moves. No network here."""
import io
import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.lens as lens
from app import orchestrator
from app.adapters.llm import ModelGate, build_messages
from app.api import app
from app.checks.landcover import CELLS_FILE
from app.tracing import trace
from app.world import geo

pytestmark = pytest.mark.skipif(not CELLS_FILE.exists(), reason="data/reference_cells.csv.gz not built")
ASKED = "nvidia/nemotron-3.5-super-vl-120b-a12b"
ANSWERED = "nvidia/nemotron-3.5-super-vl-120b-a12b-2026-10"
MODEL_ENV = {"MODEL_PROVIDER": "nvidia", "MODEL_API_KEY": "test-key-not-real", "SAMPLE_MODE": "false"}


class FakeVision:
    """Stands in for openai.OpenAI, in this test file only: answers each chat request with the next text."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **request):
        self.calls.append(request)
        message = SimpleNamespace(content=self.answers.pop(0))
        return SimpleNamespace(model=ANSWERED, choices=[SimpleNamespace(message=message)], usage=None)


def answer(labels):
    return json.dumps({"cells": [{"cell": cell, "label": label, "confidence": 0.8}
                                 for cell, label in zip(lens.CELLS, labels)]})


def traced():
    lines = []
    return lines, lambda record: lines.append(json.loads(trace(record, stream=io.StringIO())))


def gate_with(fake, log):
    return ModelGate("lens-test", env=MODEL_ENV, client_factory=lambda provider: fake, log=log)


def with_before(tmp_path, plot_id="P-042"):
    """A scenes folder holding the real recent scene plus a 2020 scene and a Lens image (copies, for the test)."""
    import shutil
    real = lens.SCENES_DIR
    for name in (f"{plot_id}.jpg", f"{plot_id}.json"):
        shutil.copy(real / name, tmp_path / name)
    before = json.loads((real / f"{plot_id}.json").read_text(encoding="utf-8"))
    before.update(scene_id="S2A_TEST_20200115", date="2020-01-15", datetime="2020-01-15T10:59:00Z")
    (tmp_path / f"{plot_id}_2020.json").write_text(json.dumps(before), encoding="utf-8")
    shutil.copy(real / f"{plot_id}.jpg", tmp_path / f"{plot_id}_2020.jpg")
    composite = b"\xff\xd8\xff\xe0 test composite of two dates"
    (tmp_path / f"{plot_id}_lens.jpg").write_bytes(composite)
    (tmp_path / f"{plot_id}_lens.json").write_text(json.dumps({"jpg_sha256": "test"}), encoding="utf-8")
    return lens.scene_for(plot_id, directory=tmp_path), composite


def with_cells(tmp_path, plot_id="P-042"):
    """with_before, plus the nine cell pairs (test bytes, one distinct JPEG-looking file per cell)."""
    scene, composite = with_before(tmp_path, plot_id)
    pairs = {}
    for cell in lens.CELLS:
        pairs[cell] = b"\xff\xd8\xff\xe0 pair " + cell.encode()
        (tmp_path / f"{plot_id}_lens_{cell}.jpg").write_bytes(pairs[cell])
    (tmp_path / f"{plot_id}_lens_cells.json").write_text(json.dumps({"cells": list(lens.CELLS)}), encoding="utf-8")
    return lens.scene_for(plot_id, directory=tmp_path), composite, pairs


def one(label, confidence=0.7):
    return json.dumps({"label": label, "confidence": confidence})


# ---- the code's judgement, cell by cell ----------------------------------------------------------

@pytest.mark.parametrize("label, lost, status", [
    ("forest_lost", True, lens.CONFIRMED), ("forest_lost", False, lens.REJECTED),
    ("forest_kept", True, lens.MISSED), ("no_forest_in_2020", True, lens.MISSED),
    ("forest_kept", False, lens.CONFIRMED), ("no_forest_in_2020", False, lens.CONFIRMED),
    ("cannot_see", True, lens.NOT_SEEN), ("cannot_see", False, lens.NOT_SEEN), (None, True, lens.NOT_SEEN),
])
def test_each_cell_is_judged_against_the_maps(label, lost, status):
    assert lens.judge(label, lost) == status  # "I cannot see" is never "I missed"


def test_the_score_leaves_unseen_cells_out():
    statuses = [lens.CONFIRMED] * 4 + [lens.MISSED] * 2 + [lens.REJECTED] + [lens.NOT_SEEN] * 2
    assert lens.score([{"status": st} for st in statuses]) == {"right": 4, "seen": 7, "missed": 2, "wrong": 1, "of": 9}


def test_the_grid_tiles_the_frame_and_reads_the_real_cells():
    scene = lens.scene_for("P-042")
    cells = lens.grid(scene.frame)
    assert [c["cell"] for c in cells] == list(lens.CELLS)
    assert cells[0]["lat_max"] == scene.frame["lat_max"] and cells[-1]["lon_max"] == pytest.approx(scene.frame["lon_max"])
    measured = lens.maps_by_cell(scene.frame)
    assert sum(m["cells"] for m in measured.values()) > 1000  # about 1 200 Hansen cells under a 1200 x 800 m frame
    assert measured["B2"]["lost"] and not measured["C3"]["lost"]  # the clearing east of P-042's centre; none in C3


# ---- the model looks, the code checks ------------------------------------------------------------

def test_the_model_sees_one_image_of_both_dates_and_every_claim_is_checked_and_traced(tmp_path):
    scene, composite = with_before(tmp_path)
    measured = lens.maps_by_cell(scene.frame)
    # The model says forest was lost everywhere: right where the maps lost forest, wrong elsewhere.
    fake = FakeVision(answer(["forest_lost"] * 9))
    lines, log = traced()
    out = lens.look(scene, "lens-test", gate=gate_with(fake, log), log=log)

    (request,) = fake.calls
    system, user = request["messages"][0]["content"], request["messages"][1]["content"]
    images = [part["image_url"]["url"] for part in user if part["type"] == "image_url"]
    import base64
    assert len(images) == 1 and base64.b64decode(images[0].split(",", 1)[1]) == composite  # one image: before | after
    assert "LEFT half" in system and "2020-01-15" in system and scene.meta["date"] in system
    assert request["model"] == ASKED and request["timeout"] == lens.TIMEOUT_S and request["max_tokens"] == 2500
    assert request["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False

    lost = [c for c in lens.CELLS if measured[c]["lost"]]
    assert [c["status"] for c in out["cells"]] == [lens.CONFIRMED if c in lost else lens.REJECTED for c in lens.CELLS]
    assert out["score"] == {"right": len(lost), "seen": 9, "missed": 0, "wrong": 9 - len(lost), "of": 9}
    assert out["images"]["before"].endswith("P-042_2020.jpg") and out["images"]["composite"].endswith("P-042_lens.jpg")
    checks = [line for line in lines if line["action"] == "check_vision_claim"]
    assert len(checks) == 9 and all(line["agent_name"] == "Evaluator" and line["provider"] == "nvidia" for line in checks)
    assert all(line["model"] == ANSWERED and ASKED in line["input_summary"] for line in checks)
    assert {line["status"] for line in checks} == {"confirmed", "rejected"}


def test_nine_cannot_see_count_neither_for_nor_against(tmp_path):
    scene, _ = with_before(tmp_path)
    out = lens.look(scene, "lens-test", gate=gate_with(FakeVision(answer(["cannot_see"] * 9)), traced()[1]), log=traced()[1])
    assert all(c["status"] == lens.NOT_SEEN for c in out["cells"])
    assert out["score"] == {"right": 0, "seen": 0, "missed": 0, "wrong": 0, "of": 9}  # not "AI 0/9, corrected 7"


def test_a_cell_the_model_left_out_is_not_seen(tmp_path):
    scene, _ = with_before(tmp_path, "P-041")
    nine = json.loads(answer(["forest_kept"] * 9))
    nine["cells"][4] = {"cell": "A1", "label": "no_forest_in_2020", "confidence": 0.5}  # B2 named nowhere, A1 twice
    out = lens.look(scene, "lens-test", gate=gate_with(FakeVision(json.dumps(nine)), traced()[1]), log=traced()[1])
    b2 = next(c for c in out["cells"] if c["cell"] == "B2")
    assert b2["label"] is None and b2["status"] == lens.NOT_SEEN and b2["lost"]  # a loss there, but no answer
    assert next(c for c in out["cells"] if c["cell"] == "A1")["label"] == "forest_kept"  # first answer counts


def test_without_the_2020_scene_the_model_is_not_asked(tmp_path):
    import shutil
    for name in ("P-042.jpg", "P-042.json"):  # the recent scene only, whatever the repository ships
        shutil.copy(lens.SCENES_DIR / name, tmp_path / name)
    fake = FakeVision()  # no answer at all: a call would fail the test
    lines, log = traced()
    out = lens.look(lens.scene_for("P-042", directory=tmp_path), "lens-test", gate=gate_with(fake, log), log=log)
    assert fake.calls == [] and out["ai"] is False and out["message"] == lens.NEEDS_BEFORE
    assert all("loss_after_2020_ha" in c for c in out["cells"])  # the code's grid still stands


def test_an_answer_that_is_not_the_schema_falls_back_to_the_code_grid(tmp_path):
    scene, _ = with_before(tmp_path)
    fake = FakeVision("I think it is forest.", '{"cells": []}')
    out = lens.look(scene, "lens-test", gate=gate_with(fake, traced()[1]), log=traced()[1])
    assert out["ai"] is False and out["score"] is None and all(c["status"] is None for c in out["cells"])
    assert out["message"].startswith("no model answer")


def test_sample_mode_calls_no_model_and_says_so(tmp_path):
    def no_client(provider):
        raise AssertionError("no client in sample mode")

    scene, _ = with_before(tmp_path)
    lines, log = traced()
    gate = ModelGate("lens-test", env={**MODEL_ENV, "SAMPLE_MODE": "true"}, client_factory=no_client, log=log)
    out = lens.look(scene, "lens-test", gate=gate, log=log)
    assert out["ai"] is False and out["message"] == lens.NEEDS_MODEL
    assert [line["action"] for line in lines] == ["check_vision_claim"]  # no gate call at all, said in one line


def test_no_scene_outside_the_frames():
    assert lens.scene_for("P-900", 6.70, -6.45) is None
    assert lens.scene_for("P-900", 5.7300, -6.5220).plot_id == "P-042"  # a plot drawn inside P-042's frame


# ---- the verdict is the rules', whatever the lens says -------------------------------------------

def test_the_lens_never_moves_a_verdict(tmp_path):
    ring = [[float(x), float(y)] for x, y in (p.split() for p in geo.square_wkt(5.7300, -6.5220, 10.0)[9:-2].split(", "))]
    plot = {"type": "Feature", "properties": {"plot_id": "P-900"}, "geometry": {"type": "Polygon", "coordinates": [ring]}}
    client = TestClient(app)
    before = client.post("/run", json={"run_id": "lens-verdict", "input": {"claim": "100% deforestation-free since 2021.",
                                                                          "plot": plot}, "options": {"case": 1}}).json()
    assert before["verdict"] == "CONTRADICTED"
    # The model swears the forest is all still there; the lens runs on the same run.
    lines, log = traced()
    scene, _ = with_before(tmp_path)
    looked = lens.look(scene, "lens-verdict", gate=gate_with(FakeVision(answer(["forest_kept"] * 9)), log), log=log)
    assert looked["ai"] and looked["score"]["missed"] > 0  # the model really answered, and was wrong
    after = client.get("/runs/lens-verdict").json()
    assert (after["verdict"], after["verdict_sha256"]) == (before["verdict"], before["verdict_sha256"])
    assert orchestrator.RUNS["lens-verdict"].output["verdict"] == "CONTRADICTED"
    source = (lens.Path(lens.__file__)).read_text(encoding="utf-8")
    assert "app.rules" not in source and "orchestrator" not in source  # no road from the lens to the rules


# ---- the adapter: images are optional and change nothing when absent ----------------------------

def test_messages_without_images_are_unchanged():
    messages = build_messages("Say hi.", [("doc", "text")], {"type": "object"})
    assert isinstance(messages[1]["content"], str)
    with_image = build_messages("Say hi.", [("doc", "text")], {"type": "object"}, [b"\xff\xd8\xff\xe0 jpeg"])
    assert with_image[1]["content"][0] == {"type": "text", "text": messages[1]["content"]}
    with pytest.raises(ValueError):
        build_messages("Say hi.", [], {"type": "object"}, [b"PNG not jpeg"])


# ---- the page's routes ---------------------------------------------------------------------------

def test_the_lens_route_runs_in_the_background_and_says_what_it_lacks(monkeypatch):
    monkeypatch.setenv("SAMPLE_MODE", "true")
    client = TestClient(app)
    assert client.post("/api/lens", json={"lat": 6.70, "lon": -6.45}).json() == {
        "status": "no_scene", "message": lens.NEEDS_SCENE}
    started = client.post("/api/lens", json={"plot": "P-041"}).json()
    assert started["status"] in ("running", "done") and started["key"].endswith("-sample")
    for _ in range(50):
        job = client.get(f"/api/lens/{started['key']}").json()
        if job["status"] != "running":
            break
        time.sleep(0.1)
    assert job["status"] == "done" and job["result"]["message"] == lens.NEEDS_MODEL
    assert [c["cell"] for c in job["result"]["cells"]] == list(lens.CELLS)
    assert client.get("/api/lens/nope").status_code == 404


def test_one_vision_call_at_a_time_and_a_missing_answer_is_asked_again(monkeypatch):
    import threading
    from app.web import lens as web_lens

    running, most, calls = [0], [0], []
    guard = threading.Lock()

    def slow_look(scene, run_id):
        with guard:
            running[0] += 1
            most[0] = max(most[0], running[0])
        time.sleep(0.2)
        with guard:
            running[0] -= 1
        calls.append(scene.plot_id)
        return {"ai": False, "message": "no model answer (no model provider answered)", "plot_id": scene.plot_id}

    monkeypatch.setenv("SAMPLE_MODE", "false")
    monkeypatch.setattr(web_lens.lens, "look", slow_look)
    monkeypatch.setattr(web_lens, "JOBS", {})
    client = TestClient(app)

    def finish(key):
        for _ in range(100):
            job = client.get(f"/api/lens/{key}").json()
            if job["status"] == "done":
                return job
            time.sleep(0.05)
        raise AssertionError("the lens did not finish")

    first = client.post("/api/lens", json={"plot": "P-042"}).json()
    second = client.post("/api/lens", json={"plot": "P-041"}).json()
    finish(first["key"]), finish(second["key"])
    assert most[0] == 1  # never two calls to the provider at once
    finish(client.post("/api/lens", json={"plot": "P-042"}).json()["key"])
    assert calls.count("P-042") == 2  # no answer is not cached: asking again asks again


# ---- one question per cell, the grid as fallback ---------------------------------------------------

def test_one_question_per_cell_with_its_own_pair_one_at_a_time(tmp_path):
    scene, _, pairs = with_cells(tmp_path)
    measured = lens.maps_by_cell(scene.frame)
    labels = ["forest_lost" if measured[c]["lost"] else "forest_kept" for c in lens.CELLS]
    labels[8] = "cannot_see"
    fake, pauses = FakeVision(*[one(label) for label in labels]), []
    lines, log = traced()
    out = lens.look(scene, "lens-test", gate=gate_with(fake, log), log=log, sleep=pauses.append)

    assert len(fake.calls) == 9 and pauses == [lens.CELL_PAUSE_S] * 8  # nine questions, a pause between two
    import base64
    for cell, request in zip(lens.CELLS, fake.calls):
        user = request["messages"][1]["content"]
        (image,) = [part["image_url"]["url"] for part in user if part["type"] == "image_url"]
        assert base64.b64decode(image.split(",", 1)[1]) == pairs[cell]  # this cell's pair, nothing else
        system = request["messages"][0]["content"]
        assert f"cell, {cell}," in system and "2020-01-15" in system and "rough, irregular" in system
        assert request["timeout"] == lens.TIMEOUT_S
    assert out["method"] == "cells" and out["fallback"] is None
    assert out["score"] == {"right": 8, "seen": 8, "missed": 0, "wrong": 0, "of": 9}
    checks = [line for line in lines if line["action"] == "check_vision_claim"]
    assert len(checks) == 9 and all("(cells method)" in line["input_summary"] for line in checks)


def test_a_cell_without_an_answer_falls_back_to_the_grid_and_says_so(tmp_path):
    scene, composite, _ = with_cells(tmp_path)
    cells_fake = FakeVision(one("forest_kept"), "not json", "still not json")  # A1 answers, B1 never does
    grid_fake = FakeVision(answer(["forest_kept"] * 9))
    log = traced()[1]
    gates = iter([gate_with(cells_fake, log), gate_with(grid_fake, log)])
    out = lens.look(scene, "lens-test", gate_factory=lambda: next(gates), log=log, sleep=lambda s: None)
    assert out["ai"] and out["method"] == "grid" and out["fallback"].startswith("cell B1:")
    import base64
    (request,) = grid_fake.calls
    image = [p for p in request["messages"][1]["content"] if p["type"] == "image_url"][0]["image_url"]["url"]
    assert base64.b64decode(image.split(",", 1)[1]) == composite  # the one-image grid, on a fresh gate


def test_the_grid_method_can_be_asked_alone(tmp_path):
    scene, composite, _ = with_cells(tmp_path)
    fake = FakeVision(answer(["no_forest_in_2020"] * 9))
    out = lens.look(scene, "lens-test", gate=gate_with(fake, traced()[1]), log=traced()[1], method="grid")
    assert out["method"] == "grid" and len(fake.calls) == 1


# ---- scripts/lens_probe.py: the tool for the real test --------------------------------------------

def test_the_probe_refuses_sample_mode(monkeypatch, capsys):
    from scripts import lens_probe

    monkeypatch.setenv("SAMPLE_MODE", "true")
    assert lens_probe.main(["--plot", "P-042"]) == 2
    assert "SAMPLE_MODE is true" in capsys.readouterr().out


def test_the_probe_prints_the_ai_grid_next_to_the_code(tmp_path):
    from scripts import lens_probe

    scene, _, _ = with_cells(tmp_path)
    measured = lens.maps_by_cell(scene.frame)
    labels = ["forest_kept"] * 8 + ["cannot_see"]
    fake = FakeVision(*[one(label) for label in labels])
    result = lens.look(scene, "probe-test", gate=gate_with(fake, traced()[1]), log=traced()[1], sleep=lambda s: None)
    text = lens_probe.report(result, 12.3)
    assert "P-042 · cells method · nvidia" in text and "12.3 s" in text
    assert "C3 --" in text  # cannot see, counted neither way
    lost = [c for c in lens.CELLS[:8] if measured[c]["lost"]]
    assert text.count("MISSED BY AI") == len(lost)
    assert f"AI right {8 - len(lost)} / seen 8 · missed {len(lost)} · wrong 0" in text
