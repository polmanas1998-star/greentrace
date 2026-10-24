"""The orbits of Sentinel-2A, 2B and 2C: two-line element sets (TLE) from CelesTrak, and a dated copy.

    python -m app.geo.tle refresh   fetch the three TLEs from CelesTrak, write data/tle/sentinel-2_<UTC time>.json
                                    (the copy sample mode reads: commit it), and remove the older copies there
    python -m app.geo.tle show      what the newest copy holds and how old each TLE is

Where a TLE comes from, in this order, and nothing else:
  sample mode   the newest copy in data/tle/ (committed) or data/cache/tle/ (live fetches, never committed); no network
  live mode     the same copy while it is younger than TLE_REFRESH_AFTER_HOURS, else CelesTrak
                (https://celestrak.org/NORAD/elements/gp.php?CATNR=<id>&FORMAT=TLE), kept in data/cache/tle/;
                a fetch that fails falls back to the copy and says why
No copy and no fetch is "not_queried" with the reason: a TLE is never made up.

Every TLE is checked before it is used: a name line and two element lines, the modulo-10 checksum of both,
the NORAD number asked for, and the name CelesTrak gives it (SENTINEL-2A ...). NORAD numbers checked on
24/10/2026 against public satellite catalogues: 40697 Sentinel-2A (COSPAR 2015-028A), 42063 Sentinel-2B
(2017-013A), 60989 Sentinel-2C (2024-157A).
"""
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from sgp4.api import Satrec

from app.geo import settings

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_DIR = ROOT / "data" / "tle"
CACHE_DIR = ROOT / "data" / "cache" / "tle"
URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR={norad}&FORMAT=TLE"
SATELLITES = {"Sentinel-2A": 40697, "Sentinel-2B": 42063, "Sentinel-2C": 60989}
FILE_PREFIX = "sentinel-2_"
J2000_JD = 2451545.0
J2000 = datetime(2000, 1, 1, 12, tzinfo=timezone.utc)
NO_COPY = ("no TLE copy in data/tle/ yet, and sample mode makes no network call: run "
           "python -m app.geo.tle refresh where CelesTrak can be reached, and commit the file it writes")


class TleError(ValueError):
    """A TLE that cannot be fetched or does not pass the checks."""


@dataclass(frozen=True)
class Tle:
    satellite: str
    norad: int
    name: str      # as CelesTrak gives it, e.g. SENTINEL-2B
    line1: str
    line2: str

    def satrec(self) -> Satrec:
        return Satrec.twoline2rv(self.line1, self.line2)

    @property
    def epoch(self) -> datetime:
        sat = self.satrec()
        return J2000 + timedelta(days=(sat.jdsatepoch - J2000_JD) + sat.jdsatepochF)


@dataclass
class TleSet:
    tles: list[Tle]
    source: str                      # "celestrak": fetched for this answer; "cache": read from a copy
    fetched_at: datetime
    file: Optional[str] = None       # the copy, relative to the repository
    problems: list[str] = field(default_factory=list)


def checksum(line: str) -> int:
    """The TLE checksum: the sum of the digits of the first 68 characters, each minus sign counting 1, modulo 10."""
    return sum(int(c) if c.isdigit() else 1 if c == "-" else 0 for c in line[:68]) % 10


def parse(text: str, satellite: str, norad: int) -> Tle:
    """One TLE as CelesTrak serves it (name line, line 1, line 2), checked; a TleError says what is wrong."""
    lines = [line.rstrip() for line in text.strip().splitlines() if line.strip()]
    if len(lines) != 3:
        raise TleError(f"{satellite}: expected a name line and two element lines, got {len(lines)} line(s)")
    name, line1, line2 = lines
    for number, line in ((1, line1), (2, line2)):
        if len(line) != 69 or not line.startswith(f"{number} "):
            raise TleError(f"{satellite}: line {number} is not a TLE line of 69 characters")
        if not line[68].isdigit() or checksum(line) != int(line[68]):
            raise TleError(f"{satellite}: line {number} fails its checksum")
    if line1[2:7].strip() != str(norad) or line2[2:7].strip() != str(norad):
        raise TleError(f"{satellite}: the TLE is for catalogue number {line1[2:7].strip()}, not {norad}")
    if name.strip().upper().replace(" ", "") != satellite.upper():
        raise TleError(f"{satellite}: CelesTrak names it {name.strip()!r}")
    tle = Tle(satellite, norad, name.strip(), line1, line2)
    if tle.satrec().error:
        raise TleError(f"{satellite}: sgp4 refuses these elements (error {tle.satrec().error})")
    return tle


def default_fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "GreenTrace (EUDR plot check)"})
    try:
        with urllib.request.urlopen(request, timeout=settings.TLE_TIMEOUT_S) as response:
            return response.read().decode("ascii", errors="replace")
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise TleError(f"{url}: {error}") from error


def fetch_all(fetch: Callable[[str], str], now: datetime) -> TleSet:
    """The three TLEs from CelesTrak; the ones that fail are listed, and none at all is a TleError."""
    tles, problems = [], []
    for satellite, norad in SATELLITES.items():
        try:
            tles.append(parse(fetch(URL.format(norad=norad)), satellite, norad))
        except TleError as error:
            problems.append(str(error))
    if not tles:
        raise TleError("; ".join(problems))
    return TleSet(tles, "celestrak", now, problems=problems)


# ---- the dated copies --------------------------------------------------------------------------------------------

def write_copy(tle_set: TleSet, directory: Path) -> Path:
    """data/tle/sentinel-2_<UTC time>.json, and the older copies in that directory removed."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{FILE_PREFIX}{tle_set.fetched_at.strftime('%Y%m%dT%H%MZ')}.json"
    body = {
        "fetched_at": iso(tle_set.fetched_at),
        "source": URL,
        "satellites": [{"satellite": t.satellite, "norad": t.norad, "name": t.name, "line1": t.line1,
                        "line2": t.line2} for t in tle_set.tles],
        "not_fetched": tle_set.problems,
    }
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    for older in directory.glob(f"{FILE_PREFIX}*.json"):
        if older != path:
            older.unlink()
    return path


def read_copy(path: Path) -> TleSet:
    body = json.loads(path.read_text(encoding="utf-8"))
    tles, problems = [], list(body.get("not_fetched", []))
    for entry in body["satellites"]:
        try:
            text = "\n".join((entry["name"], entry["line1"], entry["line2"]))
            tles.append(parse(text, entry["satellite"], int(entry["norad"])))
        except (TleError, KeyError, ValueError) as error:
            problems.append(f"{path.name}: {error}")
    if not tles:
        raise TleError(f"{path.name}: no usable TLE ({'; '.join(problems)})")
    return TleSet(tles, "cache", parse_iso(body["fetched_at"]), shown(path), problems)


def shown(path: Path) -> str:
    """A copy's path as answers give it: relative to the repository when it lies inside."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def newest_copy(directories: tuple[Path, ...]) -> Optional[TleSet]:
    """The most recently fetched readable copy among the directories, or None."""
    found = []
    for directory in directories:
        for path in directory.glob(f"{FILE_PREFIX}*.json"):
            try:
                found.append(read_copy(path))
            except (TleError, KeyError, ValueError, OSError):
                continue
    return max(found, key=lambda s: s.fetched_at, default=None)


def current(sample: bool, now: datetime, *, fetch: Callable[[str], str] = default_fetch,
            shipped: Optional[Path] = None, cache: Optional[Path] = None) -> tuple[Optional[TleSet], Optional[str]]:
    """(the TLEs to use, None) or (None, why there are none). shipped, cache: data/tle/ and data/cache/tle/."""
    shipped, cache = shipped or SHIPPED_DIR, cache or CACHE_DIR
    copy = newest_copy((shipped, cache))
    if sample:
        return (copy, None) if copy is not None else (None, NO_COPY)
    if copy is not None and now - copy.fetched_at < timedelta(hours=settings.TLE_REFRESH_AFTER_HOURS):
        return copy, None
    try:
        fresh = fetch_all(fetch, now)
    except TleError as error:
        if copy is None:
            return None, f"CelesTrak did not answer ({error}) and there is no TLE copy in data/tle/"
        copy.problems.append(f"CelesTrak did not answer ({error}): the copy fetched {iso(copy.fetched_at)} is used")
        return copy, None
    try:
        fresh.file = shown(write_copy(fresh, cache))
    except OSError as error:  # a read-only disk: the TLEs are still good for this answer
        fresh.problems.append(f"the fetched TLEs could not be kept in {cache}: {error}")
    return fresh, None


def iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str) -> datetime:
    when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def age_days(tle: Tle, now: datetime) -> float:
    return round((now - tle.epoch).total_seconds() / 86400.0, 2)


def main(argv: list[str]) -> int:
    command = argv[0] if argv else "show"
    now = datetime.now(timezone.utc)
    if command == "refresh":
        try:
            fetched = fetch_all(default_fetch, now)
        except TleError as error:
            print(f"CelesTrak did not answer: {error}", file=sys.stderr)
            return 1
        print(f"wrote {shown(write_copy(fetched, SHIPPED_DIR))}")
        for problem in fetched.problems:
            print(f"not fetched: {problem}", file=sys.stderr)
    elif command != "show":
        print(__doc__)
        return 2
    copy = newest_copy((SHIPPED_DIR, CACHE_DIR))
    if copy is None:
        print(NO_COPY)
        return 1
    print(f"{copy.file}: fetched {iso(copy.fetched_at)}")
    for tle in copy.tles:
        print(f"  {tle.satellite} ({tle.norad}) epoch {iso(tle.epoch)}, {age_days(tle, now)} days old")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
