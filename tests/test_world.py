import math
import re

import pytest

from app.world import geo
from app.world.base import POINT_PLOTS, build_base_world
from app.world.cases import CASES, build_case
from app.world.store import dq_invalid_records, to_sqlite, world_sha256
from scripts.build_data import main as build_main


@pytest.fixture(scope="module")
def base():
    return build_base_world()


def plot(world, plot_id):
    return next(p for p in world["plots"] if p["plot_id"] == plot_id)


def alert(world, alert_id):
    return next(a for a in world["alerts"] if a["alert_id"] == alert_id)


def plot_reach_m(p):
    """How far the plot extends from its centre: half-diagonal of the square, or disk radius."""
    if p["polygon_wkt"]:
        return geo.square_side_m(p["area_ha"]) * math.sqrt(2) / 2
    return geo.disk_radius_m(p["area_ha"])


def tonnes(world, year, supplier=None):
    return sum(d["tonnes"] for d in world["deliveries"]
               if d["year"] == year and supplier in (None, d["supplier_id"]))


# ---- the base world keeps every promise the cases rely on --------------------------

def test_sizes(base):
    assert len(base["suppliers"]) == 12
    assert len(base["plots"]) == 40
    assert len(base["alerts"]) == 30
    docs = [r["doc_id"] for r in base["report_paragraphs"]]
    assert docs.count("rse_2025") == 12 and docs.count("ngo_2025") == 8


def test_same_seed_same_world():
    assert world_sha256(build_base_world()) == world_sha256(build_base_world())


def test_manifest_is_rebuilt_identically():
    assert build_main(["build_data", "--check"]) == 0


def test_demo_plot_p014(base):
    p = plot(base, "P-014")
    assert (p["supplier_id"], p["area_ha"]) == ("F-007", 3.2) and p["polygon_wkt"]
    assert {d["tonnes"] for d in base["deliveries"] if d["plot_id"] == "P-014"} == {2.4}


def test_every_base_yield_is_plausible(base):
    area = {p["plot_id"]: p["area_ha"] for p in base["plots"]}
    assert max(d["tonnes"] / area[d["plot_id"]] for d in base["deliveries"]) < 1.5


def test_large_plots_have_a_polygon(base):
    for p in base["plots"]:
        assert bool(p["polygon_wkt"]) == (p["plot_id"] not in POINT_PLOTS)
        if not p["polygon_wkt"]:
            assert p["area_ha"] < 4


def test_no_base_alert_near_any_plot(base):
    gaps = [geo.distance_m(a["lat"], a["lon"], p["lat"], p["lon"]) - plot_reach_m(p)
            for a in base["alerts"] for p in base["plots"]]
    assert min(gaps) > 900


def test_world_sits_in_cote_divoire_and_away_from_soubre(base):
    for p in base["plots"]:
        assert 6.3 < p["lat"] < 10.8 and -8.6 < p["lon"] < -2.5


@pytest.mark.parametrize("year", range(2021, 2026))
def test_f031_is_twelve_percent(base, year):
    assert tonnes(base, year, "F-031") / tonnes(base, year) == pytest.approx(0.12, abs=0.002)


def test_about_forty_percent_certified(base):
    certified = sum(d["tonnes"] for d in base["deliveries"] if d["certified"] == "yes")
    total = sum(d["tonnes"] for d in base["deliveries"])
    assert certified / total == pytest.approx(0.40, abs=0.05)


def test_ngo_base_names_no_supplier_and_no_plot(base):
    names = {s["name"] for s in base["suppliers"]}
    for r in base["report_paragraphs"]:
        if r["doc_id"] == "ngo_2025":
            assert not any(name in r["text"] for name in names)
            assert not re.search(r"P-\d|plot number", r["text"], re.IGNORECASE)


def test_company_total_matches_fiscal_year(base):
    assert base["company"][0]["total_tonnes"] == pytest.approx(tonnes(base, 2025))


# ---- each case changes what it says, and nothing else breaks ------------------------

@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c.number:02d}_{c.slug}")
def test_case_builds_and_loads_into_sqlite(case):
    world = build_case(case.number)
    connection = to_sqlite(world)
    count = connection.execute("SELECT COUNT(*) FROM plots").fetchone()[0]
    assert count == len(world["plots"])
    connection.close()


def test_every_case_but_the_base_changes_something(base):
    base_hash = world_sha256(base)
    same = [c.number for c in CASES if world_sha256(build_case(c.number)) == base_hash]
    assert same == [1, 20]  # 20 only marks the alert source as unavailable


def test_case_2_alert_is_inside_p014():
    world = build_case(2)
    p, a = plot(world, "P-014"), alert(world, "A-221")
    half = geo.square_side_m(p["area_ha"]) / 2
    assert geo.distance_m(a["lat"], a["lon"], p["lat"], p["lon"]) < half
    assert a["alert_date"] == "2022-03-14" and a["confidence"] == "high"


def test_case_5_alert_is_300m_outside_the_polygon():
    world = build_case(5)
    p, a = plot(world, "P-003"), alert(world, "A-222")
    edge = geo.distance_m(a["lat"], a["lon"], p["lat"], p["lon"]) - geo.square_side_m(p["area_ha"]) / 2
    assert edge == pytest.approx(300, abs=2)


def test_case_6_alert_is_within_500m_of_the_point_plot_disk():
    world = build_case(6)
    p, a = plot(world, "P-033"), alert(world, "A-223")
    edge = geo.distance_m(a["lat"], a["lon"], p["lat"], p["lon"]) - geo.disk_radius_m(p["area_ha"])
    assert edge == pytest.approx(300, abs=2)


def test_case_8_leaves_twelve_percent_untraced():
    world = build_case(8)
    untraced = sum(d["tonnes"] for d in world["deliveries"] if d["year"] == 2025 and d["plot_id"] is None)
    assert untraced / world["company"][0]["total_tonnes"] == pytest.approx(0.12, abs=0.002)


def test_case_9_yield_is_impossible():
    world = build_case(9)
    d = next(d for d in world["deliveries"] if d["plot_id"] == "P-009" and d["year"] == 2024)
    assert d["tonnes"] / plot(world, "P-009")["area_ha"] > 3


def test_case_16_is_caught_by_data_quality_rules():
    issues = dq_invalid_records(to_sqlite(build_case(16)))
    assert issues == [{"rule": "in_cocoa_belt", "table": "plots", "entity_id": "P-027"}]


def test_base_has_no_data_quality_issue(base):
    assert dq_invalid_records(to_sqlite(base)) == []
