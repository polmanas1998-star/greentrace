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


# ---- the code's judgement, cell by cell ----------------------------------------------------------

@pytest.mark.parametrize("label, lost, status", [
    ("recent_clearing", True, lens.CONFIRMED), ("recent_clearing", False, lens.REJECTED),
    ("intact_forest", True, lens.MISSED), ("plantation", False, lens.CONFIRMED),
    ("bare_soil", True, lens.MISSED), ("cannot_see", True, lens.MISSED), ("cannot_see", False, lens.NO_CLAIM),
    (None, False, lens.NO_CLAIM),
])
def test_each_cell_is_judged_against_the_maps(label, lost, status):
    assert lens.judge(label, lost) == status


def test_the_grid_tiles_the_frame_and_reads_the_real_cells():
    scene = lens.scene_for("P-042")
    cells = lens.grid(scene.frame)
    assert [c["cell"] for c in cells] == list(lens.CELLS)
    assert cells[0]["lat_max"] == scene.frame["lat_max"] and cells[-1]["lon_max"] == pytest.approx(scene.frame["lon_max"])
    measured = lens.maps_by_cell(scene.frame)
    assert sum(m["cells"] for m in measured.values()) > 1000  # about 1 200 Hansen cells under a 1200 x 800 m frame
    assert measured["B2"]["lost"] and not measured["C3"]["lost"]  # the clearing east of P-042's centre; none in C3


# ---- the model looks, the code checks ------------------------------------------------------------

def test_the_model_sees_the_bare_image_and_every_claim_is_checked_and_traced():
    scene = lens.scene_for("P-042")
    measured = lens.maps_by_cell(scene.frame)
    # The model calls every cell a clearing: right where the maps lost forest, wrong elsewhere.
    fake = FakeVision(answer(["recent_clearing"] * 9))
    lines, log = traced()
    out = lens.look(scene, "lens-test", gate=gate_with(fake, log), log=log)

    (request,) = fake.calls
    user = request["messages"][1]["content"]
    image = next(part for part in user if part["type"] == "image_url")["image_url"]["url"]
    assert image.startswith("data:image/jpeg;base64,")
    import base64
    assert base64.b64decode(image.split(",", 1)[1]) == scene.image.read_bytes()  # the image as it is, no mark on it
    assert request["model"] == ASKED and request["timeout"] == lens.TIMEOUT_S
    assert request["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False

    lost = [c for c in lens.CELLS if measured[c]["lost"]]
    assert [c["status"] for c in out["cells"]] == [lens.CONFIRMED if c in lost else lens.REJECTED for c in lens.CELLS]
    assert out["score"] == {"ai_confirmed": len(lost), "code_corrected": 9 - len(lost), "of": 9}
    checks = [line for line in lines if line["action"] == "check_vision_claim"]
    assert len(checks) == 9 and all(line["agent_name"] == "Evaluator" and line["provider"] == "nvidia" for line in checks)
    assert all(line["model"] == ANSWERED and ASKED in line["input_summary"] for line in checks)


def test_a_cell_the_model_left_out_is_no_claim_or_missed():
    scene = lens.scene_for("P-041")
    nine = json.loads(answer(["intact_forest"] * 9))
    nine["cells"][4] = {"cell": "A1", "label": "plantation", "confidence": 0.5}  # B2 named nowhere, A1 twice
    out = lens.look(scene, "lens-test", gate=gate_with(FakeVision(json.dumps(nine)), traced()[1]), log=traced()[1])
    b2 = next(c for c in out["cells"] if c["cell"] == "B2")
    assert b2["label"] is None and b2["status"] == lens.MISSED  # 0.54 ha lost after 2020 there, the model said nothing
    assert next(c for c in out["cells"] if c["cell"] == "A1")["label"] == "intact_forest"  # first answer counts


def test_an_answer_that_is_not_the_schema_falls_back_to_the_code_grid():
    fake = FakeVision("I think it is forest.", '{"cells": []}')
    out = lens.look(lens.scene_for("P-042"), "lens-test", gate=gate_with(fake, traced()[1]), log=traced()[1])
    assert out["ai"] is False and out["score"] is None and all(c["status"] is None for c in out["cells"])
    assert all("loss_after_2020_ha" in c for c in out["cells"])  # the code's grid still stands


def test_sample_mode_calls_no_model_and_says_so():
    def no_client(provider):
        raise AssertionError("no client in sample mode")

    lines, log = traced()
    gate = ModelGate("lens-test", env={**MODEL_ENV, "SAMPLE_MODE": "true"}, client_factory=no_client, log=log)
    out = lens.look(lens.scene_for("P-042"), "lens-test", gate=gate, log=log)
    assert out["ai"] is False and out["message"] == lens.NEEDS_MODEL
    assert [line["action"] for line in lines] == ["fallback_rules", "check_vision_claim"]


def test_no_scene_outside_the_frames():
    assert lens.scene_for("P-900", 6.70, -6.45) is None
    assert lens.scene_for("P-900", 5.7300, -6.5220).plot_id == "P-042"  # a plot drawn inside P-042's frame


# ---- the verdict is the rules', whatever the lens says -------------------------------------------

def test_the_lens_never_moves_a_verdict():
    ring = [[float(x), float(y)] for x, y in (p.split() for p in geo.square_wkt(5.7300, -6.5220, 10.0)[9:-2].split(", "))]
    plot = {"type": "Feature", "properties": {"plot_id": "P-900"}, "geometry": {"type": "Polygon", "coordinates": [ring]}}
    client = TestClient(app)
    before = client.post("/run", json={"run_id": "lens-verdict", "input": {"claim": "100% deforestation-free since 2021.",
                                                                          "plot": plot}, "options": {"case": 1}}).json()
    assert before["verdict"] == "CONTRADICTED"
    # The model swears the whole scene is intact forest; the lens runs on the same run.
    lines, log = traced()
    lens.look(lens.scene_for("P-042"), "lens-verdict", gate=gate_with(FakeVision(answer(["intact_forest"] * 9)), log), log=log)
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
