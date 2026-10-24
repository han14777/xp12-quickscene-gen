"""Step 1 alternative: read OSM data from a local file (.osm.pbf, .osm, .osm.bz2) instead of Overpass, e.g. a BBBike
or Geofabrik extract. The output has the same shape as the Overpass results in fetch.py, so clean is unchanged.

What is kept, inside the area's bounding box expanded by osm.bbox_buffer_m (as for Overpass):
- ways tagged building / building:part / highway with at least one node inside the box (Overpass also keeps ways
  that only cross it; the buffer covers that, and clean keeps buildings by centroid anyway)
- relations tagged building / building:part with a member way that has a node inside the box, plus those member
  ways (outline ways of building relations are often untagged)
- every node of the kept ways, with its coordinates

Two passes over the file with pyosmium: tagged ways and relations first, then the member ways the relations need.
A source given as an http(s) link (such as the link in a BBBike e-mail) is downloaded once into the job's raw
folder. Results are cached in raw/ and reused while the file and the bounding box stay the same.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from quickscene.fetch import USER_AGENT, kinds_for, query_bbox
from quickscene.log import get_logger

log = get_logger("fetch")

KINDS = {"buildings": ("building", "building:part"), "roads": ("highway",), "vegetation": ("natural", "landuse")}
VEGETATION = {("natural", "tree_row"), ("landuse", "forest"), ("natural", "wood")}


def wanted(kind: str, tags) -> bool:
    """Whether an element with these tags belongs to kind (vegetation keeps only the tags vegetation.py reads)."""
    if kind == "vegetation":
        return any(tags.get(k) == v for k, v in VEGETATION)
    return any(k in tags for k in KINDS[kind])


def _relation_kind(tags) -> str | None:
    if "building" in tags or "building:part" in tags:
        return "buildings"
    if wanted("vegetation", tags) and tags.get("type") == "multipolygon":
        return "vegetation"
    return None
SUFFIXES = (".pbf", ".osm", ".bz2", ".gz")


def is_link(source: str) -> bool:
    return source.strip().lower().startswith(("https://", "http://"))


def local_copy(job, source: str) -> Path:
    """The file to read: the path itself, or for a link a copy downloaded once into raw/."""
    source = source.strip().strip('"')
    if not is_link(source):
        path = Path(source)
        if not path.is_file():
            raise RuntimeError(f"OSM file not found: {path}; choose another file or switch the data source to Overpass")
        return path
    name = Path(urlparse(source).path).name or "download.osm.pbf"
    if not name.lower().endswith(SUFFIXES):
        name += ".osm.pbf"
    target = job.raw_dir / "source" / name
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    log.info("Downloading OSM file %s", source)
    tmp = target.with_suffix(target.suffix + ".part")
    with requests.get(source, headers={"User-Agent": USER_AGENT}, stream=True, timeout=(15, 120)) as response:
        response.raise_for_status()
        with open(tmp, "wb") as f:
            for block in response.iter_content(1 << 20):
                f.write(block)
    tmp.replace(target)
    log.info("Saved %s (%.1f MB)", target, target.stat().st_size / 1e6)
    return target


def _inside(bbox, lon: float, lat: float) -> bool:
    west, south, east, north = bbox
    return west <= lon <= east and south <= lat <= north


def _check_coverage(path: Path, bbox) -> None:
    """Fail early when the file's own bounds (if it declares any) miss the area."""
    import osmium

    box = osmium.FileProcessor(str(path)).header.box()
    if not box.valid():
        return
    west, south, east, north = bbox
    bl, tr = box.bottom_left, box.top_right
    if east < bl.lon or west > tr.lon or north < bl.lat or south > tr.lat:
        raise RuntimeError(f"{path.name} covers ({bl.lon:.3f}, {bl.lat:.3f}) - ({tr.lon:.3f}, {tr.lat:.3f}), "
                           "which does not include the area; use a file for this area")
    if west < bl.lon or east > tr.lon or south < bl.lat or north > tr.lat:
        log.warning("%s covers only part of the area's bounding box; buildings outside it will be missing", path.name)


def extract(path: Path, bbox, kinds=("buildings", "roads")) -> dict:
    """{kind: Overpass-style {"elements": [...]}} for the bounding box (west, south, east, north)."""
    import osmium

    keys = [k for kind in kinds for k in KINDS[kind]]
    nodes = {}                     # id -> (lon, lat) of nodes of kept ways
    ways = {}                      # id -> (refs, tags) of kept tagged ways
    relations = {}                 # id -> (members, tags)
    member_ways = set()
    trees = []                     # natural=tree nodes (vegetation)

    for obj in osmium.FileProcessor(str(path)).with_locations().with_filter(osmium.filter.KeyFilter(*keys)):
        if obj.is_node():
            if "vegetation" in kinds and obj.tags.get("natural") == "tree" and                     _inside(bbox, obj.location.lon, obj.location.lat):
                trees.append({"type": "node", "id": obj.id, "lon": obj.location.lon, "lat": obj.location.lat,
                              "tags": dict(obj.tags)})
        elif obj.is_way():
            tags = dict(obj.tags)
            if not any(wanted(k, tags) for k in kinds):
                continue
            coords = [(n.ref, n.lon, n.lat) for n in obj.nodes if n.location.valid()]
            if any(_inside(bbox, lon, lat) for _, lon, lat in coords):
                ways[obj.id] = ([n.ref for n in obj.nodes], tags)
                nodes.update({ref: (lon, lat) for ref, lon, lat in coords})
        elif obj.is_relation() and _relation_kind(obj.tags) in kinds:
            members = [{"type": {"n": "node", "w": "way", "r": "relation"}[m.type], "ref": m.ref, "role": m.role}
                       for m in obj.members]
            relations[obj.id] = (members, dict(obj.tags))
            member_ways.update(m["ref"] for m in members if m["type"] == "way")

    # Member ways of building relations (any tags), to see which relations touch the box
    outlines = {}
    if member_ways:
        processor = osmium.FileProcessor(str(path)).with_locations().with_filter(osmium.filter.IdFilter(member_ways))
        for obj in processor:
            if obj.is_way() and obj.id in member_ways:
                outlines[obj.id] = ([n.ref for n in obj.nodes], dict(obj.tags),
                                    [(n.ref, n.lon, n.lat) for n in obj.nodes if n.location.valid()])
    kept_relations = {}
    for rid, (members, tags) in relations.items():
        refs = [m["ref"] for m in members if m["type"] == "way" and m["ref"] in outlines]
        if any(_inside(bbox, lon, lat) for ref in refs for _, lon, lat in outlines[ref][2]):
            kept_relations[rid] = (members, tags)
            for ref in refs:
                nodes.update({nid: (lon, lat) for nid, lon, lat in outlines[ref][2]})

    def node_elements(refs):
        return [{"type": "node", "id": ref, "lon": nodes[ref][0], "lat": nodes[ref][1]}
                for ref in sorted(refs) if ref in nodes]

    out = {}
    for kind in kinds:
        elements, used_nodes = [], set()
        for wid, (refs, tags) in ways.items():
            if wanted(kind, tags):
                elements.append({"type": "way", "id": wid, "nodes": refs, "tags": tags})
                used_nodes.update(refs)
        if kind in ("buildings", "vegetation"):
            for rid, (members, tags) in kept_relations.items():
                if _relation_kind(tags) != kind:
                    continue
                elements.append({"type": "relation", "id": rid, "members": members, "tags": tags})
                for m in members:
                    if m["type"] == "way" and m["ref"] in outlines and m["ref"] not in ways:
                        refs, way_tags, _ = outlines[m["ref"]]
                        element = {"type": "way", "id": m["ref"], "nodes": refs}
                        if way_tags:
                            element["tags"] = way_tags
                        elements.append(element)
                        used_nodes.update(refs)
                    elif m["type"] == "way" and m["ref"] in ways:
                        used_nodes.update(ways[m["ref"]][0])
        out[kind] = {"elements": node_elements(used_nodes) + elements + (trees if kind == "vegetation" else [])}
    return out


def fetch_file(job, settings: dict, source: str, force: bool = False) -> dict:
    """Like fetch.fetch, but from an OSM file; writes raw/file_<kind>.json and raw/chunks.json."""
    zones = job.zones()
    bbox = query_bbox(zones, settings["osm"]["bbox_buffer_m"])
    job.raw_dir.mkdir(parents=True, exist_ok=True)
    path = local_copy(job, source)
    stat = path.stat()
    signature = {"type": "file", "source": source.strip(), "path": str(path), "size": stat.st_size,
                 "mtime": int(stat.st_mtime)}
    index = job.raw_dir / "chunks.json"
    if not force and index.exists():
        previous = json.loads(index.read_text(encoding="utf-8"))
        old = previous.get("bbox_wsen")
        if previous.get("source") == signature and old and set(kinds_for(settings)) <= set(previous["files"]) and old[0] <= bbox[0] and old[1] <= bbox[1] \
                and old[2] >= bbox[2] and old[3] >= bbox[3] \
                and all((job.raw_dir / n).exists() for names in previous["files"].values() for n in names):
            log.info("Using data already read from %s", path.name)
            return {kind: 1 for kind in previous["files"]}

    _check_coverage(path, bbox)
    log.info("Reading %s (%.1f MB) for (W,S,E,N) = %s", path.name, stat.st_size / 1e6, tuple(round(v, 5) for v in bbox))
    data = extract(path, bbox, kinds_for(settings))
    files = {}
    for kind, result in data.items():
        result["_meta"] = {"source": signature, "read_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        target = job.raw_dir / f"file_{kind}.json"
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)
        files[kind] = [target.name]
        counts = {t: sum(e["type"] == t for e in result["elements"]) for t in ("node", "way", "relation")}
        log.info("%s: %d ways, %d relations, %d nodes", kind, counts["way"], counts["relation"], counts["node"])
    if not any(e["type"] != "node" for e in data["buildings"]["elements"]):
        raise RuntimeError(f"No buildings in {path.name} inside this area; check that the file covers it")
    index.write_text(json.dumps({"bbox_wsen": bbox, "chunks": [], "files": files, "source": signature}, indent=1),
                     encoding="utf-8")
    return {kind: 1 for kind in files}
