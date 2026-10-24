"""Where airports come from:

- X-Plane's own airport database, Global Scenery/Global Airports/Earth nav data/apt.dat (all Scenery Gateway
  airports shipped with the sim, ~380 MB). It is indexed once (ICAO -> byte offset, length, name, position) into
  <cache_dir>/global_airports.json and re-indexed when the file changes
- installed scenery packs in Custom Scenery with an Earth nav data/apt.dat (their DSF items can be imported too,
  see dsf_import.py)
- the X-Plane Scenery Gateway web API (gateway.x-plane.com/apiv1): the recommended scenery of an airport is a zip
  holding <ICAO>.dat (apt.dat) and, for 3D sceneries, <ICAO>.txt (the DSF as text)
"""

import base64
import io
import json
import os
import re
import zipfile
from pathlib import Path

import requests

from quickscene.airport import aptdat
from quickscene.config import project_path, xplane_root
from quickscene.log import get_logger

log = get_logger("airport")

GATEWAY = "https://gateway.x-plane.com/apiv1"
USER_AGENT = "QuickScene/0.2 (X-Plane scenery tool)"
INDEX_VERSION = 1
_HEADER = re.compile(rb"^(1|16|17)\s+(-?\d+)\s+\S+\s+\S+\s+(\S+)\s*(.*)$")


def global_apt_path(settings: dict) -> Path | None:
    root = xplane_root(settings)
    path = root / "Global Scenery" / "Global Airports" / "Earth nav data" / "apt.dat" if root else None
    return path if path and path.is_file() else None


def _position(rows: list[bytes]):
    """Rough position of an airport block: datum from 1302 rows, else the first runway / helipad / node."""
    lat = lon = None
    for row in rows:
        words = row.split()
        if not words:
            continue
        if words[0] == b"1302" and len(words) >= 3 and words[1] in (b"datum_lat", b"datum_lon"):
            try:
                if words[1] == b"datum_lat":
                    lat = float(words[2])
                else:
                    lon = float(words[2])
            except ValueError:
                pass
        elif lat is None and words[0] in (b"100", b"102", b"111", b"14", b"1300", b"101") and len(words) >= 4:
            try:
                i = 9 if words[0] == b"100" else 4 if words[0] == b"101" else 2 if words[0] == b"102" else 1
                lat, lon = float(words[i]), float(words[i + 1])
            except (ValueError, IndexError):
                continue
        if lat is not None and lon is not None:
            break
    return (round(lon, 5), round(lat, 5)) if lat is not None and lon is not None else (None, None)


def build_index(path: Path) -> dict:
    """{ICAO: [offset, length, name, lon, lat, elevation_ft]} for every airport in an apt.dat."""
    index = {}
    with open(path, "rb") as f:
        start = None
        head = None
        rows: list[bytes] = []
        offset = 0
        for line in f:
            m = _HEADER.match(line.rstrip())
            if m or line.startswith(b"99"):
                if head is not None:
                    lon, lat = _position(rows[:60])
                    index[head[0]] = [start, offset - start, head[1], lon, lat, head[2]]
                head = None
                if m:
                    start = offset
                    head = (m.group(3).decode("latin-1"), m.group(4).decode("utf-8", "replace").strip(),
                            int(m.group(2)))
                    rows = []
            elif head is not None and len(rows) < 60:
                rows.append(line)
            offset += len(line)
        if head is not None:
            lon, lat = _position(rows)
            index[head[0]] = [start, offset - start, head[1], lon, lat, head[2]]
    return index


class GlobalAirports:
    def __init__(self, path: Path, index: dict):
        self.path = path
        self.index = index

    @classmethod
    def load(cls, settings: dict) -> "GlobalAirports":
        path = global_apt_path(settings)
        if path is None:
            raise RuntimeError("X-Plane's Global Airports apt.dat was not found; check the X-Plane folder setting")
        st = path.stat()
        signature = [st.st_size, int(st.st_mtime)]
        cache = project_path(settings["paths"].get("cache_dir", "cache")) / "global_airports.json"
        if cache.is_file():
            try:
                data = json.loads(cache.read_text(encoding="utf-8"))
                if data.get("version") == INDEX_VERSION and data.get("signature") == signature:
                    return cls(path, data["airports"])
            except (OSError, ValueError):
                pass
        log.info("Indexing %s (first time only)", path)
        index = build_index(path)
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": INDEX_VERSION, "signature": signature, "airports": index}),
                       encoding="utf-8")
        os.replace(tmp, cache)
        log.info("Indexed %d airports", len(index))
        return cls(path, index)

    def search(self, query: str, limit: int = 30) -> list[dict]:
        q = query.strip().lower()
        if not q:
            return []
        exact, starts, contains = [], [], []
        for icao, (_, _, name, lon, lat, elev) in self.index.items():
            low = icao.lower()
            item = {"icao": icao, "name": name, "lon": lon, "lat": lat, "elevation_ft": elev}
            if low == q:
                exact.append(item)
            elif low.startswith(q):
                starts.append(item)
            elif q in name.lower() or q in low:
                contains.append(item)
            if len(exact) + len(starts) >= limit:
                break
        return (exact + sorted(starts, key=lambda i: i["icao"]) + contains)[:limit]

    def text(self, icao: str) -> str:
        entry = self.index.get(icao)
        if entry is None:
            raise FileNotFoundError(f"{icao} is not in X-Plane's airport database")
        with open(self.path, "rb") as f:
            f.seek(entry[0])
            return f.read(entry[1]).decode("utf-8", "replace")

    def airport(self, icao: str) -> dict:
        airports = aptdat.parse_text(self.text(icao))
        if not airports:
            raise ValueError(f"Could not read {icao} from the airport database")
        return airports[0]


# ---------- installed scenery packs ----------

def installed_airport_packs(settings: dict) -> list[dict]:
    """[{pack, path, airports: [{icao, name}]}] for Custom Scenery packs with an apt.dat (headers only)."""
    root = xplane_root(settings)
    out = []
    if not root or not (root / "Custom Scenery").is_dir():
        return out
    for folder in sorted((root / "Custom Scenery").iterdir(), key=lambda p: p.name.lower()):
        apt = folder / "Earth nav data" / "apt.dat"
        try:
            if not apt.is_file() or apt.stat().st_size > 50_000_000:
                continue
            airports = []
            with open(apt, "rb") as f:
                for line in f:
                    m = _HEADER.match(line.rstrip())
                    if m:
                        airports.append({"icao": m.group(3).decode("latin-1"),
                                         "name": m.group(4).decode("utf-8", "replace").strip()})
        except OSError:
            continue
        if airports:
            out.append({"pack": folder.name, "path": str(folder), "airports": airports[:50]})
    return out


def pack_airport(pack: Path, icao: str) -> dict:
    text = (Path(pack) / "Earth nav data" / "apt.dat").read_text(encoding="utf-8", errors="replace")
    for data in aptdat.parse_text(text):
        if data["header"]["icao"] == icao:
            return data
    raise FileNotFoundError(f"{icao} is not in {pack}")


# ---------- Scenery Gateway ----------

def gateway_scenery(icao: str, timeout: float = 60) -> dict:
    """{"apt": apt.dat text, "dsf_text": DSF text or None, "scenery_id", "artist"} of the recommended scenery."""
    icao = icao.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{3,7}", icao):
        raise ValueError("Enter an ICAO / airport code (3-7 letters and digits)")
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(f"{GATEWAY}/airport/{icao}", headers=headers, timeout=timeout)
    if r.status_code == 404:
        raise FileNotFoundError(f"{icao} is not on the Scenery Gateway")
    r.raise_for_status()
    airport = r.json().get("airport") or {}
    scenery_id = airport.get("recommendedSceneryId")
    if not scenery_id:
        raise FileNotFoundError(f"{icao} has no recommended scenery on the Gateway")
    r = requests.get(f"{GATEWAY}/scenery/{scenery_id}", headers=headers, timeout=timeout)
    r.raise_for_status()
    scenery = r.json().get("scenery") or {}
    blob = base64.b64decode(scenery.get("masterZipBlob") or "")
    apt = dsf = None
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for name in z.namelist():
            low = name.lower()
            if low.endswith(".dat") and apt is None:
                apt = z.read(name).decode("utf-8", "replace")
            elif low.endswith(".txt") and dsf is None:
                text = z.read(name).decode("utf-8", "replace")
                # the Gateway's DSF text starts straight with PROPERTY rows (no A / 800 / DSF2TEXT header)
                if "DSF2TEXT" in text[:200] or text.lstrip().startswith("PROPERTY sim/") or "OBJECT_DEF" in text:
                    dsf = text
    if apt is None:
        raise ValueError("The Gateway scenery has no apt.dat")
    return {"apt": apt, "dsf_text": dsf, "scenery_id": scenery_id, "artist": scenery.get("userName")}
