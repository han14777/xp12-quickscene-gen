"""Distant-block OBJs: grouping, LOD bands, geometry orientation, atlas."""

import copy
import struct

from quickscene import impostors
from quickscene.config import load_settings
from quickscene.geo import Projector, utm_crs
from quickscene.obj8 import read_obj8

SETTINGS = load_settings()
LON0, LAT0 = 144.96, -37.81
PROJ = Projector(utm_crs(LON0, LAT0))


def square(dx_m: float, dy_m: float, size_m: float = 20) -> list:
    k = 111_320.0
    lon = LON0 + dx_m / (k * 0.79)
    lat = LAT0 + dy_m / k
    d_lon, d_lat = size_m / (k * 0.79), size_m / k
    return [(lon, lat), (lon + d_lon, lat), (lon + d_lon, lat + d_lat), (lon, lat + d_lat)]


def facade(style_prefix: str) -> str:
    return next(f["path"] for f in SETTINGS["facades"] if style_prefix in f["path"])


def test_groups_by_cell_and_lod_with_lod_band(tmp_path):
    out = tmp_path / "pack" / "objects" / "impostors"
    out.mkdir(parents=True)
    polygons = [
        (facade("mid_modern_01"), 30, square(0, 0)),      # LOD 10 km
        (facade("mid_modern_02"), 25, square(40, 0)),     # same cell, same LOD -> same OBJ
        (facade("high_universal_01"), 150, square(60, 0)),  # LOD 18 km -> separate OBJ
        (facade("mid_modern_01"), 30, square(2000, 0)),   # another cell
    ]
    placements, stats = impostors.build_impostors("-38+144", polygons, SETTINGS, out, PROJ)
    assert stats["impostor_objects"] == len(placements) == 3
    assert all(path.startswith("objects/impostors/") for path, *_ in placements)
    overlap = SETTINGS["dsf"]["impostors"]["overlap_m"]
    far = SETTINGS["dsf"]["render_distance_km"] * 1000
    lods = sorted(read_obj8(tmp_path / "pack" / path)["lods"][1] for path, *_ in placements)
    assert lods == [(10000 - overlap, far), (10000 - overlap, far), (18000 - overlap, far)]


def test_mesh_faces_outward_and_placeholder_is_underground(tmp_path):
    out = tmp_path / "pack" / "objects" / "impostors"
    out.mkdir(parents=True)
    placements, _ = impostors.build_impostors("-38+144", [(facade("mid_modern_01"), 30, square(0, 0))],
                                              SETTINGS, out, PROJ)
    obj = read_obj8(tmp_path / "pack" / placements[0][0])
    assert obj["point_counts"][3] == len(obj["indices"])
    (near_start, near_count), (main_start, main_count) = obj["tris"]
    assert near_count == 3 and main_start == 0 and main_count == len(obj["indices"]) - 3
    placeholder = [obj["vertices"][i] for i in obj["indices"][near_start:near_start + 3]]
    assert all(v[1] < -SETTINGS["dsf"]["impostors"]["sink_m"] for v in placeholder)
    heights = [obj["vertices"][i][1] for i in obj["indices"][:main_count]]
    assert max(heights) == 30
    # Front face is the clockwise side: cross(b - a, c - a) opposes the vertex normal
    for t in range(0, main_count, 3):
        a, b, c = (obj["vertices"][i] for i in obj["indices"][t:t + 3])
        u = [b[k] - a[k] for k in range(3)]
        w = [c[k] - a[k] for k in range(3)]
        n = (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0])
        assert sum(n[k] * a[3 + k] for k in range(3)) < 0


def test_render_distance_within_facade_range_writes_nothing(tmp_path):
    settings = copy.deepcopy(SETTINGS)
    settings["dsf"]["render_distance_km"] = 10
    out = tmp_path / "pack" / "objects" / "impostors"
    out.mkdir(parents=True)
    polygons = [(facade("mid_modern_01"), 30, square(0, 0)), (facade("low_modern_01"), 6, square(40, 0))]
    placements, _ = impostors.build_impostors("-38+144", polygons, settings, out, PROJ)
    # Only the 8 km low-rise facade falls short of 10 km
    assert len(placements) == 1 and "lod8k" in placements[0][0]


def test_atlas_is_valid_png(tmp_path):
    impostors.write_atlas(tmp_path)
    for name in (impostors.ATLAS_NAME, impostors.ATLAS_LIT_NAME):
        data = (tmp_path / name).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", data[16:24])
        assert (width, height) == (impostors.COL_W * len(impostors.STYLES), impostors.ATLAS_H)
