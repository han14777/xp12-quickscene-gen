"""Library index, asset readers and the library / placement API routes, on a fake X-Plane folder."""

import json

import pytest
from PIL import Image

from cityscene import assets, library
from cityscene.config import Job
from cityscene.gui import routes

from test_pipeline import ZONE, make_settings

OBJ = """I
800
OBJ

TEXTURE tex/box.png
POINT_COUNTS 4 0 0 9
VT 0 0 0 0 1 0 0 0
VT 1 0 0 0 1 0 1 0
VT 0 1 0 0 1 0 0 1
VT 1 1 0 0 1 0 1 1
IDX10 0 1 2 1 3 2 0 1 2
IDX 0
ATTR_LOD 0 500
TRIS 0 3
ATTR_LOD 0 2000
TRIS 3 3
LIGHT_NAMED airplane_beacon 0 2 0
ATTR_LOD 500 4000
TRIS 6 3
"""

FAC = """A
800
FACADE
FLOORS_MIN 2
FLOORS_MAX 8
TEXTURE tex/wall.png
LOD 0 10000
TEX_SIZE 1024 1024
WALL 0 50
SCALE 100 100
LEFT 0 100
CENTER 100 400
RIGHT 400 512
BOTTOM 0 64
MIDDLE 64 96
TOP 96 128
LOD 10000 20000
WALL 0 50
"""

FOREST = """A
800
FOREST
TEXTURE tex/trees.png
SCALE_X 512
SCALE_Y 512
SPACING 10 10
TREE 0 0 128 256 64 50 8 12 1 0 tree1
"""


@pytest.fixture
def xp(tmp_path):
    root = tmp_path / "X-Plane 12"
    lib = root / "Custom Scenery" / "L - Test Lib"
    (lib / "tex").mkdir(parents=True)
    (lib / "library.txt").write_text(
        "A\n800\nLIBRARY\n\nEXPORT lib/test/box.obj box.obj\nEXPORT test/house_a.fac house.fac\n"
        "EXPORT_RATIO 0.5 lib/test/trees.for trees.for\nPRIVATE\nEXPORT lib/test/secret.obj box.obj\nPUBLIC\n"
        "EXPORT_BACKUP lib/test/box.obj box.obj\nEXPORT lib/test/ground.ter ground.ter\n"
        "EXPORT test/sub folder/thing.obj sub folder\\thing.obj\n", encoding="utf-8")
    (lib / "box.obj").write_text(OBJ, encoding="latin-1")
    (lib / "house.fac").write_text(FAC, encoding="latin-1")
    (lib / "trees.for").write_text(FOREST, encoding="latin-1")
    Image.new("RGBA", (64, 64), (200, 50, 50, 255)).save(lib / "tex" / "box.png")
    Image.new("RGBA", (32, 32), (50, 200, 50, 255)).save(lib / "tex" / "wall.png")
    default = root / "Resources" / "default scenery" / "1000 autogen"
    default.mkdir(parents=True)
    (default / "library.txt").write_text("A\n800\nLIBRARY\nEXPORT lib/buildings/x.fac x.fac\n", encoding="utf-8")
    settings = make_settings(tmp_path)
    settings["xplane"] = {"root": str(root)}
    settings["paths"]["cache_dir"] = str(tmp_path / "cache")
    library._cached.clear()
    return settings, root, lib


def test_parse_library_flags_and_types(xp):
    settings, root, lib = xp
    entries = library.parse_library(lib / "library.txt")
    paths = {e[0]: e for e in entries}
    # .ter is not placeable; a virtual path cannot contain spaces, so "test/sub folder/thing.obj" reads as "test/sub"
    assert set(paths) == {"lib/test/box.obj", "test/house_a.fac", "lib/test/trees.for", "lib/test/secret.obj"}
    assert paths["lib/test/secret.obj"][2] == "p"
    assert [e[2] for e in entries if e[0] == "lib/test/box.obj"] == ["", "b"]
    assert library.categorize("lib/vegetation/trees/x.obj", "obj") == "vegetation"
    assert library.categorize("lib/airport/ground/x.pol", "pol") == "airport"


def test_index_search_resolve_dependencies(xp):
    settings, root, lib = xp
    index = library.get_index(settings)
    assert [p["name"] for p in index.packs] == ["1000 autogen", "L - Test Lib"]
    found = index.search("box")
    assert found["total"] == 1 and found["items"][0]["library"] == "L - Test Lib"
    assert index.search("secret")["total"] == 0 and index.search("secret", show_hidden=True)["total"] == 1
    assert index.search(kind="fac")["total"] == 2
    assert index.resolve("lib/test/box.obj") == lib / "box.obj"
    deps = index.dependencies(["lib/test/box.obj", "lib/buildings/x.fac", "lib/nothing.obj"])
    assert deps == {"libraries": {"L - Test Lib": ["lib/test/box.obj"], "X-Plane 12 (default)": ["lib/buildings/x.fac"]},
                    "missing": ["lib/nothing.obj"]}
    # cached index is reused, and rebuilt when a library.txt changes
    assert library.get_index(settings) is index
    with open(lib / "library.txt", "a", encoding="utf-8") as f:
        f.write("EXPORT lib/test/new.obj box.obj\n")
    assert "lib/test/new.obj" in library.get_index(settings).items


def test_asset_readers(xp):
    settings, root, lib = xp
    obj = assets.read_asset(lib / "box.obj", settings)
    # LOD buckets starting at 0 m are kept, the 500-4000 m one is not
    assert obj["groups"] == [{"start": 0, "count": 6, "texture": "main"}]
    assert obj["lights"] == [[0.0, 2.0, 0.0]]
    assert obj["textures"]["main"].startswith("xp:Custom Scenery/L - Test Lib/tex/box.png")
    png = assets.texture_png(obj["textures"]["main"], settings)
    assert png[:4] == b"\x89PNG"
    fac = assets.read_asset(lib / "house.fac", settings)
    assert fac["facade_type"] == 1 and fac["floors_max"] == 8 and len(fac["walls"]) == 1
    assert fac["walls"][0]["center"] == [[100.0, 400.0]] and fac["walls"][0]["tex_size"] == [1024.0, 1024.0]
    forest = assets.read_asset(lib / "trees.for", settings)
    assert forest["trees"][0]["max_h"] == 12 and forest["scale"] == [512.0, 512.0]
    with pytest.raises(assets.AssetError):
        assets.token_path("xp:../../outside.txt", settings)
    with pytest.raises(assets.AssetError):
        assets.token_path("nope:x", settings)


def test_routes(xp):
    settings, root, lib = xp
    found, packs = routes.dispatch("GET", "/api/library/packs", {}, {}, settings)
    assert found and len(packs["packs"]) == 2
    found, result = routes.dispatch("GET", "/api/library/search", {"q": ["house"]}, {}, settings)
    assert result["items"][0]["path"] == "test/house_a.fac"
    found, data = routes.dispatch("GET", "/api/asset", {"ref": ["lib:lib/test/box.obj"]}, {}, settings)
    assert data["type"] == "obj" and data["libraries"] == ["L - Test Lib"]
    found, (body, ctype) = routes.dispatch("GET", "/api/texture", {"t": [data["textures"]["main"]]}, {}, settings)
    assert ctype == "image/png" and body[:4] == b"\x89PNG"
    job = Job.create("routes", ZONE, settings)
    item = {"path": "lib/test/box.obj", "lon": 144.961, "lat": -37.81, "heading": 12}
    found, saved = routes.dispatch("POST", "/api/jobs/routes/placements", {}, {"items": [item]}, settings)
    assert saved["items"][0]["heading"] == 12 and (job.root / "placements.json").is_file()
    found, models = routes.dispatch("GET", "/api/models", {"owner": ["job:routes"]}, {}, settings)
    assert models["files"] == []
    with pytest.raises(ValueError):
        routes.dispatch("GET", "/api/asset", {"ref": ["job:routes:../../x.obj"]}, {}, settings)
    assert routes.dispatch("GET", "/api/unknown", {}, {}, settings) == (False, None)


def test_airport_routes(xp, tmp_path):
    settings, root, lib = xp
    settings["paths"]["airports_dir"] = str(tmp_path / "airports")
    found, r = routes.dispatch("POST", "/api/airports", {},
                               {"name": "YNEW", "source": {"type": "blank", "icao": "YNEW", "name": "New",
                                                            "lon": 144.9, "lat": -37.8}}, settings)
    assert r["name"] == "YNEW"
    found, d = routes.dispatch("GET", "/api/airports/YNEW", {}, {}, settings)
    assert d["summary"]["errors"] == 0 and len(d["data"]["runways"]) == 1
    data = d["data"]
    data["runways"][0]["ends"][1]["name"] = "26"
    found, saved = routes.dispatch("POST", "/api/airports/YNEW", {}, {"data": data}, settings)
    assert saved["summary"]["errors"] == 1
    found, hist = routes.dispatch("GET", "/api/airports/YNEW/history", {}, {}, settings)
    assert len(hist["files"]) == 1
    found, restored = routes.dispatch("POST", "/api/airports/YNEW/restore", {}, {"file": hist["files"][0]}, settings)
    assert restored["summary"]["errors"] == 0
    with pytest.raises(ValueError):
        routes.dispatch("POST", "/api/airports/YNEW/restore", {}, {"file": "../x.json"}, settings)
    found, enums = routes.dispatch("GET", "/api/airports/enums", {}, {}, settings)
    assert ["tie_down"] == [x for x in enums["startup_types"] if x == "tie_down"]
    assert json.dumps(enums)
