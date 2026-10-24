"""BBBike extract helper (extract.bbbike.org): open the request form with the area filled in, then (when the user
asks) look the request up on the public status page.

BBBike has no API for submitting extracts and asks for fair use, so the user submits the form in the browser
(e-mail, or "nobody" to skip the e-mail and use the status page). This module only builds the form link and reads
the status page the browser would show:
- Form link parameters, as used by BBBike's own "map" links on the status page: sw_lng, sw_lat, ne_lng, ne_lat,
  format, city (the name of the extract) and coords ("lng,lat|lng,lat|..." for a polygon)
- Status page (download2.bbbike.org/osm/extract/): tables "confirmed" (waiting), "running" and "download" (ready,
  with a link to the file); rows are matched by the extract name, which gets a random suffix to be unique
"""

import html
import re

import requests
from shapely.geometry import Polygon
from urllib.parse import urlencode, urljoin

from quickscene.fetch import USER_AGENT
from quickscene.geo import Projector, metric_crs_for, zones_union

FORM_URL = "https://extract.bbbike.org/"
STATUS_URL = "https://download2.bbbike.org/osm/extract/"
FORMAT = "osm.pbf"
MAX_POINTS = 200
TABLE_STATES = {"confirmed": "waiting", "running": "running", "download": "ready"}


def area_polygon(zones: list, buffer_m: float) -> Polygon:
    """One polygon around the area, buffer_m wider (as for the Overpass bounding box): several shapes are merged,
    and when they stay apart their convex hull is used. Simplified to at most MAX_POINTS points."""
    projector = Projector(metric_crs_for(zones))
    shape_m = projector.to_metric(zones_union(zones)).buffer(buffer_m, join_style="mitre")
    if shape_m.geom_type != "Polygon":
        shape_m = shape_m.convex_hull
    shape_m = Polygon(shape_m.exterior)
    tolerance = 1.0
    while len(shape_m.exterior.coords) > MAX_POINTS + 1:
        shape_m = shape_m.simplify(tolerance, preserve_topology=True)
        tolerance *= 2
    return projector.to_wgs84(shape_m)


def form_url(zones: list, buffer_m: float, name: str) -> str:
    poly = area_polygon(zones, buffer_m)
    west, south, east, north = poly.bounds
    ring = list(poly.exterior.coords)[:-1]
    params = {
        "sw_lng": f"{west:.5f}", "sw_lat": f"{south:.5f}", "ne_lng": f"{east:.5f}", "ne_lat": f"{north:.5f}",
        "format": FORMAT, "city": name,
        "coords": "|".join(f"{lng:.5f},{lat:.5f}" for lng, lat in ring),
    }
    return FORM_URL + "?" + urlencode(params)


def parse_status(page: str) -> list:
    """Rows of the status page: [{"state", "name", "format", "link"}], link only for ready extracts."""
    rows = []
    for table_id, state in TABLE_STATES.items():
        match = re.search(rf'<table id="{table_id}">(.*?)</table>', page, re.S)
        if not match:
            continue
        for row in re.findall(r"<tr>(.*?)</tr>", match.group(1), re.S):
            name = re.search(r'<span title="([^"]*)"', row)
            if not name:
                continue
            fmt = re.search(r'class="format_([^"]+)"', row)
            link = re.search(r'<a title="[^"]*" href="([^"]+)">download</a>', row)
            rows.append({"state": state, "name": html.unescape(name.group(1)),
                         "format": fmt.group(1) if fmt else None,
                         "link": urljoin(STATUS_URL, html.unescape(link.group(1))) if link else None})
    return rows


def status(name: str) -> dict:
    """{"state": waiting | running | ready | not_found, "link"} of the extract with this name (PBF only)."""
    response = requests.get(STATUS_URL, params={"date": "24h"}, headers={"User-Agent": USER_AGENT}, timeout=(10, 60))
    response.raise_for_status()
    found = [r for r in parse_status(response.text) if r["name"] == name and r["format"] in (None, "osm_pbf")]
    for state in ("ready", "running", "waiting"):
        for row in found:
            if row["state"] == state:
                return {"state": state, "link": row["link"]}
    return {"state": "not_found", "link": None}
