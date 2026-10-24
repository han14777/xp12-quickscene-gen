"""JSON API for the library browser, asset previews, model import, placed items and airport projects (the city
pipeline routes stay in server.py). Each route is (method, path pattern) -> handler(match, query, body, settings);
"<name>" segments match one path segment. Handlers return JSON-able data, or (bytes, content type) for files.

  GET  /api/library/packs?refresh=1          installed libraries
  GET  /api/library/search?q&pack&type&category&offset&limit&hidden
  GET  /api/asset?ref=lib:<path>[&pack=n] | file:<token> | job:<name>:<rel> | apt:<name>:<rel>   preview data
  GET  /api/texture?t=<token>                texture as PNG
  GET  /api/models?owner=job:<name>|apt:<name>              the project's own asset files
  POST /api/models/import {owner, path, scale, up}          copy / convert a 3D model into the project
  GET  /api/jobs/<name>/placements            placed items of a city project
  POST /api/jobs/<name>/placements {items}    save them (validated)
  POST /api/jobs/<name>/import-dsf {path}     add the items of a scenery pack's DSFs inside the area
  GET  /api/airports                          airport projects
  GET  /api/airports/enums                    value tables for the editor
  GET  /api/airports/search?q=                X-Plane's airport database
  GET  /api/airports/packs                    installed scenery packs with airports
  POST /api/airports {name, source, overwrite} create a project from a source (airport/create.py)
  GET  /api/airports/<name>                   model, validation, build / install state
  POST /api/airports/<name> {data}            save (+ validation)
  POST /api/airports/<name>/export | install | uninstall
  GET  /api/airports/<name>/history           earlier saves; POST /api/airports/<name>/restore {file}
"""

import json
import re
import threading
from pathlib import Path

from cityscene import assets, dsf_import, install as installer, library, model_import, placements
from cityscene.airport import create, model as apt_model, sources, validate
from cityscene.airport.project import AirportProject, export as export_airport, list_projects
from cityscene.config import Job, check_name, xplane_root
from cityscene.geo import zones_union
from cityscene.tools_setup import ensure_dsftool

ROUTES = []
_airport_lock = threading.Lock()


def route(method: str, pattern: str):
    regex = re.compile("^" + re.sub(r"<(\w+)>", r"(?P<\1>[^/]+)", pattern) + "$")

    def wrap(fn):
        ROUTES.append((method, regex, fn))
        return fn
    return wrap


def dispatch(method: str, path: str, query: dict, body: dict, settings: dict):
    """Returns (found, result)."""
    for m, regex, fn in ROUTES:
        if m == method:
            match = regex.match(path)
            if match:
                return True, fn(match.groupdict(), query, body, settings)
    return False, None


def q1(query: dict, key: str, default: str = "") -> str:
    return query.get(key, [default])[0]


# ---------- library and previews ----------

@route("GET", "/api/library/packs")
def library_packs(m, query, body, settings):
    index = library.get_index(settings, refresh=q1(query, "refresh") == "1")
    return {"packs": [{**p, "index": i} for i, p in enumerate(index.packs)], "total": len(index.items),
            "categories": library.CATEGORIES, "types": sorted(set(library.ASSET_TYPES.values()))}


@route("GET", "/api/library/search")
def library_search(m, query, body, settings):
    index = library.get_index(settings)
    pack = q1(query, "pack")
    return index.search(q1(query, "q"), int(pack) if pack.isdigit() else None, q1(query, "type"),
                        q1(query, "category"), q1(query, "hidden") == "1", int(q1(query, "offset", "0") or 0),
                        min(500, int(q1(query, "limit", "200") or 200)))


def owner_models_dir(owner: str, settings: dict) -> Path:
    kind, _, name = owner.partition(":")
    if kind == "job":
        return Job.open(name, settings).models_dir
    if kind == "apt":
        return AirportProject.open(name, settings).models_dir
    raise ValueError(f"Unknown owner {owner!r}")


def resolve_ref(ref: str, settings: dict, pack: str = "") -> Path:
    kind, _, rest = ref.partition(":")
    if kind == "lib":
        return library.get_index(settings).resolve(rest, int(pack) if pack.isdigit() else None)
    if kind == "file":
        return assets.token_path(rest, settings)
    if kind in ("job", "apt"):
        name, _, rel = rest.partition(":")
        base = owner_models_dir(f"{kind}:{name}", settings).resolve()
        path = (base / rel).resolve()
        if base not in path.parents:
            raise ValueError("File outside the models folder")
        return path
    raise ValueError(f"Unknown asset reference {ref!r}")


@route("GET", "/api/asset")
def asset(m, query, body, settings):
    ref = q1(query, "ref")
    path = resolve_ref(ref, settings, q1(query, "pack"))
    data = assets.read_asset(path, settings)
    data["ref"] = ref
    if ref.startswith("lib:"):
        index = library.get_index(settings)
        data["libraries"] = index.providers_of(ref[4:])
    return data


@route("GET", "/api/texture")
def texture(m, query, body, settings):
    return assets.texture_png(q1(query, "t"), settings), "image/png"


@route("GET", "/api/models")
def models(m, query, body, settings):
    owner = q1(query, "owner")
    base = owner_models_dir(owner, settings)
    files = []
    if base.is_dir():
        files = sorted(p.relative_to(base).as_posix() for p in base.rglob("*")
                       if p.suffix.lower() in (".obj", ".agp", ".fac", ".for", ".pol", ".lin", ".str"))
    return {"owner": owner, "dir": str(base), "files": files}


@route("POST", "/api/models/import")
def models_import(m, query, body, settings):
    base = owner_models_dir(str(body.get("owner") or ""), settings)
    source = Path(str(body.get("path") or "").strip().strip('"'))
    return model_import.import_any(base, source, float(body.get("scale") or 1), str(body.get("up") or "y"))


# ---------- city placements ----------

@route("GET", "/api/jobs/<name>/placements")
def job_placements(m, query, body, settings):
    return {"items": placements.load(Job.open(m["name"], settings))}


@route("POST", "/api/jobs/<name>/placements")
def save_job_placements(m, query, body, settings):
    return {"items": placements.save(Job.open(m["name"], settings), body.get("items") or [])}


@route("POST", "/api/jobs/<name>/import-dsf")
def job_import_dsf(m, query, body, settings):
    job = Job.open(m["name"], settings)
    pack = Path(str(body.get("path") or "").strip().strip('"'))
    if not (pack / "Earth nav data").is_dir():
        raise ValueError(f"{pack} is not a scenery pack (no Earth nav data folder)")
    bounds = zones_union(job.zones()).bounds
    items, report = dsf_import.pack_items(pack, ensure_dsftool(settings), job.root / "processed" / "import", bounds)
    if report.get("local_assets"):
        job.models_dir.mkdir(parents=True, exist_ok=True)
        report["copied_folders"] = dsf_import.copy_local_assets(pack, items, job.models_dir)
    report.pop("local_assets", None)
    current = placements.load(job)
    saved = placements.save(job, current + items)
    return {"items": saved, "report": report, "added": len(items)}


# ---------- airports ----------

def _enum(table: dict) -> list:
    return [[k, v] for k, v in table.items()]


@route("GET", "/api/airports")
def airports(m, query, body, settings):
    return list_projects(settings)


@route("GET", "/api/airports/enums")
def airport_enums(m, query, body, settings):
    return {"surfaces": _enum(apt_model.SURFACES), "shoulders": _enum(apt_model.SHOULDERS),
            "markings": _enum(apt_model.MARKINGS), "approach_lights": _enum(apt_model.APPROACH_LIGHTS),
            "edge_lights": _enum(apt_model.EDGE_LIGHTS), "reil": _enum(apt_model.REIL),
            "beacons": _enum(apt_model.BEACONS), "sign_sizes": _enum(apt_model.SIGN_SIZES),
            "light_fixtures": _enum(apt_model.LIGHT_FIXTURES), "line_types": _enum(apt_model.LINE_TYPES),
            "light_types": _enum(apt_model.LIGHT_TYPES), "startup_types": list(apt_model.STARTUP_TYPES),
            "categories": list(apt_model.CATEGORIES), "width_codes": list(apt_model.WIDTH_CODES),
            "operations": list(apt_model.OPERATIONS),
            "frequency_types": list(apt_model.FREQUENCY_TYPES.values()), "taxi_usage": list(apt_model.TAXI_USAGE),
            "edge_kinds": list(apt_model.EDGE_KINDS), "active_types": list(apt_model.ACTIVE_TYPES),
            "helipad_edge_lights": _enum(apt_model.HELIPAD_EDGE_LIGHTS)}


@route("GET", "/api/airports/search")
def airport_search(m, query, body, settings):
    return sources.GlobalAirports.load(settings).search(q1(query, "q"))


@route("GET", "/api/airports/packs")
def airport_packs(m, query, body, settings):
    return sources.installed_airport_packs(settings)


@route("POST", "/api/airports")
def airport_create(m, query, body, settings):
    name = check_name(str(body.get("name") or "").strip())
    source = body.get("source") or {}
    if not _airport_lock.acquire(blocking=False):
        raise RuntimeError("Another airport import is running; wait for it to finish")
    try:
        if AirportProject.dir_for(name, settings).joinpath("airport.json").exists() and not body.get("overwrite"):
            raise FileExistsError(f"Airport project {name} already exists")
        staging = AirportProject(name, AirportProject.dir_for(name, settings))
        staging.root.mkdir(parents=True, exist_ok=True)
        data, report = create.build(source, settings, staging)
        project = AirportProject.create(name, data, settings, overwrite=True)
        return {"name": project.name, "report": report}
    finally:
        _airport_lock.release()


def _airport_state(project: AirportProject, settings: dict) -> dict:
    root = xplane_root(settings)
    link = root / "Custom Scenery" / project.scenery_name if root else None
    return {"built": (project.build_dir(settings) / "Earth nav data").is_dir(),
            "installed": bool(link) and link.exists() and installer._same_target(link, project.build_dir(settings)),
            "build_dir": str(project.build_dir(settings)), "models_dir": str(project.models_dir)}


@route("GET", "/api/airports/<name>")
def airport_get(m, query, body, settings):
    project = AirportProject.open(m["name"], settings)
    data = project.load()
    findings = validate.check(data)
    return {"name": project.name, "data": data, "findings": findings, "summary": validate.summary(findings),
            **_airport_state(project, settings)}


@route("POST", "/api/airports/<name>")
def airport_save(m, query, body, settings):
    project = AirportProject.open(m["name"], settings)
    data = project.save(body.get("data") or {})
    findings = validate.check(data)
    return {"findings": findings, "summary": validate.summary(findings), "placements": data["placements"],
            "options": data["options"]}


@route("POST", "/api/airports/<name>/export")
def airport_export(m, query, body, settings):
    project = AirportProject.open(m["name"], settings)
    log = []
    report = export_airport(project, settings, on_log=log.append)
    return {"report": report, "log": log, **_airport_state(project, settings)}


@route("POST", "/api/airports/<name>/install")
def airport_install(m, query, body, settings):
    project = AirportProject.open(m["name"], settings)
    return {**installer.install(project, settings), **_airport_state(project, settings)}


@route("POST", "/api/airports/<name>/uninstall")
def airport_uninstall(m, query, body, settings):
    project = AirportProject.open(m["name"], settings)
    return {**installer.uninstall(project, settings), **_airport_state(project, settings)}


@route("GET", "/api/airports/<name>/history")
def airport_history(m, query, body, settings):
    project = AirportProject.open(m["name"], settings)
    folder = project.root / "history"
    files = sorted((p.name for p in folder.glob("airport-*.json")), reverse=True) if folder.is_dir() else []
    return {"files": files}


@route("POST", "/api/airports/<name>/restore")
def airport_restore(m, query, body, settings):
    project = AirportProject.open(m["name"], settings)
    file = str(body.get("file") or "")
    if not re.fullmatch(r"airport-[\w\-]+\.json", file):
        raise ValueError("Bad history file name")
    data = json.loads((project.root / "history" / file).read_text(encoding="utf-8"))
    project.save(data)
    return airport_get(m, query, body, settings)
