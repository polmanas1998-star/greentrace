"""Tree cover loss anywhere: the Hansen/UMD layer as Global Forest Watch serves it, in public tiles.

Outside data/reference_cells.csv.gz (Soubre), live mode reads the loss year from the public,
key-free tiles of Global Forest Watch:

    https://tiles.globalforestwatch.org/umd_tree_cover_loss/v1.12/dynamic/{z}/{x}/{y}.png
        ?implementation=tcd_30

Web Mercator tiles of 256 x 256 RGBA pixels (XYZ scheme, y from the north). DECODING: a pixel
shows loss when its alpha is not 0, and its BLUE channel is the year of loss minus 2000
(1 = 2001 ... 24 = 2024); tcd_30 means loss where tree canopy density in 2000 was at least
30 %, as in Hansen's own definition. This decoding was checked on 14/10/2026 by comparing
tiles with the Hansen GeoTIFF; scripts/probe_gfw.py checks it again against the committed
Hansen cells of Soubre and prints the agreement (run it before relying on a new version).

Each cell of the Hansen grid (0.00025 degree) in the box takes the year of the tile pixel
under its centre (nearest neighbour: at zoom 12 a pixel is about 38 m at the equator, a
Hansen cell about 28 m; a small offset between the two grids is expected and stated). The
cells carry forest_fraction_2020 None: no EU 2020 forest map is read here, so the rules can
call the loss tree cover, never forest (app/rules.py, NO_EU_FOREST_MAP).

Tiles are cached on disk (data/cache/gfw/z/x/y.png, never committed), fetched with a timeout
and one retry; a tile that cannot be read (an HTTP error, 404 included, or not a PNG) makes the
whole box unavailable: never zero.
"""
import hashlib
import math
import os
import struct
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from app.checks.landcover import CELL_DEG, GRID_ORIGIN

ROOT = Path(__file__).resolve().parents[2]
TILE_URL = ("https://tiles.globalforestwatch.org/umd_tree_cover_loss/v1.12/dynamic/{z}/{x}/{y}.png"
            "?implementation=tcd_30")
CACHE_DIR = ROOT / "data" / "cache" / "gfw"
ZOOM = 12  # the deepest zoom of the dynamic tiles: about 38 m a pixel at the equator
TILE_PX = 256
FIRST_YEAR, LAST_YEAR = 2001, 2024  # v1.12
TIMEOUT_S = 15.0
MAX_TILES = 64  # per box: a plot needing more is larger than any plot this product checks
SOURCE = "Hansen/UMD/Google/USGS/NASA GFC v1.12, read from Global Forest Watch tiles (tcd_30)"


class GfwUnavailable(RuntimeError):
    """A tile could not be read: the area has no reference data for now."""


@dataclass
class Reading:
    cells: list[dict]
    tiles: list[dict] = field(default_factory=list)  # {z, x, y, url, sha256, cached}


# ---- tile geometry (web mercator, XYZ) -------------------------------------------------------------------

def tile_xy(lat: float, lon: float, zoom: int = ZOOM) -> tuple[float, float]:
    """Fractional tile coordinates of a point: the integer part is the tile, the rest the pixel / 256."""
    n = 2 ** zoom
    lat_rad = math.radians(max(min(lat, 85.0511), -85.0511))
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n
    return x, y


def decode_year(pixel: tuple[int, ...]) -> int:
    """The loss year a pixel shows, 0 for none (see DECODING above)."""
    r, g, b, a = pixel if len(pixel) == 4 else (*pixel, 255)
    if a == 0 or not FIRST_YEAR - 2000 <= b <= LAST_YEAR - 2000:
        return 0
    return 2000 + b


# ---- reading tiles -----------------------------------------------------------------------------------------

def default_fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "GreenTrace (EUDR plot check)"})
    last: Optional[Exception] = None
    for _ in range(2):  # one retry
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            # A 404 is NOT read as "no loss": a wrong URL would then clear every plot. Until
            # scripts/probe_gfw.py shows how GFW serves an empty tile, it makes the area unavailable.
            last = error
        except (OSError, ValueError) as error:
            last = error
    raise GfwUnavailable(f"{url.split('?')[0]}: {last}")


class Tiles:
    """Tiles from GFW (or GFW_TILE_URL), through a disk cache."""

    def __init__(self, url: Optional[str] = None, cache_dir: Optional[Path] = None,
                 fetch: Callable[[str], bytes] = default_fetch, zoom: int = ZOOM):
        self.url = url or os.environ.get("GFW_TILE_URL") or TILE_URL
        self.cache_dir, self.fetch, self.zoom = cache_dir or CACHE_DIR, fetch, zoom
        self._decoded: dict[tuple[int, int], Optional[list[list[tuple]]]] = {}

    def tile(self, x: int, y: int, record: list[dict]) -> Optional[list[list[tuple]]]:
        """The tile's pixels (None for an empty tile: no loss anywhere in it)."""
        if (x, y) in self._decoded:
            return self._decoded[(x, y)]
        path = self.cache_dir / str(self.zoom) / str(x) / f"{y}.png"
        url = self.url.format(z=self.zoom, x=x, y=y)
        cached = path.exists()
        if cached:
            data = path.read_bytes()
        else:
            data = self.fetch(url)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        record.append({"z": self.zoom, "x": x, "y": y, "url": url, "cached": cached,
                       "sha256": hashlib.sha256(data).hexdigest()})
        try:
            pixels = decode_png(data) if data else None
        except ValueError as error:
            path.unlink(missing_ok=True)  # never keep a tile we cannot read
            raise GfwUnavailable(f"tile {self.zoom}/{x}/{y}: {error}") from error
        self._decoded[(x, y)] = pixels
        return pixels

    def cells(self, lat_min: float, lat_max: float, lon_min: float, lon_max: float) -> Reading:
        """Hansen-grid cells in the box with the loss year read from the tiles, forest unknown."""
        top = math.floor((GRID_ORIGIN[0] - lat_max) / CELL_DEG)
        bottom = math.floor((GRID_ORIGIN[0] - lat_min) / CELL_DEG)
        left = math.floor((lon_min - GRID_ORIGIN[1]) / CELL_DEG)
        right = math.floor((lon_max - GRID_ORIGIN[1]) / CELL_DEG)
        x0, y0 = tile_xy(lat_max, lon_min, self.zoom)
        x1, y1 = tile_xy(lat_min, lon_max, self.zoom)
        if (int(x1) - int(x0) + 1) * (int(y1) - int(y0) + 1) > MAX_TILES:
            raise GfwUnavailable(f"the box needs more than {MAX_TILES} tiles")
        tiles: list[dict] = []
        cells = []
        for row in range(top, bottom + 1):
            lat = round(GRID_ORIGIN[0] - (row + 0.5) * CELL_DEG, 6)
            for col in range(left, right + 1):
                lon = round(GRID_ORIGIN[1] + (col + 0.5) * CELL_DEG, 6)
                fx, fy = tile_xy(lat, lon, self.zoom)
                pixels = self.tile(int(fx), int(fy), tiles)
                year = 0
                if pixels is not None:
                    px = min(int((fx - int(fx)) * TILE_PX), TILE_PX - 1)
                    py = min(int((fy - int(fy)) * TILE_PX), TILE_PX - 1)
                    year = decode_year(pixels[py][px])
                cells.append({"lat": lat, "lon": lon, "forest_fraction_2020": None, "loss_year": year})
        return Reading(cells, tiles)


# ---- a small PNG reader (8-bit RGB, RGBA, grey or palette, not interlaced): no new dependency ----------------

def decode_png(data: bytes) -> list[list[tuple]]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, palette, alpha = 8, b"", None, None
    width = height = depth = colour = interlace = None
    while pos < len(data):
        (length,), kind = struct.unpack(">I", data[pos:pos + 4]), data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]
        elif kind == b"tRNS":
            alpha = list(body)
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
    if depth != 8 or interlace != 0 or colour not in (0, 2, 3, 4, 6):
        raise ValueError(f"unsupported PNG (bit depth {depth}, colour type {colour}, interlace {interlace})")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour]
    raw, stride = zlib.decompress(idat), width * channels
    rows, previous = [], bytearray(stride)
    for r in range(height):
        kind, line = raw[r * (stride + 1)], bytearray(raw[r * (stride + 1) + 1:(r + 1) * (stride + 1)])
        for i in range(stride):
            left = line[i - channels] if i >= channels else 0
            up, corner = previous[i], previous[i - channels] if i >= channels else 0
            if kind == 1:
                line[i] = (line[i] + left) & 255
            elif kind == 2:
                line[i] = (line[i] + up) & 255
            elif kind == 3:
                line[i] = (line[i] + (left + up) // 2) & 255
            elif kind == 4:
                p = left + up - corner
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - corner)
                line[i] = (line[i] + (left if pa <= pb and pa <= pc else up if pb <= pc else corner)) & 255
        rows.append(line)
        previous = line
    out = []
    for line in rows:
        if colour == 3:
            out.append([(*palette[i], alpha[i] if alpha and i < len(alpha) else 255) for i in line])
        elif colour == 0:
            out.append([(v, v, v, 255) for v in line])
        elif colour == 4:
            out.append([(line[i], line[i], line[i], line[i + 1]) for i in range(0, stride, 2)])
        else:
            out.append([tuple(line[i:i + channels]) for i in range(0, stride, channels)])
    return out


def describe(reading: Reading) -> dict[str, Any]:
    """What the receipt keeps of a tile reading: the source, the tiles and their hashes."""
    return {"source": SOURCE, "tiles": reading.tiles, "decoding": "alpha > 0 and blue = loss year - 2000",
            "grid": "nearest tile pixel under each Hansen cell centre"}
