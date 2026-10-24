"""Coordinates and tiles: everything is WGS84 (lon, lat); distances and areas are computed in the area's UTM zone."""

import math

import shapely
from pyproj import Transformer
from shapely.geometry import box, shape
from shapely.ops import unary_union


def utm_crs(lon: float, lat: float) -> str:
    """EPSG code of the WGS84 / UTM zone containing the point (326xx north, 327xx south)."""
    zone = min(60, int((lon + 180) // 6) + 1)
    return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"


def zones_union(zones: list):
    return unary_union([shape(z["geometry"]) for z in zones])


def metric_crs_for(zones: list) -> str:
    centroid = zones_union(zones).centroid
    return utm_crs(centroid.x, centroid.y)


class Projector:
    """WGS84 <-> projected coordinates (metres)."""

    def __init__(self, metric_crs: str):
        self.crs = metric_crs
        self._to_metric = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True).transform
        self._to_wgs84 = Transformer.from_crs(metric_crs, "EPSG:4326", always_xy=True).transform

    def to_metric(self, geom):
        return shapely.transform(geom, self._to_metric, interleaved=False)

    def to_wgs84(self, geom):
        return shapely.transform(geom, self._to_wgs84, interleaved=False)

    @property
    def to_wgs84_xy(self):
        """(xs, ys) -> (lons, lats), for point-wise conversion."""
        return self._to_wgs84


def area_km2(zones: list) -> float:
    projector = Projector(metric_crs_for(zones))
    return projector.to_metric(zones_union(zones)).area / 1e6


def tiles_for(geom) -> list:
    """1x1 degree DSF tiles [(lat, lon)] touched by the geometry (south-west corner, floored)."""
    west, south, east, north = geom.bounds
    tiles = []
    for lat in range(math.floor(south), math.floor(north) + 1):
        for lon in range(math.floor(west), math.floor(east) + 1):
            if geom.intersects(box(lon, lat, lon + 1, lat + 1)):
                tiles.append((lat, lon))
    return tiles


def tile_name(lat: int, lon: int) -> str:
    return f"{lat:+03d}{lon:+04d}"


def tile_dir(lat: int, lon: int) -> str:
    """The 10-degree DSF folder, floored (e.g. -38+144 -> -40+140)."""
    return tile_name(math.floor(lat / 10) * 10, math.floor(lon / 10) * 10)
