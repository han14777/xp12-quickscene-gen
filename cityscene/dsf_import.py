"""Read the scenery items of an existing DSF (from any pack: WED exports, Gateway airports, other tools) back into
editable placed items (placements.py), so existing work can be imported, refined and re-exported.

DSF text (DSFTool --dsf2text) is read as WED writes it: OBJECT_DEF / POLYGON_DEF tables, OBJECT <def> <lon> <lat>
<heading>, and BEGIN_POLYGON <def> <param> <coords per point> ... BEGIN_WINDING / POLYGON_POINT / END_WINDING ...
END_POLYGON. The param is decoded per asset type (placements.py). Road networks, exclusions and raster data are not
items and are skipped (counted in the report); points keep
their extra per-point values (wall types, UV, Bezier control points). Paths inside the pack (not library paths) are reported as "local" so the caller can copy the
files.
"""

import os
import subprocess
from collections import Counter
from pathlib import Path

from cityscene import placements


def parse_dsf_text(text: str, bounds=None) -> tuple[list, dict]:
    """(items, report). bounds = (west, south, east, north) keeps only items with a point inside."""
    objects_def, polygons_def = [], []
    items, stats = [], Counter()
    poly = None
    # UV-mapped draped polygons and facades with wall types keep their extra per-point values (placements.py)
    ring = None

    def inside(lon, lat):
        return bounds is None or (bounds[0] <= lon <= bounds[2] and bounds[1] <= lat <= bounds[3])

    lines = text.splitlines()
    # definitions first: the Gateway's DSF text may list them after the items using them
    for line in lines:
        if line.startswith("OBJECT_DEF "):
            objects_def.append(line.split(None, 1)[1].strip())
        elif line.startswith("POLYGON_DEF "):
            polygons_def.append(line.split(None, 1)[1].strip())
    for line in lines:
        words = line.split()
        if not words:
            continue
        key = words[0]
        if key in ("OBJECT_DEF", "POLYGON_DEF"):
            continue
        elif key in ("OBJECT", "OBJECT_MSL", "OBJECT_AGL") and len(words) >= 5:
            lon, lat = float(words[2]), float(words[3])
            if not inside(lon, lat):
                stats["outside"] += 1
                continue
            heading = float(words[4])
            item = {"path": objects_def[int(words[1])], "lon": lon, "lat": lat, "heading": heading % 360}
            if key == "OBJECT_AGL" and len(words) > 5:
                item["agl"] = float(words[5])
            elif key == "OBJECT_MSL":
                stats["msl_heights_dropped"] += 1  # OBJECT_MSL sinks in XP12; the object is put on the ground
            items.append(item)
            stats["objects"] += 1
        elif key == "BEGIN_POLYGON":
            poly = {"def": polygons_def[int(words[1])], "param": int(words[2]), "rings": []}
        elif key == "BEGIN_WINDING" and poly is not None:
            ring = []
        elif key == "POLYGON_POINT" and ring is not None:
            ring.append([float(v) for v in words[1:5]])
        elif key == "END_WINDING" and poly is not None and ring is not None:
            poly["rings"].append(ring)
            ring = None
        elif key == "END_POLYGON" and poly is not None:
            item = _polygon_item(poly, stats)
            if item and any(inside(p[0], p[1]) for r in poly["rings"] for p in r):
                items.append(item)
            elif item:
                stats["outside"] += 1
            poly = None
        elif key.startswith("BEGIN_SEGMENT") or key == "NETWORK_DEF":
            stats["network_skipped"] += key != "NETWORK_DEF"
        elif key == "PROPERTY" and len(words) > 1 and words[1].startswith("sim/exclude_"):
            stats["exclusions_skipped"] += 1
    valid = []
    for item in items:
        try:
            valid.append(placements.validate_item(item))
        except ValueError:
            stats["invalid"] += 1
    stats["imported"] = len(valid)
    return valid, dict(stats)


def _polygon_item(poly: dict, stats: Counter):
    path, param, rings = poly["def"], poly["param"], [r for r in poly["rings"] if r]
    ext = placements.ext_of(path)
    if not rings or ext not in placements.KINDS_BY_EXT:
        stats["polygons_skipped"] += 1
        return None
    if ext == ".fac":
        stats["facades"] += 1
        return {"path": path, "rings": rings[:1], "height": max(1, param)}
    if ext == ".for":
        stats["forests"] += 1
        fill = {0: "area", 1: "line", 2: "points"}.get(param // 256, "area")
        return {"path": path, "rings": rings, "density": param % 256, "fill": fill}
    if ext == ".pol":
        stats["draped_polygons"] += 1
        return {"path": path, "rings": rings[:1], "heading": 0 if param == 65535 else param}
    if ext == ".lin":
        stats["lines"] += 1
        return {"path": path, "points": rings[0], "closed": param == 1}
    if ext == ".str":
        stats["strings"] += 1
        return {"path": path, "points": rings[0], "spacing": max(1, param)}
    stats["polygons_skipped"] += 1
    return None


def decompile(dsftool: Path, dsf: Path, out_txt: Path) -> str:
    out_txt.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([str(dsftool), "--dsf2text", str(dsf), str(out_txt)], capture_output=True, text=True,
                            errors="replace")
    if result.returncode != 0 or not out_txt.is_file():
        raise RuntimeError(f"DSFTool could not read {dsf.name}: {result.stdout}{result.stderr}")
    return out_txt.read_text(encoding="utf-8", errors="replace")


def pack_items(pack: Path, dsftool: Path, work_dir: Path, bounds=None) -> tuple[list, dict]:
    """All items of every DSF in a pack (optionally only inside bounds). Local asset paths (files inside the pack)
    are listed in report["local_assets"]."""
    items, report = [], Counter()
    earth = Path(pack) / "Earth nav data"
    for dsf in sorted(earth.rglob("*.dsf")) if earth.is_dir() else []:
        if bounds is not None:
            try:
                lat, lon = int(dsf.stem[:3]), int(dsf.stem[3:])
                if lon > bounds[2] or lon + 1 < bounds[0] or lat > bounds[3] or lat + 1 < bounds[1]:
                    continue
            except ValueError:
                pass
        text = decompile(dsftool, dsf, work_dir / f"{dsf.stem}.txt")
        got, stats = parse_dsf_text(text, bounds)
        items += got
        report.update(stats)
    local = sorted({i["path"] for i in items if (Path(pack) / i["path"]).is_file()})
    out = dict(report)
    out["local_assets"] = local
    return items, out


def copy_local_assets(pack: Path, items: list, models_dir: Path) -> int:
    """Copy assets that live inside the imported pack into the project's models folder (keeping their relative
    paths and the folders they sit in, so textures and sub-objects come along) and point the items at them."""
    import shutil
    pack = Path(pack)
    folders = set()
    for item in items:
        if (pack / item["path"]).is_file():
            folders.add(os.path.dirname(item["path"]))
    for folder in folders:
        src = pack / folder
        if src.resolve() == pack.resolve():
            for f in src.iterdir():
                if f.is_file():
                    (models_dir / "imported").mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, models_dir / "imported" / f.name)
        else:
            shutil.copytree(src, models_dir / folder, dirs_exist_ok=True)
    for item in items:
        if (pack / item["path"]).is_file():
            folder = os.path.dirname(item["path"])
            item["path"] = (f"imported/{item['path']}" if not folder else item["path"])
    return len(folders)
