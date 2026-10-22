"""A promise about another commodity or another company is out of scope before any query, never a verdict."""
import io
import json

import pytest

from app.agents import extractor
from app.agents.context import RunContext
from app.memory.fallback import MemoryEngine
from app.pipeline import run_case

COFFEE = "Our coffee supply chain is fully sustainable and we protect forests."


def run(claim, tmp_path, run_id="scope-run"):
    out = run_case({"run_id": run_id, "input": {"claim": claim}, "options": {"sample_mode": True, "case": 2}},
                   log_dir=tmp_path, stream=io.StringIO(), memory_engine=MemoryEngine())
    steps = [json.loads(line)["action"] for line in (tmp_path / f"{run_id}.jsonl").read_text("utf-8").splitlines()]
    return out, steps


def test_the_judges_coffee_promise_is_out_of_scope_before_any_query(tmp_path):
    out, steps = run(COFFEE, tmp_path)
    assert out["status"] == "out_of_scope" and "verdict" not in out and "findings" not in out
    assert out["summary"] == ("GreenTrace has no data on coffee here. Upload your own plots (GeoJSON) to check "
                              "them, or try the example cocoa supply chain.")
    assert "check_scope" in steps and "query_zetaris" not in steps


def test_palm_oil_is_out_of_scope(tmp_path):
    out, _ = run("100% of our palm oil is deforestation-free since 2021.", tmp_path)
    assert out["status"] == "out_of_scope" and out["summary"].startswith("GreenTrace has no data on palm oil here.")


def test_a_cocoa_promise_is_investigated(tmp_path):
    out, steps = run("100% of our cocoa comes from deforestation-free farms since 2021.", tmp_path)
    assert out["status"] == "success" and out["verdict"] == "CONTRADICTED" and "check_scope" not in steps


def test_an_unknown_brand_is_out_of_scope(tmp_path):
    out, steps = run("Ferrero guarantees that 100% of its cocoa is deforestation-free since 2021.", tmp_path)
    assert out["status"] == "out_of_scope" and "verdict" not in out and "query_zetaris" not in steps
    assert out["summary"].startswith("GreenTrace has no data on this company here.")


def test_a_vague_promise_is_too_vague_with_a_clear_sentence(tmp_path):
    out, steps = run("We are sustainable.", tmp_path)
    assert out["status"] == "too_vague" and out["summary"] == extractor.TOO_VAGUE and "query_zetaris" not in steps


@pytest.mark.parametrize("claim, commodities", [
    ("Our chocolate is deforestation-free.", ["cocoa"]),
    ("Our café and cacao are traceable.", ["cocoa", "coffee"]),
    ("RSPO-certified palm kernel and soybeans.", ["palm oil", "soy"]),
    ("Our leather and beef come from zero-deforestation ranches.", ["cattle"]),
    ("Natural latex, FSC timber and paper.", ["rubber", "wood"]),
    ("We are sustainable.", []),
])
def test_commodity_words_and_synonyms(claim, commodities):
    assert extractor.commodities_in(claim) == commodities


@pytest.mark.parametrize("claim, company", [
    ("Ferrero guarantees deforestation-free cocoa.", "Ferrero"),
    ("Nestlé's cocoa is 100% deforestation-free.", "Nestlé"),
    ("All cocoa bought by Barry Callebaut is traceable.", "Barry Callebaut"),
    ("ChocoDurable SA sources 100% deforestation-free cocoa.", "ChocoDurable SA"),
    ("100% of our cocoa comes from deforestation-free farms since 2021.", None),
    ("Our cocoa from Côte d'Ivoire is Rainforest Alliance certified and meets the EU EUDR.", None),
    ("Sustainably sourced cocoa, deforestation-free since 2021.", None),
    ("All our cocoa is covered by our Cocoa Horizons programme.", None),
])
def test_the_company_a_promise_names(claim, company):
    assert extractor.company_in(claim) == company


def test_the_demo_company_and_brand_stay_in_scope(tmp_path):
    out, _ = run("ChocoDurable SA: 100% of our cocoa is deforestation-free since 2021.", tmp_path)
    assert out["status"] == "success"
    out, _ = run("Kalou Chocolat's cocoa is 100% deforestation-free since 2021.", tmp_path, run_id="kalou")
    assert out["status"] == "success"


class Model:
    """A model that names a commodity the rules do not know a word for, and a company."""
    def __init__(self, answer):
        self.answer, self.calls = answer, 0

    def complete_json(self, agent, system, blocks, schema):
        self.calls += 1
        return self.answer, {"provider": "fake", "model": "fake"}


def ctx_with(model):
    return RunContext(run_id="scope", gate=None, memory=None, company_id="chocodurable", log=lambda r: None,
                      model=model)


def test_the_model_adds_what_the_rules_missed_and_a_company_only_if_written():
    claim = "Our mocha blend is 100% deforestation-free."
    model = Model({"commodities": ["coffee"], "company": "none"})
    assert extractor.scope(ctx_with(model), claim, extractor.read_claim(claim)).startswith(
        "GreenTrace has no data on coffee here.") and model.calls == 1
    invented = Model({"commodities": ["cocoa"], "company": "Mars"})  # not in the promise: ignored
    cocoa = "100% of our cocoa is deforestation-free."
    assert extractor.scope(ctx_with(invented), cocoa, extractor.read_claim(cocoa)) is None
