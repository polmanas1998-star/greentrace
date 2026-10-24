"""app/geo: satellite passes, calendars, convergence of the sources, the time slider.

No network. The orbits here are MADE UP for the tests (a sun-synchronous orbit at 786 km built with sgp4,
see synthetic_tle): they are not the elements of the real Sentinel-2 satellites, which only ever come from
CelesTrak (python -m app.geo.tle refresh). Global Forest Watch tiles and the Whisp API are fakes, in this
file only. Every calendar file is parsed with the icalendar package.
"""
import io
import json
import math
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import numpy as np
import pytest
from fastapi.testclient import TestClient
from icalendar import Calendar
from sgp4 import exporter
from sgp4.api import WGS72, Satrec
from sgp4.propagation import gstime

from app import orchestrator
from app.api import app
from app.checks import gfw
from app.checks.landcover import CELLS_FILE, load_meta, read_cells, plot_box
from app.geo import convergence, events, ics, passes, plots, settings, time_slider, tle as tles, whisp
from app.memory.fallback import MemoryEngine
from app.pipeline import run_case
from app.rules import CONTRADICTED, PROVEN, UNVERIFIABLE, check_plot
from tests.test_gfw import tile_with

pytestmark = pytest.mark.skipif(not CELLS_FILE.exists(), reason="data/reference_cells.csv.gz not built")
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
SOUBRE = (5.73, -6.522)
MEASURED = load_meta()["measured"]
EXAMPLE_05 = json.loads(open("examples/05_real_plot_contradicted.json", encoding="utf-8").read())


# ---- made-up orbits ------------------------------------------------------------------------------------------------

def sun_right_ascension_deg(when: datetime) -> float:
    n = (when - datetime(2000, 1, 1, 12, tzinfo=timezone.utc)).total_seconds() / 86400.0
    g = math.radians(357.528 + 0.9856003 * n)
    lam = math.radians(280.460 + 0.9856474 * n + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    eps = math.radians(23.439 - 0.0000004 * n)
    return math.degrees(math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))) % 360.0


def synthetic_tle(satellite: str, norad: int, epoch: datetime, phase_deg: float = 0.0) -> str:
    """A MADE-UP sun-synchronous orbit for the tests (786 km, 98.56 degrees, descending node near 10:30 local time):
    not the real satellite's elements."""
    raan = (sun_right_ascension_deg(epoch) + 157.5) % 360.0  # ascending node at 22:30, descending at 10:30
    satrec = Satrec()
    satrec.sgp4init(WGS72, "i", norad, (epoch - datetime(1949, 12, 31, tzinfo=timezone.utc)).total_seconds() / 86400,
                    0.0, 0.0, 0.0, 0.0001, math.radians(90.0), math.radians(98.56), math.radians(phase_deg),
                    14.30824 * 2 * math.pi / 1440.0, math.radians(raan))
    line1, line2 = exporter.export_tle(satrec)
    return f"{satellite.upper()}\n{line1}\n{line2}\n"


def synthetic_set(epoch: datetime, fetched_at: datetime) -> tles.TleSet:
    found = [tles.parse(synthetic_tle(name, norad, epoch, phase), name, norad)
             for (name, norad), phase in zip(tles.SATELLITES.items(), (0.0, 180.0, 90.0))]
    return tles.TleSet(found, "celestrak", fetched_at)


@pytest.fixture
def tle_dirs(tmp_path, monkeypatch):
    """data/tle/ and data/cache/tle/ in a temporary folder, with a copy fetched one day before the test clock."""
    shipped, cache = tmp_path / "tle", tmp_path / "cache_tle"
    clock = datetime.now(timezone.utc).replace(microsecond=0)
    tles.write_copy(synthetic_set(clock - timedelta(days=1), clock - timedelta(hours=2)), shipped)
    monkeypatch.setattr(tles, "SHIPPED_DIR", shipped)
    monkeypatch.setattr(tles, "CACHE_DIR", cache)
    monkeypatch.setenv("SAMPLE_MODE", "true")
    return shipped, cache


def no_network(url):
    raise AssertionError(f"no network call expected, got {url}")


# ---- TLE -----------------------------------------------------------------------------------------------------------

def test_norad_numbers_and_the_celestrak_url():
    assert tles.SATELLITES == {"Sentinel-2A": 40697, "Sentinel-2B": 42063, "Sentinel-2C": 60989}
    assert tles.URL.format(norad=42063) == "https://celestrak.org/NORAD/elements/gp.php?CATNR=42063&FORMAT=TLE"


def test_a_tle_is_checked_before_use():
    text = synthetic_tle("Sentinel-2B", 42063, NOW)
    good = tles.parse(text, "Sentinel-2B", 42063)
    assert good.name == "SENTINEL-2B" and tles.checksum(good.line1) == int(good.line1[68])
    assert abs((good.epoch - NOW).total_seconds()) < 1.0
    name, line1, line2 = text.strip().splitlines()
    broken = line1[:30] + ("1" if line1[30] != "1" else "2") + line1[31:]
    with pytest.raises(tles.TleError, match="checksum"):
        tles.parse("\n".join((name, broken, line2)), "Sentinel-2B", 42063)
    with pytest.raises(tles.TleError, match="catalogue number"):
        tles.parse(text, "Sentinel-2B", 40697)
    with pytest.raises(tles.TleError, match="names it"):
        tles.parse(text, "Sentinel-2C", 42063)
    with pytest.raises(tles.TleError, match="two element lines"):
        tles.parse("\n".join((name, line1)), "Sentinel-2B", 42063)


def test_sample_mode_reads_the_dated_copy_and_never_the_network(tmp_path):
    shipped, cache = tmp_path / "tle", tmp_path / "cache"
    assert tles.current(True, NOW, fetch=no_network, shipped=shipped, cache=cache) == (None, tles.NO_COPY)
    path = tles.write_copy(synthetic_set(NOW - timedelta(days=2), NOW - timedelta(days=1)), shipped)
    assert path.name == "sentinel-2_20261023T1200Z.json"
    found, reason = tles.current(True, NOW, fetch=no_network, shipped=shipped, cache=cache)
    assert reason is None and found.source == "cache" and len(found.tles) == 3
    assert found.fetched_at == NOW - timedelta(days=1)


def test_a_new_copy_replaces_the_older_one(tmp_path):
    tles.write_copy(synthetic_set(NOW - timedelta(days=9), NOW - timedelta(days=8)), tmp_path)
    newest = tles.write_copy(synthetic_set(NOW - timedelta(days=2), NOW - timedelta(days=1)), tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == [newest.name]


def test_live_mode_fetches_when_the_copy_is_old_and_falls_back_when_celestrak_fails(tmp_path):
    shipped, cache = tmp_path / "tle", tmp_path / "cache"
    tles.write_copy(synthetic_set(NOW - timedelta(days=3), NOW - timedelta(hours=3)), shipped)
    found, _ = tles.current(False, NOW, fetch=no_network, shipped=shipped, cache=cache)  # young copy: no fetch
    assert found.source == "cache"

    later = NOW + timedelta(days=2)
    asked = []

    def celestrak(url):
        asked.append(url)
        norad = int(url.split("CATNR=")[1].split("&")[0])
        name = next(n for n, i in tles.SATELLITES.items() if i == norad)
        return synthetic_tle(name, norad, later - timedelta(hours=6))

    found, _ = tles.current(False, later, fetch=celestrak, shipped=shipped, cache=cache)
    assert found.source == "celestrak" and len(asked) == 3 and found.file is not None
    assert [p.name for p in cache.iterdir()] == ["sentinel-2_20261026T1200Z.json"]

    def down(url):
        raise tles.TleError(f"{url}: connection refused")

    found, _ = tles.current(False, later + timedelta(days=3), fetch=down, shipped=shipped, cache=cache)
    assert found.source == "cache" and "CelesTrak did not answer" in found.problems[-1]
    empty = tmp_path / "nothing"
    found, reason = tles.current(False, NOW, fetch=down, shipped=empty, cache=empty)
    assert found is None and reason.startswith("CelesTrak did not answer")


# ---- passes --------------------------------------------------------------------------------------------------------

def test_the_threshold_is_the_edge_of_the_swath():
    assert settings.MIN_ELEVATION_DEG == round(settings.swath_edge_elevation_deg(), 2)
    assert 78.0 < settings.MIN_ELEVATION_DEG < 78.5
    assert settings.swath_edge_elevation_deg(swath_km=0.0) == pytest.approx(90.0)


def test_sidereal_time_is_the_one_sgp4_uses():
    for jd in (2451545.0, 2460000.25, 2461337.731):
        assert passes.gmst(np.array([jd]))[0] == pytest.approx(gstime(jd), abs=1e-9)


def geodetic(position):
    x, y, z = position
    e2 = passes.WGS84_F * (2.0 - passes.WGS84_F)
    p, lat = math.hypot(x, y), math.atan2(z, math.hypot(x, y) * (1.0 - e2))
    for _ in range(20):
        n = passes.WGS84_A_KM / math.sqrt(1.0 - e2 * math.sin(lat) ** 2)
        height = p / math.cos(lat) - n
        lat = math.atan2(z, p * (1.0 - e2 * n / (n + height)))
    return math.degrees(lat), math.degrees(math.atan2(y, x))


def test_a_satellite_straight_above_the_plot_is_at_90_degrees():
    tle = tles.parse(synthetic_tle("Sentinel-2A", 40697, NOW), "Sentinel-2A", 40697)
    jd, fr = passes.julian(NOW + timedelta(minutes=17))
    fixed, _, error = passes.earth_fixed(tle.satrec(), jd, np.array([fr]))
    lat, lon = geodetic(fixed[0])
    assert error[0] == 0
    assert passes.elevation_deg(tle.satrec(), jd, np.array([fr]), passes.observer(lat, lon))[0] == pytest.approx(
        90.0, abs=0.01)


def test_the_sun():
    solstice_noon = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    assert passes.sun_elevation_deg(solstice_noon, 0.0, 0.0) == pytest.approx(90.0 - 23.44, abs=0.6)
    assert passes.sun_elevation_deg(datetime(2026, 10, 24, 0, 30, tzinfo=timezone.utc), *SOUBRE) < -60.0


def test_daylight_passes_over_the_plot_with_their_window():
    tle = tles.parse(synthetic_tle("Sentinel-2B", 42063, NOW), "Sentinel-2B", 42063)
    every = passes.passes_of(tle, *SOUBRE, NOW, 30, daylight_only=False)
    day = passes.passes_of(tle, *SOUBRE, NOW, 30)
    assert day and len(every) > len(day) and all(not p["daylight"] for p in every if p not in day)
    satrec, site = tle.satrec(), passes.observer(*SOUBRE)
    for found in day:
        assert found["daylight"] and found["sun_elevation_deg"] >= 0
        assert found["max_elevation_deg"] >= settings.MIN_ELEVATION_DEG
        assert found["direction"] == "descending"  # a 10:30 descending node: the daylight passes go south
        start, peak, end = (tles.parse_iso(found[k]) for k in ("start", "time", "end"))
        assert start < peak < end and (end - start).total_seconds() < 120
        around = [peak + timedelta(seconds=s) for s in (-5, 0, 5)]
        jd, _ = passes.julian(NOW)
        seen = passes.elevation_deg(satrec, jd, np.array([passes.julian(t)[1] + passes.julian(t)[0] - jd
                                                          for t in around]), site)
        assert seen[1] >= max(seen[0], seen[2])
    times = [p["time"] for p in day]
    assert times == sorted(times)


def test_the_passes_answer(tmp_path):
    shipped = tmp_path / "tle"
    tles.write_copy(synthetic_set(NOW - timedelta(days=2), NOW - timedelta(days=1)), shipped)
    options = dict(days=30, now=NOW, sample=True, fetch=no_network, shipped=shipped, cache=tmp_path / "c")
    answer = passes.report(*SOUBRE, **options)
    assert answer == passes.report(*SOUBRE, **options)  # same orbits, same clock: same answer
    assert answer["status"] == "success" and answer["tle_source"] == "cache"
    assert answer["tle_epoch"] == "2026-10-22T12:00:00Z" and answer["tle_age_days"] == 2.0
    assert answer["tle_stale"] is False and answer["tle_file"].endswith("sentinel-2_20261023T1200Z.json")
    assert {p["satellite"] for p in answer["passes"]} == set(tles.SATELLITES)
    for found in answer["passes"]:
        assert set(found) >= {"satellite", "time", "max_elevation_deg", "daylight"}
        assert NOW < tles.parse_iso(found["time"]) < NOW + timedelta(days=30)
    assert "pass over the plot" in answer["note"] and "guarantee" not in json.dumps(answer)
    with pytest.raises(ValueError):
        passes.report(*SOUBRE, **{**options, "days": 31})
    with pytest.raises(ValueError):
        passes.report(95.0, 0.0, **options)


def test_no_tle_is_said_never_made_up(tmp_path):
    answer = passes.report(*SOUBRE, now=NOW, sample=True, fetch=no_network, shipped=tmp_path, cache=tmp_path)
    assert answer["status"] == "not_queried" and answer["reason"] == tles.NO_COPY and answer["passes"] == []
    assert answer["tle_epoch"] is None and answer["tle_source"] is None


def test_the_passes_routes(tle_dirs):
    client = TestClient(app)
    answer = client.get("/api/plots/passes", params={"lat": SOUBRE[0], "lon": SOUBRE[1], "days": 30}).json()
    assert answer["status"] == "success" and answer["tle_source"] == "cache" and answer["passes"]
    by_id = client.get("/api/plots/passes", params={"plot_id": "P-042"}).json()
    assert by_id["plot_id"] == "P-042" and by_id["lat"] == 5.73
    assert client.get("/api/plots/passes", params={"lat": 5.73, "lon": -6.5, "days": 31}).status_code == 422
    assert client.get("/api/plots/passes", params={"lat": 5.73}).status_code == 422
    response = client.get("/api/plots/passes.ics", params={"plot_id": "P-042"})
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/calendar")
    found = Calendar.from_ical(response.text).walk("VEVENT")
    assert len(found) == len(by_id["passes"])
    assert all(str(e["SUMMARY"]).endswith("pass over plot P-042") for e in found)
    assert all(isinstance(e["DTSTART"].dt, datetime) for e in found)


def test_the_passes_calendar_without_a_tle_says_why(tmp_path, monkeypatch):
    monkeypatch.setattr(tles, "SHIPPED_DIR", tmp_path / "none")
    monkeypatch.setattr(tles, "CACHE_DIR", tmp_path / "none")
    monkeypatch.setenv("SAMPLE_MODE", "true")
    response = TestClient(app).get("/api/plots/passes.ics", params={"lat": 5.73, "lon": -6.522})
    assert response.status_code == 503 and response.json()["detail"] == tles.NO_COPY


# ---- calendar files ------------------------------------------------------------------------------------------------

def test_an_ics_file_is_valid_rfc_5545():
    long_text = "Café, champ; ligne\nnouvelle \\ " + "é" * 120
    found = [
        {"kind": "pass", "uid": "p1@greentrace", "title": "Sentinel-2B pass over plot P-042",
         "start": NOW, "end": NOW + timedelta(seconds=40), "description": long_text},
        {"kind": "expiry", "uid": "e1@greentrace", "title": "Evidence expiry: alerts, P-014",
         "start": date(2026, 11, 23), "end": date(2026, 11, 24), "description": "a, b; c"},
    ]
    text = ics.calendar(found, NOW, "GreenTrace test")
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")
    assert all(len(line.encode("utf-8")) <= 75 for line in text.split("\r\n"))
    parsed = Calendar.from_ical(text)
    assert str(parsed["VERSION"]) == "2.0" and str(parsed["PRODID"]) == ics.PRODID
    first, second = parsed.walk("VEVENT")
    assert str(first["DESCRIPTION"]) == long_text and first["DTSTART"].dt == NOW
    assert first["DTSTAMP"].dt == NOW and str(first["UID"]) == "p1@greentrace"
    assert second["DTSTART"].dt == date(2026, 11, 23) and not isinstance(second["DTSTART"].dt, datetime)
    assert str(second["SUMMARY"]) == "Evidence expiry: alerts, P-014"


def test_the_google_link_carries_the_title_and_the_dates_only():
    event = {"title": "Follow up supplier letter, day 3: plot P-033", "start": date(2026, 10, 27),
             "end": date(2026, 10, 28), "description": "Dear Yobouakro (F-034), ..."}
    url = ics.google_url(event)
    assert url.startswith("https://calendar.google.com/calendar/render?action=TEMPLATE&text=")
    query = parse_qs(urlparse(url).query)
    assert set(query) == {"action", "text", "dates"} and query["dates"] == ["20261027/20261028"]
    assert query["text"] == [event["title"]] and "Yobouakro" not in url and "F-034" not in url
    timed = ics.google_url({"title": "x", "start": NOW, "end": NOW + timedelta(seconds=40)})
    assert "dates=20261024T120000Z/20261024T120040Z" in timed


# ---- a run's calendar ----------------------------------------------------------------------------------------------

def finished(request: dict, tmp_path) -> orchestrator.Investigation:
    out = run_case(request, log_dir=tmp_path / "logs", stream=io.StringIO(), memory_engine=MemoryEngine())
    return orchestrator.RUNS[out["run_id"]]


def case(number: int, run_id: str) -> dict:
    return {"run_id": run_id, "input": {"claim": "100% of our cocoa is deforestation-free since 2021."},
            "options": {"sample_mode": True, "case": number}}


def test_an_unverifiable_run_gets_a_pass_its_expiry_dates_and_two_follow_ups(tle_dirs, tmp_path):
    run = finished(case(6, "geo-cal-6"), tmp_path)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    answer, found = events.run_events(run, now=now, sample=True)
    assert answer["verdict"] == UNVERIFIABLE and answer["run_date_from"] == "the first line of logs/geo-cal-6.jsonl"
    run_day = tles.parse_iso(answer["run_date"]).date()
    kinds = [e["kind"] for e in answer["events"]]
    assert kinds.count("pass") == 1 and kinds.count("follow_up") == 2

    first_pass = next(e for e in answer["events"] if e["kind"] == "pass")
    assert first_pass["title"].endswith("pass over plot P-033")
    p033 = next(p for p in run.evidence.plots if p["plot_id"] == "P-033")
    expected = passes.predict(tles.current(True, now)[0], p033["lat"], p033["lon"], now, 30)[0]
    assert first_pass["start"] == expected["start"]

    alerts = next(e for e in answer["events"] if e["title"].startswith("Evidence expiry: alert data"))
    assert alerts["start"] == (run_day + timedelta(days=settings.ALERTS_VALID_DAYS)).isoformat()
    assert "ALERTS_VALID_DAYS" in alerts["description"]
    documents = sorted(e["title"].split("document ")[1].split(" ")[0] for e in answer["events"]
                       if "document" in e["title"])
    assert documents == ["ngo_2025", "rse_2025"]
    assert {e["start"] for e in answer["events"] if "document" in e["title"]} == {"2026-12-31"}

    follow = [e for e in answer["events"] if e["kind"] == "follow_up"]
    assert [e["start"] for e in follow] == [(run_day + timedelta(days=d)).isoformat() for d in (3, 7)]
    letter = run.output["supplier_letters"][0]["letter"]
    assert all(e["description"].endswith(letter) for e in follow)
    names = [s["name"] for s in run.evidence.suppliers] + [s["supplier_id"] for s in run.evidence.suppliers]
    for event in answer["events"]:
        assert set(event) >= {"kind", "title", "start", "google_calendar_url"}
        assert event["kind"] in ("pass", "expiry", "follow_up")
        assert not any(name in event["google_calendar_url"] for name in names)
        assert "Dear" not in event["google_calendar_url"]

    text = ics.calendar(found, now, "test")
    assert len(Calendar.from_ical(text).walk("VEVENT")) == len(answer["events"])


def test_a_proven_run_has_no_follow_up(tle_dirs, tmp_path):
    answer, _ = events.run_events(finished(case(1, "geo-cal-1"), tmp_path), sample=True)
    assert answer["verdict"] == PROVEN and "follow_up" not in [e["kind"] for e in answer["events"]]
    assert [e["title"] for e in answer["events"] if e["kind"] == "pass"] == [
        next(e["title"] for e in answer["events"] if e["kind"] == "pass")]
    assert "the supply area of run geo-cal-1" in answer["events"][0]["title"]


def test_alerts_that_did_not_answer_are_not_scheduled(tle_dirs, tmp_path):
    answer, _ = events.run_events(finished(case(20, "geo-cal-20"), tmp_path), sample=True)
    assert {"kind": "expiry", "source": "alerts", "reason": "the alert source did not answer in this run"} in answer[
        "not_scheduled"]
    assert not any("alert data" in e["title"] for e in answer["events"])


def test_a_plot_measured_on_the_maps_gets_a_version_check(tle_dirs, tmp_path):
    run = finished({**EXAMPLE_05, "run_id": "geo-cal-05"}, tmp_path)
    answer, _ = events.run_events(run, sample=True)
    built = date.fromisoformat(load_meta()["built_on"])
    checks = {e["title"]: e["start"] for e in answer["events"] if "still current" in e["title"]}
    assert checks == {
        "Evidence expiry: check that JRC GFC2020 v4 is still current (run geo-cal-05)":
            events.add_months(built, 12).isoformat(),
        "Evidence expiry: check that Hansen GFC v1.12 is still current (run geo-cal-05)":
            events.add_months(built, 12).isoformat(),
    }
    assert answer["verdict"] == CONTRADICTED and "follow_up" not in [e["kind"] for e in answer["events"]]


def test_without_its_trace_a_run_says_so_and_without_tle_the_pass_says_why(tmp_path, monkeypatch):
    monkeypatch.setattr(tles, "SHIPPED_DIR", tmp_path / "none")
    monkeypatch.setattr(tles, "CACHE_DIR", tmp_path / "none")
    run = finished(case(6, "geo-cal-notrace"), tmp_path)
    (tmp_path / "logs" / "geo-cal-notrace.jsonl").unlink()
    answer, _ = events.run_events(run, now=NOW, sample=True)
    assert answer["run_date"] == "2026-10-24T12:00:00Z"
    assert answer["run_date_from"] == "logs/geo-cal-notrace.jsonl not found: dates counted from now"
    assert {"kind": "pass", "reason": tles.NO_COPY} in answer["not_scheduled"]
    assert [e["start"] for e in answer["events"] if e["kind"] == "follow_up"] == ["2026-10-27", "2026-10-31"]


def test_the_run_calendar_routes(tle_dirs, tmp_path):
    client = TestClient(app)
    run = finished(case(6, "geo-cal-route"), tmp_path)
    answer = client.get(f"/runs/{run.ctx.run_id}/calendar").json()
    assert {e["kind"] for e in answer["events"]} == {"pass", "expiry", "follow_up"}
    response = client.get(f"/runs/{run.ctx.run_id}/calendar.ics")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/calendar")
    assert [str(e["SUMMARY"]) for e in Calendar.from_ical(response.text).walk("VEVENT")] == [
        e["title"] for e in answer["events"]]
    assert client.get("/runs/no-such-run/calendar").status_code == 404
    assert client.get("/runs/no-such-run/calendar.ics").status_code == 404


def test_months_are_added_on_the_calendar():
    assert events.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert events.add_months(date(2026, 10, 22), 12) == date(2027, 10, 22)
    assert events.declared_date("rse_2025")[0] == date(2025, 12, 31)
    assert events.declared_date("annual_report") is None


# ---- convergence ---------------------------------------------------------------------------------------------------

def test_p042_two_of_two_sources_queried_agree_in_sample_mode():
    out = convergence.converge(plots.by_id("P-042"), sample=True, whisp_key=None)
    jrc, hansen, tiles, whisp_entry = out["sources"]
    assert (jrc["name"], jrc["says"], jrc["value"], jrc["unit"], jrc["status"]) == (
        "JRC GFC2020", "forest_2020", MEASURED["P-042"]["forest_2020_ha"], "ha", "queried")
    after = sum(ha for y, ha in MEASURED["P-042"]["loss_ha_by_year"].items() if int(y) > 2020)
    assert hansen["says"] == "loss_after_2020" and hansen["value"] == pytest.approx(after, abs=1e-3)
    assert tiles["status"] == "not_queried" and "sample mode makes no network call" in tiles["reason"]
    assert whisp_entry["status"] == "not_queried" and whisp_entry["reason"] == "WHISP_API_KEY is not set"
    assert (out["agree"], out["queried"]) == (2, 2)
    assert out["conclusion"] == "2 of 2 sources queried agree: forest in 2020 and loss after 2020"
    assert "%" not in json.dumps(out["conclusion"]) and "certainty" not in json.dumps(out["sources"])


def test_p041_the_sources_queried_do_not_agree():
    out = convergence.converge(plots.by_id("P-041"), sample=True)
    assert (out["agree"], out["queried"]) == (1, 2)
    assert out["conclusion"] == ("the 2 sources queried do not agree: JRC GFC2020 says not forest in 2020; "
                                 "Hansen GFC v1.12 says tree cover loss after 2020")


def test_outside_the_maps_no_source_is_queried():
    out = convergence.converge(plots.by_id("P-014"), sample=True)
    assert (out["agree"], out["queried"]) == (0, 0)
    assert [s["status"] for s in out["sources"]] == ["unavailable", "unavailable", "not_queried", "not_queried"]
    assert out["conclusion"].startswith("no source could be queried for this plot (JRC GFC2020: no reference map")


class FakeWhisp:
    """The Whisp API as its code answers (queued, then completed), in this test file only."""

    def __init__(self, properties, queued=1, error=None):
        self.properties, self.queued, self.error, self.calls = properties, queued, error, []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, json.loads(body) if body else None))
        if self.error:
            return 401, {"code": "auth_invalid_api_key", "message": "Invalid or expired API key."}
        if self.queued:
            self.queued -= 1
            return 202, {"code": "analysis_queued", "message": "queued", "context": {"token": "tok-1"}}
        return 200, {"code": "analysis_completed", "message": "done", "context": {"token": "tok-1"},
                     "data": {"type": "FeatureCollection",
                              "features": [{"type": "Feature", "properties": self.properties, "geometry": None}]}}


DEFORESTED = {"Ind_01_treecover": "yes", "Ind_04_disturbance_after_2020": "yes", "risk_pcrop": "high",
              "EUFO_2020": 4.5}


def live_tiles(tmp_path, year):
    tile = tile_with(year, year)
    return gfw.Tiles(url="http://tiles.test/{z}/{x}/{y}.png?implementation=tcd_30", cache_dir=tmp_path / "gfw",
                     fetch=lambda url: tile)


def test_live_mode_four_sources_and_the_whisp_api_as_its_code_shows(tmp_path):
    fake = FakeWhisp(DEFORESTED)
    out = convergence.converge(plots.by_id("P-042"), sample=False, remote=live_tiles(tmp_path, 2022),
                               whisp_key="key-123", whisp_transport=fake, sleep=lambda s: None)
    assert [s["status"] for s in out["sources"]] == ["queried"] * 4
    assert out["sources"][2]["says"] == "loss_after_2020" and out["sources"][2]["same_layer_as"] == "Hansen GFC v1.12"
    assert out["sources"][3]["says"] == "tree_cover_2020_and_disturbance_after_2020"
    assert out["conclusion"] == "4 of 4 sources queried agree: forest in 2020 and loss after 2020"
    (method, url, headers, body), (poll_method, poll_url, _, _) = fake.calls
    assert (method, url) == ("POST", "https://whisp.openforis.org/api/submit/geojson")
    assert headers["X-API-KEY"] == "key-123" and body["type"] == "FeatureCollection"
    assert body["analysisOptions"]["unitType"] == "ha" and body["features"][0]["geometry"]["type"] == "Polygon"
    assert (poll_method, poll_url) == ("GET", "https://whisp.openforis.org/api/status/tok-1")


def test_a_dissenting_source_is_named(tmp_path):
    fake = FakeWhisp({**DEFORESTED, "Ind_04_disturbance_after_2020": "no"}, queued=0)
    out = convergence.converge(plots.by_id("P-042"), sample=False, remote=live_tiles(tmp_path, 2022),
                               whisp_key="key", whisp_transport=fake)
    assert out["conclusion"] == ("3 of 4 sources queried agree: forest in 2020 and loss after 2020; "
                                 "Whisp (FAO/OpenForis) says tree cover in 2020, no disturbance after 2020")


def test_whisp_errors_and_sample_mode_are_said(tmp_path):
    refused = convergence.converge(plots.by_id("P-042"), sample=False, remote=live_tiles(tmp_path, 2019),
                                   whisp_key="bad", whisp_transport=FakeWhisp(DEFORESTED, error=True))
    entry = refused["sources"][3]
    assert entry["status"] == "unavailable" and "auth_invalid_api_key" in entry["reason"]
    assert refused["sources"][2]["says"] == "no_loss_after_2020"  # the fake tiles show loss in 2019 only
    asleep = FakeWhisp(DEFORESTED, queued=10_000)
    stuck = convergence.converge(plots.by_id("P-042"), sample=False, whisp_key="k", whisp_transport=asleep,
                                 sleep=lambda s: None)
    assert "still analysis_queued" in stuck["sources"][3]["reason"]
    assert stuck["sources"][2]["status"] == "not_queried"  # no tiles given
    sample = convergence.converge(plots.by_id("P-042"), sample=True, whisp_key="k",
                                  whisp_transport=FakeWhisp(DEFORESTED))
    assert sample["sources"][3]["status"] == "not_queried" and sample["sources"][3]["reason"].startswith("sample mode")
    assert whisp.api_key({"WHISP_API_KEY": "  "}) is None and whisp.api_key({"WHISP_API_KEY": "k"}) == "k"


def test_the_convergence_routes(monkeypatch):
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.delenv("WHISP_API_KEY", raising=False)
    client = TestClient(app)
    answer = client.get("/api/plots/convergence", params={"plot_id": "P-042"}).json()
    assert set(answer) >= {"sources", "agree", "queried", "conclusion"}
    assert all(set(s) >= {"name", "says", "value", "unit", "status"} for s in answer["sources"])
    drawn = {"type": "Feature", "properties": {"area_ha": 3.0},
             "geometry": {"type": "Point", "coordinates": [SOUBRE[1], SOUBRE[0]]}}
    posted = client.post("/api/plots/convergence", json=drawn).json()
    assert posted["plot"]["plot_id"] == "P-900" and posted["queried"] == 2
    assert client.post("/api/plots/convergence", json={"type": "Point"}).status_code == 422
    assert client.get("/api/plots/convergence", params={"plot_id": "P-999"}).status_code == 404


# ---- time slider ---------------------------------------------------------------------------------------------------

def first_year_over_threshold(measured: dict, area_ha: float) -> int:
    """From the measure committed in data/reference_cells.meta.json, without the code under test."""
    threshold, total = min(0.5, 0.1 * area_ha), 0.0
    for year, ha in sorted(measured["forest_loss_ha_by_year"].items()):
        if int(year) > 2020:
            total += ha
            if total >= threshold:
                return int(year)
    raise AssertionError("never over the threshold")


def test_p042_flips_to_contradicted_the_year_the_loss_crosses_the_threshold():
    out = time_slider.series(plots.by_id("P-042"))
    flip = first_year_over_threshold(MEASURED["P-042"], 10.0)
    assert out["first_contradicted_year"] == flip == 2021
    for year in out["years"]:
        if year["year"] < flip:
            assert year["verdict"] in (PROVEN, UNVERIFIABLE) and year["loss_after_cutoff_ha"] < 0.5
        else:
            assert year["verdict"] == CONTRADICTED and year["loss_after_cutoff_ha"] >= 0.5
    assert out["years"][-2]["loss_after_cutoff_ha"] == MEASURED["P-042"]["loss_after_2020_ha"]  # 2024: today's map
    assert out["years"][-1]["notes"][-1] == "Hansen v1.12 maps loss up to 2024: 2025 shows the 2024 state"


def test_the_slider_is_the_rule_unchanged_and_reproducible():
    plot = plots.by_id("P-042")
    today = check_plot(plot, read_cells(*plot_box(plot)), {2024})[0]
    at = time_slider.verdict_at(plot, 2024)
    assert (at["verdict"], at["rule"], at["code"], at["detail"]) == (today.verdict, today.rule, today.code,
                                                                     today.detail)
    assert time_slider.verdict_at(plot, 2022) == time_slider.verdict_at(plot, 2022)
    assert {y["verdict"] for y in time_slider.series(plots.by_id("P-041"))["years"]} == {PROVEN, UNVERIFIABLE}
    with pytest.raises(ValueError):
        time_slider.verdict_at(plot, 2015)


def test_the_slider_routes():
    client = TestClient(app)
    before = client.get("/api/plots/verdict_at", params={"plot_id": "P-042", "year": 2020}).json()
    after = client.get("/api/plots/verdict_at", params={"plot_id": "P-042", "year": 2022}).json()
    assert {k: before[k] for k in ("year", "loss_after_cutoff_ha", "verdict")} == {
        "year": 2020, "loss_after_cutoff_ha": 0.0, "verdict": PROVEN}
    assert after["year"] == 2022 and after["verdict"] == CONTRADICTED and after["loss_after_cutoff_ha"] > 0.5
    assert client.get("/api/plots/verdict_at", params={"plot_id": "P-042", "year": 2026}).status_code == 422
    assert client.get("/api/plots/verdict_series", params={"plot_id": "P-042"}).json()[
        "first_contradicted_year"] == 2021
