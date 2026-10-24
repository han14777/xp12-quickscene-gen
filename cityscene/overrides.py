"""Per-building edits that survive regeneration: jobs/<name>/overrides.json, keyed by OSM building_id
(e.g. "way/123"), so re-downloading OSM or re-running any step keeps them.

  {"buildings": {
     "way/1": {"delete": true},
     "way/2": {"height_m": 85, "style": "glass"},
     "way/3": {"model": {"path": "eureka/eureka.obj", "heading": 0, "offset_e_m": 0, "offset_n_m": 0}}}}

- height_m: new height of the building's tallest block; every block is scaled by the same factor, so podiums
  and towers keep their proportions (applied in classify)
- style: facade style for the whole building (glass / modern / classic / universal; applied in classify)
- facade: a specific facade for the whole building, by library path (any installed library, e.g. picked in the
  library browser) or a .fac under jobs/<name>/models/; replaces style (classify)
- delete: the building is left out of the DSF (build_dsf)
- model: the building's facades and distant blocks are replaced by an OBJ placed at the building origin plus the
  offset (metres east / north), turned by heading (degrees clockwise from true north). path is either a file under
  jobs/<name>/models/ (copied into the pack with everything else in that folder, so textures come along) or an
  X-Plane library path such as lib/airport/... (build_dsf)

The building origin is the centroid of all its blocks. reference_obj() exports the blocks around that same origin as
a Wavefront OBJ for Blender (default import settings: -Z forward, Y up), so a model built on top of it lines up
when exported with XPlane2Blender.
"""

import json
import math
import shutil
from collections import defaultdict
from pathlib import Path, PurePosixPath

from shapely.geometry import Polygon, shape
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from cityscene.impostors import local_en

STYLES = ("glass", "modern", "classic", "universal")
MAX_OFFSET_M = 500
# OBJ8 lines naming texture files (relative to the .obj)
TEXTURE_KEYS = ("TEXTURE", "TEXTURE_LIT", "TEXTURE_NORMAL", "TEXTURE_DRAPED", "TEXTURE_DRAPED_NORMAL")
IMAGE_EXTS = (".png", ".dds")


def load(job) -> dict:
    """{building_id: edit}; empty when the job has no edits."""
    if not job.overrides_path.exists():
        return {}
    return json.loads(job.overrides_path.read_text(encoding="utf-8")).get("buildings", {})


def save(job, buildings: dict) -> None:
    job.overrides_path.write_text(json.dumps({"buildings": buildings}, ensure_ascii=False, indent=1), encoding="utf-8")


def model_is_local(job, path: str) -> bool:
    return (job.models_dir / path).is_file()


def import_model(job, source: Path, scale: float = 1.0, up: str = "y") -> dict:
    """Make a model chosen anywhere on disk usable by this job (model_import.import_any: X-Plane OBJ8 is copied,
    Wavefront / glTF converted)."""
    from cityscene.model_import import import_any
    return import_any(job.models_dir, source, scale, up)


def import_model_into(models: Path, source: Path) -> dict:
    """Copy an X-Plane OBJ / AGP into a models folder: a file already under it is used in place; otherwise the OBJ
    goes to <models>/<name>/ together with the textures it names (each also in its .png / .dds twin, which X-Plane
    may load instead), and its texture lines are rewritten to the copied file names.
    Returns {"path": path relative to the models folder, "copied": [...], "missing": [...]}."""
    source = Path(source)
    if not source.is_file() or source.suffix.lower() not in (".obj", ".agp"):
        raise ValueError(f"{source} is not an .obj or .agp file")
    try:
        return {"path": source.resolve().relative_to(models.resolve()).as_posix(), "copied": [], "missing": []}
    except ValueError:
        pass
    target_dir = models / source.stem
    target_dir.mkdir(parents=True, exist_ok=True)
    copied, missing = [source.name], []
    # OBJ8 is ASCII; latin-1 round-trips any byte so nothing else in the file changes
    lines = source.read_text(encoding="latin-1").splitlines(keepends=True)
    if source.suffix.lower() == ".obj":
        for i, line in enumerate(lines):
            words = line.split()
            if len(words) < 2 or words[0] not in TEXTURE_KEYS:
                continue
            texture = (source.parent / words[1].replace("\\", "/")).resolve()
            twins = [texture.with_suffix(ext) for ext in IMAGE_EXTS] if texture.suffix.lower() in IMAGE_EXTS else [texture]
            found = [t for t in twins if t.is_file()]
            if not found:
                missing.append(words[1])
                continue
            for t in found:
                shutil.copyfile(t, target_dir / t.name)
                copied.append(t.name)
            lines[i] = line.replace(words[1], texture.name, 1)
    (target_dir / source.name).write_text("".join(lines), encoding="latin-1", newline="")
    return {"path": f"{source.stem}/{source.name}", "copied": sorted(set(copied)), "missing": missing}


def validate(job, edit: dict, settings: dict) -> dict:
    """Return a cleaned edit or raise ValueError. An edit with nothing left in it is returned as {}."""
    out = {}
    if edit.get("delete"):
        return {"delete": True}
    if edit.get("height_m") not in (None, ""):
        height = float(edit["height_m"])
        if not 1 <= height <= settings["heights"]["max_valid_height_m"]:
            raise ValueError(f"Height must be between 1 and {settings['heights']['max_valid_height_m']} m")
        out["height_m"] = round(height, 1)
    if edit.get("facade"):
        path = str(edit["facade"]).strip().replace("\\", "/")
        pure = PurePosixPath(path)
        if pure.is_absolute() or ".." in pure.parts or ":" in path or not path.lower().endswith(".fac"):
            raise ValueError("Facade must be a .fac library path or a file in the job's models folder")
        out["facade"] = path
    if edit.get("style") and not out.get("facade"):
        if edit["style"] not in STYLES:
            raise ValueError(f"Unknown facade style {edit['style']!r}; choose from {', '.join(STYLES)}")
        out["style"] = edit["style"]
    model = edit.get("model")
    if model:
        path = str(model.get("path") or "").strip().replace("\\", "/")
        pure = PurePosixPath(path)
        if not path or pure.is_absolute() or ".." in pure.parts or ":" in path:
            raise ValueError("Model path must be relative: a file under the job's models folder or a library path")
        if not path.lower().endswith((".obj", ".agp")):
            raise ValueError("Model path must point to an .obj (or .agp) file")
        if not model_is_local(job, path) and path.startswith(("objects/", "models/")):
            raise ValueError(f"{path} not found in {job.models_dir}")
        offsets = [float(model.get(k) or 0) for k in ("offset_e_m", "offset_n_m")]
        if any(abs(v) > MAX_OFFSET_M for v in offsets):
            raise ValueError(f"Offsets must be within {MAX_OFFSET_M} m")
        out = {"model": {"path": path, "heading": float(model.get("heading") or 0) % 360,
                         "offset_e_m": offsets[0], "offset_n_m": offsets[1]}}
    return out


def set_edit(job, building_id: str, edit: dict | None, settings: dict) -> dict:
    """Store (or with an empty edit, remove) one building's edit; returns all edits."""
    buildings = load(job)
    cleaned = validate(job, edit or {}, settings)
    if cleaned:
        buildings[building_id] = cleaned
    else:
        buildings.pop(building_id, None)
    save(job, buildings)
    return buildings


def apply_heights(features: list, edits: dict) -> int:
    """Scale the blocks of buildings with a height_m edit in place; returns the number of buildings changed."""
    by_building = defaultdict(list)
    for feature in features:
        if "height_m" in edits.get(feature["properties"]["building_id"], {}):
            by_building[feature["properties"]["building_id"]].append(feature["properties"])
    for building_id, blocks in by_building.items():
        factor = edits[building_id]["height_m"] / max(b["height_m"] for b in blocks)
        for b in blocks:
            b["height_m"] = round(b["height_m"] * factor, 1)
            b["height_source"] = "edited"
    return len(by_building)


def edit_kind(edit: dict | None) -> str | None:
    if not edit:
        return None
    if edit.get("delete"):
        return "delete"
    if edit.get("model"):
        return "model"
    return "edit"


def building_origins(features: list) -> dict:
    """{building_id: (lon, lat)}: centroid of the union of each building's blocks."""
    blocks = defaultdict(list)
    for feature in features:
        blocks[feature["properties"]["building_id"]].append(shape(feature["geometry"]))
    out = {}
    for building_id, geoms in blocks.items():
        c = unary_union(geoms).centroid
        out[building_id] = (c.x, c.y)
    return out


def offset_lonlat(lon: float, lat: float, east_m: float, north_m: float) -> tuple[float, float]:
    k = math.radians(1) * 6371008.8
    return lon + east_m / (k * math.cos(math.radians(lat))), lat + north_m / k


def reference_obj(features: list, building_id: str) -> str:
    """Wavefront OBJ of one building's blocks (extruded to their heights) around the building origin, in metres:
    x east, y up, z south. Imported into Blender with default settings this gives X east, Y north, Z up, which is
    what XPlane2Blender expects for a model placed with heading 0."""
    blocks = [f for f in features if f["properties"]["building_id"] == building_id]
    if not blocks:
        raise ValueError(f"Building {building_id} not found in the classified data; regenerate the job first")
    origin = building_origins(blocks)[building_id]
    lines = [f"# xp12-cityscene-gen reference for {building_id}",
             f"# origin lon {origin[0]:.9f} lat {origin[1]:.9f}; x east, y up, z south (metres)"]
    vertex = 0
    for n, feature in enumerate(blocks, 1):
        height = feature["properties"]["height_m"]
        geom = shape(feature["geometry"])
        for poly in getattr(geom, "geoms", [geom]):
            ring = local_en(list(orient(Polygon(poly.exterior), sign=1.0).exterior.coords)[:-1], origin)
            count = len(ring)
            lines.append(f"o block_{n}_{feature['properties']['id'].replace('/', '_')}_{height:g}m")
            for z in (0.0, height):
                lines += [f"v {e:.3f} {z:.3f} {-north:.3f}" for e, north in ring]
            bottom = [vertex + i + 1 for i in range(count)]
            top = [vertex + count + i + 1 for i in range(count)]
            lines.append("f " + " ".join(str(i) for i in reversed(bottom)))
            lines.append("f " + " ".join(str(i) for i in top))
            for i in range(count):
                j = (i + 1) % count
                lines.append(f"f {bottom[i]} {bottom[j]} {top[j]} {top[i]}")
            vertex += 2 * count
    return "\n".join(lines) + "\n"
