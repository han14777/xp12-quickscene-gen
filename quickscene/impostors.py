"""Distant blocks (impostors): X-Plane's default facades stop drawing at their own LOD distance (8-18 km, the
facades list in settings.yaml). Past that, merged low-poly OBJs take over up to dsf.render_distance_km, so the
skyline does not vanish. Approach from melbourne-cbd-xp12 pipeline/common/impostors.py.

- Blocks are grouped per 1-degree tile, dsf.impostors.cell_m grid cell and facade LOD; each group becomes one OBJ
  placed at the mean of its blocks' centroids (always inside the tile)
- Outlines are simplified by simplify_m; walls carry a band of window rows from a small generated atlas
  (one column per facade style), roofs are flat; walls reach sink_m below ground to hide terrain slope in a cell
- The OBJ shows from (facade LOD - overlap_m) to the render distance. Facade distance is measured per building and
  the OBJ distance from its origin, so they can differ by up to half a cell diagonal; overlap_m covers that so no
  gap appears at the hand-over. The spec requires the first ATTR_LOD to start at 0, so the 0 - near band holds a
  single small triangle buried underground
"""

import math
import struct
import zlib
from collections import defaultdict
from pathlib import Path

import shapely
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

from quickscene.obj8 import Mesh, enu_to_obj, write_obj8

EARTH_RADIUS_M = 6371008.8
ATLAS_NAME = "impostor_atlas.png"
ATLAS_LIT_NAME = "impostor_atlas_LIT.png"
COL_W = 16
# Power-of-two atlas (64 x 256)
ROOF_H = 32
FLOOR_PX = 14
FLOORS_PER_BAND = 16
PROFILE_H = FLOOR_PX * FLOORS_PER_BAND
ATLAS_H = ROOF_H + PROFILE_H
FLOOR_M = 3.5
BAND_M = FLOOR_M * FLOORS_PER_BAND
# Per facade style: (wall, window, roof) RGB, roughly the average look of the default facades of that style
STYLE_COLOURS = {
    "glass": ((86, 100, 112), (44, 58, 74), (96, 98, 100)),
    "modern": ((176, 172, 162), (70, 80, 92), (120, 120, 118)),
    "classic": ((182, 160, 130), (78, 68, 58), (110, 100, 92)),
    "universal": ((150, 150, 144), (64, 74, 86), (112, 112, 110)),
}
STYLES = list(STYLE_COLOURS)
WINDOW_PX = 8
WINDOW_LIT = (70, 56, 34)


def _png(width: int, height: int, rows: list[bytes]) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + row for row in rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def atlas_rows(lit: bool) -> list[bytes]:
    """Atlas pixel rows top to bottom: ROOF_H rows of roof colour, then FLOORS_PER_BAND floors, each a window row
    (WINDOW_PX) over a wall row."""
    rows = []
    for y in range(ATLAS_H):
        row = bytearray()
        for style in STYLES:
            wall, window, roof = STYLE_COLOURS[style]
            if y < ROOF_H:
                colour = (0, 0, 0) if lit else roof
            elif (y - ROOF_H) % FLOOR_PX < WINDOW_PX:
                colour = WINDOW_LIT if lit else window
            else:
                colour = (0, 0, 0) if lit else wall
            row += bytes(colour) * COL_W
        rows.append(bytes(row))
    return rows


def write_atlas(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    width = COL_W * len(STYLES)
    (out_dir / ATLAS_NAME).write_bytes(_png(width, ATLAS_H, atlas_rows(lit=False)))
    (out_dir / ATLAS_LIT_NAME).write_bytes(_png(width, ATLAS_H, atlas_rows(lit=True)))


def _s(style: str) -> float:
    col = STYLES.index(style) if style in STYLE_COLOURS else STYLES.index("universal")
    return (col * COL_W + COL_W / 2) / (COL_W * len(STYLES))


# Band t range (texture bottom to top), half a pixel in from each edge so filtering does not pick up the roof colour
T_BOTTOM = 0.5 / ATLAS_H
T_TOP = (PROFILE_H - 0.5) / ATLAS_H
T_ROOF = 1 - ROOF_H / 2 / ATLAS_H


def local_en(lonlat: list, origin: tuple) -> list:
    """Lon/lat to east/north metres from origin (equirectangular; negligible error within a few hundred metres)."""
    lon0, lat0 = origin
    k = math.radians(1) * EARTH_RADIUS_M
    return [((lon - lon0) * k * math.cos(math.radians(lat0)), (lat - lat0) * k) for lon, lat in lonlat]


def simplify_ring(ring: list, tolerance_m: float) -> list:
    """Simplified counter-clockwise outer ring without the closing point; the original ring if simplifying degenerates it."""
    simplified = Polygon(ring).simplify(tolerance_m, preserve_topology=True)
    if simplified.is_empty or simplified.geom_type != "Polygon" or len(simplified.exterior.coords) < 4:
        return list(orient(Polygon(ring), sign=1.0).exterior.coords)[:-1]
    return list(orient(simplified, sign=1.0).exterior.coords)[:-1]


def add_block(mesh: Mesh, ring: list, height: float, style: str, sink_m: float) -> None:
    """ring: outer ring in east/north metres, counter-clockwise, no closing point."""
    s = _s(style)
    bands = max(1, round(height / BAND_M))
    band_h = height / bands
    for i, (e0, n0) in enumerate(ring):
        e1, n1 = ring[(i + 1) % len(ring)]
        de, dn = e1 - e0, n1 - n0
        length = math.hypot(de, dn)
        if length < 1e-3:
            continue
        normal = enu_to_obj(dn / length, -de / length, 0)  # outward normal of a counter-clockwise ring
        # The underground part uses the bottom row's colour
        levels = [(-sink_m, T_BOTTOM, 0.0, T_BOTTOM)]
        levels += [(k * band_h, T_BOTTOM, (k + 1) * band_h, T_TOP) for k in range(bands)]
        for z0, t0, z1, t1 in levels:
            mesh.add_quad([enu_to_obj(e0, n0, z0), enu_to_obj(e1, n1, z0), enu_to_obj(e1, n1, z1),
                           enu_to_obj(e0, n0, z1)], [(s, t0), (s, t0), (s, t1), (s, t1)], normal)
    roof_uv = [(s, T_ROOF)] * 3
    for tri in shapely.constrained_delaunay_triangles(Polygon(ring)).geoms:
        pts = list(tri.exterior.coords)[:3]
        mesh.add_triangle([enu_to_obj(e, n, height) for e, n in pts], roof_uv, (0, 1, 0))


def facade_info(settings: dict) -> dict:
    """facade library path -> (style, LOD distance in metres)."""
    return {f["path"]: (f["style"], f.get("lod_m")) for f in settings["facades"]}


def build_impostors(tile_name: str, polygons: list, settings: dict, out_dir: Path, projector) -> tuple[list, dict]:
    """polygons: [(facade path, height m, [(lon, lat)])] of one tile. Writes OBJs to out_dir and returns
    ([(object path relative to the pack root, lon, lat, heading)], stats)."""
    dsf = settings["dsf"]
    far_m = dsf["render_distance_km"] * 1000
    imp = dsf["impostors"]
    info = facade_info(settings)
    groups = defaultdict(list)
    for facade, height, lonlat in polygons:
        style, lod_m = info.get(facade, ("universal", None))
        # Unknown facade LOD: assume the shortest default facade range so the far blocks still cover it
        lod_m = lod_m or min(v[1] for v in info.values() if v[1])
        if lod_m >= far_m:
            continue
        x, y = projector.to_metric(Polygon(lonlat)).centroid.coords[0]
        key = (math.floor(x / imp["cell_m"]), math.floor(y / imp["cell_m"]), int(lod_m))
        groups[key].append((style, height, lonlat, Polygon(lonlat).centroid.coords[0]))

    rel_dir = out_dir.relative_to(out_dir.parents[1]).as_posix()
    placements, triangles = [], 0
    for (i, j, lod_m), members in sorted(groups.items()):
        lon0 = sum(c[0] for *_, c in members) / len(members)
        lat0 = sum(c[1] for *_, c in members) / len(members)
        mesh = Mesh()
        for style, height, lonlat, _ in members:
            ring = simplify_ring(local_en(lonlat, (lon0, lat0)), imp["simplify_m"])
            add_block(mesh, ring, max(1, round(height)), style, imp["sink_m"])
        if not mesh.triangle_count:
            continue
        main_tris = mesh.triangle_count
        hidden_y = -imp["sink_m"] - 5
        mesh.add_triangle([(0, hidden_y, 0), (1, hidden_y, 0), (0, hidden_y, 1)], [(_s("universal"), T_ROOF)] * 3,
                          (0, 1, 0))
        name = f"{tile_name}_c{i}_{j}_lod{lod_m // 1000}k.obj"
        near_m = max(1, lod_m - imp["overlap_m"])
        write_obj8(out_dir / name, mesh, ATLAS_NAME, ATLAS_LIT_NAME, lod_far_m=far_m, lod_near_m=near_m,
                   comments=[f"QuickScene distant blocks: {len(members)} blocks, cell {i},{j}"])
        placements.append((f"{rel_dir}/{name}", lon0, lat0, 0))
        triangles += main_tris
    return placements, {"impostor_objects": len(placements), "impostor_triangles": triangles}
