"""Check, on a machine that reaches them, the two public map services the product relies on outside Soubre.

    python -m scripts.probe_gfw

1. Global Forest Watch tree cover loss tiles (app/checks/gfw.py): reads the tiles over the committed
   Hansen cells of Soubre (data/reference_cells.csv.gz, read from the Hansen GeoTIFF itself) and compares
   the loss year cell by cell. The decoding is right when the agreement is high; a few percent of
   disagreement is expected from the two grids (web mercator pixels of about 38 m, Hansen cells of 28 m).
2. How GFW serves a tile with no loss (an ocean tile): HTTP status and size, so app/checks/gfw.py can
   decide whether a 404 may mean "no loss" (today it does not: a 404 makes the area unavailable).
3. The EU JRC GFC2020 WMS: whether its capabilities answer, and the layer names it lists.

Prints what it finds; writes nothing but the tile cache (data/cache/gfw, git-ignored).
"""
import sys
import urllib.error
import urllib.request

from app.checks import gfw, landcover

BOX = (5.7200, 5.7400, -6.5300, -6.5100)  # around P-042: 2020 forest cleared in 2021-2024
OCEAN = (2, 2)  # zoom 12 tile in the Gulf of Guinea, far from land
PNG_SIGNATURE = b"\x89PNG"
JRC_WMS = "https://ies-ows.jrc.ec.europa.eu/iforce/gfc2020/wms.py?SERVICE=WMS&REQUEST=GetCapabilities"


def main() -> int:
    committed = {(c["lat"], c["lon"]): c["loss_year"] for c in landcover.read_cells(*BOX)}
    try:
        reading = gfw.Tiles().cells(*BOX)
    except gfw.GfwUnavailable as error:
        print(f"1. GFW tiles: UNAVAILABLE ({error})")
        return 2
    read = {(c["lat"], c["lon"]): c["loss_year"] for c in reading.cells}
    both = [k for k in committed if k in read]
    same_year = sum(committed[k] == read[k] for k in both)
    loss_agree = sum(bool(committed[k]) == bool(read[k]) for k in both)
    print(f"1. GFW tiles: {len(reading.tiles)} tile(s), {len(both)} cells compared with the Hansen GeoTIFF cells")
    print(f"   same loss year: {same_year / len(both):.1%}; loss / no loss agree: {loss_agree / len(both):.1%}")
    years = sorted({y for y in read.values() if y})
    print(f"   loss years decoded: {years}")

    url = gfw.TILE_URL.format(z=gfw.ZOOM, x=OCEAN[0], y=2 ** gfw.ZOOM // 2)
    try:
        with urllib.request.urlopen(url, timeout=gfw.TIMEOUT_S) as response:
            body = response.read()
            kind = "a PNG" if body[:4] == PNG_SIGNATURE else "not a PNG"
            print(f"2. empty tile: HTTP {response.status}, {len(body)} bytes, {kind}")
    except urllib.error.HTTPError as error:
        print(f"2. empty tile: HTTP {error.code}")
    except OSError as error:
        print(f"2. empty tile: {error}")

    try:
        with urllib.request.urlopen(JRC_WMS, timeout=30) as response:
            text = response.read().decode("utf-8", "replace")
        names = [part.split("</Name>")[0] for part in text.split("<Name>")[1:]]
        print(f"3. JRC GFC2020 WMS: answers; layers: {names[:12]}")
    except (OSError, ValueError) as error:
        print(f"3. JRC GFC2020 WMS: does not answer ({error})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
