"""Read X-Plane art assets for the browser preview: OBJ8 meshes, facades, forests, draped polygons, lines, object
strings and autogen points (AGP). Every reader returns plain JSON for the three.js viewer in gui/static/viewer.js.

Textures are named by a token (file_token) and served as PNG by texture_png: X-Plane looks for a .dds next to a
.png of the same name (and the other way round), so both are tried. DDS (BC1-BC7) is decoded by Pillow and the
image is shrunk to at most TEXTURE_MAX px, cached in <cache_dir>/textures/.

Only what a preview needs is read: the nearest level of detail, no animation (animated parts are shown at rest),
lights as points. Formats per Laminar's OBJ8, facade, forest, draped polygon, line and AGP specifications and the
XP12 default library files (facades: TEX_SIZE pixel coordinates with SCALE metres per texture; forests: MESH /
VERTEX / IDX blocks for 3D trees next to the classic TREE billboard rows).
"""

import hashlib
import io
from pathlib import Path

from cityscene.config import PROJECT_ROOT, project_path

TEXTURE_MAX = 1024
MAX_VERTICES = 400_000
LIGHT_KEYS = ("LIGHT_NAMED", "LIGHT_PARAM", "LIGHT_CUSTOM", "LIGHT_SPILL_CUSTOM")


class AssetError(ValueError):
    pass


# ---------- file tokens: which files the browser may ask for ----------

def _roots(settings: dict) -> dict:
    from cityscene.config import xplane_root
    roots = {"prj": PROJECT_ROOT}
    root = xplane_root(settings)
    if root:
        roots["xp"] = Path(root)
    return roots


def file_token(path: Path, settings: dict) -> str | None:
    """"xp:<path relative to the X-Plane folder>" or "prj:<relative to this project>"; None outside both."""
    path = Path(path).resolve()
    for key, base in _roots(settings).items():
        try:
            return f"{key}:{path.relative_to(Path(base).resolve()).as_posix()}"
        except ValueError:
            continue
    return None


def token_path(token: str, settings: dict) -> Path:
    key, _, rel = str(token).partition(":")
    base = _roots(settings).get(key)
    if base is None or not rel:
        raise AssetError(f"Bad file token {token!r}")
    base = Path(base).resolve()
    path = (base / rel).resolve()
    if base not in path.parents:
        raise AssetError("File outside the allowed folders")
    return path


def find_texture(base_dir: Path, name: str) -> Path | None:
    """The texture file X-Plane would load for name (relative to base_dir): the file itself or its .dds / .png twin."""
    name = name.strip().replace("\\", "/")
    if not name:
        return None
    path = (base_dir / name)
    candidates = [path]
    if path.suffix.lower() in (".png", ".dds"):
        candidates = [path.with_suffix(".dds"), path.with_suffix(".png"), path]
    for c in candidates:
        if c.is_file():
            return c
    return None


def texture_png(token: str, settings: dict) -> bytes:
    path = token_path(token, settings)
    if not path.is_file():
        raise FileNotFoundError(f"Texture not found: {path.name}")
    st = path.stat()
    key = hashlib.sha1(f"{path}|{st.st_size}|{st.st_mtime}".encode()).hexdigest()
    cache = project_path(settings["paths"].get("cache_dir", "cache")) / "textures" / f"{key}.png"
    if cache.is_file():
        return cache.read_bytes()
    from PIL import Image
    try:
        with Image.open(path) as image:
            image.load()
            image = image.convert("RGBA")
    except Exception as exc:  # Pillow raises many types for unsupported DDS variants
        raise AssetError(f"Cannot decode {path.name}: {exc}") from exc
    image.thumbnail((TEXTURE_MAX, TEXTURE_MAX))
    out = io.BytesIO()
    image.save(out, "PNG", optimize=False)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(out.getvalue())
    return out.getvalue()


def _lines(path: Path) -> list[list[str]]:
    try:
        text = path.read_text(encoding="latin-1")
    except OSError as exc:
        raise AssetError(f"Cannot read {path}: {exc}") from exc
    return [line.split() for line in text.splitlines()]


def _tex(base: Path, name: str, settings: dict):
    found = find_texture(base, name) if name else None
    return file_token(found, settings) if found else None


def _floats(words, start, count):
    try:
        return [float(v) for v in words[start:start + count]]
    except (ValueError, IndexError):
        return None


# ---------- OBJ8 ----------

def read_obj(path: Path, settings: dict) -> dict:
    """{"type": "obj", positions, normals, uvs (flat lists), groups: [{start, count, texture: main|draped}], indices,
    textures: {main, lit, draped}, lights: [[x, y, z]], bounds}. Only LOD buckets starting at 0 m are drawn."""
    rows = _lines(path)
    if len(rows) < 3 or not rows[1] or rows[1][0] not in ("800", "850", "1000"):
        version = rows[1][0] if len(rows) > 1 and rows[1] else "?"
        raise AssetError(f"{path.name}: OBJ version {version} is not supported (OBJ8 only)")
    base = path.parent
    positions, normals, uvs, indices = [], [], [], []
    textures = {"main": None, "lit": None, "draped": None}
    lights, groups = [], []
    lod_count, in_first_lod, draped = 0, True, False
    for words in rows[3:]:
        if not words:
            continue
        key = words[0]
        if key == "VT" and len(words) >= 9:
            if len(positions) // 3 >= MAX_VERTICES:
                raise AssetError(f"{path.name} has more than {MAX_VERTICES} vertices; too large to preview")
            positions += [float(words[1]), float(words[2]), float(words[3])]
            normals += [float(words[4]), float(words[5]), float(words[6])]
            uvs += [float(words[7]), float(words[8])]
        elif key == "IDX10" or key == "IDX":
            indices += [int(v) for v in words[1:]]
        elif key == "TEXTURE" and len(words) > 1:
            textures["main"] = _tex(base, words[1], settings)
        elif key == "TEXTURE_LIT" and len(words) > 1:
            textures["lit"] = _tex(base, words[1], settings)
        elif key == "TEXTURE_DRAPED" and len(words) > 1:
            textures["draped"] = _tex(base, words[1], settings)
        elif key == "ATTR_LOD":
            # LOD buckets may overlap (several starting at 0 m are all drawn up close): keep those starting at 0
            lod_count += 1
            in_first_lod = len(words) > 1 and float(words[1]) == 0
        elif key == "ATTR_draped":
            draped = True
        elif key == "ATTR_no_draped":
            draped = False
        elif key == "TRIS" and len(words) >= 3 and in_first_lod:
            start, count = int(words[1]), int(words[2])
            kind = "draped" if draped and textures["draped"] else "main"
            if groups and groups[-1]["texture"] == kind and groups[-1]["start"] + groups[-1]["count"] == start:
                groups[-1]["count"] += count
            else:
                groups.append({"start": start, "count": count, "texture": kind})
        elif key in LIGHT_KEYS and in_first_lod:
            xyz = _floats(words, 2 if key in ("LIGHT_NAMED", "LIGHT_PARAM") else 1, 3)
            if xyz:
                lights.append(xyz)
    if not positions:
        raise AssetError(f"{path.name} has no geometry")
    xs, ys, zs = positions[0::3], positions[1::3], positions[2::3]
    return {"type": "obj", "positions": positions, "normals": normals, "uvs": uvs, "indices": indices,
            "groups": groups, "textures": textures, "lights": lights[:2000],
            "bounds": [min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)]}


# ---------- facades ----------

def read_fac(path: Path, settings: dict) -> dict:
    """Type 1 facades (walls cut from the texture by LEFT / CENTER / RIGHT and BOTTOM / MIDDLE / TOP ranges) come
    back with their walls so the viewer can build a box from them; type 2 facades (FLOOR / SEGMENT / SPELLING with
    OBJ meshes) come back with their textures and parameters only."""
    rows = _lines(path)
    base = path.parent
    out = {"type": "fac", "facade_type": 1, "textures": {"wall": None, "roof": None}, "walls": [],
           "floors_min": None, "floors_max": None, "lod": None, "roof_height": None, "objects": [],
           "ring": None, "graded": False, "version": rows[1][0] if len(rows) > 1 and rows[1] else "?"}
    shader = "wall"
    tex_size = [1.0, 1.0]
    wall = None
    lods = 0
    for words in rows[3:]:
        if not words or words[0].startswith("#"):
            continue
        key = words[0]
        if key == "SHADER_WALL":
            shader = "wall"
        elif key == "SHADER_ROOF":
            shader = "roof"
        elif key == "TEXTURE" and len(words) > 1:
            if out["textures"][shader] is None:
                out["textures"][shader] = _tex(base, words[1], settings)
        elif key == "FLOORS_MIN":
            out["floors_min"] = float(words[1])
        elif key == "FLOORS_MAX":
            out["floors_max"] = float(words[1])
        elif key == "RING":
            out["ring"] = int(float(words[1]))
        elif key == "GRADED":
            out["graded"] = True
        elif key == "ROOF_HEIGHT":
            out["roof_height"] = float(words[1])
        elif key == "LOD":
            lods += 1
            if lods == 1 and len(words) >= 3:
                out["lod"] = [float(words[1]), float(words[2])]
        elif key in ("FLOOR", "SEGMENT", "SPELLING", "SEGMENT_CURVED"):
            out["facade_type"] = 2
        elif key == "OBJ" and len(words) > 1:
            out["objects"].append(words[1])
        elif lods > 1:
            continue
        elif key == "TEX_SIZE":
            tex_size = _floats(words, 1, 2) or tex_size
        elif key == "WALL":
            wall = {"scale": [1.0, 1.0], "tex_size": list(tex_size), "left": [], "center": [], "right": [],
                    "bottom": [], "middle": [], "top": [], "range": _floats(words, 1, 2)}
            out["walls"].append(wall)
        elif wall is not None and key == "SCALE":
            wall["scale"] = _floats(words, 1, 2) or wall["scale"]
        elif wall is not None and key in ("LEFT", "CENTER", "RIGHT", "BOTTOM", "MIDDLE", "TOP"):
            pair = _floats(words, 1, 2)
            if pair:
                wall[key.lower()].append(pair)
    if out["facade_type"] == 1 and not any(w["center"] or w["left"] for w in out["walls"]):
        out["facade_type"] = 2
    out["walls"] = out["walls"][:4]
    out["objects"] = out["objects"][:40]
    return out


# ---------- forests ----------

def read_for(path: Path, settings: dict) -> dict:
    """{"type": "for", textures: {billboard, mesh}, scale: [x, y], spacing, trees: [{s, t, w, h, offset, freq,
    min_h, max_h, meshes: [names]}], meshes: {name: {positions, normals, uvs, indices}}}."""
    rows = _lines(path)
    base = path.parent
    out = {"type": "for", "textures": {"billboard": None, "mesh": None}, "scale": [1, 1], "spacing": [10, 10],
           "trees": [], "meshes": {}}
    shader = "2d"
    mesh_order = []
    current = None
    for words in rows[3:]:
        if not words or words[0].startswith("#"):
            continue
        key = words[0]
        if key == "SHADER_2D":
            shader = "2d"
        elif key == "SHADER_3D":
            shader = "3d"
        elif key == "TEXTURE" and len(words) > 1:
            slot = "billboard" if shader == "2d" else "mesh"
            if out["textures"][slot] is None:
                out["textures"][slot] = _tex(base, words[1], settings)
        elif key == "SCALE_X":
            out["scale"][0] = float(words[1])
        elif key == "SCALE_Y":
            out["scale"][1] = float(words[1])
        elif key == "SPACING":
            out["spacing"] = _floats(words, 1, 2) or out["spacing"]
        elif key == "TREE" and len(words) >= 9:
            v = _floats(words, 1, 8)
            if v:
                out["trees"].append({"s": v[0], "t": v[1], "w": v[2], "h": v[3], "offset": v[4], "freq": v[5],
                                     "min_h": v[6], "max_h": v[7], "meshes": []})
        elif key == "MESH_3D" and len(words) > 1 and out["trees"]:
            out["trees"][-1]["meshes"].append(words[1])
        elif key == "MESH" and len(words) >= 6:
            name = words[1]
            current = {"positions": [], "normals": [], "uvs": [], "indices": [], "lod": _floats(words, 2, 2)}
            mesh_order.append(name)
            # keep only meshes a preview can afford
            out["meshes"][name] = current if len(out["meshes"]) < 6 else None
            if out["meshes"][name] is None:
                current = None
        elif key == "VERTEX" and current is not None and len(words) >= 9:
            current["positions"] += [float(words[1]), float(words[2]), float(words[3])]
            current["normals"] += [float(words[4]), float(words[5]), float(words[6])]
            current["uvs"] += [float(words[7]), float(words[8])]
        elif key == "IDX" and current is not None:
            current["indices"] += [int(v) for v in words[1:]]
    out["meshes"] = {k: v for k, v in out["meshes"].items() if v}
    out["trees"] = out["trees"][:24]
    return out


# ---------- draped polygons, lines, strings ----------

def read_pol(path: Path, settings: dict) -> dict:
    rows = _lines(path)
    out = {"type": "pol", "texture": None, "scale": [10, 10], "nowrap": False, "surface": None}
    for words in rows[3:]:
        if not words:
            continue
        key = words[0]
        if key in ("TEXTURE", "TEXTURE_NOWRAP") and len(words) > 1 and out["texture"] is None:
            out["texture"] = _tex(path.parent, words[-1], settings)
            out["nowrap"] = key == "TEXTURE_NOWRAP"
        elif key == "SCALE":
            out["scale"] = _floats(words, 1, 2) or out["scale"]
        elif key == "SURFACE" and len(words) > 1:
            out["surface"] = words[1]
    return out


def read_lin(path: Path, settings: dict) -> dict:
    rows = _lines(path)
    out = {"type": "lin", "texture": None, "scale": [10, 10], "tex_size": [1, 1], "layers": []}
    for words in rows[3:]:
        if not words:
            continue
        key = words[0]
        if key == "TEXTURE" and len(words) > 1 and out["texture"] is None:
            out["texture"] = _tex(path.parent, words[-1], settings)
        elif key == "SCALE":
            out["scale"] = _floats(words, 1, 2) or out["scale"]
        elif key == "TEX_WIDTH":
            out["tex_size"][0] = float(words[1])
        elif key == "TEX_HEIGHT":
            out["tex_size"][1] = float(words[1])
        elif key == "S_OFFSET":
            v = _floats(words, 1, 4)
            if v:
                out["layers"].append(v)
    return out


def read_str(path: Path, settings: dict) -> dict:
    rows = _lines(path)
    out = {"type": "str", "objects": [], "offset": None}
    for words in rows[3:]:
        if not words:
            continue
        if words[0] == "OBJECT" and len(words) >= 4:
            out["objects"].append(words[3])
        elif words[0] == "OFFSET":
            out["offset"] = float(words[1])
    out["children"] = _children(path, out["objects"], settings)
    return out


def read_agp(path: Path, settings: dict) -> dict:
    """Objects of the first tile placed in metres around the anchor point (pixels x TEXTURE_WIDTH / TEXTURE_SCALE),
    plus the tile's ground texture."""
    rows = _lines(path)
    out = {"type": "agp", "texture": None, "objects": [], "placements": [], "tile": None, "size_m": None}
    scale = [1.0, 1.0]
    width = height = None
    tile = anchor = None
    placements = []
    for words in rows[3:]:
        if not words or words[0].startswith("#"):
            continue
        key = words[0]
        if key == "TEXTURE" and len(words) > 1 and out["texture"] is None:
            out["texture"] = _tex(path.parent, words[-1], settings)
        elif key == "TEXTURE_SCALE":
            scale = _floats(words, 1, 2) or scale
        elif key == "TEXTURE_WIDTH":
            width = float(words[1])
        elif key == "TEXTURE_HEIGHT":
            height = float(words[1])
        elif key == "OBJECT" and len(words) > 1:
            out["objects"].append(words[1])
        elif key == "TILE":
            if tile is not None:
                break  # first tile only
            tile = _floats(words, 1, 4)
        elif key in ("OBJ_DRAPED", "OBJ_GRADED", "OBJ_SCRAPER", "OBJ_DELTA") and len(words) >= 5:
            v = _floats(words, 1, 4)
            if v:
                placements.append({"x": v[0], "y": v[1], "heading": v[2], "object": int(v[3])})
        elif key == "ANCHOR_PT":
            anchor = _floats(words, 1, 2)
    width = width or 10.0
    height = height or width
    kx, ky = width / (scale[0] or 1), height / (scale[1] or 1)
    tile = tile or [0, 0, scale[0], scale[1]]
    anchor = anchor or [(tile[0] + tile[2]) / 2, (tile[1] + tile[3]) / 2]
    for p in placements:
        if 0 <= p["object"] < len(out["objects"]):
            out["placements"].append({"x": (p["x"] - anchor[0]) * kx, "z": -(p["y"] - anchor[1]) * ky,
                                      "heading": p["heading"], "object": p["object"]})
    out["tile"] = [(tile[0] - anchor[0]) * kx, -(tile[1] - anchor[1]) * ky, (tile[2] - anchor[0]) * kx,
                   -(tile[3] - anchor[1]) * ky]
    out["tile_uv"] = [tile[0] / (scale[0] or 1), tile[1] / (scale[1] or 1), tile[2] / (scale[0] or 1),
                      tile[3] / (scale[1] or 1)]
    out["children"] = _children(path, out["objects"], settings)
    return out


def _children(path: Path, names: list, settings: dict) -> list:
    """For each object named by an .agp / .str: {"ref": asset reference for /api/asset} — a library path, or a file
    next to the asset."""
    out = []
    for name in names:
        name = name.replace("\\", "/")
        local = path.parent / name
        if local.is_file():
            token = file_token(local, settings)
            out.append({"name": name, "ref": f"file:{token}" if token else None})
        else:
            out.append({"name": name, "ref": f"lib:{name}"})
    return out


READERS = {"obj": read_obj, "fac": read_fac, "for": read_for, "pol": read_pol, "lin": read_lin, "str": read_str,
           "agp": read_agp}


def read_asset(path: Path, settings: dict) -> dict:
    kind = path.suffix.lower().lstrip(".")
    reader = READERS.get(kind)
    if reader is None:
        raise AssetError(f"No preview for .{kind} files")
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    data = reader(path, settings)
    data["file"] = path.name
    return data
