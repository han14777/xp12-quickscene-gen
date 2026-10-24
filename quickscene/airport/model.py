"""The airport model: one JSON document per airport project, mirroring apt.dat (1200 format) element by element so
nothing is lost between import, editing and export. Coordinates are [lon, lat] like everywhere in this project
(apt.dat itself writes lat lon).

  header       {"type": land|sea|heli, "icao", "name", "elevation_ft"}
  meta         {key: value} for 1302 rows (city, country, iata_code, datum_lat, transition_alt, ...)
  runways      [{id, width_m, surface, shoulder, smoothness, centre_lights, edge_lights, distance_signs,
                 ends: [{name, lon, lat, displaced_m, blastpad_m, markings, approach_lights, tdz_lights, reil}] x 2}]
  water_runways [{id, width_m, buoys, ends: [{name, lon, lat}] x 2}]
  helipads     [{id, name, lon, lat, heading, length_m, width_m, surface, markings, shoulder, smoothness, edge_lights}]
  pavements    [{id, name, surface, smoothness, texture_heading, rings: [[node, ...], ...]}]   (110; first ring outer)
  lines        [{id, name, closed, nodes: [node, ...]}]                                       (120 linear features)
  boundaries   [{id, name, rings}]                                                            (130)
     node = {lon, lat, ctrl: [lon, lat] or null (Bezier control point), line: line type or 0, light: light type or 0}
  viewpoints   [{id, lon, lat, height_ft, name}]                                              (14 tower viewpoint)
  startups     [{id, lon, lat, heading, type, categories: [...], name, width_code, operation, airlines: [...]}]
                                                                                              (1300 + 1301)
  beacons      [{id, lon, lat, type}] (18)    windsocks [{id, lon, lat, lit}] (19)
  signs        [{id, lon, lat, heading, size, text}] (20)
  light_fixtures [{id, lon, lat, type, heading, angle, runway, description}] (21: VASI / PAPI / guard lights)
  frequencies  [{id, type: atis|unicom|clearance|ground|tower|approach|departure, mhz, name}] (1050-1056)
  taxi_nodes   [{id, lon, lat, usage: dest|init|both|junc, name}] (1201)
  taxi_edges   [{id, a, b, oneway, kind: taxiway|taxiway_A..F|runway, name, active: [{type, runways}]}]
                                                                                              (1202 + 1204)
  raw          ["apt.dat row", ...] rows kept verbatim (ATC flows 1000-1110, trucks 1206 / 1400 / 1401, others)
  placements   DSF items (placements.py: objects, facades, forests, polygons, lines from any library)
  options      {"scenery_name", "exclude": [obj, fac, for]}

Value tables follow the apt.dat 1200 specification (developer.x-plane.com, "apt.dat file format").
"""

import copy
import uuid

SURFACES = {1: "Asphalt", 2: "Concrete", 3: "Turf / grass", 4: "Dirt", 5: "Gravel", 12: "Dry lakebed",
            13: "Water", 14: "Snow / ice", 15: "Transparent",
            20: "Light asphalt 1", 21: "Light asphalt 2", 22: "Light asphalt 3", 23: "Asphalt 4", 24: "Asphalt 5",
            25: "Asphalt 6", 26: "Asphalt 7", 27: "Asphalt 8", 28: "Dark asphalt 9", 29: "Dark asphalt 10",
            30: "Dark asphalt 11", 31: "Dark asphalt 12", 32: "Dark asphalt 13", 33: "Dark asphalt 14",
            34: "Dark asphalt 15", 35: "Dark asphalt 16", 36: "Dark asphalt 17", 37: "Dark asphalt 18",
            38: "Dark asphalt 19", 50: "Light concrete 1", 51: "Light concrete 2", 52: "Light concrete 3",
            53: "Concrete 4", 54: "Concrete 5", 55: "Concrete 6", 56: "Dark concrete 7", 57: "Dark concrete 8"}
SHOULDERS = {0: "None", 1: "Asphalt", 2: "Concrete"}
MARKINGS = {0: "None", 1: "Visual", 2: "Non-precision", 3: "Precision", 4: "UK non-precision", 5: "UK precision"}
APPROACH_LIGHTS = {0: "None", 1: "ALSF-I", 2: "ALSF-II", 3: "Calvert", 4: "Calvert ILS Cat II/III", 5: "SSALR",
                   6: "SSALF", 7: "SALS", 8: "MALSR", 9: "MALSF", 10: "MALS", 11: "ODALS", 12: "RAIL"}
EDGE_LIGHTS = {0: "None", 1: "Low intensity (LIRL)", 2: "Medium intensity (MIRL)", 3: "High intensity (HIRL)"}
REIL = {0: "None", 1: "Omnidirectional", 2: "Unidirectional"}
HELIPAD_MARKINGS = {0: "Default"}
HELIPAD_EDGE_LIGHTS = {0: "None", 1: "Yellow"}
BEACONS = {0: "None", 1: "Airport (white-green)", 2: "Seaport (white-yellow)", 3: "Heliport (green-yellow-white)",
           4: "Military (white-white-green)"}
SIGN_SIZES = {1: "Small taxiway", 2: "Medium taxiway", 3: "Large taxiway", 4: "Large distance remaining",
              5: "Small distance remaining"}
LIGHT_FIXTURES = {1: "VASI", 2: "PAPI-4L (left side)", 3: "PAPI-4R (right side)", 4: "Space Shuttle PAPI",
                  5: "Tri-colour VASI", 6: "Runway guard (wig-wag) lights"}
# Known line types; files may hold others (newer styles), which are kept as numbers and editable as numbers
LINE_TYPES = {0: "None", 1: "Taxiway centreline (solid yellow)", 2: "Miscellaneous boundary (broken yellow)",
              3: "Taxiway edge (double solid yellow)", 4: "Runway hold position", 5: "Other hold position",
              6: "ILS critical area (hatched)", 7: "Taxiway centreline hold position", 8: "Queue lane (broken)",
              9: "Double queue lane (broken)", 20: "Roadway (solid white)", 21: "Roadway (white chequerboard)",
              22: "Roadway centreline (broken white)",
              51: "Taxiway centreline, black border", 52: "Miscellaneous boundary, black border",
              53: "Taxiway edge, black border", 54: "Runway hold position, black border",
              55: "Other hold position, black border", 56: "ILS critical area, black border",
              57: "Centreline hold position, black border", 58: "Queue lane, black border"}
LIGHT_TYPES = {0: "None", 101: "Taxiway centreline (green)", 102: "Taxiway edge (blue)",
               103: "Hold short (amber)", 104: "Runway hold (pulsating amber)",
               105: "ILS critical area (green/amber)", 106: "Hazard boundary (red)",
               107: "Taxiway centreline, one way (green)", 108: "ILS critical, one way (green/amber)"}
STARTUP_TYPES = ("gate", "hangar", "tie_down", "misc")
CATEGORIES = ("heavy", "jets", "turboprops", "props", "helos", "fighters")
WIDTH_CODES = ("A", "B", "C", "D", "E", "F")
OPERATIONS = ("none", "general_aviation", "airline", "cargo", "military")
FREQUENCY_TYPES = {1050: "atis", 1051: "unicom", 1052: "clearance", 1053: "ground", 1054: "tower",
                   1055: "approach", 1056: "departure"}
TAXI_USAGE = ("dest", "init", "both", "junc")
EDGE_KINDS = ("taxiway", "taxiway_A", "taxiway_B", "taxiway_C", "taxiway_D", "taxiway_E", "taxiway_F", "runway")
ACTIVE_TYPES = ("arrival", "departure", "ils")
HEADER_TYPES = {"land": 1, "sea": 16, "heli": 17}

LISTS = ("runways", "water_runways", "helipads", "pavements", "lines", "boundaries", "viewpoints", "startups",
         "beacons", "windsocks", "signs", "light_fixtures", "frequencies", "taxi_nodes", "taxi_edges")


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:8]


def empty(icao: str = "XXXX", name: str = "New airport", elevation_ft: float = 0) -> dict:
    data = {"version": 1, "header": {"type": "land", "icao": icao, "name": name, "elevation_ft": elevation_ft},
            "meta": {}, "raw": [], "placements": [], "options": {"exclude": []}}
    for key in LISTS:
        data[key] = []
    return data


def runway_end(name: str, lon: float, lat: float) -> dict:
    return {"name": name, "lon": lon, "lat": lat, "displaced_m": 0.0, "blastpad_m": 0.0, "markings": 1,
            "approach_lights": 0, "tdz_lights": 0, "reil": 0}


def runway(ends: list, width_m: float = 30.0, surface: int = 1) -> dict:
    return {"id": new_id("rwy_"), "width_m": width_m, "surface": surface, "shoulder": 0, "smoothness": 0.25,
            "centre_lights": 0, "edge_lights": 0, "distance_signs": 0, "ends": ends}


def node(lon: float, lat: float, line: int = 0, light: int = 0, ctrl=None) -> dict:
    return {"lon": lon, "lat": lat, "ctrl": ctrl, "line": line, "light": light}


def normalise(data: dict) -> dict:
    """Fill missing lists / keys and give every element an id (documents from the browser or older versions)."""
    data = copy.deepcopy(data)
    base = empty()
    for key, value in base.items():
        data.setdefault(key, copy.deepcopy(value))
    data["header"] = {**base["header"], **data["header"]}
    for key in LISTS:
        for item in data[key]:
            if key != "taxi_nodes":
                item.setdefault("id", new_id())
    return data


def bbox(data: dict):
    """(west, south, east, north) of everything with coordinates, or None."""
    pts = list(points(data))
    if not pts:
        return None
    lons, lats = [p[0] for p in pts], [p[1] for p in pts]
    return min(lons), min(lats), max(lons), max(lats)


def points(data: dict):
    for r in data.get("runways", []) + data.get("water_runways", []):
        for e in r["ends"]:
            yield e["lon"], e["lat"]
    for key in ("helipads", "viewpoints", "startups", "beacons", "windsocks", "signs", "light_fixtures",
                "taxi_nodes"):
        for item in data.get(key, []):
            yield item["lon"], item["lat"]
    for key in ("pavements", "boundaries"):
        for item in data.get(key, []):
            for ring in item["rings"]:
                for n in ring:
                    yield n["lon"], n["lat"]
    for item in data.get("lines", []):
        for n in item["nodes"]:
            yield n["lon"], n["lat"]


def centre(data: dict):
    box = bbox(data)
    if not box:
        return None
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
