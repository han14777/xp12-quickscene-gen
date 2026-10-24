"""Step 1, fetch: download buildings (building + building:part) and roads (highway) for the area from
Overpass and cache them in jobs/<name>/raw/.

- The area's bounding box is expanded by osm.bbox_buffer_m in projected coordinates; the exact filter
  (centroid inside the area) happens in clean
- Large areas are queried in chunks on a globally aligned osm.chunk_deg grid, each cached separately,
  so small edits to the area do not re-download chunks already on disk
- Cached chunks are skipped; force=True re-downloads everything
"""

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from shapely.geometry import box

from quickscene.geo import Projector, metric_crs_for, zones_union
from quickscene.log import get_logger

log = get_logger("fetch")

USER_AGENT = "QuickScene/0.2 (X-Plane scenery tool)"

QUERIES = {
    "buildings": (
        '(\n  way["building"]{area};\n  relation["building"]{area};\n'
        '  way["building:part"]{area};\n  relation["building:part"]{area};\n);\n'
    ),
    "roads": 'way["highway"]{area};\n',
    # Only downloaded when trees are switched on (settings extras, vegetation.py)
    "vegetation": (
        '(\n  node["natural"="tree"]{area};\n  way["natural"="tree_row"]{area};\n'
        '  way["landuse"="forest"]{area};\n  way["natural"="wood"]{area};\n'
        '  relation["landuse"="forest"]{area};\n  relation["natural"="wood"]{area};\n);\n'
    ),
}


def kinds_for(settings: dict) -> list:
    """The data kinds a job needs: buildings and roads, plus vegetation when any tree option is on."""
    extras = settings.get("extras") or {}
    wanted = ["buildings", "roads"]
    if extras.get("trees") or extras.get("tree_rows") or extras.get("woods"):
        wanted.append("vegetation")
    return wanted


def query_bbox(zones: list, buffer_m: float) -> tuple[float, float, float, float]:
    """Area bounding box expanded by buffer_m metres, as (west, south, east, north)."""
    projector = Projector(metric_crs_for(zones))
    minx, miny, maxx, maxy = projector.to_metric(zones_union(zones)).bounds
    expanded = box(minx - buffer_m, miny - buffer_m, maxx + buffer_m, maxy + buffer_m)
    return projector.to_wgs84(expanded).bounds


def chunks(bbox: tuple, chunk_deg: float) -> list:
    """Split the bounding box on a globally aligned grid; returns [(south, west, north, east)] rounded to 6 decimals (used in file names)."""
    west, south, east, north = bbox
    out = []
    for j in range(math.floor(south / chunk_deg), math.ceil(north / chunk_deg)):
        for i in range(math.floor(west / chunk_deg), math.ceil(east / chunk_deg)):
            s, w = j * chunk_deg, i * chunk_deg
            cell = (round(max(s, south), 6), round(max(w, west), 6),
                    round(min(s + chunk_deg, north), 6), round(min(w + chunk_deg, east), 6))
            if cell[0] >= cell[2] or cell[1] >= cell[3]:
                continue
            out.append(cell)
    return out


def build_query(kind: str, swne: tuple, timeout_s: int) -> str:
    area = "({},{},{},{})".format(*swne)
    return f"[out:json][timeout:{timeout_s}];\n" + QUERIES[kind].format(area=area) + "out body;\n>;\nout skel qt;\n"


def overpass_urls(value) -> list[str]:
    """osm.overpass_url may be one URL or a list of mirrors tried in turn."""
    urls = [value] if isinstance(value, str) else list(value or [])
    if not urls:
        raise RuntimeError("osm.overpass_url is empty")
    return urls


def post_overpass(urls, query: str, timeout_s: int, max_retries: int, backoff_s: float) -> dict:
    """Try each mirror in turn; after a full round without success wait backoff_s x round, up to max_retries rounds.
    A 200 response whose remark reports a runtime error (timeout, out of memory) holds partial data and counts as
    a failure, so it is never cached."""
    urls = overpass_urls(urls)
    attempts = max_retries * len(urls)
    for attempt in range(1, attempts + 1):
        url = urls[(attempt - 1) % len(urls)]
        host = urlparse(url).netloc
        try:
            response = requests.post(url, data={"data": query}, headers={"User-Agent": USER_AGENT},
                                     timeout=(15, timeout_s + 30))
            if response.status_code == 200:
                data = response.json()
                remark = data.get("remark") or ""
                if "runtime error" not in remark:
                    return data
                log.warning("Attempt %d/%d (%s) returned partial data: %s", attempt, attempts, host, remark.strip())
            else:
                log.warning("Attempt %d/%d (%s) returned HTTP %d", attempt, attempts, host, response.status_code)
        except (requests.RequestException, ValueError) as exc:
            log.warning("Attempt %d/%d (%s) failed: %s", attempt, attempts, host, exc)
        if attempt < attempts and attempt % len(urls) == 0:
            wait = backoff_s * (attempt // len(urls))
            log.info("All Overpass servers busy; retrying in %.0f s", wait)
            time.sleep(wait)
    raise RuntimeError(f"Overpass failed on every server ({attempts} attempts); try again later "
                       "or add another mirror to osm.overpass_url")


# A tiny query (a count of nodes in a few square metres) to check that a server answers
TEST_QUERY = "[out:json][timeout:15];node(-33.8570,151.2140,-33.8566,151.2146);out count;"


def clean_server_list(urls) -> list[str]:
    """Validate a server list from the UI: http(s) URLs, no duplicates, at least one."""
    out = []
    for url in urls or []:
        url = str(url).strip()
        if not url.startswith(("https://", "http://")):
            raise ValueError(f"Not a server URL: {url!r}")
        if url not in out:
            out.append(url)
    if not out:
        raise ValueError("Keep at least one Overpass server")
    return out


def test_server(url: str, timeout_s: float = 25) -> dict:
    """Send TEST_QUERY: {"url", "ok", "status" (HTTP code or None), "ms", "error" (None, "timeout" or a message)}."""
    started = time.monotonic()
    result = {"url": url, "ok": False, "status": None, "ms": None, "error": None}
    try:
        response = requests.post(url, data={"data": TEST_QUERY}, headers={"User-Agent": USER_AGENT},
                                 timeout=(10, timeout_s))
        result["status"] = response.status_code
        if response.status_code == 200:
            response.json()  # an Overpass server answers with JSON; a captive portal or proxy page does not
            result["ok"] = True
        else:
            result["error"] = f"HTTP {response.status_code}"
    except requests.Timeout:
        result["error"] = "timeout"
    except (requests.RequestException, ValueError) as exc:
        result["error"] = str(exc)[:200]
    result["ms"] = round((time.monotonic() - started) * 1000)
    return result


def test_servers(urls: list) -> list:
    """Test several servers at once; results in the same order."""
    urls = list(urls)[:12]
    if not urls:
        return []
    with ThreadPoolExecutor(max_workers=len(urls)) as pool:
        return list(pool.map(test_server, urls))


def merge_overpass(datasets: list) -> dict:
    """Merge several Overpass JSON results (neighbouring chunks overlap), de-duplicated by (type, id); the tagged copy of an element wins."""
    merged = {}
    for data in datasets:
        for element in data["elements"]:
            key = (element["type"], element["id"])
            if key not in merged or ("tags" in element and "tags" not in merged[key]):
                merged[key] = element
    return {"elements": list(merged.values())}


def chunk_path(raw_dir, kind: str, swne: tuple):
    return raw_dir / f"{kind}_{'_'.join(f'{v:.6f}' for v in swne)}.json"


def cached_cover(raw_dir, kind: str, swne: tuple):
    """A cached chunk file that fully covers swne (reused when an edited area shrinks or shifts slightly), else None."""
    s, w, n, e = swne
    for path in sorted(raw_dir.glob(f"{kind}_*.json")):
        try:
            cs, cw, cn, ce = (float(v) for v in path.stem.split("_")[1:])
        except ValueError:
            continue
        if cs <= s and cw <= w and cn >= n and ce >= e:
            return path
    return None


def fetch(job, settings: dict, force: bool = False) -> dict:
    """Download and cache; returns {kind: chunk count}."""
    osm = settings["osm"]
    zones = job.zones()
    bbox = query_bbox(zones, osm["bbox_buffer_m"])
    cells = chunks(bbox, osm["chunk_deg"])
    job.raw_dir.mkdir(parents=True, exist_ok=True)
    log.info("Bounding box (W,S,E,N) = %s, %d chunks", tuple(round(v, 5) for v in bbox), len(cells))

    files = {}
    for kind in kinds_for(settings):
        files[kind] = []
        for n, swne in enumerate(cells, 1):
            cached = None if force else cached_cover(job.raw_dir, kind, swne)
            if cached is not None:
                files[kind].append(cached.name)
                continue
            path = chunk_path(job.raw_dir, kind, swne)
            log.info("Downloading %s chunk %d/%d %s", kind, n, len(cells), swne)
            query = build_query(kind, swne, osm["request_timeout_s"])
            data = post_overpass(osm["overpass_url"], query, osm["request_timeout_s"],
                                 osm["max_retries"], osm["retry_backoff_s"])
            data["_meta"] = {
                "query": query,
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "osm_base_timestamp": data.get("osm3s", {}).get("timestamp_osm_base"),
            }
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
            files[kind].append(path.name)
    (job.raw_dir / "chunks.json").write_text(
        json.dumps({"bbox_wsen": bbox, "chunks": cells, "files": files}, indent=1), encoding="utf-8")
    log.info("Download complete (cached in %s)", job.raw_dir)
    return {kind: len(names) for kind, names in files.items()}


def load(job, kind: str) -> dict | None:
    """Load and merge the chunks used by the current area; None when the cache is missing."""
    index = job.raw_dir / "chunks.json"
    if not index.exists():
        return None
    names = json.loads(index.read_text(encoding="utf-8"))["files"].get(kind, [])
    paths = [job.raw_dir / name for name in names]
    if not paths or not all(p.exists() for p in paths):
        return None
    return merge_overpass([json.loads(p.read_text(encoding="utf-8")) for p in paths])
