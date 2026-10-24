"""Generated packs carry their job and can be imported back (also into another copy), including older packs."""

import json
import shutil

import pytest
from shapely.geometry import shape

from quickscene import overrides, pipeline, transfer
from quickscene.config import Job

from test_clean import OsmBuilder
from test_pipeline import ZONE, _dsftool, _fake_fetch, make_settings


def copy_settings(tmp_path, tool):
    """Settings of a separate copy of the tool, with no X-Plane (so the real Custom Scenery is never scanned)."""
    settings = make_settings(tmp_path)
    settings["tools"]["dsftool"] = tool
    settings["xplane"]["root"] = str(tmp_path / "no_xplane")
    return settings


def built_job(tmp_path, tool):
    settings = copy_settings(tmp_path, tool)
    job = Job.create("src", ZONE, settings, options={"dsf": {"render_distance_km": 25}})
    b = OsmBuilder()
    b.rect(10, 10, 4, 4, {"building": "yes", "height": "120"}, element_id=1)
    b.rect(20, 10, 3, 3, {"building": "yes", "building:levels": "6"}, element_id=2)
    b.rect(30, 30, 6, 6, {"building": "yes"}, element_id=3)
    roads = OsmBuilder()
    roads.line([(0, 50), (90, 50)], {"highway": "residential"})
    _fake_fetch(job, b, roads)
    (job.models_dir / "tower").mkdir(parents=True)
    (job.models_dir / "tower" / "tower.obj").write_text("I\n800\nOBJ\n")
    overrides.set_edit(job, "way/1", {"model": {"path": "tower/tower.obj"}}, settings)
    overrides.set_edit(job, "way/2", {"height_m": 33}, settings)
    pipeline.run(job, settings, steps=["clean", "classify", "build_dsf"])
    return job, settings


@pytest.fixture
def tool():
    found = _dsftool()
    if not found:
        pytest.skip("DSFTool not available")
    return found


def test_pack_from_another_copy_brings_the_whole_job(tmp_path, tool):
    job, settings = built_job(tmp_path / "a", tool)
    pack = job.build_dir(settings)
    assert transfer.pack_kind(pack) == "job" and transfer.source_job(pack) == job.root
    other = copy_settings(tmp_path / "b", tool)
    summary = transfer.import_pack(pack, other)
    assert summary["kind"] == "job" and summary["edits"] == 2 and summary["models"] == 1 and summary["blocks"] == 3
    imported = Job.open("src", other)
    # The OSM cache came along, so even clean can rerun without a download
    assert (imported.raw_dir / "chunks.json").is_file()
    assert pipeline.run(imported, other, steps=["clean", "classify", "build_dsf"])["build_dsf"]["models"] == 1


def test_pack_carries_job_and_imports_into_another_copy(tmp_path, tool):
    job, settings = built_job(tmp_path / "a", tool)
    # A pack on its own (sent by someone, no job folder next to it)
    pack = tmp_path / "shared" / "CityScene_src"
    shutil.copytree(job.build_dir(settings), pack)
    assert transfer.pack_kind(pack) == "data"
    assert {p.name for p in (pack / transfer.DATA_DIR).iterdir()} == {
        "job.json", "zone.geojson", "overrides.json", "buildings_clean.geojson"}

    other = copy_settings(tmp_path / "b", tool)
    assert transfer.find_packs(other) == []
    summary = transfer.import_pack(pack, other)
    assert summary == {"name": "src", "scenery_name": "CityScene_src", "kind": "data", "blocks": 3, "edits": 2,
                       "models": 1, "options": {"dsf": {"render_distance_km": 25}}}
    imported = Job.open("src", other)
    assert overrides.load(imported) == overrides.load(job)
    assert (imported.models_dir / "tower" / "tower.obj").is_file()
    classified = json.loads((imported.processed_dir / "buildings_classified.geojson").read_text())["features"]
    assert {f["properties"]["building_id"]: f["properties"]["edit"] for f in classified}["way/2"] == "edit"
    assert (imported.build_dir(other) / "Earth nav data").is_dir()

    # Edit and regenerate in the new copy without any download
    overrides.set_edit(imported, "way/3", {"delete": True}, other)
    built = pipeline.run(imported, other, steps=["classify", "build_dsf"])["build_dsf"]
    assert built["polygons"] == 1 and built["models"] == 1 and built["render_distance_km"] == 25

    # Importing again needs overwrite; the build folder of an existing job is not offered for import
    with pytest.raises(FileExistsError):
        transfer.import_pack(pack, other)
    assert transfer.import_pack(pack, other, overwrite=True)["edits"] == 2
    assert transfer.find_packs(other) == []


def test_legacy_pack_recovers_area_and_render_distance(tmp_path, tool):
    job, settings = built_job(tmp_path / "a", tool)
    pack = tmp_path / "CityScene_old"
    shutil.copytree(job.build_dir(settings), pack)
    shutil.rmtree(pack / transfer.DATA_DIR)
    assert transfer.pack_kind(pack) == "legacy"

    other = copy_settings(tmp_path / "b", tool)
    summary = transfer.import_pack(pack, other)
    assert summary["kind"] == "legacy" and summary["name"] == "old" and summary["blocks"] == 0
    assert summary["options"] == {"dsf": {"render_distance_km": 25}}
    zone = shape(Job.open("old", other).zones()[0]["geometry"])
    original = shape(ZONE)
    # The area is rebuilt from 10 m exclusion cells, so it matches the original to within a cell
    assert zone.symmetric_difference(original).area / original.area < 0.05


def test_rejects_foreign_folders_and_pack_name_clashes(tmp_path, tool):
    other = copy_settings(tmp_path / "b", tool)
    foreign = tmp_path / "SomeAirport"
    foreign.mkdir()
    (foreign / "README.txt").write_text("An airport")
    with pytest.raises(ValueError, match="not a scenery pack made by"):
        transfer.import_pack(foreign, other)

    job, settings = built_job(tmp_path / "a", tool)
    Job.create("mine", ZONE, other, "CityScene_src")
    with pytest.raises(ValueError, match="already uses the pack name"):
        transfer.import_pack(job.build_dir(settings), other)


def test_copy_of_an_imported_pack_imports_under_a_new_name(tmp_path, tool):
    job, settings = built_job(tmp_path / "a", tool)
    pack = tmp_path / "shared" / "CityScene_src"
    shutil.copytree(job.build_dir(settings), pack)
    other = copy_settings(tmp_path / "b", tool)
    transfer.import_pack(pack, other)
    with pytest.raises(FileExistsError):
        transfer.import_pack(pack, other)
    copy = transfer.import_pack(pack, other, name="src_2")
    assert copy["name"] == "src_2" and copy["scenery_name"] == "CityScene_src_2"
    assert Job.open("src", other).scenery_name == "CityScene_src"
    assert (Job.open("src_2", other).build_dir(other) / "Earth nav data").is_dir()


def test_packs_from_before_the_rename_and_placed_items(tmp_path, tool):
    from quickscene import placements
    job, settings = built_job(tmp_path / "a", tool)
    placements.save(job, [{"path": "lib/cars/car_static.obj", "lon": 144.961, "lat": -37.81, "heading": 5}])
    pipeline.run(job, settings, steps=["build_dsf"])
    pack = tmp_path / "shared" / "CityScene_src"
    shutil.copytree(job.build_dir(settings), pack)
    assert (pack / transfer.DATA_DIR / "placements.json").is_file()
    # what an older version wrote: data in cityscene/, its README mark, its DSF creation agent
    (pack / transfer.DATA_DIR).rename(pack / "cityscene")
    readme = pack / "README.txt"
    readme.write_text(readme.read_text(encoding="utf-8").replace("Generated by QuickScene", "Generated by xp12-cityscene-gen"),
                      encoding="utf-8")
    assert transfer.pack_kind(pack) == "data"
    other = copy_settings(tmp_path / "b", tool)
    transfer.import_pack(pack, other)
    assert placements.load(Job.open("src", other))[0]["heading"] == 5
    shutil.rmtree(pack / "cityscene")
    assert transfer.pack_kind(pack) == "legacy"
