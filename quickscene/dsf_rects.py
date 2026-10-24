"""DSF rectangle exclusions: split a polygon into lon/lat axis-aligned rectangles.

Tested in XP12 (melbourne-cbd-xp12, 2026-10-14): polygon exclusion zones have no effect, rectangular ones work.
"""

import math

import shapely.prepared
from shapely.geometry import box, shape


def exclusion_rects(zone_geometry: dict, cell_m: float) -> list:
    """Split the area into lon/lat axis-aligned rectangles [(west, south, east, north)] for rectangle exclusions.

    The area is covered with a grid of cell_m squares (degree steps derived from latitude); a cell is excluded
    when at least half of it lies inside the area. Consecutive cells in a row merge into a span, and vertically
    adjacent spans with the same start and end merge into one rectangle.
    """
    polygon = shape(zone_geometry)
    west, south, east, north = polygon.bounds
    dlat = cell_m / 111_320.0
    dlon = cell_m / (111_320.0 * math.cos(math.radians((south + north) / 2)))
    cols = math.ceil((east - west) / dlon)
    rows = math.ceil((north - south) / dlat)

    prepared = shapely.prepared.prep(polygon)
    row_spans = []
    for j in range(rows):
        y0, y1 = south + j * dlat, south + (j + 1) * dlat
        band = polygon.intersection(box(west, y0, east, y1))
        spans, start = [], None
        for i in range(cols + 1):
            inside = False
            if i < cols and not band.is_empty:
                cell = box(west + i * dlon, y0, west + (i + 1) * dlon, y1)
                if prepared.contains(cell):
                    inside = True
                elif prepared.intersects(cell):
                    inside = band.intersection(cell).area >= 0.5 * cell.area
            if inside and start is None:
                start = i
            elif not inside and start is not None:
                spans.append((start, i))
                start = None
        row_spans.append(spans)

    rects, open_rects = [], {}
    for j, spans in enumerate(row_spans + [[]]):
        current = set(spans)
        for span in list(open_rects):
            if span not in current:
                j0 = open_rects.pop(span)
                rects.append((west + span[0] * dlon, south + j0 * dlat, west + span[1] * dlon, south + j * dlat))
        for span in spans:
            open_rects.setdefault(span, j)
    return rects
