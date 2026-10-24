"""Trees from OSM (optional, settings section extras): single trees (natural=tree nodes), tree rows
(natural=tree_row ways) and woods (landuse=forest / natural=wood) inside the area, as X-Plane forests.

X-Plane places individual trees with a forest in point mode (each vertex is one tree) and rows with line mode,
exactly as WED does (forest param = density + 256 x fill mode, see placements.py). Woods are area forests with
their holes kept. Everything is kept off the generated buildings (footprint + extras.clearance_m) and off drivable
roads (the carriageway buffer from clean.load_roads), because overlay forests do not avoid them on their own.

Input: raw vegetation download (fetch kind "vegetation"), processed/buildings_classified.geojson, raw roads.
Output: polygons [(forest path, param, rings)] for build_dsf, and a report.
"""

import json
from collections import Counter

import shapely
from shapely.geometry import Point, Polygon, shape
from shapely.ops import unary_union
from shapely.prepared import prep

from cityscene import clean, fetch
from cityscene.geo import Projector, metric_crs_for, zones_union
from cityscene.log import get_logger
from cityscene.osm_geometry import relation_polygon, way_polygon

log = get_logger("vegetation")

WOOD_TAGS = (("landuse", "forest"), ("natural", "wood"))
# Points per point-mode forest winding (kept small; X-Plane has no documented limit)
POINTS_PER_WINDING = 250


def enabled(settings: dict) -> bool:
    extras = settings.get("extras") or {}
    return bool(extras.get("trees") or extras.get("tree_rows") or extras.get("woods"))


def _is_wood(tags: dict) -> bool:
    return any(tags.get(k) == v for k, v in WOOD_TAGS)


def _rings_wgs84(geom, projector) -> list:
    """[[outer ring], [hole], ...] per polygon, in lon/lat, without the closing point."""
    out = []
    for poly in getattr(geom, "geoms", [geom]):
        if poly.geom_type != "Polygon" or poly.is_empty:
            continue
        poly = projector.to_wgs84(poly.simplify(0.5))
        if poly.is_empty or poly.geom_type != "Polygon":
            continue
        rings = [list(poly.exterior.coords)[:-1]] + [list(r.coords)[:-1] for r in poly.interiors]
        out.append([[[round(x, 9), round(y, 9)] for x, y in ring] for ring in rings if len(ring) >= 4])
    return [r for r in out if r and len(r[0]) >= 3]


def build(job, settings: dict) -> tuple[list, dict]:
    extras = settings["extras"]
    stats = Counter()
    if not enabled(settings):
        return [], {}
    data = fetch.load(job, "vegetation")
    if data is None:
        log.warning("Trees are switched on but no vegetation data was downloaded; regenerate with all steps")
        return [], {"missing_data": True}
    zones = job.zones()
    projector = Projector(metric_crs_for(zones))
    area = projector.to_metric(zones_union(zones))
    to_metric = projector._to_metric

    # Keep-out: generated buildings and drivable roads
    keep_out = []
    path = job.processed_dir / "buildings_classified.geojson"
    if path.exists():
        features = json.loads(path.read_text(encoding="utf-8"))["features"]
        keep_out += [projector.to_metric(shape(f["geometry"])).buffer(extras["clearance_m"]) for f in features]
    roads = clean.load_roads(fetch.load(job, "roads"), to_metric, settings["roads"])
    keep_out += [r["surface"] for r in roads]
    blocked = unary_union(keep_out) if keep_out else Polygon()
    blocked_prepared = prep(blocked) if not blocked.is_empty else None

    nodes = {e["id"]: (e["lon"], e["lat"]) for e in data["elements"] if e["type"] == "node"}
    ways = {e["id"]: e for e in data["elements"] if e["type"] == "way"}
    trees, rows, woods = [], [], []
    for e in data["elements"]:
        tags = e.get("tags") or {}
        if e["type"] == "node" and tags.get("natural") == "tree" and extras.get("trees"):
            p = projector.to_metric(Point(e["lon"], e["lat"]))
            if not area.contains(p):
                continue
            if blocked_prepared and blocked_prepared.contains(p):
                stats["trees_blocked"] += 1
                continue
            trees.append([round(e["lon"], 9), round(e["lat"], 9)])
        elif e["type"] == "way" and tags.get("natural") == "tree_row" and extras.get("tree_rows"):
            coords = [nodes[n] for n in e.get("nodes", []) if n in nodes]
            if len(coords) < 2:
                continue
            line = projector.to_metric(shapely.LineString(coords)).intersection(area)
            if not blocked.is_empty:
                line = line.difference(blocked)
            for part in getattr(line, "geoms", [line]):
                if part.geom_type == "LineString" and part.length >= 3:
                    rows.append([[round(x, 9), round(y, 9)] for x, y in projector.to_wgs84(part).coords])
        elif _is_wood(tags) and extras.get("woods") and e["type"] in ("way", "relation"):
            geom, reason = way_polygon(e, nodes) if e["type"] == "way" else relation_polygon(e, ways, nodes)
            if geom is None:
                stats[f"woods_failed_{reason}"] += 1
                continue
            geom = projector.to_metric(geom.buffer(0)).intersection(area)
            if not blocked.is_empty:
                geom = geom.difference(blocked)
            if geom.is_empty or geom.area < extras["min_wood_m2"]:
                stats["woods_too_small"] += 1
                continue
            woods += _rings_wgs84(geom, projector)

    polygons = []
    density = int(extras["density"])
    for i in range(0, len(trees), POINTS_PER_WINDING):
        polygons.append((extras["tree_asset"], density + 512, [trees[i:i + POINTS_PER_WINDING]]))
    polygons += [(extras["tree_row_asset"], density + 256, [r]) for r in rows]
    polygons += [(extras["wood_asset"], density, rings) for rings in woods]
    report = {"trees": len(trees), "tree_rows": len(rows), "woods": len(woods), **dict(stats)}
    log.info("Vegetation: %d single trees, %d tree rows, %d wood polygons; %d trees on buildings / roads skipped",
             len(trees), len(rows), len(woods), stats["trees_blocked"])
    return polygons, report
