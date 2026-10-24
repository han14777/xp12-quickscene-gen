"""Tests for jobs, download cache, classification, DSF and install; the end-to-end test runs clean -> classify -> build_dsf on synthetic data (skipped without DSFTool)."""

import json
from pathlib import Path

import pytest
from shapely.geometry import Polygon, box, mapping, shape

from quickscene import build_dsf, classify, fetch, install, pipeline
from quickscene.config import Job, load_settings, normalize_zone
from quickscene.dsf_rects import exclusion_rects
from quickscene.geo import Projector, tile_dir, tile_name, tiles_for, utm_crs

from test_clean import LAT0, LON0, OsmBuilder

SETTINGS = load_settings()
ZONE = mapping(box(LON0, LAT0, LON0 + 0.01, LAT0 + 0.01))


def make_settings(tmp_path: Path) -> dict:
    settings = json.loads(json.dumps(SETTINGS))
    settings["paths"] = {"jobs_dir": str(tmp_path / "jobs"), "build_dir": str(tmp_path / "build")}
    return settings


# ---------- coordinates and tiles ----------


def test_utm_zone():
    assert utm_crs(144.96, -37.8) == "EPSG:32755"
    assert utm_crs(113.3, 23.1) == "EPSG:32649"
    assert utm_crs(-0.1, 51.5) == "EPSG:32630"


def test_tile_naming():
    assert tile_name(-38, 144) == "-38+144"
    assert tile_dir(-38, 144) == "-40+140"
    assert tile_name(23, 113) == "+23+113"
    assert tile_dir(23, 113) == "+20+110"
    assert tile_dir(51, -1) == "+50-010"


def test_tiles_for_zone_crossing_boundary():
    assert tiles_for(box(143.99, -37.9, 144.01, -37.8)) == [(-38, 143), (-38, 144)]


# ---------- jobs ----------


def test_normalize_zone_keeps_polygons_and_numbers_them():
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"x": 1}, "geometry": ZONE},
        {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [0, 0]}},
        {"type": "Feature", "properties": {}, "geometry": ZONE},
    ]}
    out = normalize_zone(fc)
    assert [f["properties"]["id"] for f in out["features"]] == ["zone_1", "zone_2"]
    with pytest.raises(ValueError):
        normalize_zone({"type": "Point", "coordinates": [0, 0]})


@pytest.mark.parametrize("name", ["", "has space", "中文", "a/b", "x" * 65])
def test_bad_job_names_rejected(tmp_path, name):
    with pytest.raises(ValueError):
        Job.create(name, ZONE, make_settings(tmp_path))


def test_job_options_override_only_allowed_sections(tmp_path):
    settings = make_settings(tmp_path)
    job = Job.create("t1", ZONE, settings, options={"heights": {"default_height_m": 9}, "tools": {"dsftool": "x"}})
    merged = job.settings(settings)
    assert merged["heights"]["default_height_m"] == 9
    assert merged["heights"]["level_height_m"] == settings["heights"]["level_height_m"]
    assert merged["tools"] == settings["tools"]
    assert job.scenery_name == "CityScene_t1"
    with pytest.raises(FileExistsError):
        Job.create("t1", ZONE, settings)


# ---------- download cache ----------


def test_chunks_cover_bbox_on_aligned_grid():
    bbox = (144.94, -37.82, 145.06, -37.78)
    cells = fetch.chunks(bbox, 0.05)
    assert len(cells) == 4 * 2  # four columns (144.90|144.95|145.00|145.05) by two rows
    union = shape({"type": "MultiPolygon", "coordinates": [
        [[[w, s], [e, s], [e, n], [w, n], [w, s]]] for s, w, n, e in cells]})
    assert union.buffer(1e-9).contains(box(*bbox))


def test_cached_cover_reuses_larger_chunk(tmp_path):
    (tmp_path / "buildings_-37.900000_144.900000_-37.800000_145.000000.json").write_text("{}")
    assert fetch.cached_cover(tmp_path, "buildings", (-37.85, 144.95, -37.81, 144.99)) is not None
    assert fetch.cached_cover(tmp_path, "buildings", (-37.85, 144.95, -37.79, 144.99)) is None
    assert fetch.cached_cover(tmp_path, "roads", (-37.85, 144.95, -37.81, 144.99)) is None


# ---------- classification ----------


def _feature(bid, height, building="yes", heritage=False):
    return {"type": "Feature", "geometry": ZONE, "properties": {
        "id": bid, "building_id": bid, "height_m": height, "building": building, "heritage": heritage}}


def test_classify_categories_and_floor_ranges():
    feats = [_feature("a", 200), _feature("b", 30), _feature("c", 7), _feature("d", 12, "church")]
    out, report = classify.classify(feats, SETTINGS)
    by_id = {f["properties"]["id"]: f["properties"] for f in out}
    assert by_id["a"]["category"] == "high_rise"
    assert by_id["b"]["category"] == "mid_rise"
    assert by_id["c"]["category"] == "low_rise"
    assert by_id["d"]["category_reason"] == "heritage_building_type"
    facades = {f["path"]: f for f in SETTINGS["facades"]}
    # 200 m is about 57 floors; the facade must be unlimited or allow >= 57 floors, or the tower gets cut short
    tall = facades[by_id["a"]["facade"]]
    assert tall["floors_max"] is None or tall["floors_max"] >= by_id["a"]["floors"]
    assert report["blocks"] == 4


def test_classify_is_deterministic():
    feats = [_feature(f"way/{i}", 25) for i in range(20)]
    first = [f["properties"]["facade"] for f in classify.classify(feats, SETTINGS)[0]]
    second = [f["properties"]["facade"] for f in classify.classify(feats, SETTINGS)[0]]
    assert first == second and len(set(first)) > 1


# ---------- DSF ----------


def test_prepare_ring_counter_clockwise_without_closing_point():
    ring, reason, _ = build_dsf.prepare_ring(Polygon([(0, 0), (0, 10), (10, 10), (10, 0)]))
    assert reason is None and len(ring) == 4
    assert Polygon(ring).exterior.is_ccw


def test_split_holes_turns_courtyard_into_hole_free_pieces():
    courtyard = box(0, 0, 30, 30).difference(box(10, 10, 20, 20))
    pieces = build_dsf.split_holes(courtyard)
    assert len(pieces) >= 2 and all(not p.interiors for p in pieces)
    assert sum(p.area for p in pieces) == pytest.approx(courtyard.area)


def test_build_text_integer_heights_and_only_configured_exclusions():
    polygons = [("lib/a.fac", 39.5, [(144.961, -37.814), (144.962, -37.814), (144.962, -37.813)])]
    text, report = build_dsf.build_text((-38, 144), polygons, normalize_zone(ZONE)["features"], SETTINGS)
    assert "BEGIN_POLYGON 0 40 2" in text
    kinds = {line.split()[1] for line in text.splitlines() if line.startswith("PROPERTY sim/exclude_")}
    assert kinds == {f"sim/exclude_{k}" for k in SETTINGS["dsf"]["exclusion_types"]}
    assert report["exclusion_rects"] > 0
    assert "PROPERTY sim/west 144" in text and "PROPERTY sim/north -37" in text


def test_exclusion_rects_clipped_to_tile():
    zone = box(143.995, -37.9, 144.005, -37.89)
    rects = exclusion_rects(mapping(zone.intersection(box(144, -38, 145, -37))), 10)
    assert rects and all(r[0] >= 144 - 1e-9 for r in rects)


# ---------- install ----------

INI = "I\r\n1000 Version\r\nSCENERY\r\n\r\nSCENERY_PACK Custom Scenery/My Airport/\r\n" \
      "SCENERY_PACK *GLOBAL_AIRPORTS*\r\nSCENERY_PACK Custom Scenery/simHeaven_X-World/\r\n"


def test_insert_before_global_airports_and_remove_roundtrip():
    entry = "SCENERY_PACK Custom Scenery/CityScene_x/"
    text, result = install.insert_entry(INI, entry, ["*GLOBAL_AIRPORTS*"])
    assert result == "inserted"
    lines = text.split("\r\n")
    assert lines.index(entry) == lines.index("SCENERY_PACK *GLOBAL_AIRPORTS*") - 1
    assert install.insert_entry(text, entry, ["*GLOBAL_AIRPORTS*"])[1] == "present"
    removed, changed = install.remove_entry(text, entry)
    assert changed and removed == INI


def test_insert_at_top_when_marker_missing():
    entry = "SCENERY_PACK Custom Scenery/CityScene_x/"
    text, result = install.insert_entry(INI, entry, ["nothing"])
    assert result == "top"
    assert text.split("\r\n")[4] == entry


def test_disabled_entry_counts_as_present():
    entry = "SCENERY_PACK Custom Scenery/CityScene_x/"
    ini = INI + "SCENERY_PACK_DISABLED Custom Scenery/CityScene_x/\r\n"
    assert install.insert_entry(ini, entry, ["*GLOBAL_AIRPORTS*"])[1] == "present"


def test_ensure_link_refuses_real_directory(tmp_path):
    (tmp_path / "link").mkdir()
    (tmp_path / "target").mkdir()
    with pytest.raises(RuntimeError):
        install.ensure_link(tmp_path / "link", tmp_path / "target")


def test_ensure_link_creates_and_detects_existing(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "f.txt").write_text("x")
    assert install.ensure_link(tmp_path / "link", target) in ("symlink", "junction")
    assert (tmp_path / "link" / "f.txt").read_text() == "x"
    assert install.ensure_link(tmp_path / "link", target) == "exists"


# ---------- end to end ----------


def _fake_fetch(job, builder, roads):
    """No network: write the synthetic data in fetch's cache format."""
    job.raw_dir.mkdir(parents=True)
    names = {"buildings": "buildings_0_0_1_1.json", "roads": "roads_0_0_1_1.json"}
    (job.raw_dir / names["buildings"]).write_text(json.dumps(builder.data()))
    (job.raw_dir / names["roads"]).write_text(json.dumps(roads.data()))
    (job.raw_dir / "chunks.json").write_text(json.dumps({"chunks": [], "files": {k: [v] for k, v in names.items()}}))


def _dsftool():
    from quickscene.config import dsftool_path
    found = dsftool_path(SETTINGS)
    fallback = Path("D:/xptools_win_24-5/tools/DSFTool.exe")
    return str(found or (fallback if fallback.is_file() else "")) or None


def test_end_to_end_builds_verified_dsf(tmp_path):
    tool = _dsftool()
    if not tool:
        pytest.skip("DSFTool not available")
    settings = make_settings(tmp_path)
    settings["tools"]["dsftool"] = tool
    job = Job.create("e2e", ZONE, settings)
    b = OsmBuilder()
    b.rect(10, 10, 4, 4, {"building": "yes", "height": "120"}, element_id=1)
    b.rect(20, 10, 3, 3, {"building": "yes", "building:levels": "6"}, element_id=2)
    b.rect(30, 30, 6, 6, {"building": "yes"}, element_id=3)
    b.rect(-30, 10, 3, 3, {"building": "yes"}, element_id=4)  # outside the area
    roads = OsmBuilder()
    roads.line([(0, 50), (90, 50)], {"highway": "residential"})
    _fake_fetch(job, b, roads)

    reports = pipeline.run(job, settings, steps=["clean", "classify", "build_dsf"])
    assert reports["clean"]["kept_blocks"] == 3
    built = reports["build_dsf"]
    assert built["roundtrip_verified"] and built["polygons"] == 3
    dsf = job.build_dir(settings) / "Earth nav data" / "-40+140" / "-38+144.dsf"
    assert dsf.is_file() and dsf.stat().st_size > 0
    assert (job.build_dir(settings) / "README.txt").read_text(encoding="utf-8").count("OpenStreetMap") == 1
    roundtrip = (job.processed_dir / "-38+144.roundtrip.txt").read_text(encoding="utf-8", errors="replace")
    kinds = {line.split()[1] for line in roundtrip.splitlines() if line.startswith("PROPERTY sim/exclude_")}
    assert kinds == {"sim/exclude_obj", "sim/exclude_fac"}
    assert (job.root / "build.log").read_text(encoding="utf-8").count("build_dsf finished") == 1

    # Distant blocks: placed in the DSF (survive the round trip) and their OBJs exist in the pack
    objects_dir = job.build_dir(settings) / "objects" / "impostors"
    defs = [line.split()[1] for line in roundtrip.splitlines() if line.startswith("OBJECT_DEF")]
    assert built["impostor_objects"] == len(defs) > 0
    assert sum(line.startswith("OBJECT ") for line in roundtrip.splitlines()) == len(defs)
    assert "PROPERTY sim/require_object 1/0" in roundtrip
    assert all((job.build_dir(settings) / d).is_file() for d in defs)
    assert (objects_dir / "impostor_atlas.png").is_file()

    # A render distance inside every facade's own range needs no distant blocks
    settings["dsf"]["render_distance_km"] = 5
    reports = pipeline.run(job, settings, steps=["build_dsf"])
    assert reports["build_dsf"]["impostor_objects"] == 0 and not objects_dir.exists()

    # Output blocks: centroid inside the area, valid geometry, height in (0, 350)
    features = json.loads((job.processed_dir / "buildings_classified.geojson").read_text(encoding="utf-8"))["features"]
    zone = shape(ZONE)
    for f in features:
        geom = shape(f["geometry"])
        assert geom.is_valid and zone.contains(geom.centroid)
        assert 0 < f["properties"]["height_m"] < 350
    projector = Projector(utm_crs(LON0, LAT0))
    assert projector.crs == "EPSG:32755"
