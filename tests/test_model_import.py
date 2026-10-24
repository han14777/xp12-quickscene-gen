"""Model import: Wavefront .obj + .mtl and glTF converted to OBJ8 with a texture atlas."""

import base64
import json
import struct

import numpy as np
import pytest
from PIL import Image

from quickscene import model_import
from quickscene.obj8 import read_obj8

CUBE_OBJ = """mtllib cube.mtl
v 0 0 0
v 2 0 0
v 2 0 2
v 0 0 2
v 0 2 0
v 2 2 0
v 2 2 2
v 0 2 2
vt 0 0
vt 1 0
vt 1 1
vt 0 1
usemtl brick
f 1/1 2/2 6/3 5/4
f 2/1 3/2 7/3 6/4
usemtl roof
f 5 6 7 8
"""


def _wavefront(tmp_path):
    (tmp_path / "cube.obj").write_text(CUBE_OBJ, encoding="utf-8")
    (tmp_path / "cube.mtl").write_text("newmtl brick\nKd 1 1 1\nmap_Kd tex/brick.png\nnewmtl roof\nKd 0.5 0.1 0.1\n",
                                       encoding="utf-8")
    (tmp_path / "tex").mkdir()
    Image.new("RGBA", (64, 32), (200, 80, 40, 255)).save(tmp_path / "tex" / "brick.png")
    return tmp_path / "cube.obj"


def test_detects_obj8(tmp_path):
    (tmp_path / "a.obj").write_text("I\n800\nOBJ\n", encoding="latin-1")
    (tmp_path / "b.obj").write_text("# Blender\nv 0 0 0\n", encoding="latin-1")
    assert model_import.is_obj8(tmp_path / "a.obj")
    assert not model_import.is_obj8(tmp_path / "b.obj")


def test_wavefront_converted_with_atlas(tmp_path):
    out = tmp_path / "models"
    r = model_import.import_any(out, _wavefront(tmp_path))
    assert r["converted"] and r["path"] == "cube/cube.obj"
    assert r["triangles"] == 6 and r["materials"] == 2
    obj = read_obj8(out / "cube" / "cube.obj")
    assert obj["textures"]["TEXTURE"] == "cube_atlas.png"
    assert obj["point_counts"] == (18, 0, 0, 18) and obj["tris"] == [(0, 18)]
    atlas = Image.open(out / "cube" / "cube_atlas.png")
    assert atlas.width == atlas.height and atlas.width & (atlas.width - 1) == 0
    v = np.array(obj["vertices"])
    assert v[:, 0].max() == 2 and v[:, 1].max() == 2  # Y up kept
    # OBJ8 front faces are clockwise: the face normal from the winding points against the vertex normal
    a, b, c = v[0, :3], v[1, :3], v[2, :3]
    assert np.dot(np.cross(b - a, c - a), v[0, 3:6]) < 0
    # texture coordinates of the brick faces fall inside the atlas
    assert (v[:, 6:8] >= 0).all() and (v[:, 6:8] <= 1).all()


def test_scale_and_z_up(tmp_path):
    r = model_import.import_any(tmp_path / "m", _wavefront(tmp_path), scale=0.5, up="z", name="z up!")
    assert r["path"] == "z_up/z_up.obj"
    v = np.array(read_obj8(tmp_path / "m" / "z_up" / "z_up.obj")["vertices"])
    # source z (0..2) became height y, scaled by 0.5; source y became -z
    assert v[:, 1].max() == pytest.approx(1.0) and v[:, 2].min() == pytest.approx(-1.0)


def test_rejects_unknown_format(tmp_path):
    (tmp_path / "x.fbx").write_bytes(b"x")
    with pytest.raises(ValueError):
        model_import.import_any(tmp_path / "m", tmp_path / "x.fbx")


def _glb(path):
    pos = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32).tobytes()
    idx = np.array([0, 1, 2], np.uint16).tobytes() + b"\0\0"
    binary = pos + idx
    gltf = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
            "nodes": [{"mesh": 0, "translation": [10, 0, 0]}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}],
            "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [0, 1, 0, 1]}}],
            "buffers": [{"byteLength": len(binary)}],
            "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 36},
                            {"buffer": 0, "byteOffset": 36, "byteLength": 6}],
            "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
                          {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR"}]}
    js = json.dumps(gltf).encode()
    js += b" " * (-len(js) % 4)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(binary), 0x004E4942) + binary
    path.write_bytes(b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body)


def test_glb_node_transform_and_colour(tmp_path):
    _glb(tmp_path / "tri.glb")
    r = model_import.import_any(tmp_path / "m", tmp_path / "tri.glb")
    obj = read_obj8(tmp_path / "m" / "tri" / "tri.obj")
    v = np.array(obj["vertices"])
    assert r["triangles"] == 1 and v[:, 0].min() == 10  # node translation applied
    atlas = Image.open(tmp_path / "m" / "tri" / "tri_atlas.png").convert("RGBA")
    assert atlas.getpixel((2, 2)) == (0, 255, 0, 255)
