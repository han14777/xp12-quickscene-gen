"""Local OSM file source: the extract matches what the Overpass queries return, and the pipeline runs on it."""

import json

import pytest

pytest.importorskip("osmium")

from quickscene import fetch, osm_file, pipeline  # noqa: E402
from quickscene.config import Job, check_source  # noqa: E402

from test_pipeline import ZONE, _dsftool, make_settings  # noqa: E402
from test_clean import LAT0, LON0  # noqa: E402

D = 0.0001  # about 10 m


def node(nid, dx, dy):
    return f'<node id="{nid}" version="1" lat="{LAT0 + dy * D:.7f}" lon="{LON0 + dx * D:.7f}"/>'


def write_osm(path):
    """Inside the area: building way 100, highway 200, building relation 300 with an untagged outline way 301.
    Far outside: building way 400."""
    nodes = [node(1, 10, 10), node(2, 14, 10), node(3, 14, 14), node(4, 10, 14),
             node(5, 0, 50), node(6, 90, 50),
             node(7, 30, 30), node(8, 36, 30), node(9, 36, 36), node(10, 30, 36),
             node(11, 5000, 5000), node(12, 5004, 5000), node(13, 5004, 5004)]
    body = "\n".join(nodes) + """
<way id="100" version="1"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/><tag k="building" v="yes"/><tag k="height" v="40"/></way>
<way id="200" version="1"><nd ref="5"/><nd ref="6"/><tag k="highway" v="residential"/></way>
<way id="301" version="1"><nd ref="7"/><nd ref="8"/><nd ref="9"/><nd ref="10"/><nd ref="7"/></way>
<way id="400" version="1"><nd ref="11"/><nd ref="12"/><nd ref="13"/><nd ref="11"/><tag k="building" v="yes"/></way>
<relation id="300" version="1"><member type="way" ref="301" role="outer"/><tag k="type" v="multipolygon"/><tag k="building" v="office"/></relation>
"""
    path.write_text(f'<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6" generator="test">\n{body}</osm>\n',
                    encoding="utf-8")
    return path


def test_extract_keeps_area_elements_in_overpass_shape(tmp_path):
    path = write_osm(tmp_path / "area.osm")
    bbox = fetch.query_bbox(Job.create("x", ZONE, make_settings(tmp_path)).zones(), 100)
    data = osm_file.extract(path, bbox)
    ids = {kind: {(e["type"], e["id"]) for e in d["elements"]} for kind, d in data.items()}
    assert ("way", 100) in ids["buildings"] and ("relation", 300) in ids["buildings"] and ("way", 301) in ids["buildings"]
    assert ("way", 400) not in ids["buildings"] and ("way", 200) not in ids["buildings"]
    assert ("way", 200) in ids["roads"] and ("way", 100) not in ids["roads"]
    # Every node a kept way refers to is present with coordinates
    for d in data.values():
        node_ids = {e["id"] for e in d["elements"] if e["type"] == "node"}
        for e in d["elements"]:
            if e["type"] == "way":
                assert set(e["nodes"]) <= node_ids
    outline = next(e for e in data["buildings"]["elements"] if e.get("id") == 301 and e["type"] == "way")
    assert "tags" not in outline


def test_file_source_runs_the_pipeline_and_reuses_its_cache(tmp_path):
    settings = make_settings(tmp_path)
    path = write_osm(tmp_path / "area.osm")
    job = Job.create("file", ZONE, settings, source={"type": "file", "path": str(path)})
    assert job.source == {"type": "file", "path": str(path)}
    reports = pipeline.run(job, settings, steps=["fetch", "clean"])
    assert reports["clean"]["buildings"] == 2
    index = json.loads((job.raw_dir / "chunks.json").read_text())
    assert index["files"] == {"buildings": ["file_buildings.json"], "roads": ["file_roads.json"]}
    stamp = (job.raw_dir / "file_buildings.json").stat().st_mtime_ns
    pipeline.run(job, settings, steps=["fetch"])
    assert (job.raw_dir / "file_buildings.json").stat().st_mtime_ns == stamp  # reused, not read again


def test_file_errors_are_clear(tmp_path):
    settings = make_settings(tmp_path)
    job = Job.create("missing", ZONE, settings, source={"type": "file", "path": str(tmp_path / "nope.osm.pbf")})
    with pytest.raises(RuntimeError, match="OSM file not found"):
        pipeline.run(job, settings, steps=["fetch"])
    empty = tmp_path / "empty.osm"
    empty.write_text('<?xml version="1.0"?>\n<osm version="0.6"></osm>\n', encoding="utf-8")
    job = Job.create("empty", ZONE, settings, source={"type": "file", "path": str(empty)})
    with pytest.raises(RuntimeError, match="No buildings"):
        pipeline.run(job, settings, steps=["fetch"])
    with pytest.raises(ValueError):
        check_source({"type": "file", "path": " "})
    assert check_source(None) == {"type": "overpass"}


def test_file_source_end_to_end_builds_dsf(tmp_path):
    tool = _dsftool()
    if not tool:
        pytest.skip("DSFTool not available")
    settings = make_settings(tmp_path)
    settings["tools"]["dsftool"] = tool
    job = Job.create("dsf", ZONE, settings, source={"type": "file", "path": str(write_osm(tmp_path / "a.osm"))})
    assert pipeline.run(job, settings)["build_dsf"]["roundtrip_verified"]
