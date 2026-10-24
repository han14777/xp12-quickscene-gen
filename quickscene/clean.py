"""Step 2, clean: turn raw OSM data into clean building blocks inside the area, with heights filled in.

The rules are ported from melbourne-cbd-xp12 pipeline/02_clean_buildings.py (each one fixes a real problem
found in that project), minus the Melbourne-specific surveyed heights. Order of processing:
1. Geometry: closed ways become polygons; multipolygon / type=building relations are stitched;
   invalid outlines are repaired with make_valid and split into parts
2. Area filter: an outline is kept only if its centroid (projected) lies in an area polygon; zone_id is recorded
3. Drops: clean.drop_building_values (roof, bridge, ...), clean.drop_tags (shelters, bridge towers),
   fragments that are too small
4. building:part: each part goes to the outline it overlaps most; buildings with parts output their parts
   plus the uncovered remainder (podium)
5. Height: height -> building:levels x level height -> default; a part without height inherits its building's
6. Road conflicts: blocks crossed by a road centreline are cut, blocks with >= on_road_share on the carriageway
   are dropped (tunnels, covered ways, bridges and lower layers are ignored)
7. De-duplicate (IoU >= duplicate_iou); "tallest wins" removes overlaps; small structures without height data
   get a low default height

Output: processed/buildings_clean.geojson (WGS84) and clean_report.json
"""

import json
import re
from collections import Counter

import shapely
from shapely.geometry import LineString, mapping, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

from quickscene import fetch
from quickscene.geo import Projector, metric_crs_for
from quickscene.log import get_logger
from quickscene.osm_geometry import index_elements, relation_polygon, way_polygon

log = get_logger("clean")

FEET_TO_M = 0.3048
_MEASURE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(m|meters?|metres?|ft|feet|')?\s*$", re.IGNORECASE)

# Height sources preferred when de-duplicating; lower is better
SOURCE_RANK = {"osm_height": 1, "osm_part_height": 1, "osm_levels": 2, "default": 3, "default_small": 3}

# Remainders narrower than this are slivers left by cutting and are not output
SLIVER_HALF_WIDTH_M = 0.5

# Coordinate precision (metres) when cutting, so float error does not leave gaps
GRID_M = 0.01


# ---------- tag parsing ----------


def parse_height(value) -> float | None:
    """'45', '45 m', '45m', '45,5', '150 ft' to metres; None if unparseable."""
    if value is None:
        return None
    match = _MEASURE.match(str(value))
    if not match:
        return None
    number = float(match.group(1).replace(",", "."))
    unit = (match.group(2) or "m").lower()
    if unit in ("ft", "feet", "'"):
        number *= FEET_TO_M
    return number


def parse_levels(value) -> float | None:
    """'12', '12.5', '3;4' (largest) to a level count; None if unparseable."""
    if value is None:
        return None
    levels = []
    for piece in str(value).split(";"):
        try:
            levels.append(float(piece.strip().replace(",", ".")))
        except ValueError:
            pass
    levels = [v for v in levels if v > 0]
    return max(levels) if levels else None


# ---------- geometry ----------


def split_polygons(geom) -> tuple[list, bool]:
    """Repair invalid geometry and split it into Polygons; returns (polygons, whether it was repaired)."""
    repaired = not geom.is_valid
    if repaired:
        geom = shapely.make_valid(geom)
    return [p for p in _flatten(geom) if not p.is_empty], repaired


def _flatten(geom) -> list:
    if geom.geom_type == "Polygon":
        return [geom]
    if hasattr(geom, "geoms"):
        return [p for g in geom.geoms for p in _flatten(g)]
    return []


def is_sliver(polygon_m) -> bool:
    return polygon_m.buffer(-SLIVER_HALF_WIDTH_M).is_empty


# ---------- heights ----------


def resolve_height(tags: dict, heights_cfg: dict) -> tuple[float, str, float | None, bool]:
    """Height from height -> levels x level height -> default.

    Returns (height in metres, source, levels, whether a height tag was out of range).
    Source is osm_height / osm_levels / default.
    """
    max_valid = heights_cfg["max_valid_height_m"]

    def valid(h):
        return h is not None and 0 < h < max_valid

    height = parse_height(tags.get("height"))
    out_of_range = height is not None and not valid(height)
    levels = parse_levels(tags.get("building:levels"))

    if valid(height):
        return height, "osm_height", levels, out_of_range
    if levels is not None and valid(levels * heights_cfg["level_height_m"]):
        return levels * heights_cfg["level_height_m"], "osm_levels", levels, out_of_range
    return heights_cfg["default_height_m"], "default", levels, out_of_range


def apply_small_structures(records: list, heights_cfg: dict, stats) -> None:
    """Small standalone structures with no height data (kiosks, substations, ...) get a low default height.

    Only whole, uncut buildings are affected: below small_structure_max_area_m2 the height becomes
    small_structure_height_m with source default_small. Otherwise the general default would draw them
    as thin towers.
    """
    max_area = heights_cfg["small_structure_max_area_m2"]
    for r in records:
        if r["height_source"] == "default" and r["id"] == r["building_id"] and r["metric"].area < max_area:
            r["height_m"] = heights_cfg["small_structure_height_m"]
            r["height_source"] = "default_small"
            stats["small_structure_default_height"] += 1


def resolve_part_height(part_tags: dict, building: dict, heights_cfg: dict) -> tuple[float, str, float | None, bool]:
    """A part with its own height is osm_part_height; otherwise it inherits the building's height and source."""
    height, source, levels, out_of_range = resolve_height(part_tags, heights_cfg)
    if source == "osm_height":
        return height, "osm_part_height", levels, out_of_range
    if source == "default":
        return building["height_m"], building["height_source"], building["levels"], out_of_range
    return height, source, levels, out_of_range


# ---------- road conflicts ----------


def _layer(tags: dict) -> float:
    try:
        return float(tags.get("layer", 0))
    except ValueError:
        return 0.0


def _road_excluded(tags: dict, ignore_service: tuple = ()) -> bool:
    """Tunnels, covered ways (e.g. building_passage), bridges, underground or indoor roads and the service types in ignore_service are not checked."""
    if tags.get("tunnel", "no") != "no" or tags.get("covered", "no") != "no" or tags.get("bridge", "no") != "no":
        return True
    if tags.get("location") in ("underground", "indoor") or tags.get("indoor") == "yes" or tags.get("area") == "yes":
        return True
    if tags.get("highway") == "service" and tags.get("service") in ignore_service:
        return True
    return _layer(tags) < 0


def load_roads(data: dict | None, to_metric, roads_cfg: dict) -> list:
    """Returns [{line, surface, highway}]; surface is the carriageway buffered by an assumed half width."""
    if not data:
        return []
    nodes = {e["id"]: (e["lon"], e["lat"]) for e in data["elements"] if e["type"] == "node"}
    half_widths = roads_cfg["half_width_m"]
    roads = []
    for element in data["elements"]:
        tags = element.get("tags", {})
        if element["type"] != "way" or tags.get("highway") not in roads_cfg["drivable"]:
            continue
        if _road_excluded(tags, tuple(roads_cfg["ignore_service"])):
            continue
        coords = [nodes[n] for n in element.get("nodes", []) if n in nodes]
        if len(coords) < 2:
            continue
        line = shapely.transform(LineString(coords), to_metric, interleaved=False)
        half_width = half_widths.get(tags["highway"], half_widths["default"])
        roads.append(
            {
                "line": line,
                "surface": line.buffer(half_width, cap_style="flat"),
                "highway": tags["highway"],
                "layer": _layer(tags),
            }
        )
    return roads


def apply_roads(records: list, roads: list, settings: dict, dropped, stats) -> list:
    """Drop blocks mostly on the carriageway; cut away the carriageway from blocks crossed by a centreline."""
    if not roads:
        return records
    cfg = settings["roads"]
    min_area = settings["clean"]["min_area_m2"]
    line_tree = STRtree([r["line"] for r in roads])
    surface_tree = STRtree([r["surface"] for r in roads])
    out = []
    for record in records:
        block = record["metric"]
        # A building on a higher layer than the road spans over it (elevated building in OSM): no conflict
        building_layer = _layer(record["tags"])
        surfaces = [roads[int(k)]["surface"] for k in surface_tree.query(block) if roads[int(k)]["layer"] >= building_layer]
        if surfaces and block.intersection(unary_union(surfaces)).area >= cfg["on_road_share"] * block.area:
            dropped["on_road"] += 1
            continue
        inner = block.buffer(-0.5)
        crossing = [
            roads[int(k)]
            for k in line_tree.query(block)
            if not inner.is_empty
            and roads[int(k)]["layer"] >= building_layer
            and roads[int(k)]["line"].intersection(inner).length >= cfg["centerline_min_inside_m"]
        ]
        if not crossing:
            out.append(record)
            continue
        stats["road_crossed_blocks"] += 1
        for road in crossing:
            stats[f"road_crossed_by_{road['highway']}"] += 1
        remaining = block.difference(unary_union([r["surface"] for r in crossing]))
        pieces = [p for p in _flatten(remaining) if p.area >= min_area and not is_sliver(p)]
        if not pieces:
            dropped["road_cut_empty"] += 1
            continue
        for index, piece in enumerate(pieces):
            suffix = f"^{index + 1}" if len(pieces) > 1 else ""
            out.append({**record, "id": f"{record['id']}{suffix}", "metric": piece})
    return out


# ---------- de-duplication ----------


def find_duplicates(records: list, iou_threshold: float) -> set:
    """Indices of records to drop. Of two blocks with IoU at or above the threshold, the one with the better height source, greater height and more tags is kept."""
    geoms = [r["metric"] for r in records]
    tree = STRtree(geoms)
    removed = set()
    for i, geom in enumerate(geoms):
        if i in removed:
            continue
        for j in tree.query(geom):
            j = int(j)
            if j <= i or j in removed:
                continue
            inter = geom.intersection(geoms[j]).area
            union = geom.area + geoms[j].area - inter
            if union > 0 and inter / union >= iou_threshold:
                removed.add(j if _keep_score(records[i]) <= _keep_score(records[j]) else i)
                if i in removed:
                    break
    return removed


def _keep_score(record: dict) -> tuple:
    """Lower is kept first. Blocks are extruded from the ground, so the taller one wins at the same spot."""
    return (SOURCE_RANK[record["height_source"]], -record["height_m"], -len(record["tags"]))


def count_overlaps(records: list, min_area_m2: float = 1.0) -> int:
    """Number of block pairs overlapping by more than min_area_m2 (including blocks of the same building)."""
    geoms = [r["metric"] for r in records]
    tree = STRtree(geoms)
    pairs = 0
    for i, geom in enumerate(geoms):
        for j in tree.query(geom):
            j = int(j)
            if j > i and geom.intersection(geoms[j]).area > min_area_m2:
                pairs += 1
    return pairs


def resolve_overlaps(records: list, settings: dict, stats) -> list:
    """Remove all overlaps: from tallest to lowest, each block keeps only what taller blocks have not claimed.

    Facades are extruded from the ground, so each spot should have one block, the tallest. Overlaps come from
    duplicated outlines in OSM and nested building:parts (an overhanging upper floor around a tower).
    Fragments and slivers left after trimming are dropped; fully covered blocks are dropped entirely.
    """
    min_area = settings["clean"]["min_area_m2"]
    order = sorted(range(len(records)), key=lambda i: -records[i]["height_m"])
    rank = {i: r for r, i in enumerate(order)}
    tree = STRtree([r["metric"] for r in records])
    placed: dict[int, list] = {}
    out = []
    for i in order:
        record = records[i]
        block = shapely.set_precision(record["metric"], GRID_M)
        higher = [p for k in tree.query(block) if rank[int(k)] < rank[i] for p in placed.get(int(k), [])]
        overlapping = [p for p in higher if p.intersection(block).area > 0]
        if overlapping:
            block = shapely.set_precision(block.difference(unary_union(overlapping)), GRID_M)
            stats["overlap_trimmed_blocks"] += 1
        pieces = [p for p in _flatten(block) if p.area >= min_area and not is_sliver(p)]
        placed[i] = pieces
        if not pieces:
            stats["overlap_removed_blocks"] += 1
            continue
        for index, piece in enumerate(pieces):
            suffix = f"%{index + 1}" if len(pieces) > 1 else ""
            out.append({**record, "id": f"{record['id']}{suffix}", "metric": piece})
    return out


# ---------- main flow ----------


def collect_buildings(buildings, geometry_of, zones_metric, to_metric, settings, dropped, stats) -> list:
    """Assemble, repair and filter building outlines; returns building records (parts not yet applied).

    zones_metric is [(area id, projected polygon)]; a building belongs to the area containing its centroid.
    """
    clean_cfg = settings["clean"]
    records = []
    for element in buildings:
        tags = element["tags"]
        geom, reason = geometry_of(element)
        if geom is None:
            dropped[reason] += 1
            continue
        polygons, repaired = split_polygons(geom)
        if not polygons:
            dropped["invalid_geometry"] += 1
            continue
        stats["repaired_geometry"] += repaired

        for index, polygon in enumerate(polygons):
            metric = shapely.transform(polygon, to_metric, interleaved=False)
            zone_id = next((zid for zid, zone in zones_metric if zone.contains(metric.centroid)), None)
            if zone_id is None:
                stats["outside_zone"] += 1
                continue
            if tags["building"] in clean_cfg["drop_building_values"]:
                dropped[f"building={tags['building']}"] += 1
                continue
            dropped_tag = next((k for k, values in clean_cfg["drop_tags"].items() if tags.get(k) in values), None)
            if dropped_tag:
                dropped[f"{dropped_tag}={tags[dropped_tag]}"] += 1
                continue
            if metric.area < clean_cfg["min_area_m2"]:
                dropped["too_small"] += 1
                continue
            height, source, levels, out_of_range = resolve_height(tags, settings["heights"])
            stats["out_of_range_height_tag"] += out_of_range
            suffix = f"#{index + 1}" if len(polygons) > 1 else ""
            building_id = f"{element['type']}/{element['id']}{suffix}"
            records.append(
                {
                    "id": building_id,
                    "building_id": building_id,
                    "zone_id": zone_id,
                    "kind": "building",
                    "metric": metric,
                    "tags": tags,
                    "name": tags.get("name"),
                    "height_m": height,
                    "height_source": source,
                    "levels": levels,
                    "repaired": repaired,
                    "parts": [],
                }
            )
    return records


def assign_parts(part_elements, geometry_of, buildings: list, to_metric, stats) -> None:
    """Assign each part to the outline it overlaps most (the part's interior point must lie in that outline).

    On a tie the larger outline wins: sometimes a building outline coincides with a part and sits inside an
    outer outline, and the part belongs to the outer one.
    """
    if not buildings:
        return
    tree = STRtree([b["metric"] for b in buildings])
    for element in part_elements:
        geom, _ = geometry_of(element)
        if geom is None:
            stats["part_bad_geometry"] += 1
            continue
        polygons, _ = split_polygons(geom)
        for index, polygon in enumerate(polygons):
            metric = shapely.transform(polygon, to_metric, interleaved=False)
            point = metric.representative_point()
            best, best_key = None, (0.0, 0.0)
            for k in tree.query(metric):
                building = buildings[int(k)]
                if building["metric"].contains(point):
                    # Round the overlap to 0.1 m² so float error does not decide ties
                    key = (round(building["metric"].intersection(metric).area, 1), building["metric"].area)
                    if key > best_key:
                        best, best_key = building, key
            if best is None:
                stats["part_unassigned"] += 1
                continue
            suffix = f"#{index + 1}" if len(polygons) > 1 else ""
            best["parts"].append({"id": f"{element['type']}/{element['id']}{suffix}", "metric": metric, "tags": element["tags"]})


def expand_parts(buildings: list, settings: dict, stats) -> list:
    """Expand buildings with parts into part blocks and remainders; returns the final block records."""
    heights_cfg = settings["heights"]
    min_area = settings["clean"]["min_area_m2"]
    records = []
    for building in buildings:
        parts = building.pop("parts")
        if not parts:
            records.append(building)
            continue
        stats["buildings_with_parts"] += 1
        for part in parts:
            height, source, levels, out_of_range = resolve_part_height(part["tags"], building, heights_cfg)
            stats["out_of_range_height_tag"] += out_of_range
            records.append(
                {
                    **building,
                    "id": part["id"],
                    "kind": "part",
                    "metric": part["metric"],
                    "name": part["tags"].get("name") or building["name"],
                    "height_m": height,
                    "height_source": source,
                    "levels": levels,
                }
            )
        remainder = building["metric"].difference(unary_union([p["metric"] for p in parts]))
        pieces = [p for p in _flatten(remainder) if p.area >= min_area and not is_sliver(p)]
        for index, piece in enumerate(pieces):
            records.append({**building, "id": f"{building['id']}~{index + 1}", "kind": "remainder", "metric": piece})
        stats["remainder_pieces"] += len(pieces)
    return records


def to_feature(record: dict, to_wgs84) -> dict:
    tags = record["tags"]
    geometry = shapely.transform(record["metric"], to_wgs84, interleaved=False)
    return {
        "type": "Feature",
        "geometry": mapping(geometry),
        "properties": {
            "id": record["id"],
            "building_id": record["building_id"],
            "zone_id": record["zone_id"],
            "kind": record["kind"],
            "name": record["name"],
            "building": tags["building"],
            "building_use": tags.get("building:use"),
            "heritage": "heritage" in tags or "historic" in tags,
            "height_m": round(record["height_m"], 1),
            "height_source": record["height_source"],
            "osm_height_m": round(record.get("osm_height_m", record["height_m"]), 1),
            "osm_height_source": record.get("osm_height_source", record["height_source"]),
            "levels": record["levels"],
            "area_m2": round(record["metric"].area, 1),
            "repaired": record["repaired"],
        },
    }


def clean(data: dict, zones: list, settings: dict, roads_data: dict | None = None,
          metric_crs: str | None = None) -> tuple[list, dict]:
    """Returns (GeoJSON features, report). zones are the area Features; road conflicts are skipped when roads_data is None."""
    projector = Projector(metric_crs or metric_crs_for(zones))
    to_metric = projector._to_metric
    zones_metric = [(z["properties"]["id"], projector.to_metric(shape(z["geometry"]))) for z in zones]

    nodes, ways, building_elements, part_elements = index_elements(data)

    def geometry_of(element):
        if element["type"] == "way":
            return way_polygon(element, nodes)
        return relation_polygon(element, ways, nodes)

    dropped, stats = Counter(), Counter()
    stats["input_buildings"] = len(building_elements)
    stats["input_parts"] = len(part_elements)

    buildings = collect_buildings(building_elements, geometry_of, zones_metric, to_metric, settings, dropped, stats)
    stats["kept_buildings"] = len(buildings)
    assign_parts(part_elements, geometry_of, buildings, to_metric, stats)
    records = expand_parts(buildings, settings, stats)
    stats["osm_blocks"] = len(records)

    roads = load_roads(roads_data, to_metric, settings["roads"])
    stats["drivable_roads"] = len(roads)
    records = apply_roads(records, roads, settings, dropped, stats)

    duplicates = find_duplicates(records, settings["clean"]["duplicate_iou"])
    dropped["duplicate"] = len(duplicates)
    records = [r for i, r in enumerate(records) if i not in duplicates]
    stats["overlapping_pairs_before_resolve"] = count_overlaps(records)
    records = resolve_overlaps(records, settings, stats)
    stats["overlapping_pairs"] = count_overlaps(records)
    apply_small_structures(records, settings["heights"], stats)

    report = {
        "metric_crs": projector.crs,
        "kept_blocks": len(records),
        "buildings": len({r["building_id"] for r in records}),
        "kinds": dict(Counter(r["kind"] for r in records)),
        "dropped": dict(dropped),
        "stats": dict(stats),
        "height_sources": dict(Counter(r["height_source"] for r in records)),
        "max_height_m": round(max((r["height_m"] for r in records), default=0), 1),
    }
    return [to_feature(r, projector._to_wgs84) for r in records], report


def run(job, settings: dict) -> dict:
    data = fetch.load(job, "buildings")
    if data is None:
        raise RuntimeError("No OSM building data; run fetch first")
    roads_data = fetch.load(job, "roads")
    if roads_data is None:
        log.warning("No road data; skipping the road conflict check")

    features, report = clean(data, job.zones(), settings, roads_data)
    out_dir = job.processed_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "buildings_clean.geojson", "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, ensure_ascii=False)
    with open(out_dir / "clean_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    stats = Counter(report["stats"])
    log.info("Input: %d buildings, %d parts; %d outside the area", stats["input_buildings"], stats["input_parts"], stats["outside_zone"])
    for reason, count in sorted(report["dropped"].items()):
        log.info("Dropped %-28s %d", reason, count)
    log.info("Road conflicts: %d drivable roads; %d blocks on roads dropped, %d blocks cut", stats["drivable_roads"],
             report["dropped"].get("on_road", 0), stats["road_crossed_blocks"])
    log.info("Overlaps: %d overlapping block pairs -> %d", stats["overlapping_pairs_before_resolve"], stats["overlapping_pairs"])
    log.info("Output: %d buildings, %d blocks; height sources %s; tallest %.1f m", report["buildings"], report["kept_blocks"],
             report["height_sources"], report["max_height_m"])
    return report
