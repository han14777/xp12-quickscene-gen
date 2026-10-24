"""Assemble OSM (Overpass JSON) elements into geometry: closed ways become polygons, multipolygon /
building relations are stitched together.

Copied from melbourne-cbd-xp12 pipeline/common/osm_geometry.py. Coordinates are (lon, lat).
"""

from shapely.geometry import LineString, Polygon
from shapely.ops import linemerge, polygonize, unary_union


def _way_coords(way: dict, nodes: dict) -> list | None:
    coords = [nodes.get(node_id) for node_id in way.get("nodes", [])]
    return None if not coords or None in coords else coords


def way_polygon(way: dict, nodes: dict):
    """Returns (geometry, failure reason)."""
    coords = _way_coords(way, nodes)
    if coords is None:
        return None, "missing_nodes"
    if coords[0] != coords[-1] or len(coords) < 4:
        return None, "unclosed_or_too_few_vertices"
    return Polygon(coords), None


def relation_polygon(relation: dict, ways: dict, nodes: dict):
    """Stitch a relation into a polygon; returns (geometry, failure reason).

    multipolygon: outer/inner members. type=building (Simple 3D Buildings relation): the outline member is the
    outer ring, and that outline way often carries no tags at all (e.g. 839 Collins Street, Melbourne).
    """
    relation_type = relation.get("tags", {}).get("type")
    if relation_type not in ("multipolygon", "building"):
        return None, "relation_not_multipolygon"
    roles = {"outline": "outer"} if relation_type == "building" else {"outer": "outer", "": "outer", "inner": "inner"}
    lines = {"outer": [], "inner": []}
    for member in relation.get("members", []):
        if member["type"] != "way":
            continue
        role = roles.get(member.get("role") or "")
        if role is None:
            continue
        way = ways.get(member["ref"])
        coords = _way_coords(way, nodes) if way else None
        if coords is None or len(coords) < 2:
            return None, "missing_nodes"
        lines[role].append(LineString(coords))

    outers = _rings_to_polygons(lines["outer"])
    if not outers:
        return None, "unclosed_or_too_few_vertices"
    geom = unary_union(outers)
    inners = _rings_to_polygons(lines["inner"])
    if inners:
        geom = geom.difference(unary_union(inners))
    return geom, None


def _rings_to_polygons(lines: list) -> list:
    if not lines:
        return []
    merged = linemerge(lines)
    parts = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    return list(polygonize(parts))


def index_elements(data: dict):
    """Returns (nodes {id: (lon, lat)}, ways {id: way}, building elements, building:part elements)."""
    nodes, ways, buildings, parts = {}, {}, [], []
    for element in data["elements"]:
        if element["type"] == "node":
            nodes[element["id"]] = (element["lon"], element["lat"])
        elif element["type"] == "way":
            ways[element["id"]] = element
        tags = element.get("tags", {})
        if "building" in tags:
            buildings.append(element)
        elif "building:part" in tags:
            parts.append(element)
    return nodes, ways, buildings, parts

