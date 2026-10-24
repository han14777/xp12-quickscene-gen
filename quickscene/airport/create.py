"""Start an airport project from one of the sources (sources.py, osm_airport.py) or from scratch.

source = {"type": "blank", "icao", "name", "lon", "lat", "elevation_ft"}
       | {"type": "xplane", "icao"}                      X-Plane's Global Airports
       | {"type": "gateway", "icao"}                     Scenery Gateway (apt.dat + DSF items of 3D sceneries)
       | {"type": "pack", "path", "icao", "dsf": bool}   an installed scenery pack (apt.dat, optionally DSF items
                                                         inside the airport area; the pack's own models are copied)
       | {"type": "osm", "icao"} | {"type": "osm", "bbox": [w, s, e, n]}
"""

import math
import shutil

from quickscene import dsf_import
from quickscene.airport import aptdat, model, osm_airport, sources
from quickscene.airport.project import AirportProject
from quickscene.log import get_logger
from quickscene.tools_setup import ensure_dsftool

log = get_logger("airport")
# Items of an imported pack are kept within the airport's extent plus this margin (degrees of latitude ~ 1.1 km)
MARGIN_DEG = 0.01


def blank(icao: str, name: str, lon: float, lat: float, elevation_ft: float = 0) -> dict:
    """A new airport with one 1500 m runway (09/27) centred on the point, ready to be moved and edited."""
    data = model.empty(icao, name, elevation_ft)
    half = 750 / (111320 * math.cos(math.radians(lat)))
    data["runways"].append(model.runway([model.runway_end("09", lon - half, lat),
                                         model.runway_end("27", lon + half, lat)], 30.0, 1))
    data["windsocks"].append({"id": model.new_id("ws_"), "lon": lon, "lat": lat + 0.0008, "lit": 0})
    data["meta"].update({"datum_lon": f"{lon:.6f}", "datum_lat": f"{lat:.6f}"})
    return data


def _bounds(data: dict):
    box = model.bbox(data)
    if not box:
        return None
    return box[0] - MARGIN_DEG, box[1] - MARGIN_DEG, box[2] + MARGIN_DEG, box[3] + MARGIN_DEG


def build(source: dict, settings: dict, project: AirportProject | None = None) -> tuple[dict, dict]:
    """(airport model, report). DSF items whose assets live inside an imported pack are copied into
    project.models_dir when a project is given."""
    kind = source.get("type")
    report = {"source": kind}
    if kind == "blank":
        data = blank(str(source.get("icao") or "XXXX").upper(), str(source.get("name") or "New airport"),
                     float(source["lon"]), float(source["lat"]), float(source.get("elevation_ft") or 0))
    elif kind == "xplane":
        data = sources.GlobalAirports.load(settings).airport(str(source["icao"]).strip().upper())
    elif kind == "gateway":
        got = sources.gateway_scenery(str(source["icao"]))
        airports = aptdat.parse_text(got["apt"])
        if not airports:
            raise ValueError("The Gateway apt.dat holds no airport")
        data = airports[0]
        report.update(scenery_id=got["scenery_id"], artist=got["artist"])
        if got["dsf_text"]:
            items, stats = dsf_import.parse_dsf_text(got["dsf_text"])
            data["placements"] = items
            report["dsf"] = stats
    elif kind == "pack":
        data = sources.pack_airport(source["path"], str(source["icao"]))
        if source.get("dsf", True):
            work = (project.root if project else cache_dir(settings)) / "processed" / "import"
            items, stats = dsf_import.pack_items(source["path"], ensure_dsftool(settings), work, _bounds(data))
            if project is not None and stats.get("local_assets"):
                project.models_dir.mkdir(parents=True, exist_ok=True)
                stats["copied_folders"] = dsf_import.copy_local_assets(source["path"], items, project.models_dir)
            data["placements"] = items
            stats.pop("local_assets", None)
            report["dsf"] = stats
            if work.exists():
                shutil.rmtree(work, ignore_errors=True)
    elif kind == "osm":
        raw = osm_airport.fetch(settings, str(source.get("icao") or ""), source.get("bbox"))
        data, stats = osm_airport.convert(raw, settings, str(source.get("icao") or "").upper())
        report["osm"] = stats
    else:
        raise ValueError(f"Unknown airport source {kind!r}")
    report["counts"] = {k: len(data[k]) for k in model.LISTS if data[k]}
    report["placements"] = len(data.get("placements") or [])
    return data, report


def cache_dir(settings: dict):
    from quickscene.config import project_path
    return project_path(settings["paths"].get("cache_dir", "cache"))
