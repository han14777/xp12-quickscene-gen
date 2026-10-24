"""Per-building edits: validation, height scaling, style, reference OBJ, and the full edit -> regenerate loop."""

import json
import math

import pytest
from shapely.geometry import box, mapping

from quickscene import classify, overrides, pipeline
from quickscene.config import Job

from test_clean import LAT0, LON0, OsmBuilder
from test_pipeline import SETTINGS, ZONE, _dsftool, _fake_fetch, make_settings


def feature(building_id: str, block_id: str, height: float, dx: float = 0.0) -> dict:
    geom = box(LON0 + dx, LAT0, LON0 + dx + 0.0002, LAT0 + 0.0002)
    return {"type": "Feature", "geometry": mapping(geom), "properties": {
        "id": block_id, "building_id": building_id, "height_m": height, "height_source": "osm_height",
        "building": "yes", "heritage": False}}


@pytest.fixture
def job(tmp_path):
    return Job.create("edits", ZONE, make_settings(tmp_path))


def test_validate_cleans_and_rejects(job):
    assert overrides.validate(job, {"delete": True, "height_m": 5}, SETTINGS) == {"delete": True}
    assert overrides.validate(job, {"height_m": "42.04", "style": ""}, SETTINGS) == {"height_m": 42.0}
    assert overrides.validate(job, {"height_m": None, "style": None}, SETTINGS) == {}
    with pytest.raises(ValueError, match="between"):
        overrides.validate(job, {"height_m": 0}, SETTINGS)
    with pytest.raises(ValueError, match="style"):
        overrides.validate(job, {"style": "brick"}, SETTINGS)
    for bad in ("../x.obj", "C:/x.obj", "/x.obj", "model.fbx", ""):
        with pytest.raises(ValueError):
            overrides.validate(job, {"model": {"path": bad}}, SETTINGS)
    lib = overrides.validate(job, {"model": {"path": "lib\\airport\\x.obj", "heading": 370}}, SETTINGS)
    assert lib == {"model": {"path": "lib/airport/x.obj", "heading": 10.0, "offset_e_m": 0.0, "offset_n_m": 0.0}}
    with pytest.raises(ValueError, match="within"):
        overrides.validate(job, {"model": {"path": "lib/x.obj", "offset_e_m": 900}}, SETTINGS)


def test_set_edit_stores_and_removes(job):
    overrides.set_edit(job, "way/1", {"height_m": 50}, SETTINGS)
    overrides.set_edit(job, "way/2", {"delete": True}, SETTINGS)
    assert overrides.load(job) == {"way/1": {"height_m": 50.0}, "way/2": {"delete": True}}
    overrides.set_edit(job, "way/1", None, SETTINGS)
    assert overrides.load(job) == {"way/2": {"delete": True}}


def test_height_edit_scales_all_blocks_and_style_edit_applies():
    features = [feature("way/1", "way/1", 40), feature("way/1", "way/5", 20), feature("way/2", "way/2", 10, 0.001)]
    edits = {"way/1": {"height_m": 80, "style": "classic"}, "way/2": {"delete": True}, "way/9": {"delete": True}}
    out, report = classify.classify(features, SETTINGS, edits)
    by_id = {f["properties"]["id"]: f["properties"] for f in out}
    assert by_id["way/1"]["height_m"] == 80 and by_id["way/5"]["height_m"] == 40
    assert by_id["way/1"]["height_source"] == "edited"
    assert {by_id[i]["facade_style"] for i in ("way/1", "way/5")} == {"classic"}
    assert by_id["way/1"]["edit"] == "edit" and by_id["way/2"]["edit"] == "delete"
    assert report["edits_not_found"] == ["way/9"]


def test_reference_obj_is_closed_and_centred_with_outward_faces():
    features = [feature("way/1", "way/1", 30)]
    text = overrides.reference_obj(features, "way/1")
    verts = [tuple(map(float, line.split()[1:])) for line in text.splitlines() if line.startswith("v ")]
    faces = [[int(i) - 1 for i in line.split()[1:]] for line in text.splitlines() if line.startswith("f ")]
    assert len(verts) == 8 and len(faces) == 6
    assert abs(sum(v[0] for v in verts) / 8) < 0.01 and abs(sum(v[2] for v in verts) / 8) < 0.01
    assert {v[1] for v in verts} == {0.0, 30.0}
    centre = [sum(v[k] for v in verts) / 8 for k in range(3)]
    for face in faces:  # Newell normal points away from the box centre
        pts = [verts[i] for i in face]
        n = [sum((pts[i][(k + 1) % 3] - pts[(i + 1) % len(pts)][(k + 1) % 3]) *
                 (pts[i][(k + 2) % 3] + pts[(i + 1) % len(pts)][(k + 2) % 3]) for i in range(len(pts))) for k in range(3)]
        mid = [sum(p[k] for p in pts) / len(pts) - centre[k] for k in range(3)]
        assert sum(n[k] * mid[k] for k in range(3)) > 0
    with pytest.raises(ValueError):
        overrides.reference_obj(features, "way/404")


def test_edit_then_regenerate_end_to_end(tmp_path):
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
    roads = OsmBuilder()
    roads.line([(0, 50), (90, 50)], {"highway": "residential"})
    _fake_fetch(job, b, roads)
    pipeline.run(job, settings, steps=["clean", "classify", "build_dsf"])

    (job.models_dir / "tower").mkdir(parents=True)
    (job.models_dir / "tower" / "tower.obj").write_text("I\n800\nOBJ\n")
    (job.models_dir / "tower" / "tower.png").write_bytes(b"png")
    ids = sorted({f["properties"]["building_id"] for f in json.loads(
        (job.processed_dir / "buildings_classified.geojson").read_text())["features"]})
    assert ids == ["way/1", "way/2", "way/3"]
    overrides.set_edit(job, "way/1", {"model": {"path": "tower/tower.obj", "heading": 45, "offset_e_m": 10}}, settings)
    overrides.set_edit(job, "way/2", {"height_m": 33}, settings)
    overrides.set_edit(job, "way/3", {"delete": True}, settings)

    reports = pipeline.run(job, settings, steps=["classify", "build_dsf"])
    built = reports["build_dsf"]
    assert built["polygons"] == 1 and built["models"] == 1
    assert built["blocks_deleted"] == 1 and built["blocks_replaced_by_model"] == 1
    pack = job.build_dir(settings)
    assert (pack / "objects" / "models" / "tower" / "tower.png").is_file()
    roundtrip = (job.processed_dir / "-38+144.roundtrip.txt").read_text(encoding="utf-8", errors="replace")
    assert "OBJECT_DEF objects/models/tower/tower.obj" in roundtrip
    # Exact placement is checked on the DSF text (the compiled DSF quantises coordinates)
    text = (job.processed_dir / "-38+144.dsf.txt").read_text(encoding="utf-8")
    defs = [line.split()[1] for line in text.splitlines() if line.startswith("OBJECT_DEF")]
    placed = [line.split() for line in text.splitlines() if line.startswith("OBJECT ")]
    model = next(p for p in placed if defs[int(p[1])].startswith("objects/models/"))
    assert math.isclose(float(model[4]), 45, abs_tol=0.01)
    # Placed 10 m east of the building centroid
    classified = json.loads((job.processed_dir / "buildings_classified.geojson").read_text())["features"]
    origin = overrides.building_origins([f for f in classified if f["properties"]["building_id"] == "way/1"])["way/1"]
    east_m = (float(model[2]) - origin[0]) * math.radians(1) * 6371008.8 * math.cos(math.radians(origin[1]))
    assert abs(east_m - 10) < 0.05 and abs(float(model[3]) - origin[1]) < 1e-7
    heights = {f["properties"]["building_id"]: f["properties"]["height_m"] for f in classified}
    assert heights["way/2"] == 33
    # Distant blocks no longer include the replaced or deleted buildings
    assert built["impostor_objects"] == 1

    # Removing the edits brings everything back
    for bid in ids:
        overrides.set_edit(job, bid, None, settings)
    built = pipeline.run(job, settings, steps=["classify", "build_dsf"])["build_dsf"]
    assert built["polygons"] == 3 and built["models"] == 0 and not (pack / "objects" / "models").exists()
