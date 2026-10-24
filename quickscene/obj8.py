"""X-Plane OBJ8 mesh and text output (trimmed from melbourne-cbd-xp12 pipeline/common/obj8.py).

Format per Laminar's "OBJ8 File Format Specification" (developer.x-plane.com):
- Header I / 800 / OBJ; attributes (TEXTURE ...) first, then data (POINT_COUNTS, VT, IDX), then commands (ATTR_LOD, TRIS)
- Units are metres; +X east, +Y up, +Z south; the origin is the placement point on the ground
- VT x y z nx ny nz s t; texture s runs left to right, t bottom to top
- The front face of a triangle is the side where its vertices run clockwise (checked against the XP12
  gateKeep_01.obj: cross(b - a, c - a) always points opposite to the vertex normal)
"""

from pathlib import Path

# Decimal places for VT positions (millimetres)
POSITION_DECIMALS = 3
MIN_TRIANGLE_AREA_M2 = 1e-3


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(u, v):
    return (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])


def _dot(u, v):
    return u[0] * v[0] + u[1] * v[1] + u[2] * v[2]


def enu_to_obj(east: float, north: float, up: float) -> tuple:
    """Local east-north-up metres to OBJ8 coordinates (+X east, +Y up, +Z south)."""
    return (east, up, -north)


class Mesh:
    """Triangle mesh; vertices are de-duplicated by (position, normal, texture coordinate)."""

    def __init__(self):
        self.vertices: list[tuple] = []
        self.indices: list[int] = []
        self._lookup: dict[tuple, int] = {}

    def _vertex(self, position, normal, uv) -> int:
        key = (*position, *(round(v, 4) for v in normal), *(round(v, 5) for v in uv))
        index = self._lookup.get(key)
        if index is None:
            index = self._lookup[key] = len(self.vertices)
            self.vertices.append(key)
        return index

    def add_triangle(self, points, uvs, normal) -> None:
        """points and normal in OBJ8 coordinates; the winding is flipped as needed so the front faces along normal.
        Orientation is judged on coordinates rounded to the output precision, otherwise thin triangles can flip
        after rounding; triangles smaller than MIN_TRIANGLE_AREA_M2 are ignored."""
        points = [tuple(round(v, POSITION_DECIMALS) for v in p) for p in points]
        a, b, c = points
        n = _cross(_sub(b, a), _sub(c, a))
        if _dot(n, n) < (2 * MIN_TRIANGLE_AREA_M2) ** 2:
            return
        facing = _dot(n, normal)
        if abs(facing) < 1e-9:
            return
        order = (0, 1, 2) if facing < 0 else (0, 2, 1)
        for i in order:
            self.indices.append(self._vertex(points[i], normal, uvs[i]))

    def add_quad(self, points, uvs, normal) -> None:
        """Four vertices in winding order (either direction)."""
        self.add_triangle(points[:3], uvs[:3], normal)
        self.add_triangle((points[0], points[2], points[3]), (uvs[0], uvs[2], uvs[3]), normal)

    @property
    def triangle_count(self) -> int:
        return len(self.indices) // 3


def write_obj8(path: Path, mesh: Mesh, texture: str, texture_lit: str | None = None,
               lod_far_m: float | None = None, lod_near_m: float | None = None, comments: list[str] = ()) -> None:
    """Write OBJ8 text; texture paths are relative to the .obj.

    lod_near_m: show only within [lod_near_m, lod_far_m]. The spec requires the first ATTR_LOD to start at 0, so the
    mesh's last triangle is used as the 0 - lod_near_m placeholder (the caller puts it out of sight, e.g. underground).
    """
    lines = ["I", "800", "OBJ", ""]
    lines += [f"# {c}" for c in comments]
    lines.append(f"TEXTURE {texture}")
    if texture_lit:
        lines.append(f"TEXTURE_LIT {texture_lit}")
    # The spec: GLOBAL_specular 1.0 should almost always be used
    lines += ["GLOBAL_specular 1.0", ""]
    lines.append(f"POINT_COUNTS {len(mesh.vertices)} 0 0 {len(mesh.indices)}")
    for x, y, z, nx, ny, nz, s, t in mesh.vertices:
        lines.append(f"VT {x:.3f} {y:.3f} {z:.3f} {nx:.4f} {ny:.4f} {nz:.4f} {s:.5f} {t:.5f}")
    full = len(mesh.indices) // 10 * 10
    for i in range(0, full, 10):
        lines.append("IDX10 " + " ".join(str(v) for v in mesh.indices[i:i + 10]))
    for v in mesh.indices[full:]:
        lines.append(f"IDX {v}")
    lines.append("")
    if lod_near_m:
        if not lod_far_m or len(mesh.indices) < 6:
            raise ValueError("lod_near_m needs lod_far_m and at least one triangle besides the placeholder")
        main = len(mesh.indices) - 3
        lines += [f"ATTR_LOD 0 {lod_near_m:g}", f"TRIS {main} 3",
                  f"ATTR_LOD {lod_near_m:g} {lod_far_m:g}", f"TRIS 0 {main}"]
    else:
        if lod_far_m:
            lines.append(f"ATTR_LOD 0 {lod_far_m:g}")
        lines.append(f"TRIS 0 {len(mesh.indices)}")
    path.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")


def read_obj8(path: Path) -> dict:
    """Read an OBJ8 written by this module (for tests): textures, vertices, indices, point_counts, lods, tris."""
    result = {"textures": {}, "vertices": [], "indices": [], "point_counts": None, "lods": [], "tris": []}
    for line in path.read_text(encoding="ascii").splitlines()[3:]:
        words = line.split()
        if not words or words[0].startswith("#"):
            continue
        key = words[0]
        if key in ("TEXTURE", "TEXTURE_LIT"):
            result["textures"][key] = words[1]
        elif key == "POINT_COUNTS":
            result["point_counts"] = tuple(int(v) for v in words[1:5])
        elif key == "VT":
            result["vertices"].append(tuple(float(v) for v in words[1:9]))
        elif key in ("IDX", "IDX10"):
            result["indices"] += [int(v) for v in words[1:]]
        elif key == "ATTR_LOD":
            result["lods"].append((float(words[1]), float(words[2])))
        elif key == "TRIS":
            result["tris"].append((int(words[1]), int(words[2])))
    return result
