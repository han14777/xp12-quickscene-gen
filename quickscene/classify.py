"""Step 3, classify: categorise buildings by height and use, and assign an X-Plane 12 default facade to each block.

Ported from melbourne-cbd-xp12 pipeline/03_classify.py (without heritage register matching):
1. Categories are per building (all blocks with the same building_id agree), using the tallest block:
   - building in heritage_building_values (churches etc.), or an OSM heritage/historic tag below the
     high-rise threshold -> heritage
   - height >= high_rise_min_m -> high_rise; >= mid_rise_min_m -> mid_rise; otherwise low_rise
2. One facade per building where possible: a facade from the preferred styles whose floor range
   (FLOORS_MIN/MAX) covers every block. Otherwise blocks are chosen one by one, falling back to any style
   (style_fallback) and finally to the closest floor range (height_clamped)
3. Choices are stable (crc32 of building_id): the same on every run, varied between buildings
4. Per-building edits (overrides.py) apply first: height_m scales the building's blocks, style replaces the
   preferred styles, facade (any library .fac) is used as is for every block; every block carries "edit" (None / edit / delete / model) for build_dsf and the map preview

Output: processed/buildings_classified.geojson and classify_report.json
"""

import json
import zlib
from collections import Counter, defaultdict

from quickscene import overrides
from quickscene.log import get_logger

log = get_logger("classify")


def building_category(blocks: list, cfg: dict) -> tuple[str, str]:
    """Returns (category, reason). blocks are the properties of all blocks of one building."""
    height = max(b["height_m"] for b in blocks)
    if any(b["building"] in cfg["heritage_building_values"] for b in blocks):
        return "low_rise", "heritage_building_type"
    if height < cfg["high_rise_min_m"] and any(b["heritage"] for b in blocks):
        return "low_rise", "heritage_osm"
    if height >= cfg["high_rise_min_m"]:
        return "high_rise", "height"
    if height >= cfg["mid_rise_min_m"]:
        return "mid_rise", "height"
    return "low_rise", "height"


def floors_for(height_m: float, floor_height_m: float) -> int:
    return max(1, round(height_m / floor_height_m))


def _fits(facade: dict, floors: int) -> bool:
    upper = facade["floors_max"] if facade["floors_max"] is not None else float("inf")
    return facade["floors_min"] <= floors <= upper


def _distance(facade: dict, floors: int) -> float:
    upper = facade["floors_max"] if facade["floors_max"] is not None else float("inf")
    return max(facade["floors_min"] - floors, floors - upper, 0)


def _pick(candidates: list, seed: str) -> dict:
    candidates = sorted(candidates, key=lambda f: f["path"])
    return candidates[zlib.crc32(seed.encode("utf-8")) % len(candidates)]


def building_facade(floor_list: list, styles: list, facades: list, seed: str) -> dict | None:
    """A facade from the preferred styles covering every block's floor count, or None."""
    for style in styles:
        candidates = [f for f in facades if f["style"] == style and all(_fits(f, n) for n in floor_list)]
        if candidates:
            return _pick(candidates, seed)
    return None


def choose_facade(floors: int, styles: list, facades: list, seed: str) -> tuple[dict, bool, bool]:
    """Pick a facade for one block; returns (facade, fell back to a non-preferred style, floors outside every facade's range)."""
    for style in styles:
        candidates = [f for f in facades if f["style"] == style and _fits(f, floors)]
        if candidates:
            return _pick(candidates, seed), False, False
    candidates = [f for f in facades if _fits(f, floors)]
    if candidates:
        return _pick(candidates, seed), True, False

    def preference(f):
        return (_distance(f, floors), styles.index(f["style"]) if f["style"] in styles else len(styles))

    best = min(preference(f) for f in facades)
    return _pick([f for f in facades if preference(f) == best], seed), True, True


def classify(features: list, settings: dict, edits: dict | None = None) -> tuple[list, dict]:
    cfg = settings["classification"]
    facades = settings["facades"]
    edits = edits or {}
    heights_edited = overrides.apply_heights(features, edits)

    by_building = defaultdict(list)
    for feature in features:
        by_building[feature["properties"]["building_id"]].append(feature["properties"])
    categories = {bid: building_category(blocks, cfg) for bid, blocks in by_building.items()}

    def styles_for(bid):
        if edits.get(bid, {}).get("style"):
            return [edits[bid]["style"]]
        category, reason = categories[bid]
        if reason.startswith("heritage") and "heritage" in cfg["styles"]:
            return cfg["styles"]["heritage"]
        return cfg["styles"][category]

    shared = {
        bid: building_facade([floors_for(b["height_m"], cfg["facade_floor_height_m"]) for b in blocks],
                             styles_for(bid), facades, bid)
        for bid, blocks in by_building.items()
    }

    stats = Counter()
    out = []
    for feature in features:
        p = feature["properties"]
        category, reason = categories[p["building_id"]]
        floors = floors_for(p["height_m"], cfg["facade_floor_height_m"])
        custom = edits.get(p["building_id"], {}).get("facade")
        if custom:
            facade, fallback, clamped = {"path": custom, "style": "custom"}, False, False
        elif shared[p["building_id"]] is not None:
            facade, fallback, clamped = shared[p["building_id"]], False, False
        else:
            facade, fallback, clamped = choose_facade(floors, styles_for(p["building_id"]), facades, p["building_id"])
        stats["style_fallback"] += fallback
        stats["height_clamped"] += clamped
        out.append({
            "type": "Feature",
            "geometry": feature["geometry"],
            "properties": {
                **p,
                "category": category,
                "category_reason": reason,
                "floors": floors,
                "facade": facade["path"],
                "facade_style": facade["style"],
                "style_fallback": fallback,
                "height_clamped": clamped,
                "edit": overrides.edit_kind(edits.get(p["building_id"])),
            },
        })

    report = {
        "blocks": len(out),
        "buildings_by_category": dict(Counter(c for c, _ in categories.values())),
        "heritage_buildings": dict(Counter(r for _, r in categories.values() if r.startswith("heritage"))),
        "buildings_single_facade": sum(v is not None for v in shared.values()),
        "blocks_by_style": dict(Counter(f["properties"]["facade_style"] for f in out)),
        "facade_usage": dict(Counter(f["properties"]["facade"].rsplit("/", 1)[-1] for f in out).most_common()),
        "style_fallback": stats["style_fallback"],
        "height_clamped": stats["height_clamped"],
        "edited_buildings": dict(Counter(overrides.edit_kind(e) for b, e in edits.items() if b in by_building)),
        "edited_heights": heights_edited,
        "edits_not_found": sorted(b for b in edits if b not in by_building),
    }
    return out, report


def run(job, settings: dict) -> dict:
    processed = job.processed_dir
    path = processed / "buildings_clean.geojson"
    if not path.exists():
        raise RuntimeError("No cleaned buildings; run clean first")
    with open(path, encoding="utf-8") as f:
        features = json.load(f)["features"]

    out, report = classify(features, settings, overrides.load(job))
    with open(processed / "buildings_classified.geojson", "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": out}, f, ensure_ascii=False)
    with open(processed / "classify_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    log.info("%d blocks; buildings by category %s; heritage %s", report["blocks"], report["buildings_by_category"],
             report["heritage_buildings"])
    log.info("Facade styles %s; fell back to a non-preferred style %d, floors outside every facade range %d",
             report["blocks_by_style"], report["style_fallback"], report["height_clamped"])
    if report["edited_buildings"] or report["edits_not_found"]:
        log.info("Edited buildings %s; edits whose building is no longer in the data: %d", report["edited_buildings"],
                 len(report["edits_not_found"]))
    return report
