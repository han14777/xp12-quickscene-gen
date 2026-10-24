"""Bring 3D models from other tools into a project's models folder as X-Plane OBJ8.

- X-Plane OBJ8 (e.g. from XPlane2Blender): copied with the textures it names (overrides.import_model)
- Wavefront .obj + .mtl (Blender, SketchUp, 3ds Max, ...) and glTF 2.0 (.gltf / .glb): converted. An OBJ8 has one
  texture, so every material's image (diffuse map_Kd / baseColorTexture, or a swatch of its plain colour) is packed
  into one atlas PNG and the texture coordinates are remapped into its cell. Coordinates that tile (outside 0..1)
  are wrapped per vertex, which is exact only for triangles that do not cross a tile edge; such models get a warning
- Axes: source files are taken as Y up (the default of Blender's OBJ and glTF exporters: X east, Y up, -Z north),
  which is OBJ8's own convention; up="z" converts Z-up files (x, y, z) -> (x, z, -y). scale converts units to
  metres (0.01 for centimetres, 0.0254 for inches)
- Winding: source formats put front faces counter-clockwise, OBJ8 clockwise (see obj8.py), so triangles are reversed
- Normals missing in the source are computed per face

Output: models/<name>/<name>.obj and <name>_atlas.png. Returns {"path", "copied", "missing", "warnings",
"converted", "triangles"}.
"""

import base64
import json
import math
import re
import struct
from pathlib import Path

import numpy as np

from quickscene import overrides

ATLAS_MAX = 4096
CELL_MAX = 1024
SWATCH = 16


def is_obj8(path: Path) -> bool:
    try:
        with open(path, encoding="latin-1") as f:
            head = [line.strip() for _, line in zip(range(6), f) if line.strip()]
    except OSError:
        return False
    return len(head) >= 3 and head[0] in ("I", "A") and head[1].split()[0] in ("800", "850", "1000") and \
        head[2].startswith("OBJ")


def safe_name(stem: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_\-]+", "_", stem).strip("_")
    return name or "model"


# ---------- material images ----------

class Material:
    def __init__(self, name: str, color=(0.8, 0.8, 0.8, 1.0), image=None):
        self.name = name
        self.color = tuple(color)
        self.image = image  # PIL image or None


def _open_image(path_or_bytes):
    from PIL import Image
    if isinstance(path_or_bytes, (bytes, bytearray)):
        import io
        image = Image.open(io.BytesIO(path_or_bytes))
    else:
        image = Image.open(path_or_bytes)
    image.load()
    return image.convert("RGBA")


# ---------- Wavefront ----------

def read_mtl(path: Path, warnings: list) -> dict:
    materials, current = {}, None
    if not path.is_file():
        warnings.append(f"Material file {path.name} not found; using grey")
        return materials
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        words = line.split()
        if not words:
            continue
        if words[0] == "newmtl":
            current = materials[" ".join(words[1:])] = Material(" ".join(words[1:]))
        elif current is None:
            continue
        elif words[0] == "Kd" and len(words) >= 4:
            current.color = (float(words[1]), float(words[2]), float(words[3]), current.color[3])
        elif words[0] == "d" and len(words) >= 2:
            current.color = current.color[:3] + (float(words[1]),)
        elif words[0] == "map_Kd" and len(words) >= 2:
            # options such as -s 1 1 1 may come before the file name; the file is the last word(s)
            name = line.split(None, 1)[1].strip()
            name = re.sub(r"^(-\w+\s+(\S+\s+){0,3})+", "", name).strip()
            image_path = (path.parent / name.replace("\\", "/"))
            if not image_path.is_file():
                image_path = path.parent / Path(name.replace("\\", "/")).name
            if image_path.is_file():
                try:
                    current.image = _open_image(image_path)
                except Exception as exc:
                    warnings.append(f"Cannot read texture {name}: {exc}")
            else:
                warnings.append(f"Texture {name} not found")
    return materials


def read_wavefront(path: Path, warnings: list):
    """Returns (positions Nx3, uvs Nx2 or None per corner, normals, triangles grouped by material name, materials).
    Faces are fan-triangulated; each triangle corner carries its own (v, vt, vn) indices."""
    v, vt, vn = [], [], []
    materials = {}
    groups: dict[str, list] = {}
    current = "default"
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        words = line.split()
        if not words:
            continue
        key = words[0]
        if key == "v" and len(words) >= 4:
            v.append([float(words[1]), float(words[2]), float(words[3])])
        elif key == "vt" and len(words) >= 3:
            vt.append([float(words[1]), float(words[2])])
        elif key == "vn" and len(words) >= 4:
            vn.append([float(words[1]), float(words[2]), float(words[3])])
        elif key == "mtllib":
            for name in line.split(None, 1)[1].strip().split(" "):
                materials.update(read_mtl(path.parent / name, warnings))
        elif key == "usemtl":
            current = " ".join(words[1:]) or "default"
        elif key == "f" and len(words) >= 4:
            corners = []
            for word in words[1:]:
                parts = (word.split("/") + ["", ""])[:3]

                def idx(text, count):
                    if not text:
                        return -1
                    i = int(text)
                    return i - 1 if i > 0 else count + i
                corners.append((idx(parts[0], len(v)), idx(parts[1], len(vt)), idx(parts[2], len(vn))))
            tris = groups.setdefault(current, [])
            for i in range(1, len(corners) - 1):
                tris.append((corners[0], corners[i], corners[i + 1]))
    return np.array(v, float).reshape(-1, 3), np.array(vt, float).reshape(-1, 2), \
        np.array(vn, float).reshape(-1, 3), groups, materials


def wavefront_mesh(path: Path, warnings: list) -> list:
    """[(Material, corners Px3x3 positions, uv Px3x2 or None, normals Px3x3 or None)] per material."""
    v, vt, vn, groups, materials = read_wavefront(path, warnings)
    out = []
    for name, tris in groups.items():
        t = np.array(tris, int)  # P x 3 corners x (v, vt, vn)
        if not len(t):
            continue
        if (t[:, :, 0] < 0).any() or (t[:, :, 0] >= len(v)).any():
            raise ValueError(f"{path.name}: face refers to a vertex that does not exist")
        positions = v[t[:, :, 0]]
        uv = vt[t[:, :, 1]] if len(vt) and (t[:, :, 1] >= 0).all() else None
        normals = vn[t[:, :, 2]] if len(vn) and (t[:, :, 2] >= 0).all() else None
        material = materials.get(name) or Material(name)
        out.append((material, positions, uv, normals))
    return out


# ---------- glTF ----------

COMPONENT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def _load_gltf(path: Path):
    data = path.read_bytes()
    binary = None
    if data[:4] == b"glTF":
        _, _, length = struct.unpack_from("<III", data, 0)
        offset, gltf = 12, None
        while offset < length:
            chunk_len, chunk_type = struct.unpack_from("<II", data, offset)
            chunk = data[offset + 8: offset + 8 + chunk_len]
            if chunk_type == 0x4E4F534A:
                gltf = json.loads(chunk.decode("utf-8"))
            elif chunk_type == 0x004E4942:
                binary = chunk
            offset += 8 + chunk_len
    else:
        gltf = json.loads(data.decode("utf-8"))
    buffers = []
    for i, buf in enumerate(gltf.get("buffers", [])):
        uri = buf.get("uri")
        if uri is None:
            buffers.append(binary)
        elif uri.startswith("data:"):
            buffers.append(base64.b64decode(uri.split(",", 1)[1]))
        else:
            buffers.append((path.parent / uri).read_bytes())
    return gltf, buffers


def _accessor(gltf, buffers, index):
    acc = gltf["accessors"][index]
    view = gltf["bufferViews"][acc["bufferView"]]
    dtype = np.dtype(COMPONENT[acc["componentType"]])
    width = WIDTH[acc["type"]]
    buf = buffers[view["buffer"]]
    start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    stride = view.get("byteStride") or dtype.itemsize * width
    count = acc["count"]
    if stride == dtype.itemsize * width:
        arr = np.frombuffer(buf, dtype, count * width, start).reshape(count, width)
    else:
        rows = [np.frombuffer(buf, dtype, width, start + i * stride) for i in range(count)]
        arr = np.array(rows).reshape(count, width)
    arr = arr.astype(float)
    if acc.get("normalized") and dtype.kind in "iu":
        arr /= np.iinfo(dtype).max
    return arr


def _node_matrix(node) -> np.ndarray:
    if "matrix" in node:
        return np.array(node["matrix"], float).reshape(4, 4).T
    t = np.eye(4)
    if "translation" in node:
        t[:3, 3] = node["translation"]
    r = np.eye(4)
    if "rotation" in node:
        x, y, z, w = node["rotation"]
        r[:3, :3] = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    s = np.eye(4)
    if "scale" in node:
        s[0, 0], s[1, 1], s[2, 2] = node["scale"]
    return t @ r @ s


def gltf_mesh(path: Path, warnings: list) -> list:
    gltf, buffers = _load_gltf(path)
    images = {}

    def image_for(tex_index):
        if tex_index in images:
            return images[tex_index]
        img = None
        try:
            source = gltf["textures"][tex_index]["source"]
            info = gltf["images"][source]
            if "bufferView" in info:
                view = gltf["bufferViews"][info["bufferView"]]
                raw = buffers[view["buffer"]][view.get("byteOffset", 0): view.get("byteOffset", 0) + view["byteLength"]]
                img = _open_image(bytes(raw))
            elif info.get("uri", "").startswith("data:"):
                img = _open_image(base64.b64decode(info["uri"].split(",", 1)[1]))
            else:
                img = _open_image(path.parent / info["uri"])
        except Exception as exc:
            warnings.append(f"Cannot read texture {tex_index}: {exc}")
        images[tex_index] = img
        return img

    materials = []
    for i, m in enumerate(gltf.get("materials", [])):
        pbr = m.get("pbrMetallicRoughness", {})
        image = image_for(pbr["baseColorTexture"]["index"]) if "baseColorTexture" in pbr else None
        materials.append(Material(m.get("name") or f"material_{i}", pbr.get("baseColorFactor", (1, 1, 1, 1)), image))
    default = Material("default")
    out = []

    def visit(node_index, parent):
        node = gltf["nodes"][node_index]
        matrix = parent @ _node_matrix(node)
        if "mesh" in node:
            normal_matrix = np.linalg.inv(matrix[:3, :3]).T
            for prim in gltf["meshes"][node["mesh"]]["primitives"]:
                if prim.get("mode", 4) != 4:
                    warnings.append("Skipped a primitive that is not made of triangles")
                    continue
                attrs = prim["attributes"]
                pos = _accessor(gltf, buffers, attrs["POSITION"])
                pos = (np.c_[pos, np.ones(len(pos))] @ matrix.T)[:, :3]
                nor = _accessor(gltf, buffers, attrs["NORMAL"]) @ normal_matrix.T if "NORMAL" in attrs else None
                uv = _accessor(gltf, buffers, attrs["TEXCOORD_0"]) if "TEXCOORD_0" in attrs else None
                if uv is not None:
                    uv = np.c_[uv[:, 0], 1.0 - uv[:, 1]]  # glTF t runs top to bottom
                idx = _accessor(gltf, buffers, prim["indices"]).astype(int).ravel() if "indices" in prim \
                    else np.arange(len(pos))
                tri = idx.reshape(-1, 3)
                mat = materials[prim["material"]] if "material" in prim else default
                out.append((mat, pos[tri], uv[tri] if uv is not None else None,
                            nor[tri] if nor is not None else None))
        for child in node.get("children", []):
            visit(child, matrix)

    scene = gltf["scenes"][gltf.get("scene", 0)] if gltf.get("scenes") else {"nodes": range(len(gltf["nodes"]))}
    for n in scene["nodes"]:
        visit(n, np.eye(4))
    return out


# ---------- atlas + OBJ8 writer ----------

def build_atlas(materials: list):
    """Pack each material's image (or colour swatch) into one atlas. Returns (PIL image, {id(material): (u0, v0,
    u1, v1)}) with v measured from the bottom like OBJ8 texture coordinates."""
    from PIL import Image
    cells = []
    for m in materials:
        if m.image is not None:
            img = m.image.copy()
            img.thumbnail((CELL_MAX, CELL_MAX))
            tint = m.color
            if any(abs(c - 1) > 1e-3 for c in tint[:3]):
                r, g, b, a = img.split()
                img = Image.merge("RGBA", [ch.point(lambda x, k=k: int(x * k)) for ch, k in
                                           zip((r, g, b), tint[:3])] + [a])
        else:
            rgba = tuple(int(max(0, min(1, c)) * 255) for c in m.color[:3]) + (int(max(0, min(1, m.color[3])) * 255),)
            img = Image.new("RGBA", (SWATCH, SWATCH), rgba)
        cells.append((m, img))
    # shelf packing, tallest first
    order = sorted(range(len(cells)), key=lambda i: -cells[i][1].height)
    width = max(SWATCH, min(ATLAS_MAX, max(c[1].width for c in cells)))
    while True:
        x = y = shelf = 0
        spots = {}
        for i in order:
            img = cells[i][1]
            if x + img.width > width:
                x, y, shelf = 0, y + shelf, 0
            spots[i] = (x, y)
            x += img.width + 2
            shelf = max(shelf, img.height + 2)
        height = y + shelf
        if height <= width or width >= ATLAS_MAX:
            break
        width = min(ATLAS_MAX, width * 2)
    size = 1 << max(4, math.ceil(math.log2(max(width, height))))
    scale = 1.0
    if size > ATLAS_MAX:
        scale = ATLAS_MAX / size
        size = ATLAS_MAX
    atlas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    rects = {}
    for i, (m, img) in enumerate(cells):
        x, y = spots[i]
        if scale != 1.0:
            img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))
            x, y = int(x * scale), int(y * scale)
        atlas.paste(img, (x, y))
        # half-texel inset keeps bilinear filtering inside the cell
        u0, u1 = (x + 0.5) / size, (x + img.width - 0.5) / size
        v_top, v_bottom = (y + 0.5) / size, (y + img.height - 0.5) / size
        rects[id(m)] = (u0, 1 - v_bottom, u1, 1 - v_top)
    return atlas, rects


def write_converted(parts: list, out_dir: Path, name: str, scale: float, up: str, warnings: list) -> dict:
    if not parts:
        raise ValueError("The model has no triangles")
    unique = []
    for m, *_ in parts:
        if all(m is not u for u in unique):
            unique.append(m)
    atlas, rects = build_atlas(unique)
    lines_vt, indices = [], []
    tiled = False
    for m, pos, uv, nor in parts:
        pos = np.asarray(pos, float) * scale
        if up == "z":
            pos = np.stack([pos[..., 0], pos[..., 2], -pos[..., 1]], axis=-1)
            if nor is not None:
                nor = np.stack([nor[..., 0], nor[..., 2], -nor[..., 1]], axis=-1)
        if nor is None or not np.isfinite(nor).all():
            face = np.cross(pos[:, 1] - pos[:, 0], pos[:, 2] - pos[:, 0])
            length = np.linalg.norm(face, axis=1, keepdims=True)
            face = np.divide(face, length, out=np.zeros_like(face), where=length > 0)
            nor = np.repeat(face[:, None, :], 3, axis=1)
        else:
            length = np.linalg.norm(nor, axis=2, keepdims=True)
            nor = np.divide(nor, length, out=np.zeros_like(nor), where=length > 0)
        u0, v0, u1, v1 = rects[id(m)]
        if uv is None or m.image is None:
            st = np.zeros(pos.shape[:2] + (2,)) + [(u0 + u1) / 2, (v0 + v1) / 2]
        else:
            uv = np.asarray(uv, float)
            if (uv < -1e-3).any() or (uv > 1 + 1e-3).any():
                tiled = True
                uv = uv - np.floor(uv.min(axis=1, keepdims=True))
                uv = np.clip(uv, 0, 1)
            st = np.stack([u0 + uv[..., 0] * (u1 - u0), v0 + uv[..., 1] * (v1 - v0)], axis=-1)
        base = len(lines_vt)
        for p in range(len(pos)):
            for c in (0, 2, 1):  # counter-clockwise source -> clockwise OBJ8
                x, y, z = pos[p, c]
                nx, ny, nz = nor[p, c]
                s, t = st[p, c]
                lines_vt.append(f"VT {x:.4f} {y:.4f} {z:.4f} {nx:.4f} {ny:.4f} {nz:.4f} {s:.5f} {t:.5f}")
        indices.extend(range(base, len(lines_vt)))
    if tiled:
        warnings.append("Some texture coordinates repeat outside 0..1; they were wrapped into the atlas, so "
                        "tiled surfaces may look stretched. Bake textures to 0..1 UVs for an exact result")
    out_dir.mkdir(parents=True, exist_ok=True)
    texture = f"{name}_atlas.png"
    atlas.save(out_dir / texture)
    lines = ["I", "800", "OBJ", "", "# converted by the scenery tool's model import", f"TEXTURE {texture}",
             "GLOBAL_specular 0.5", f"POINT_COUNTS {len(lines_vt)} 0 0 {len(indices)}"] + lines_vt
    full = len(indices) // 10 * 10
    lines += ["IDX10 " + " ".join(map(str, indices[i:i + 10])) for i in range(0, full, 10)]
    lines += [f"IDX {i}" for i in indices[full:]]
    lines += ["", f"TRIS 0 {len(indices)}", ""]
    (out_dir / f"{name}.obj").write_text("\n".join(lines), encoding="ascii", newline="\n")
    return {"triangles": len(indices) // 3, "materials": len(unique), "texture": texture}


def import_any(models_dir: Path, source: Path, scale: float = 1.0, up: str = "y", name: str | None = None) -> dict:
    """Copy (OBJ8) or convert (Wavefront / glTF) a model into models_dir; see the module docstring."""
    source = Path(source)
    if not source.is_file():
        raise ValueError(f"{source} is not a file")
    suffix = source.suffix.lower()
    if suffix == ".agp" or (suffix == ".obj" and is_obj8(source)):
        result = overrides.import_model_into(models_dir, source)
        result.update(converted=False, warnings=[])
        return result
    if not (0 < scale <= 1000):
        raise ValueError("Scale must be between 0 and 1000")
    if up not in ("y", "z"):
        raise ValueError("Up axis must be y or z")
    warnings: list = []
    if suffix == ".obj":
        parts = wavefront_mesh(source, warnings)
    elif suffix in (".gltf", ".glb"):
        parts = gltf_mesh(source, warnings)
    else:
        raise ValueError("Supported models: X-Plane .obj / .agp, Wavefront .obj (+ .mtl), glTF .gltf / .glb")
    name = safe_name(name or source.stem)
    info = write_converted(parts, models_dir / name, name, scale, up, warnings)
    return {"path": f"{name}/{name}.obj", "copied": [f"{name}.obj", info["texture"]], "missing": [],
            "converted": True, "warnings": warnings, "triangles": info["triangles"], "materials": info["materials"]}
