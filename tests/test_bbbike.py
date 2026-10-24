"""BBBike helper: form link carries the area polygon; the status page is parsed into waiting / running / ready."""

from urllib.parse import parse_qs, urlparse

from shapely.geometry import Point, Polygon, mapping, shape

from quickscene import bbbike

from test_clean import LAT0, LON0

TRIANGLE = {"type": "Feature", "properties": {"id": "zone_1"}, "geometry": mapping(
    Polygon([(LON0, LAT0), (LON0 + 0.01, LAT0), (LON0 + 0.005, LAT0 + 0.01)]))}

STATUS_PAGE = """
<table id="confirmed"><tbody>
<tr><td><span title="cityscene a 1111">cityscene a 1111</span></td><td><span class="format_osm_pbf">PBF</span></td>
<td>-</td><td>-</td><td><a class="polygon1" href="x">map</a></td></tr>
</tbody></table>
<table id="running"><tbody>
<tr><td><span title="cityscene b 2222">cityscene b 2222</span></td><td><span class="format_osm_pbf">PBF</span></td>
<td>-</td><td>-</td><td>map</td></tr>
</tbody></table>
<table id="download"><tbody>
<tr><td><span title="cityscene c 3333">cityscene c 3333</span></td><td><span class="format_osm_pbf">PBF</span></td>
<td>1.2 MB</td><td><a title="Tue, 20 Oct 2026 19:25:08 GMT" href="/osm/extract/planet_138.5,-35_138.6,-34.9.osm.pbf">download</a></td>
<td>map</td></tr>
<tr><td><span title="Tom &amp; Jerry">Tom &amp; Jerry</span></td><td><span class="format_shp_zip">Shapefile</span></td>
<td>1 MB</td><td><a title="t" href="/osm/extract/x.shp.zip">download</a></td><td>map</td></tr>
</tbody></table>
"""


def test_form_url_carries_polygon_around_the_area():
    url = bbbike.form_url([TRIANGLE], 100, "cityscene test 1a2b")
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert q["format"] == "osm.pbf" and q["city"] == "cityscene test 1a2b"
    ring = [tuple(map(float, p.split(","))) for p in q["coords"].split("|")]
    poly = Polygon(ring)
    area = shape(TRIANGLE["geometry"])
    assert poly.is_valid and poly.contains(area)
    # Close to the triangle (100 m margin), not its bounding box: the box corner above the apex stays outside
    assert not poly.contains(Point(LON0 + 0.0005, LAT0 + 0.0095))
    west, south, east, north = poly.bounds
    assert float(q["sw_lng"]) <= west + 1e-5 and float(q["ne_lat"]) >= north - 1e-5


def test_separate_shapes_become_one_hull_and_points_are_limited():
    far = {"type": "Feature", "properties": {"id": "zone_2"},
           "geometry": mapping(Point(LON0 + 0.05, LAT0).buffer(0.002, quad_segs=200))}
    poly = bbbike.area_polygon([TRIANGLE, far], 100)
    assert poly.geom_type == "Polygon" and len(poly.exterior.coords) <= bbbike.MAX_POINTS + 1
    assert poly.contains(shape(far["geometry"]).centroid) and poly.contains(shape(TRIANGLE["geometry"]).centroid)


def test_status_page_rows():
    rows = bbbike.parse_status(STATUS_PAGE)
    assert [(r["state"], r["name"]) for r in rows] == [
        ("waiting", "cityscene a 1111"), ("running", "cityscene b 2222"), ("ready", "cityscene c 3333"),
        ("ready", "Tom & Jerry")]
    assert rows[2]["link"] == "https://download2.bbbike.org/osm/extract/planet_138.5,-35_138.6,-34.9.osm.pbf"
    assert rows[0]["link"] is None and rows[3]["format"] == "shp_zip"


def test_status_matches_name_and_pbf_only(monkeypatch):
    class Reply:
        text = STATUS_PAGE

        def raise_for_status(self):
            pass

    monkeypatch.setattr(bbbike.requests, "get", lambda *a, **k: Reply())
    assert bbbike.status("cityscene c 3333")["state"] == "ready"
    assert bbbike.status("cityscene b 2222") == {"state": "running", "link": None}
    assert bbbike.status("Tom & Jerry")["state"] == "not_found"  # not a PBF
    assert bbbike.status("nothing")["state"] == "not_found"
