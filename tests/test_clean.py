"""Tests for clean: small synthetic datasets check each cleaning rule (ported from melbourne-cbd-xp12 tests/test_clean_buildings.py)."""

import pytest
import shapely
from pyproj import Transformer
from shapely.geometry import shape

from quickscene import clean as cb
from quickscene.config import load_settings
from quickscene.geo import utm_crs

SETTINGS = load_settings()
# Pin defaults so config/local.yaml cannot affect the tests (local.yaml only holds paths)
SETTINGS["heights"]["default_height_m"] = 15.0
SETTINGS["heights"]["level_height_m"] = 3.5

# Origin of the synthetic data; 1e-4 degrees is about 9-11 m
LON0, LAT0 = 144.96, -37.815
METRIC_CRS = utm_crs(LON0, LAT0)
ZONE = {
    "type": "Polygon",
    "coordinates": [[[LON0, LAT0], [LON0 + 0.01, LAT0], [LON0 + 0.01, LAT0 + 0.01], [LON0, LAT0 + 0.01], [LON0, LAT0]]],
}
ZONES = [{"type": "Feature", "properties": {"id": "test_zone"}, "geometry": ZONE}]


class OsmBuilder:
    """Builds Overpass-JSON-style elements."""

    def __init__(self):
        self.elements = []
        self._next = 1

    def _id(self):
        self._next += 1
        return self._next

    def rect(self, x, y, w, h, tags, element_id=None):
        """Rectangular way with its lower-left corner at (LON0 + x*1e-4, LAT0 + y*1e-4), w x h units of 1e-4 degrees."""
        corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        node_ids = []
        for cx, cy in corners:
            node_id = self._id()
            self.elements.append({"type": "node", "id": node_id, "lon": LON0 + cx * 1e-4, "lat": LAT0 + cy * 1e-4})
            node_ids.append(node_id)
        way_id = element_id or self._id()
        self.elements.append({"type": "way", "id": way_id, "nodes": node_ids + [node_ids[0]], "tags": tags})
        return way_id

    def relation(self, members, tags, element_id):
        """members is [(role, way_id)]."""
        self.elements.append(
            {
                "type": "relation",
                "id": element_id,
                "members": [{"type": "way", "ref": ref, "role": role} for role, ref in members],
                "tags": tags,
            }
        )

    def line(self, points, tags):
        """Open way (a road); points is [(x, y), ...] in the same units as rect."""
        node_ids = []
        for cx, cy in points:
            node_id = self._id()
            self.elements.append({"type": "node", "id": node_id, "lon": LON0 + cx * 1e-4, "lat": LAT0 + cy * 1e-4})
            node_ids.append(node_id)
        self.elements.append({"type": "way", "id": self._id(), "nodes": node_ids, "tags": tags})

    def data(self):
        return {"elements": self.elements}


def run(builder, roads=None):
    features, report = cb.clean(builder.data(), ZONES, SETTINGS, roads.data() if roads else None)
    return {f["properties"]["id"]: f["properties"] for f in features}, report


def run_features(builder, roads):
    return cb.clean(builder.data(), ZONES, SETTINGS, roads.data())


# ---------- tag parsing ----------


@pytest.mark.parametrize(
    "value, expected",
    [("45", 45.0), ("45 m", 45.0), ("45m", 45.0), ("45,5", 45.5), ("100 ft", 30.48), ("abc", None), (None, None)],
)
def test_parse_height(value, expected):
    result = cb.parse_height(value)
    assert result == pytest.approx(expected) if expected is not None else result is None


@pytest.mark.parametrize("value, expected", [("12", 12.0), ("3;4", 4.0), ("2.5", 2.5), ("x", None), ("0", None)])
def test_parse_levels(value, expected):
    assert cb.parse_levels(value) == expected


# ---------- rules ----------


def test_height_priority_and_default():
    b = OsmBuilder()
    b.rect(5, 5, 3, 3, {"building": "yes", "height": "40 m", "building:levels": "5"}, element_id=101)
    b.rect(15, 5, 3, 3, {"building": "yes", "building:levels": "4"}, element_id=102)
    b.rect(25, 5, 3, 3, {"building": "yes"}, element_id=103)
    b.rect(35, 5, 3, 3, {"building": "yes", "height": "400"}, element_id=104)
    blocks, report = run(b)
    assert (blocks["way/101"]["height_m"], blocks["way/101"]["height_source"]) == (40.0, "osm_height")
    assert (blocks["way/102"]["height_m"], blocks["way/102"]["height_source"]) == (14.0, "osm_levels")
    assert blocks["way/103"]["height_source"] == "default"
    assert blocks["way/103"]["height_m"] == SETTINGS["heights"]["default_height_m"]
    # A height above 350 m is invalid and falls back to the default
    assert blocks["way/104"]["height_source"] == "default"
    assert report["stats"]["out_of_range_height_tag"] == 1


def test_small_structure_default_height():
    """A small standalone structure without height data (about 78 m²) gets the low default; a small building with levels does not."""
    b = OsmBuilder()
    b.rect(5, 5, 1, 0.8, {"building": "yes"}, element_id=111)
    b.rect(15, 5, 1, 0.8, {"building": "yes", "building:levels": "3"}, element_id=112)
    blocks, report = run(b)
    heights = SETTINGS["heights"]
    assert (blocks["way/111"]["height_m"], blocks["way/111"]["height_source"]) == (
        heights["small_structure_height_m"], "default_small")
    assert blocks["way/112"]["height_source"] == "osm_levels"
    assert report["stats"]["small_structure_default_height"] == 1


def test_zone_roof_and_small_filters():
    b = OsmBuilder()
    b.rect(5, 5, 3, 3, {"building": "yes"}, element_id=201)
    b.rect(-20, 5, 3, 3, {"building": "yes"}, element_id=202)  # outside the area
    b.rect(15, 5, 3, 3, {"building": "roof"}, element_id=203)
    b.rect(25, 5, 0.2, 0.2, {"building": "shed"}, element_id=204)  # about 2 m x 2 m
    blocks, report = run(b)
    assert set(blocks) == {"way/201"}
    assert report["stats"]["outside_zone"] == 1
    assert report["dropped"]["building=roof"] == 1
    assert report["dropped"]["too_small"] == 1


def test_parts_replace_outline_and_keep_podium():
    b = OsmBuilder()
    b.rect(10, 10, 10, 10, {"building": "office", "name": "Tower", "height": "20"}, element_id=301)
    b.rect(10, 10, 4, 4, {"building:part": "yes", "height": "200 m"}, element_id=302)
    b.rect(16, 16, 4, 4, {"building:part": "yes"}, element_id=303)  # no height: inherits the building's
    blocks, _ = run(b)
    assert blocks["way/302"]["kind"] == "part"
    assert (blocks["way/302"]["height_m"], blocks["way/302"]["height_source"]) == (200.0, "osm_part_height")
    assert blocks["way/303"]["height_m"] == 20.0
    remainder = [p for p in blocks.values() if p["kind"] == "remainder"]
    assert remainder and all(p["building_id"] == "way/301" and p["height_m"] == 20.0 for p in remainder)
    assert "way/301" not in blocks  # the outline is replaced by its parts and remainder


def test_part_prefers_enclosing_outline_on_tie():
    """When a part coincides with a small outline and both sit inside a larger outline, the part goes to the larger one and the small outline is dropped as a duplicate."""
    b = OsmBuilder()
    b.rect(10, 10, 10, 10, {"building": "yes", "height": "20"}, element_id=401)
    b.rect(12, 12, 3, 3, {"building": "yes"}, element_id=402)
    b.rect(12, 12, 3, 3, {"building:part": "yes", "height": "250"}, element_id=403)
    blocks, report = run(b)
    assert blocks["way/403"]["building_id"] == "way/401"
    assert "way/402" not in blocks
    assert report["dropped"]["duplicate"] == 1


def test_duplicate_keeps_better_height_source():
    b = OsmBuilder()
    b.rect(5, 5, 3, 3, {"building": "yes"}, element_id=501)
    b.rect(5, 5, 3, 3, {"building": "yes", "height": "30"}, element_id=502)
    blocks, _ = run(b)
    assert set(blocks) == {"way/502"}


# ---------- dropped tags and road conflicts ----------


def test_shelter_dropped():
    b = OsmBuilder()
    b.rect(5, 5, 1, 2, {"building": "yes", "amenity": "shelter", "shelter_type": "public_transport"}, element_id=1201)
    b.rect(15, 5, 3, 3, {"building": "yes"}, element_id=1202)
    blocks, report = run(b)
    assert set(blocks) == {"way/1202"}
    assert report["dropped"]["amenity=shelter"] == 1


def test_bridge_tower_dropped_but_other_towers_kept():
    b = OsmBuilder()
    b.rect(5, 5, 1, 1, {"building": "yes", "man_made": "tower", "tower:type": "bridge", "height": "140"}, element_id=1701)
    b.rect(15, 5, 3, 3, {"building": "yes", "man_made": "tower", "name": "Spire", "height": "162"}, element_id=1702)
    blocks, report = run(b)
    assert set(blocks) == {"way/1702"}
    assert report["dropped"]["tower:type=bridge"] == 1


def test_building_relation_uses_untagged_outline():
    """type=building relation: the outline member is untagged and the building tags sit on the relation (e.g. 839 Collins Street)."""
    b = OsmBuilder()
    outline = b.rect(10, 10, 4, 4, {}, element_id=1801)
    part = b.rect(10, 10, 2, 2, {}, element_id=1802)
    b.relation([("outline", outline), ("part", part)], {"type": "building", "building": "yes", "height": "90"}, 1803)
    blocks, _ = run(b)
    assert set(blocks) == {"relation/1803"}
    assert blocks["relation/1803"]["height_m"] == 90.0
    assert blocks["relation/1803"]["area_m2"] > 0.95 * 4 * 4 * 8.8 * 11.1


def test_block_mostly_on_road_dropped():
    b = OsmBuilder()
    b.rect(10, 9.8, 1, 0.4, {"building": "yes"}, element_id=1301)  # about 9 m x 4.4 m, sitting in the middle of the road
    roads = OsmBuilder()
    roads.line([(0, 10), (30, 10)], {"highway": "primary"})
    blocks, report = run(b, roads)
    assert blocks == {}
    assert report["dropped"]["on_road"] == 1


def test_road_through_building_is_cut_out():
    b = OsmBuilder()
    b.rect(10, 10, 4, 4, {"building": "yes", "height": "30"}, element_id=1401)  # about 35 m x 44 m
    roads = OsmBuilder()
    roads.line([(12, 0), (12, 30)], {"highway": "tertiary"})  # runs north-south through the middle of the building
    features, report = run_features(b, roads)
    assert report["stats"]["road_crossed_blocks"] == 1
    assert len(features) == 2  # cut by the road into east and west halves
    to_metric = Transformer.from_crs("EPSG:4326", METRIC_CRS, always_xy=True).transform
    road = shapely.transform(
        shapely.LineString([(LON0 + 12e-4, LAT0), (LON0 + 12e-4, LAT0 + 30e-4)]), to_metric, interleaved=False
    )
    half_width = SETTINGS["roads"]["half_width_m"]["tertiary"]
    for f in features:
        piece = shapely.transform(shape(f["geometry"]), to_metric, interleaved=False)
        assert piece.distance(road) >= half_width - 0.05


def test_road_under_elevated_building_not_cut():
    """A building on layer=1 over a ground-level road (e.g. a driveway under a hotel) is not a conflict."""
    b = OsmBuilder()
    b.rect(10, 10, 4, 4, {"building": "yes", "layer": "1"}, element_id=1601)
    roads = OsmBuilder()
    roads.line([(12, 0), (12, 30)], {"highway": "unclassified"})
    blocks, report = run(b, roads)
    assert set(blocks) == {"way/1601"}
    assert report["stats"].get("road_crossed_blocks", 0) == 0


@pytest.mark.parametrize(
    "tags",
    [
        {"highway": "service", "tunnel": "building_passage"},
        {"highway": "service", "covered": "yes"},
        {"highway": "service", "service": "parking_aisle"},
        {"highway": "service", "service": "driveway"},
        {"highway": "footway"},
    ],
)
def test_passages_and_footways_not_treated_as_conflicts(tags):
    b = OsmBuilder()
    b.rect(10, 10, 4, 4, {"building": "yes"}, element_id=1501)
    roads = OsmBuilder()
    roads.line([(12, 0), (12, 30)], tags)
    blocks, report = run(b, roads)
    assert set(blocks) == {"way/1501"}
    assert report["stats"].get("road_crossed_blocks", 0) == 0


# ---------- overlap removal ----------


def test_overlapping_buildings_tallest_wins():
    """Two partly overlapping outlines (duplicated in OSM): the taller stays whole, the lower keeps only its uncovered part."""
    b = OsmBuilder()
    b.rect(10, 10, 4, 4, {"building": "yes", "height": "100"}, element_id=1901)
    b.rect(12, 10, 4, 4, {"building": "yes", "height": "20"}, element_id=1902)
    features, report = cb.clean(b.data(), ZONES, SETTINGS)
    to_metric = Transformer.from_crs("EPSG:4326", METRIC_CRS, always_xy=True).transform
    geoms = {f["properties"]["id"]: shapely.transform(shape(f["geometry"]), to_metric, interleaved=False) for f in features}
    assert set(geoms) == {"way/1901", "way/1902"}
    assert geoms["way/1901"].area == pytest.approx(4 * 4 * 8.8 * 11.1, rel=0.05)
    assert geoms["way/1902"].area == pytest.approx(geoms["way/1901"].area / 2, rel=0.05)
    assert geoms["way/1901"].intersection(geoms["way/1902"]).area < 0.01
    assert report["stats"]["overlapping_pairs"] == 0


def test_floating_wider_part_does_not_duplicate_tower():
    """A floating part wider than the tower (e.g. an overhanging upper level) must not overlap the tower once extruded from the ground."""
    b = OsmBuilder()
    b.rect(10, 10, 6, 6, {"building": "yes", "height": "30"}, element_id=2001)
    b.rect(12, 12, 2, 2, {"building:part": "yes", "height": "317", "min_height": "11"}, element_id=2002)
    b.rect(11, 11, 4, 4, {"building:part": "yes", "height": "221", "min_height": "208"}, element_id=2003)
    features, report = cb.clean(b.data(), ZONES, SETTINGS)
    to_metric = Transformer.from_crs("EPSG:4326", METRIC_CRS, always_xy=True).transform
    geoms = [shapely.transform(shape(f["geometry"]), to_metric, interleaved=False) for f in features]
    assert report["stats"]["overlapping_pairs"] == 0
    assert all(a.intersection(c).area < 0.01 for i, a in enumerate(geoms) for c in geoms[i + 1 :])
    tower = [f for f in features if f["properties"]["id"] == "way/2002"]
    assert len(tower) == 1 and tower[0]["properties"]["height_m"] == 317.0
