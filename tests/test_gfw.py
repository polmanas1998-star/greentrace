"""Tree cover loss anywhere, from Global Forest Watch tiles: decoding, cache, failures, and the verdicts.

No network: a fake tile server, in this file only, serves PNG tiles made here with the
documented encoding (alpha > 0, blue = loss year - 2000) and counts the requests.
"""
import json
import struct
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from shapely import wkt

import verify
from app import plotcheck
from app.checks import gfw
from app.checks.landcover import CELLS_FILE
from app.rules import NO_EU_FOREST_MAP, UNVERIFIABLE
from app.web.features import map_engine
from app.world import geo

pytestmark = pytest.mark.skipif(not CELLS_FILE.exists(), reason="data/reference_cells.csv.gz not built")
GHANA = (6.6900, -1.6200)  # a plot near Kumasi: far outside the committed maps of Soubre


def png(pixels, filters=(0,)):
    """An 8-bit RGBA PNG from rows of (r, g, b, a), the filters given cycling over the rows."""
    raw, previous = b"", bytes(len(pixels[0]) * 4)
    for n, row in enumerate(pixels):
        line = bytes(v for px in row for v in px)
        kind = filters[n % len(filters)]
        if kind == 2:  # "up": each byte minus the byte above
            raw += b"\x02" + bytes((a - b) & 255 for a, b in zip(line, previous))
        else:
            raw += b"\x00" + line
        previous = line
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    header = struct.pack(">IIBBBBB", len(pixels[0]), len(pixels), 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def tile_with(year_west, year_east):
    """A tile whose west half shows loss in year_west and east half in year_east (0: none)."""
    def px(year):
        return (228, 30, year - 2000, 255) if year else (0, 0, 0, 0)
    return png([[px(year_west)] * 128 + [px(year_east)] * 128 for _ in range(256)], filters=(0, 2))


class FakeTiles:
    """The tiles a test wants, per (x, y); anything else answers 404. Counts every request."""

    def __init__(self):
        self.tiles, self.requests = {}, []
        handler = self._handler()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/loss/{{z}}/{{x}}/{{y}}.png?implementation=tcd_30"

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                fake.requests.append(self.path)
                z, x, y = self.path.split("?")[0].split("/")[2:5]
                body = fake.tiles.get((int(x), int(y.split(".")[0])))
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        return Handler

    def close(self):
        self.server.shutdown()


@pytest.fixture
def fake():
    server = FakeTiles()
    yield server
    server.close()


def ghana_tile():
    x, y = gfw.tile_xy(*GHANA)
    return int(x), int(y)


def square(plot_id, lat, lon, area):
    ring = [[x, y] for x, y in wkt.loads(geo.square_wkt(lat, lon, area)).exterior.coords]
    return {"type": "Feature", "properties": {"plot_id": plot_id, "supplier": "Coop Ashanti"},
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


def live_check(fake, tmp_path, *features, batch_id="gfw-test"):
    tiles = gfw.Tiles(url=fake.url, cache_dir=tmp_path / "cache")
    return plotcheck.check({"type": "FeatureCollection", "features": list(features)}, batch_id,
                           map_engine(sample=True), year=2026, remote=tiles, log=lambda record: None)


# ---- the tile itself -----------------------------------------------------------------------------------------

def test_the_documented_decoding():
    assert gfw.decode_year((228, 30, 22, 255)) == 2022
    assert gfw.decode_year((228, 30, 1, 255)) == 2001 and gfw.decode_year((228, 30, 24, 255)) == 2024
    assert gfw.decode_year((228, 30, 22, 0)) == 0  # transparent: no loss
    assert gfw.decode_year((228, 30, 0, 255)) == 0 and gfw.decode_year((228, 30, 40, 255)) == 0


def test_tile_coordinates_are_the_xyz_scheme():
    assert gfw.tile_xy(0.0, 0.0, 1) == (1.0, 1.0)
    x, y = gfw.tile_xy(5.73, -6.522, 12)
    assert (int(x), int(y)) == (1973, 1982)


def test_the_png_reader_reads_what_was_written():
    pixels = [[(r, 2 * r % 256, (3 * r + c) % 256, 255 - c) for c in range(5)] for r in range(4)]
    assert gfw.decode_png(png(pixels, filters=(0, 2))) == pixels
    with pytest.raises(ValueError, match="not a PNG"):
        gfw.decode_png(b"GIF89a")


# ---- the verdicts where no EU forest map exists ----------------------------------------------------------------

def test_loss_after_2020_is_tree_cover_never_forest_so_unverifiable(fake, tmp_path):
    fake.tiles[ghana_tile()] = tile_with(2022, 2022)
    (result,) = live_check(fake, tmp_path, square("GH-1", *GHANA, 5.0))["plots"]
    assert (result["verdict"], result["code"]) == (UNVERIFIABLE, "tree_cover_loss_no_eu_forest_map")
    assert result["sentence"].endswith(f"{NO_EU_FOREST_MAP}.")
    assert result["measure"]["forest_map"] is False and result["measure"]["forest_2020_ha"] is None
    assert list(result["measure"]["loss_ha_by_year"]) == ["2022"]
    assert result["evidence"]["query"] == "GFW" and result["evidence"]["maps"] == gfw.SOURCE


def test_loss_before_2021_only_is_no_issue_and_says_which_map(fake, tmp_path):
    fake.tiles[ghana_tile()] = tile_with(2018, 2018)
    (result,) = live_check(fake, tmp_path, square("GH-2", *GHANA, 5.0))["plots"]
    assert result["verdict"] == plotcheck.NO_ISSUE
    assert result["sentence"] == ("No tree cover loss after 2020 inside GH-2 on the Hansen/UMD map "
                                  "(read from Global Forest Watch; no EU 2020 forest map here).")


def test_soubre_stays_on_the_committed_maps_and_never_asks_gfw(fake, tmp_path):
    p042 = square("P-042", 5.73, -6.522, 10.0)
    (result,) = live_check(fake, tmp_path, p042)["plots"]
    assert result["verdict"] == "CONTRADICTED" and result["evidence"]["query"] == "Q10"
    assert fake.requests == []


def test_tiles_come_from_the_cache_the_second_time(fake, tmp_path):
    fake.tiles[ghana_tile()] = tile_with(2022, 0)
    live_check(fake, tmp_path, square("GH-3", *GHANA, 5.0))
    asked = len(fake.requests)
    assert asked >= 1 and (tmp_path / "cache" / "12").is_dir()
    live_check(fake, tmp_path, square("GH-3", *GHANA, 5.0), batch_id="gfw-again")
    assert len(fake.requests) == asked


def test_a_missing_or_broken_tile_is_no_data_never_zero(fake, tmp_path):
    (missing,) = live_check(fake, tmp_path, square("GH-4", *GHANA, 5.0))["plots"]  # the fake answers 404
    assert (missing["verdict"], missing["code"]) == (UNVERIFIABLE, "no_reference_data")
    assert missing["sentence"] == "GH-4: the reference maps did not answer."
    fake.tiles[ghana_tile()] = b"\x89PNG\r\n\x1a\nnot really"
    (broken,) = live_check(fake, tmp_path / "other", square("GH-5", *GHANA, 5.0))["plots"]
    assert broken["code"] == "no_reference_data"
    assert not list((tmp_path / "other" / "cache").rglob("*.png"))  # an unreadable tile is not kept


def test_sample_mode_reads_no_tile_and_says_how_to_go_further(fake, tmp_path):
    out = plotcheck.check({"type": "FeatureCollection", "features": [square("GH-6", *GHANA, 5.0)]}, "gfw-sample",
                          map_engine(sample=True), year=2026, log=lambda record: None)
    (result,) = out["plots"]
    assert result["code"] == "no_reference_data" and "live mode reads the Hansen/UMD loss anywhere" in result["note"]
    assert fake.requests == []


def test_the_receipt_of_a_tile_reading_re_derives_and_the_trace_names_the_tiles(fake, tmp_path):
    fake.tiles[ghana_tile()] = tile_with(2022, 2019)
    lines = []
    tiles = gfw.Tiles(url=fake.url, cache_dir=tmp_path / "cache")
    plotcheck.check({"type": "FeatureCollection", "features": [square("GH-7", *GHANA, 5.0)]}, "gfw-receipt",
                    map_engine(sample=True), year=2026, remote=tiles, log=lines.append)
    receipt = json.loads(json.dumps(plotcheck.receipt(plotcheck.BATCHES["gfw-receipt"])))
    assert verify.check(receipt)["status"] == "VALID"
    assert [s["query_id"] for s in receipt["sources"]] == ["Q10", "GFW"]
    read = next(line for line in lines if line["action"] == "read_gfw_tiles")
    assert read["provider"] == "gfw" and "from 1 tile(s)" in read["output_summary"]
