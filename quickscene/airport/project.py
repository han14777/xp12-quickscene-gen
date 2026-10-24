"""Airport projects on disk and their export.

airports/<name>/airport.json   the model (model.py); <name> defaults to the ICAO code
airports/<name>/models/        the project's own OBJ / facade / forest files (model import, pack import)
airports/<name>/history/       the previous few saves (undo across sessions)
build/<pack>/                  exported pack: Earth nav data/apt.dat, a DSF per 1-degree tile for the placed items,
                               objects/models/ (copied models folder), README.txt

Export refuses to run while validate.check reports errors. The apt.dat is read back after writing and its element
counts compared with the model; DSFs are compiled with DSFTool and decompiled for the same count check as city
packs (build_dsf.verify_dsf). Optional exclusions (options.exclude: obj / fac / for) cover the airport boundary,
tiled into rectangles as for city packs (polygon exclusions do nothing in XP12).
"""

import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path

from shapely.geometry import Polygon, mapping

from quickscene import build_dsf, placements
from quickscene.airport import aptdat, model, validate
from quickscene.config import check_name, project_path
from quickscene.geo import tile_dir, tile_name
from quickscene.log import get_logger
from quickscene.tools_setup import ensure_dsftool

log = get_logger("airport")
HISTORY_KEEP = 20
EXCLUDABLE = ("obj", "fac", "for")


class AirportProject:
    def __init__(self, name: str, root: Path):
        self.name = name
        self.root = root

    @classmethod
    def dir_for(cls, name: str, settings: dict) -> Path:
        return project_path(settings["paths"].get("airports_dir", "airports")) / check_name(name)

    @classmethod
    def open(cls, name: str, settings: dict) -> "AirportProject":
        project = cls(name, cls.dir_for(name, settings))
        if not project.path.is_file():
            raise FileNotFoundError(f"Airport project {name} does not exist")
        return project

    @classmethod
    def create(cls, name: str, data: dict, settings: dict, overwrite: bool = False) -> "AirportProject":
        project = cls(name, cls.dir_for(name, settings))
        if project.path.exists() and not overwrite:
            raise FileExistsError(f"Airport project {name} already exists")
        project.root.mkdir(parents=True, exist_ok=True)
        data = model.normalise(data)
        data["options"].setdefault("scenery_name", default_pack_name(data))
        data["options"]["created"] = datetime.now().isoformat(timespec="seconds")
        project.save(data, keep_history=False)
        return project

    @property
    def path(self) -> Path:
        return self.root / "airport.json"

    @property
    def models_dir(self) -> Path:
        return self.root / "models"

    def load(self) -> dict:
        return model.normalise(json.loads(self.path.read_text(encoding="utf-8")))

    def save(self, data: dict, keep_history: bool = True) -> dict:
        data = model.normalise(data)
        data["placements"] = placements.validate_items(data.get("placements") or [])
        pack = check_name(str(data["options"].get("scenery_name") or default_pack_name(data)))
        data["options"]["scenery_name"] = pack
        data["options"]["exclude"] = [k for k in data["options"].get("exclude") or [] if k in EXCLUDABLE]
        if keep_history and self.path.exists():
            history = self.root / "history"
            history.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            shutil.copyfile(self.path, history / f"airport-{stamp}.json")
            for old in sorted(history.glob("airport-*.json"))[:-HISTORY_KEEP]:
                old.unlink()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)
        return data

    # install.py works with anything that has scenery_name and build_dir(settings)
    @property
    def scenery_name(self) -> str:
        return json.loads(self.path.read_text(encoding="utf-8"))["options"]["scenery_name"]

    def build_dir(self, settings: dict) -> Path:
        return project_path(settings["paths"]["build_dir"]) / self.scenery_name


def default_pack_name(data: dict) -> str:
    import re
    name = re.sub(r"[^A-Za-z0-9_\-]+", "_", f"{data['header']['icao']}_{data['header']['name']}").strip("_")
    return name[:64] or "Airport"


def list_projects(settings: dict) -> list[dict]:
    base = project_path(settings["paths"].get("airports_dir", "airports"))
    out = []
    if not base.is_dir():
        return out
    for folder in sorted(base.iterdir()):
        path = folder / "airport.json"
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                out.append({"name": folder.name, "icao": data["header"]["icao"], "title": data["header"]["name"],
                            "scenery_name": data["options"].get("scenery_name"),
                            "built": (project_path(settings["paths"]["build_dir"]) /
                                      str(data["options"].get("scenery_name")) / "Earth nav data").is_dir()})
            except (OSError, ValueError, KeyError):
                continue
    return out


# ---------- export ----------

def _boundary_zone(data: dict) -> list:
    if not data["boundaries"] or not data["boundaries"][0]["rings"]:
        return []
    ring = [(n["lon"], n["lat"]) for n in data["boundaries"][0]["rings"][0]]
    if len(ring) < 3:
        return []
    poly = Polygon(ring).buffer(0)
    return [{"type": "Feature", "properties": {"id": "boundary"}, "geometry": mapping(poly)}]


def export(project: AirportProject, settings: dict, on_log=None) -> dict:
    def say(msg, *args):
        log.info(msg, *args)
        if on_log:
            on_log("[airport] " + (msg % args if args else msg))

    data = project.load()
    findings = validate.check(data)
    errors = [f for f in findings if f["level"] == "error"]
    if errors:
        raise RuntimeError(f"{len(errors)} validation errors; fix them first (first: {errors[0]['message']})")
    build_dir = project.build_dir(settings)
    earth = build_dir / "Earth nav data"
    if earth.exists():
        shutil.rmtree(earth)
    models_out = build_dir / "objects" / "models"
    if models_out.exists():
        shutil.rmtree(models_out)
    earth.mkdir(parents=True)

    text = aptdat.write_file([data])
    (earth / "apt.dat").write_text(text, encoding="utf-8", newline="\n")
    reread = aptdat.parse_text(text)[0]
    for key in model.LISTS:
        if len(reread[key]) != len(data[key]):
            raise RuntimeError(f"apt.dat check failed: {key} {len(data[key])} written, {len(reread[key])} read back")
    say("apt.dat: %d runways, %d helipads, %d pavements, %d lines, %d startups, %d taxi routes",
        len(data["runways"]), len(data["helipads"]), len(data["pavements"]), len(data["lines"]),
        len(data["startups"]), len(data["taxi_edges"]))

    def resolve(path: str) -> str:
        if (project.models_dir / path).is_file():
            return f"objects/models/{path}"
        return path

    objects, polygons = placements.dsf_entries(data["placements"], resolve)
    stats = Counter()
    by_obj, by_poly = placements.split_by_tile(objects, polygons, stats)
    zones = _boundary_zone(data) if data["options"].get("exclude") else []
    tiles = set(by_obj) | set(by_poly)
    if zones:
        from quickscene.geo import tiles_for
        from shapely.geometry import shape
        tiles |= set(tiles_for(shape(zones[0]["geometry"])))
    dsf_report = {}
    if tiles:
        dsftool = ensure_dsftool(settings)
        dsf_settings = {"dsf": {"exclusion_cell_m": settings["dsf"]["exclusion_cell_m"],
                                "exclusion_types": data["options"].get("exclude") or []}}
        work = project.root / "processed"
        work.mkdir(exist_ok=True)
        for tile in sorted(tiles):
            name = tile_name(*tile)
            dsf_text, rep = build_dsf.build_text(tile, [], zones, dsf_settings, by_obj.get(tile, []),
                                                 by_poly.get(tile, []))
            text_path = work / f"{name}.dsf.txt"
            text_path.write_text(dsf_text, encoding="utf-8", newline="\n")
            dsf_path = earth / tile_dir(*tile) / f"{name}.dsf"
            dsf_path.parent.mkdir(parents=True, exist_ok=True)
            build_dsf.run_dsftool(dsftool, "--text2dsf", str(text_path), str(dsf_path))
            build_dsf.verify_dsf(dsftool, dsf_path, build_dsf.summarize_text(dsf_text), work / f"{name}.roundtrip.txt")
            dsf_report[name] = rep
            say("DSF %s: %d objects, %d polygons / lines, %d exclusion rectangles, decompile check passed", name,
                rep["objects"], rep["placed_polygons"], rep["exclusion_rects"])
    used = [o[0] for o in objects] + [p[0] for p in polygons]
    if any(p.startswith("objects/models/") for p in used) and project.models_dir.is_dir():
        shutil.copytree(project.models_dir, models_out)
    deps = build_dsf.library_report(used)
    readme = (f"{data['header']['icao']} {data['header']['name']} for X-Plane 12\n"
              "Made with QuickScene (airport editor).\n" + build_dsf.dependency_text(deps))
    (build_dir / "README.txt").write_text(readme, encoding="utf-8")
    shutil.copyfile(project.path, build_dir / "airport_project.json")
    report = {"pack": str(build_dir), "dsf": dsf_report, "dependencies": deps,
              "warnings": sum(f["level"] == "warning" for f in findings), **dict(stats)}
    if deps and deps["missing"]:
        say("%d assets are not in any installed library: %s", len(deps["missing"]), ", ".join(deps["missing"][:5]))
    say("Scenery pack: %s", build_dir)
    return report

