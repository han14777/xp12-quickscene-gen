"""Placed items (objects, facades, forests, draped polygons, lines) and OSM trees, through to a compiled DSF."""

import json
from collections import Counter

import pytest

from cityscene import pipeline, placements, vegetation
from cityscene.config import Job

from test_clean import LAT0, LON0, OsmBuilder
from test_pipeline import ZONE, _dsftool, make_settings


def P(x, y):
    return [LON0 + x * 1e-4, LAT0 + y * 1e-4]


def test_validate_kinds_and_params():
    obj = placements.validate_item({"path": "lib/cars/car.obj", "lon": LON0, "lat": LAT0, "heading": 370})
    assert obj["kind"] == "object" and obj["heading"] == 10
    fac = placements.validate_item({"path": "lib/a.fac", "rings": [[P(0, 0), P(1, 0), P(1, 1), P(0, 0)]],
                                    "height": 12.6})
    assert fac["height"] == 13 and len(fac["rings"][0]) == 3  # closing point dropped, integer height
    forest = placements.validate_item({"path": "lib/t.for", "rings": [[P(0, 0)]], "fill": "points"})
    assert forest["density"] == 255 and forest["fill"] == "points"
    line = placements.validate_item({"path": "lib/l.lin", "points": [P(0, 0), P(5, 5)], "closed": 1})
    assert line["kind"] == "line" and line["closed"] is True
    for bad in ({"path": "../x.obj", "lon": 0, "lat": 0}, {"path": "x.png"},
                {"path": "lib/a.fac", "rings": [[P(0, 0)]]},
                {"path": "lib/t.for", "rings": [[P(0, 0)]], "density": 300},
                {"path": "lib/a.obj", "lon": 200, "lat": 0}):
        with pytest.raises(ValueError):
            placements.validate_item(bad)


def test_dsf_params_follow_wed_encoding():
    items = placements.validate_items([
        {"path": "lib/a.fac", "rings": [[P(0, 0), P(0, 1), P(1, 1), P(1, 0)]], "height": 9},  # clockwise input
        {"path": "lib/t.for", "rings": [[P(0, 0), P(3, 0)]], "fill": "line", "density": 100},
        {"path": "lib/t.for", "rings": [[P(0, 0)]], "fill": "points"},
        {"path": "lib/g.pol", "rings": [[P(0, 0), P(1, 0), P(1, 1)]], "heading": 90},
        {"path": "lib/l.lin", "points": [P(0, 0), P(1, 1)]},
        {"path": "lib/s.str", "points": [P(0, 0), P(1, 1)], "spacing": 4},
        {"path": "models/x/x.obj", "lon": LON0, "lat": LAT0},
    ])
    objects, polygons = placements.dsf_entries(items, lambda p: "objects/" + p if p.startswith("models/") else p)
    assert objects == [("objects/models/x/x.obj", LON0, LAT0, 0.0, 0)]
    assert [p[1] for p in polygons] == [9, 100 + 256, 255 + 512, 90, 0, 4]
    ring = polygons[0][2][0]
    area = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]))
    assert area > 0  # facade ring turned counter-clockwise


def test_split_by_tile_skips_polygons_across_tiles():
    stats = Counter()
    objs, polys = placements.split_by_tile(
        [("a.obj", 144.5, -37.5, 0)],
        [("a.fac", 10, [[[144.9, -37.5], [145.1, -37.5], [145.0, -37.4]]]), ("b.pol", 0, [[[144.1, -37.1]] * 3])],
        stats)
    assert list(objs) == [(-38, 144)] and len(polys[(-38, 144)]) == 1
    assert stats["placements_skipped_cross_tile"] == 1


def _fake_fetch(job, kinds: dict):
    job.raw_dir.mkdir(parents=True, exist_ok=True)
    names = {k: f"{k}_0_0_1_1.json" for k in kinds}
    for k, b in kinds.items():
        (job.raw_dir / names[k]).write_text(json.dumps(b.data()))
    (job.raw_dir / "chunks.json").write_text(json.dumps({"chunks": [], "files": {k: [v] for k, v in names.items()}}))


def _tree(builder, x, y):
    node_id = builder._id()
    builder.elements.append({"type": "node", "id": node_id, "lon": LON0 + x * 1e-4, "lat": LAT0 + y * 1e-4,
                             "tags": {"natural": "tree"}})


def test_end_to_end_with_placements_and_trees(tmp_path):
    tool = _dsftool()
    if not tool:
        pytest.skip("DSFTool not available")
    settings = make_settings(tmp_path)
    settings["tools"]["dsftool"] = tool
    job = Job.create("place", ZONE, settings, options={"extras": {"trees": True, "woods": True, "tree_rows": True}})
    buildings, roads, veg = OsmBuilder(), OsmBuilder(), OsmBuilder()
    buildings.rect(10, 10, 4, 4, {"building": "yes", "height": "20"}, element_id=1)
    roads.line([(0, 50), (90, 50)], {"highway": "residential"})
    _tree(veg, 30, 30)
    _tree(veg, 31, 30)
    _tree(veg, 12, 12)       # inside the building: skipped
    _tree(veg, 40, 50)       # on the road: skipped
    _tree(veg, -20, 20)      # outside the area
    veg.rect(60, 60, 20, 20, {"landuse": "forest"})
    veg.line([(5, 70), (40, 70)], {"natural": "tree_row"})
    _fake_fetch(job, {"buildings": buildings, "roads": roads, "vegetation": veg})
    placements.save(job, [
        {"path": "lib/airport/Ramp_Equipment/Tug.obj", "lon": P(20, 20)[0], "lat": P(20, 20)[1], "heading": 45},
        {"path": "lib/airport/Common_Elements/Misc_Buildings/Blue_Warehouse.fac",
         "rings": [[P(40, 10), P(45, 10), P(45, 14), P(40, 14)]], "height": 8},
        {"path": "lib/airport/ground/pavement/asphalt/plain.pol", "rings": [[P(50, 0), P(55, 0), P(55, 5)]]},
    ])
    reports = pipeline.run(job, job.settings(settings), steps=["clean", "classify", "build_dsf"])
    built = reports["build_dsf"]
    assert built["roundtrip_verified"] and built["placed_items"] == 3
    assert built["vegetation"]["trees"] == 2 and built["vegetation"]["trees_blocked"] == 2
    assert built["vegetation"]["woods"] == 1 and built["vegetation"]["tree_rows"] >= 1
    text = (job.processed_dir / "-38+144.roundtrip.txt").read_text(encoding="utf-8", errors="replace")
    params = Counter(line.split()[2] for line in text.splitlines() if line.startswith("BEGIN_POLYGON"))
    assert params["767"] == 1 and params["511"] >= 1 and params["8"] == 1  # points forest, row, warehouse
    assert "OBJECT_DEF lib/airport/Ramp_Equipment/Tug.obj" in text
    assert vegetation.enabled(job.settings(settings))


def test_dsf_import_keeps_wall_types_uv_and_decodes_params():
    from cityscene import dsf_import
    text = "\n".join([
        "A", "800", "DSF2TEXT", "PROPERTY sim/exclude_obj 1/2/3/4",
        "OBJECT_DEF lib/cars/car.obj", "POLYGON_DEF lib/fence.fac", "POLYGON_DEF lib/grass.pol",
        "POLYGON_DEF lib/trees.for",
        f"OBJECT 0 {LON0} {LAT0} 90",
        "BEGIN_POLYGON 0 3 3", "BEGIN_WINDING",
        f"POLYGON_POINT {LON0} {LAT0} 1", f"POLYGON_POINT {LON0 + 1e-4} {LAT0} 2", "END_WINDING", "END_POLYGON",
        "BEGIN_POLYGON 1 65535 4", "BEGIN_WINDING",
        *[f"POLYGON_POINT {LON0 + dx} {LAT0 + dy} {dx * 1e4} {dy * 1e4}" for dx, dy in ((0, 0), (1e-4, 0), (0, 1e-4))],
        "END_WINDING", "END_POLYGON",
        "BEGIN_POLYGON 2 767 2", "BEGIN_WINDING", f"POLYGON_POINT {LON0} {LAT0}", "END_WINDING", "END_POLYGON",
    ])
    items, report = dsf_import.parse_dsf_text(text)
    assert report["imported"] == 4 and report["exclusions_skipped"] == 1
    fence, grass, trees = items[1], items[2], items[3]
    assert fence["height"] == 3 and fence["rings"][0][1][2] == 2  # wall type kept
    objects, polygons = placements.dsf_entries(items, lambda p: p)
    assert polygons[0][1] == 3 and len(polygons[0][2][0][0]) == 3
    assert polygons[1][1] == 65535 and len(polygons[1][2][0][0]) == 4
    assert trees["fill"] == "points" and polygons[2][1] == 767
