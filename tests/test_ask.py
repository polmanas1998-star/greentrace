"""Ask GreenTrace: answers come from the run's own lines, the model may only cite lines that exist."""
import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.explain import ask

CLAIM = "100% of our cocoa comes from deforestation-free farms since 2021."


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    import os
    os.environ["SAMPLE_MODE"] = "true"
    client = TestClient(app)
    client.post("/run", json={"run_id": "ask-case-2", "input": {"claim": CLAIM, "company_id": "chocodurable"},
                              "options": {"case": 2}})
    from app import orchestrator
    return client, orchestrator.RUNS["ask-case-2"]


class FakeModel:
    def __init__(self, reply):
        self.reply = reply

    def complete_json(self, agent, system, data_blocks, schema):
        assert agent == "Writer" and dict(data_blocks)["question"]
        return self.reply, {"provider": "nvidia", "model": "fake"}


def test_the_rules_answer_with_the_closest_lines(run):
    client, _ = run
    out = client.post("/runs/ask-case-2/ask", json={"question": "Did P-014 stop delivering?"}).json()
    assert out["status"] == "rules" and out["provider"] == "rules" and out["verdict"] == "CONTRADICTED"
    assert out["lines"] and all("P-014" in line["text"] for line in out["lines"])
    r3 = client.post("/runs/ask-case-2/ask", json={"question": "Which years did R3 find deliveries?"}).json()
    assert r3["lines"][0]["text"].startswith("R3 did not hold: P-014 delivered in 2021")
    assert "No model was used" in out["answer"]


def test_a_question_no_line_answers_is_said_so(run):
    client, _ = run
    out = client.post("/runs/ask-case-2/ask", json={"question": "What about coffee?"}).json()
    assert out["lines"] == [] and out["answer"].startswith("No line of this investigation answers that")


def test_bad_questions_and_unknown_runs(run):
    client, _ = run
    assert client.post("/runs/ask-case-2/ask", json={"question": ""}).status_code == 422
    assert client.post("/runs/ask-case-2/ask", json={"question": "x" * 501}).status_code == 422
    assert client.post("/runs/no-such-run/ask", json={"question": "why?"}).status_code == 404


def test_a_model_answer_that_cites_real_lines_is_kept(run):
    _, investigation = run
    numbered = ask.lines(investigation)
    first = numbered[0]
    reply = {"answer": "Alert A-221 of 2022-03-14 lies inside P-014.", "lines": [first["n"]]}
    out = ask.ask(investigation, "Why?", model=FakeModel(reply))
    assert out["status"] == "model" and out["provider"] == "nvidia" and out["lines"] == [first]


@pytest.mark.parametrize("reply", [
    {"answer": "Fine.", "lines": [999]},                                 # a line that does not exist
    {"answer": "12.5 ha were lost in 2019.", "lines": [1]},              # numbers no cited line holds
    {"answer": "", "lines": [1]},                                        # nothing said
    None,                                                                # the gate gave up
])
def test_a_model_answer_that_strays_goes_back_to_the_rules(run, reply):
    _, investigation = run
    out = ask.ask(investigation, "Why is the claim contradicted?", model=FakeModel(reply))
    assert out["status"] == "rules" and out["provider"] == "rules" and out["model"] is None


def test_the_same_question_gives_the_same_answer(run):
    client, _ = run
    a = client.post("/runs/ask-case-2/ask", json={"question": "Why is it contradicted?"}).json()
    b = client.post("/runs/ask-case-2/ask", json={"question": "Why is it contradicted?"}).json()
    assert a == b
