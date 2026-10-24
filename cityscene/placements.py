"""Hand-placed scenery items that survive regeneration: jobs/<name>/placements.json (airports keep the same list in
their airport.json). Each item names an asset by X-Plane library path or by a file under the project's models
folder, and becomes one DSF entry:

  object   .obj / .agp    {"kind": "object", "path", "lon", "lat", "heading"}
  polygon  .fac           {"kind": "polygon", "path", "rings": [[[lon, lat], ...]], "height": metres (integer)}
           .for           {"kind": "polygon", "path", "rings", "density": 0-255, "fill": "area" | "line" | "points"}
           .pol           {"kind": "polygon", "path", "rings", "heading": texture heading in degrees}
  line     .lin / .str    {"kind": "line", "path", "points": [[lon, lat], ...], "closed": bool, "spacing": m (.str)}

DSF encoding (decompiled WED 2.7 output in X-Plane 12's Global Airports, see build_dsf.py): BEGIN_POLYGON <def>
<param> <coords per point>; facade param = height in whole metres (DSFTool drops fractional ones); forest param =
density + 256 x fill mode (0 area, 1 line, 2 points); draped polygon param = texture heading; line param = 1 when
closed; object string param = spacing in metres. Rings run counter-clockwise (outer) / clockwise (holes), without
repeating the first point. Facades and draped polygons are written with their outer ring only.
"""

import json
import math
import uuid
from pathlib import PurePosixPath

from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

KINDS_BY_EXT = {".obj": "object", ".agp": "object", ".fac": "polygon", ".for": "polygon", ".pol": "polygon",
                ".lin": "line", ".str": "line"}
FILLS = {"area": 0, "line": 1, "points": 2}
MAX_ITEMS = 20000


def ext_of(path: str) -> str:
    return PurePosixPath(path).suffix.lower()


def check_path(path: str) -> str:
    path = str(path or "").strip().replace("\\", "/")
    pure = PurePosixPath(path)
    if not path or pure.is_absolute() or ".." in pure.parts or ":" in path:
        raise ValueError("Asset path must be a library path or a file in the project's models folder")
    if ext_of(path) not in KINDS_BY_EXT:
        raise ValueError(f"{path}: unsupported asset type (use .obj .agp .fac .for .pol .lin .str)")
    return path


def _lonlat(p) -> list:
    lon, lat = float(p[0]), float(p[1])
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError(f"Coordinate out of range: {p}")
    return [round(lon, 9), round(lat, 9)]


def validate_item(item: dict) -> dict:
    path = check_path(item.get("path"))
    kind = KINDS_BY_EXT[ext_of(path)]
    out = {"id": str(item.get("id") or uuid.uuid4().hex[:10])[:40], "kind": kind, "path": path}
    if item.get("name"):
        out["name"] = str(item["name"])[:80]
    ext = ext_of(path)
    if kind == "object":
        out["lon"], out["lat"] = _lonlat([item.get("lon"), item.get("lat")])
        out["heading"] = round(float(item.get("heading") or 0) % 360, 2)
    elif kind == "polygon":
        rings = item.get("rings") or []
        fill = item.get("fill", "area") if ext == ".for" else "area"
        if fill not in FILLS:
            raise ValueError(f"Forest fill must be one of {', '.join(FILLS)}")
        minimum = {"area": 3, "line": 2, "points": 1}[fill]
        rings = [[_lonlat(p) for p in ring] for ring in rings]
        rings = [r[:-1] if len(r) > 1 and r[0] == r[-1] else r for r in rings]
        if not rings or len(rings[0]) < minimum:
            raise ValueError(f"{path}: needs at least {minimum} point(s)")
        out["rings"] = rings if ext == ".for" else rings[:1]
        if ext == ".fac":
            height = round(float(item.get("height") or 10))
            if not 1 <= height <= 500:
                raise ValueError("Facade height must be 1-500 m")
            out["height"] = height
        elif ext == ".for":
            density = int(round(float(item.get("density", 255))))
            if not 0 <= density <= 255:
                raise ValueError("Forest density must be 0-255")
            out["density"], out["fill"] = density, fill
        else:
            out["heading"] = round(float(item.get("heading") or 0)) % 360
    else:
        points = [_lonlat(p) for p in item.get("points") or []]
        if len(points) < 2:
            raise ValueError(f"{path}: a line needs at least 2 points")
        out["points"] = points
        out["closed"] = bool(item.get("closed"))
        if ext == ".str":
            spacing = float(item.get("spacing") or 10)
            if not 0.5 <= spacing <= 1000:
                raise ValueError("Object string spacing must be 0.5-1000 m")
            out["spacing"] = round(spacing, 1)
    return out


def validate_items(items: list) -> list:
    if len(items) > MAX_ITEMS:
        raise ValueError(f"At most {MAX_ITEMS} placed items")
    out, seen = [], set()
    for item in items:
        v = validate_item(item)
        while v["id"] in seen:
            v["id"] = uuid.uuid4().hex[:10]
        seen.add(v["id"])
        out.append(v)
    return out


def load(job) -> list:
    path = job.root / "placements.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("items", [])


def save(job, items: list) -> list:
    items = validate_items(items)
    (job.root / "placements.json").write_text(json.dumps({"items": items}, ensure_ascii=False, indent=1),
                                               encoding="utf-8")
    return items


# ---------- DSF entries ----------

def _ccw(ring: list, outer: bool) -> list:
    if len(ring) < 3:
        return ring
    poly = orient(Polygon(ring), sign=1.0 if outer else -1.0)
    return [list(c) for c in poly.exterior.coords[:-1]]


def dsf_entries(items: list, resolve_path) -> tuple[list, list]:
    """(objects [(path, lon, lat, heading)], polygons [(path, param, rings)]). resolve_path(path) maps an asset path
    to the path written in the DSF (local models become objects/models/...)."""
    objects, polygons = [], []
    for item in items:
        path = resolve_path(item["path"])
        ext = ext_of(item["path"])
        if item["kind"] == "object":
            objects.append((path, item["lon"], item["lat"], item["heading"]))
        elif ext == ".fac":
            polygons.append((path, int(item["height"]), [_ccw(item["rings"][0], True)]))
        elif ext == ".for":
            fill = item.get("fill", "area")
            rings = item["rings"]
            if fill == "area":
                rings = [_ccw(r, i == 0) for i, r in enumerate(rings) if len(r) >= 3]
            polygons.append((path, item["density"] + 256 * FILLS[fill], rings))
        elif ext == ".pol":
            polygons.append((path, int(item["heading"]), [_ccw(item["rings"][0], True)]))
        elif ext == ".lin":
            polygons.append((path, 1 if item["closed"] else 0, [item["points"]]))
        else:
            polygons.append((path, max(1, round(item.get("spacing", 10))), [item["points"]]))
    return objects, polygons


def tile_of(lon: float, lat: float) -> tuple:
    return (math.floor(lat), math.floor(lon))


def split_by_tile(objects: list, polygons: list, stats) -> tuple[dict, dict]:
    """Objects by the tile holding them; polygons by the tile of their first point, skipped (and counted) when a
    point lies outside that tile."""
    by_obj, by_poly = {}, {}
    for o in objects:
        by_obj.setdefault(tile_of(o[1], o[2]), []).append(o)
    for p in polygons:
        lon0, lat0 = p[2][0][0]
        tile = tile_of(lon0, lat0)
        if not all(tile[1] <= lon <= tile[1] + 1 and tile[0] <= lat <= tile[0] + 1 for r in p[2] for lon, lat in r):
            stats["placements_skipped_cross_tile"] += 1
            continue
        by_poly.setdefault(tile, []).append(p)
    return by_obj, by_poly
