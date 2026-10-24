"""A first draft of an airport from OpenStreetMap aeroway data (for airports X-Plane does not have yet, or as
tracing help). Everything it makes is meant to be refined in the editor.

  aeroway=aerodrome (polygon)        boundary; name, icao, iata, ele (metres) for the header
  aeroway=runway (way)               runway between the way's end nodes; ref "09/27" names the ends (or the
                                     heading does), width / surface tags
  aeroway=taxiway / taxilane         yellow centre line (+ green centre lights for taxiways), taxi route network
                                     (nodes shared by ways connect), pavement = the line buffered by width / 2
  aeroway=apron (polygon)            pavement (merged with the taxiway pavement)
  aeroway=helipad                    helipad
  aeroway=parking_position / gate    startup location facing away from the nearest taxi route
  aeroway=windsock                   windsock
  aeroway=control_tower / tower      tower viewpoint (height tag, default 30 m)
  aeroway=terminal / hangar building facade (X-Plane default library: terminal_01 / flat white warehouse) placed
                                     as a DSF item

Ends of a runway named "09" lie at the side the runway is entered from: the bearing from end 09 to end 27 is
about 90°. Surfaces map asphalt / concrete / grass / dirt / gravel to the apt.dat codes.
"""

from collections import Counter

from shapely.geometry import LineString, Point
from shapely.ops import unary_union

from cityscene.airport import model
from cityscene.airport.validate import bearing, distance_m
from cityscene.fetch import post_overpass
from cityscene.geo import Projector, utm_crs
from cityscene.log import get_logger
from cityscene.osm_geometry import relation_polygon, way_polygon

log = get_logger("airport")

SURFACE_CODES = {"asphalt": 1, "paved": 1, "concrete": 2, "concrete:plates": 2, "concrete:lanes": 2, "grass": 3,
                 "turf": 3, "dirt": 4, "earth": 4, "ground": 4, "gravel": 5, "fine_gravel": 5, "compacted": 5,
                 "sand": 12, "snow": 14, "ice": 14, "water": 13}
# X-Plane 12 default library facades (checked against Resources/default scenery/airport scenery/library.txt)
TERMINAL_FACADE = "lib/airport/Modern_Airports/Facades/terminal_01.fac"
HANGAR_FACADE = "lib/airport/buildings/warehouses/flat/white.fac"
DEFAULT_TAXIWAY_WIDTH = 23.0
DEFAULT_TAXILANE_WIDTH = 15.0


def query(icao: str = "", bbox=None, timeout_s: int = 90) -> str:
    """Overpass query for one aerodrome (by ICAO tag) or everything aeroway in a bbox (west, south, east, north)."""
    if bbox:
        w, s, e, n = bbox
        area = f"({s},{w},{n},{e})"
        return (f"[out:json][timeout:{timeout_s}];\n(nwr[\"aeroway\"]{area};"
                f"way[\"building\"][\"aeroway\"]{area};);\nout body;\n>;\nout skel qt;\n")
    return (f"[out:json][timeout:{timeout_s}];\nnwr[\"aeroway\"=\"aerodrome\"][\"icao\"=\"{icao}\"]->.a;\n"
            ".a map_to_area->.ar;\n(.a; nwr[\"aeroway\"](area.ar); way[\"building\"][\"aeroway\"](area.ar););\n"
            "out body;\n>;\nout skel qt;\n")


def fetch(settings: dict, icao: str = "", bbox=None) -> dict:
    osm = settings["osm"]
    q = query(icao.strip().upper(), bbox, osm["request_timeout_s"])
    return post_overpass(osm["overpass_url"], q, osm["request_timeout_s"], min(osm["max_retries"], 3),
                         osm["retry_backoff_s"])


def _number(tags, key, default=None):
    try:
        return float(str(tags.get(key, "")).split()[0].replace(",", "."))
    except (ValueError, IndexError):
        return default


def _end_names(ref: str, heading: float) -> tuple[str, str]:
    """Names for (first end, last end) of a runway way: from its ref when it matches the direction, else from the
    heading (true, so possibly a few degrees off the published magnetic numbers)."""
    parts = [p.strip() for p in str(ref or "").replace("-", "/").split("/") if p.strip()]
    if len(parts) == 2 and all(p[:2].isdigit() or p[:1].isdigit() for p in parts):
        a, b = parts
        num_a = int("".join(c for c in a if c.isdigit()) or 0)
        diff = abs((heading - num_a * 10 + 180) % 360 - 180)
        return (a, b) if diff <= 90 else (b, a)
    num = round(heading / 10) % 36 or 36
    other = (num + 18 - 1) % 36 + 1
    return f"{num:02d}", f"{other:02d}"


def convert(data: dict, settings: dict, icao: str = "") -> tuple[dict, dict]:
    """Overpass JSON -> (airport model, report)."""
    stats = Counter()
    nodes = {e["id"]: (e["lon"], e["lat"]) for e in data["elements"] if e["type"] == "node"}
    node_tags = {e["id"]: e.get("tags", {}) for e in data["elements"] if e["type"] == "node" and e.get("tags")}
    ways = {e["id"]: e for e in data["elements"] if e["type"] == "way"}
    tagged = [e for e in data["elements"] if e.get("tags")]
    aerodrome = next((e for e in tagged if e["tags"].get("aeroway") == "aerodrome"), None)
    tags = aerodrome["tags"] if aerodrome else {}
    elevation_ft = round(_number(tags, "ele", 0) * 3.28084)
    airport = model.empty(tags.get("icao") or icao or "XXXX", tags.get("name:en") or tags.get("name") or icao,
                          elevation_ft)
    for key, meta in (("iata", "iata_code"), ("icao", "icao_code"), ("addr:city", "city")):
        if tags.get(key):
            airport["meta"][meta] = tags[key]

    def coords(way):
        return [nodes[n] for n in way.get("nodes", []) if n in nodes]

    # projection for buffering
    lonlats = list(nodes.values())
    if not lonlats:
        raise ValueError("OpenStreetMap returned no data for this airport")
    lon0 = sum(p[0] for p in lonlats) / len(lonlats)
    lat0 = sum(p[1] for p in lonlats) / len(lonlats)
    projector = Projector(utm_crs(lon0, lat0))

    if aerodrome is not None:
        geom = None
        if aerodrome["type"] == "way":
            geom, _ = way_polygon(aerodrome, nodes)
        elif aerodrome["type"] == "relation":
            geom, _ = relation_polygon(aerodrome, ways, nodes)
        if geom is not None and not geom.is_empty:
            poly = max(getattr(geom, "geoms", [geom]), key=lambda g: g.area)
            ring = [model.node(x, y) for x, y in list(poly.exterior.coords)[:-1]]
            airport["boundaries"].append({"id": model.new_id("bnd_"), "name": "Airport boundary", "rings": [ring]})
            airport["meta"]["datum_lon"] = f"{poly.centroid.x:.6f}"
            airport["meta"]["datum_lat"] = f"{poly.centroid.y:.6f}"

    taxi_surfaces = []
    route_ways = []
    terminals = []
    for e in tagged:
        t = e["tags"]
        aeroway = t.get("aeroway")
        if e["type"] == "way" and aeroway == "runway" and t.get("area") != "yes":
            c = coords(e)
            if len(c) < 2 or c[0] == c[-1]:
                stats["runways_skipped"] += 1
                continue
            heading = bearing(*c[0], *c[-1])
            name_a, name_b = _end_names(t.get("ref"), heading)
            width = _number(t, "width", 45.0)
            rwy = model.runway([model.runway_end(name_a, *c[0]), model.runway_end(name_b, *c[-1])], width,
                               SURFACE_CODES.get(t.get("surface"), 1))
            length = distance_m(*c[0], *c[-1])
            for end in rwy["ends"]:
                end["markings"] = 3 if length > 1800 else 2 if length > 900 else 1
            if length > 1200:
                rwy["edge_lights"] = 3 if length > 2400 else 2
                rwy["centre_lights"] = 1 if length > 2400 else 0
                rwy["distance_signs"] = 1
            airport["runways"].append(rwy)
            route_ways.append((e, "runway", f"{name_a}/{name_b}"))
        elif e["type"] == "way" and aeroway in ("taxiway", "taxilane"):
            c = coords(e)
            if len(c) < 2:
                continue
            width = _number(t, "width", DEFAULT_TAXIWAY_WIDTH if aeroway == "taxiway" else DEFAULT_TAXILANE_WIDTH)
            line = projector.to_metric(LineString(c))
            taxi_surfaces.append(line.buffer(width / 2, cap_style="round", join_style="round"))
            light = 101 if aeroway == "taxiway" else 0
            airport["lines"].append({"id": model.new_id("lin_"), "name": t.get("ref", ""), "closed": False,
                                     "nodes": [model.node(x, y, 1, light) for x, y in c]})
            route_ways.append((e, "taxiway", t.get("ref", "")))
        elif aeroway == "apron" and e["type"] in ("way", "relation"):
            geom = way_polygon(e, nodes)[0] if e["type"] == "way" else relation_polygon(e, ways, nodes)[0]
            if geom is not None:
                taxi_surfaces.append(projector.to_metric(geom.buffer(0)))
        elif aeroway == "helipad":
            pt = nodes.get(e["id"]) if e["type"] == "node" else None
            if pt is None and e["type"] == "way":
                c = coords(e)
                pt = (sum(x for x, _ in c) / len(c), sum(y for _, y in c) / len(c)) if c else None
            if pt:
                airport["helipads"].append({"id": model.new_id("hp_"), "name": f"H{len(airport['helipads']) + 1}",
                                            "lon": pt[0], "lat": pt[1], "heading": 0.0, "length_m": 20.0,
                                            "width_m": 20.0, "surface": 2, "markings": 0, "shoulder": 0,
                                            "smoothness": 0.25, "edge_lights": 1})
        elif aeroway == "windsock" and e["type"] == "node":
            airport["windsocks"].append({"id": model.new_id("ws_"), "lon": e["lon"], "lat": e["lat"], "lit": 1})
        elif aeroway in ("control_tower", "tower") or (t.get("man_made") == "tower" and aeroway):
            c = [(e["lon"], e["lat"])] if e["type"] == "node" else coords(e)
            if c:
                height = _number(t, "height", 30.0)
                airport["viewpoints"].append({"id": model.new_id("vp_"), "lon": sum(x for x, _ in c) / len(c),
                                              "lat": sum(y for _, y in c) / len(c),
                                              "height_ft": round(height * 3.28084), "name": "Tower"})
        elif aeroway in ("terminal", "hangar") and e["type"] == "way" and "building" in t:
            terminals.append(e)

    # taxiway + apron pavement
    if taxi_surfaces:
        merged = unary_union(taxi_surfaces).simplify(0.5)
        for poly in getattr(merged, "geoms", [merged]):
            if poly.geom_type != "Polygon" or poly.area < 50:
                continue
            poly = projector.to_wgs84(poly)
            rings = [[model.node(x, y) for x, y in list(poly.exterior.coords)[:-1]]]
            rings += [[model.node(x, y) for x, y in list(r.coords)[:-1]] for r in poly.interiors if len(r.coords) > 3]
            airport["pavements"].append({"id": model.new_id("pav_"), "name": "Taxiways and aprons", "surface": 1,
                                         "smoothness": 0.25, "texture_heading": 0.0, "rings": rings})

    # taxi route network: every node of a route way, joined where ways share nodes
    used = {}
    for way, kind, name in route_ways:
        refs = [n for n in way.get("nodes", []) if n in nodes]
        for a, b in zip(refs, refs[1:]):
            if a == b or distance_m(*nodes[a], *nodes[b]) < 0.5:
                continue
            for n in (a, b):
                if n not in used:
                    used[n] = len(used)
                    airport["taxi_nodes"].append({"id": used[n], "lon": nodes[n][0], "lat": nodes[n][1],
                                                  "usage": "both", "name": ""})
            airport["taxi_edges"].append({"id": model.new_id("te_"), "a": used[a], "b": used[b], "oneway": False,
                                          "kind": kind if kind == "runway" else "taxiway_E", "name": name,
                                          "active": []})
    route_lines = [LineString([nodes[n] for n in w.get("nodes", []) if n in nodes]) for w, k, _ in route_ways
                   if k == "taxiway" and len([n for n in w.get("nodes", []) if n in nodes]) >= 2]
    route_metric = unary_union([projector.to_metric(line) for line in route_lines]) if route_lines else None

    # stands
    stand_points = []
    for e in tagged:
        aeroway = e["tags"].get("aeroway")
        if aeroway not in ("parking_position", "gate"):
            continue
        c = [(e["lon"], e["lat"])] if e["type"] == "node" else coords(e)
        if not c:
            continue
        stand_points.append((aeroway, c[0] if e["type"] == "node" else c[-1], e["tags"]))
    positions = [p for kind, p, _ in stand_points if kind == "parking_position"]
    for kind, (lon, lat), t in stand_points:
        if kind == "gate" and any(distance_m(lon, lat, x, y) < 40 for x, y in positions):
            continue  # the gate's parking position is already a startup
        heading = 0.0
        if route_metric is not None and not route_metric.is_empty:
            p = projector.to_metric(Point(lon, lat))
            nearest = route_metric.interpolate(route_metric.project(p)) if route_metric.geom_type == "LineString" \
                else min((g.interpolate(g.project(p)) for g in route_metric.geoms), key=lambda q: q.distance(p))
            q = projector.to_wgs84(nearest)
            heading = bearing(q.x, q.y, lon, lat)
        ref = t.get("ref") or t.get("name") or str(len(airport["startups"]) + 1)
        airport["startups"].append({"id": model.new_id("st_"), "lon": lon, "lat": lat, "heading": round(heading, 1),
                                    "type": "gate" if kind == "gate" else "tie_down",
                                    "categories": ["heavy", "jets", "turboprops", "props"],
                                    "name": f"{'Gate' if kind == 'gate' else 'Stand'} {ref}", "width_code": "",
                                    "operation": "none", "airlines": []})

    # terminals and hangars as default facades
    for e in terminals:
        geom, _ = way_polygon(e, nodes)
        if geom is None:
            continue
        t = e["tags"]
        levels = _number(t, "building:levels")
        height = _number(t, "height") or (levels * 4 if levels else 15 if t.get("aeroway") == "terminal" else 12)
        facade = TERMINAL_FACADE if t.get("aeroway") == "terminal" else HANGAR_FACADE
        ring = [list(p) for p in list(geom.exterior.coords)[:-1]]
        airport["placements"].append({"id": model.new_id("pl_"), "kind": "polygon", "path": facade,
                                      "rings": [ring], "height": max(3, round(height))})
    if not airport["windsocks"] and airport["runways"]:
        stats["no_windsock"] += 1
    report = {k: len(airport[k]) for k in ("runways", "helipads", "pavements", "lines", "startups", "windsocks",
                                           "viewpoints", "taxi_nodes", "taxi_edges", "boundaries", "placements")}
    report.update(stats)
    report["has_aerodrome"] = aerodrome is not None
    return airport, report

