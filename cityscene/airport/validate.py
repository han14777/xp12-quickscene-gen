"""Checks before export, in the spirit of WED's validation (WED_Validate.cpp) and the Scenery Gateway's rules.
Each finding is {"level": "error" | "warning", "code", "message", "ref": {"list", "id"} or None}; errors stop the
export, warnings do not. Runway headings are compared with true north, so a large magnetic variation shows as a
warning, never an error.
"""

import math
import re

from shapely.geometry import Point, Polygon

from cityscene.airport import model

RUNWAY_NAME = re.compile(r"^(0?[1-9]|[1-2][0-9]|3[0-6])([LCRSWT])?$")
WATER_NAME = re.compile(r"^(0?[1-9]|[1-2][0-9]|3[0-6])([LCRSWT])?$|^(N|NE|E|SE|S|SW|W|NW)$")
OPPOSITE = {"L": "R", "R": "L", "C": "C", "": "", "S": "S", "W": "W", "T": "T"}
SIGN_TOKEN = re.compile(r"\{[^{}]*\}|[^{}]+")


def distance_m(lon1, lat1, lon2, lat2) -> float:
    k = math.pi / 180
    x = (lon2 - lon1) * k * math.cos((lat1 + lat2) / 2 * k)
    y = (lat2 - lat1) * k
    return math.hypot(x, y) * 6371008.8


def bearing(lon1, lat1, lon2, lat2) -> float:
    k = math.pi / 180
    y = math.sin((lon2 - lon1) * k) * math.cos(lat2 * k)
    x = math.cos(lat1 * k) * math.sin(lat2 * k) - math.sin(lat1 * k) * math.cos(lat2 * k) * math.cos((lon2 - lon1) * k)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _ring_polygon(ring):
    pts = [(n["lon"], n["lat"]) for n in ring]
    return Polygon(pts) if len(pts) >= 3 else None


def check(data: dict) -> list[dict]:
    out = []

    def add(level, code, message, list_name=None, item=None):
        out.append({"level": level, "code": code, "message": message,
                    "ref": {"list": list_name, "id": item.get("id")} if item is not None else None})

    h = data["header"]
    if not re.fullmatch(r"[A-Za-z0-9]{3,7}", str(h.get("icao", ""))):
        add("error", "icao", "Airport code must be 3-7 letters or digits")
    if not str(h.get("name", "")).strip():
        add("error", "name", "The airport needs a name")
    try:
        elevation = float(h.get("elevation_ft"))
        if not -1500 <= elevation <= 30000:
            add("error", "elevation", "Elevation must be between -1500 and 30000 ft")
        elif elevation == 0:
            add("warning", "elevation_zero", "Elevation is 0 ft; set the real field elevation")
    except (TypeError, ValueError):
        add("error", "elevation", "Elevation must be a number (feet)")

    if not (data["runways"] or data["water_runways"] or data["helipads"]):
        add("error", "no_runway", "An airport needs at least one runway, water runway or helipad")

    names = set()
    for r in data["runways"]:
        a, b = r["ends"]
        for e in (a, b):
            if not RUNWAY_NAME.match(e["name"]):
                add("error", "runway_name", f"Runway end name {e['name']!r} is not valid (01-36 with L/C/R)",
                    "runways", r)
            if e["name"] in names:
                add("error", "runway_duplicate", f"Runway {e['name']} exists twice", "runways", r)
            names.add(e["name"])
        length = distance_m(a["lon"], a["lat"], b["lon"], b["lat"])
        label = f"{a['name']}/{b['name']}"
        if length < 30:
            add("error", "runway_short", f"Runway {label} is only {length:.0f} m long", "runways", r)
            continue
        if not 1 <= float(r["width_m"]) <= 150:
            add("error", "runway_width", f"Runway {label} width must be 1-150 m", "runways", r)
        ma, mb = RUNWAY_NAME.match(a["name"]), RUNWAY_NAME.match(b["name"])
        if ma and mb:
            na, nb = int(ma.group(1)), int(mb.group(1))
            if (na + 18 - 1) % 36 + 1 != nb or OPPOSITE.get(ma.group(2) or "") != (mb.group(2) or ""):
                add("error", "runway_pair", f"Runway ends {label} do not match (expected reciprocal numbers)",
                    "runways", r)
            heading = bearing(a["lon"], a["lat"], b["lon"], b["lat"])
            diff = abs((heading - na * 10 + 180) % 360 - 180)
            if diff > 30:
                add("warning", "runway_heading",
                    f"Runway {a['name']} points {heading:.0f}° true; its number suggests {na * 10}° "
                    "(fine only where magnetic variation is that large; are the ends swapped?)", "runways", r)
        for e in (a, b):
            if float(e["displaced_m"]) + float(e["blastpad_m"]) >= length:
                add("error", "runway_displaced", f"Displaced threshold / blast pad of {e['name']} is longer "
                    "than the runway", "runways", r)

    for w in data["water_runways"]:
        for e in w["ends"]:
            if not WATER_NAME.match(e["name"]):
                add("error", "water_name", f"Water runway end name {e['name']!r} is not valid", "water_runways", w)
    for p in data["helipads"]:
        if not str(p["name"]).upper().startswith("H"):
            add("warning", "helipad_name", f"Helipad {p['name']!r} should be named H1, H2, ...", "helipads", p)
        if not (1 <= float(p["length_m"]) <= 200 and 1 <= float(p["width_m"]) <= 200):
            add("error", "helipad_size", f"Helipad {p['name']} size must be 1-200 m", "helipads", p)

    for key in ("pavements", "boundaries"):
        for item in data[key]:
            if not item["rings"] or len(item["rings"][0]) < 3:
                add("error", f"{key}_ring", f"{key[:-1].capitalize()} {item.get('name') or ''} needs at least "
                    "3 points", key, item)
                continue
            poly = _ring_polygon(item["rings"][0])
            if poly is not None and not poly.is_valid:
                add("warning", f"{key}_self_intersect", f"{key[:-1].capitalize()} {item.get('name') or ''} "
                    "crosses itself", key, item)
    for line in data["lines"]:
        if len(line["nodes"]) < 2:
            add("error", "line_short", "A line needs at least 2 points", "lines", line)

    boundary = None
    if not data["boundaries"]:
        add("warning", "no_boundary", "No airport boundary: X-Plane flattens and clears terrain inside it, and the "
            "Scenery Gateway requires one")
    elif data["boundaries"][0]["rings"] and len(data["boundaries"][0]["rings"][0]) >= 3:
        boundary = _ring_polygon(data["boundaries"][0]["rings"][0])
        if boundary is not None and not boundary.is_valid:
            boundary = boundary.buffer(0)

    startup_names = set()
    for s in data["startups"]:
        if not str(s["name"]).strip():
            add("error", "startup_name", "Every startup location needs a name", "startups", s)
        elif s["name"] in startup_names:
            add("warning", "startup_duplicate", f"Startup location name {s['name']!r} is used more than once "
                "(allowed, but ATC calls them by name)", "startups", s)
        startup_names.add(s["name"])
        if s["type"] not in model.STARTUP_TYPES:
            add("error", "startup_type", f"Startup {s['name']}: type must be one of {', '.join(model.STARTUP_TYPES)}",
                "startups", s)
        if not s["categories"] or any(c not in model.CATEGORIES for c in s["categories"]):
            add("error", "startup_categories", f"Startup {s['name']}: choose aircraft categories", "startups", s)
        if s.get("width_code") and s["width_code"] not in model.WIDTH_CODES:
            add("error", "startup_width", f"Startup {s['name']}: ICAO width code must be A-F", "startups", s)
        if boundary is not None and not boundary.contains(Point(s["lon"], s["lat"])):
            add("warning", "startup_outside", f"Startup {s['name']} is outside the airport boundary", "startups", s)

    nodes = {n["id"]: n for n in data["taxi_nodes"]}
    if len(nodes) != len(data["taxi_nodes"]):
        add("error", "taxi_node_ids", "Taxi route node ids must be unique")
    linked = set()
    for e in data["taxi_edges"]:
        if e["a"] not in nodes or e["b"] not in nodes:
            add("error", "taxi_edge_node", f"Taxi route {e.get('name') or e['id']} refers to a missing node",
                "taxi_edges", e)
            continue
        if e["a"] == e["b"] or distance_m(nodes[e["a"]]["lon"], nodes[e["a"]]["lat"], nodes[e["b"]]["lon"],
                                           nodes[e["b"]]["lat"]) < 0.5:
            add("error", "taxi_edge_zero", f"Taxi route {e.get('name') or e['id']} has no length", "taxi_edges", e)
        if e["kind"] not in model.EDGE_KINDS:
            add("error", "taxi_edge_kind", f"Taxi route kind {e['kind']!r} is not valid", "taxi_edges", e)
        if e["kind"] == "runway" and e.get("name") and not all(RUNWAY_NAME.match(x)
                                                                  for x in e["name"].split("/")):
            add("warning", "taxi_runway_name", f"Runway route {e['name']!r} should be named like 09L/27R",
                "taxi_edges", e)
        for a in e.get("active") or []:
            if a.get("type") not in model.ACTIVE_TYPES:
                add("error", "active_type", "Active zone type must be arrival, departure or ils", "taxi_edges", e)
        linked.update((e["a"], e["b"]))
    for row in data.get("raw", []):  # ground truck routes also use taxi nodes
        words = row.split()
        if words[0] == "1206" and len(words) >= 3:
            linked.update(int(w) for w in words[1:3] if w.lstrip("-").isdigit())
    orphans = [n for n in nodes if n not in linked]
    if orphans:
        add("warning", "taxi_orphans", f"{len(orphans)} taxi route nodes are not connected to any route")
    if data["taxi_edges"]:
        groups = _components(data["taxi_edges"])
        if groups > 1:
            add("warning", "taxi_disconnected", f"The taxi route network has {groups} separate parts; AI traffic "
                "cannot move between them")
    elif data["startups"] and data["runways"]:
        add("warning", "no_taxi_routes", "No taxi routes: ATC and AI traffic need a route network")

    for f in data["frequencies"]:
        if not 108 <= float(f["mhz"]) <= 137:
            add("error", "frequency_range", f"Frequency {f['mhz']} MHz is outside 108-137 MHz", "frequencies", f)
        if not str(f.get("name", "")).strip():
            add("warning", "frequency_name", f"Frequency {f['mhz']} MHz has no name", "frequencies", f)
    if any(f["type"] in ("tower", "ground") for f in data["frequencies"]) and not data["viewpoints"]:
        add("warning", "no_viewpoint", "There is a tower frequency but no tower viewpoint")
    if data["runways"] and not data["windsocks"]:
        add("warning", "no_windsock", "No windsock")
    for s in data["signs"]:
        text = str(s["text"])
        if not text or "".join(SIGN_TOKEN.findall(text)) != text or text.count("{") != text.count("}"):
            add("error", "sign_text", f"Sign text {text!r} has unbalanced braces", "signs", s)
        if not 1 <= int(s["size"]) <= 5:
            add("error", "sign_size", "Sign size must be 1-5", "signs", s)
    for lf in data["light_fixtures"]:
        if lf.get("runway") and lf["runway"] not in names:
            add("warning", "light_runway", f"Light fixture refers to runway {lf['runway']} that does not exist",
                "light_fixtures", lf)
    return out


def _components(edges) -> int:
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for e in edges:
        parent[find(e["a"])] = find(e["b"])
    return len({find(x) for x in list(parent)})


def summary(findings: list) -> dict:
    return {"errors": sum(f["level"] == "error" for f in findings),
            "warnings": sum(f["level"] == "warning" for f in findings)}



