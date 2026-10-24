"""Index of every X-Plane library the sim can see: the default libraries in Resources/default scenery and every
pack in Custom Scenery with a library.txt. A library.txt maps virtual paths (what DSF and apt.dat files name, e.g.
lib/airport/Ramp_Equipment/Tug.obj) to files inside the pack; anyone with the library installed sees the asset.

Parsing (library.txt is line based, keywords per Laminar's "Library" documentation):
- EXPORT / EXPORT_EXTEND / EXPORT_EXCLUDE / EXPORT_BACKUP / EXPORT_SEASON / EXPORT_EXTEND_SEASON /
  EXPORT_EXCLUDE_SEASON: <keyword> [season] <virtual path> <file>; EXPORT_RATIO: <keyword> <ratio> <virtual> <file>
- PRIVATE / PUBLIC / DEPRECATED / SEMI_DEPRECATED apply to the exports after them; private and deprecated items
  are hidden from browsing (WED does the same) but still resolve
- the file part is the rest of the line (it may contain spaces); backslashes are normalised to /
- only placeable asset types are kept (ASSET_TYPES); terrain (.ter), decals and textures are skipped

The index is cached in <cache_dir>/library_index.json and rebuilt when any library.txt changes (size + mtime).
"""

import json
import os
import re
from collections import Counter
from pathlib import Path

from quickscene.config import project_path, xplane_root
from quickscene.log import get_logger

log = get_logger("library")

INDEX_VERSION = 2
ASSET_TYPES = {".obj": "obj", ".agp": "agp", ".fac": "fac", ".for": "for", ".pol": "pol", ".lin": "lin",
               ".str": "str"}
EXPORT_KEYS = {"EXPORT", "EXPORT_EXTEND", "EXPORT_EXCLUDE", "EXPORT_BACKUP", "EXPORT_RATIO", "EXPORT_SEASON",
               "EXPORT_EXTEND_SEASON", "EXPORT_EXCLUDE_SEASON", "EXPORT_RATIO_SEASON", "EXPORT_EXCLUDE_BACKUP"}
# Category guesses from the virtual path, first match wins (checked after the type rules in categorize)
CATEGORY_WORDS = [
    ("airport", ("airport", "jetway", "docking", "apt_", "runway", "taxiway", "windsock", "ramp_equip", "gse",
                 "parking_stand", "navaid", "atc", "terminal", "hangar", "control_tower", "fire_station", "vasi",
                 "papi", "fuel_farm", "dockings")),
    ("vegetation", ("tree", "forest", "veget", "plant", "grass", "bush", "shrub", "flora", "palm", "flower",
                    "hedge", "cactus", "agave", "bamboo")),
    ("people", ("people", "peeps", "person", "human", "pax", "crowd", "pilot", "biker", "boy", "girl", "man_",
                "woman")),
    ("vehicles", ("car", "vehicle", "truck", "bus", "train", "traffic", "boat", "ship", "aircraft", "plane",
                  "heli", "tractor", "van", "faib", "static_ga", "yacht", "ferry", "tug")),
    ("street", ("street", "lamp", "light", "pole", "fence", "sign", "bench", "barrier", "wall", "road", "gate",
                "flag", "billboard", "hydrant", "bin", "bollard", "cone", "telephone", "power", "pylon")),
    ("buildings", ("build", "house", "home", "facade", "office", "industr", "commerc", "church", "school",
                   "warehouse", "tower", "apartment", "shop", "store", "hotel", "station", "silo", "farm",
                   "barn", "temple", "mosque", "castle", "stadium", "bridge", "landmark", "autogen", "urban")),
    ("water", ("water", "wave", "harbor", "harbour", "pier", "dock", "buoy", "sea_life", "jetty")),
]
CATEGORIES = ["buildings", "vegetation", "vehicles", "people", "street", "airport", "ground", "lines", "water",
              "other"]


def categorize(vpath: str, kind: str) -> str:
    low = vpath.lower()
    if kind == "for":
        return "vegetation"
    if kind in ("lin", "str"):
        return "airport" if "/airport/" in low or low.startswith("lib/airport") else "lines"
    if kind == "pol":
        return "airport" if "/airport/" in low or low.startswith("lib/airport") else "ground"
    if kind == "fac" and "airport" not in low:
        return "buildings"
    for category, words in CATEGORY_WORDS:
        if any(w in low for w in words):
            return category
    return "other"


def library_folders(settings: dict) -> list[dict]:
    """[{name, folder, origin}] for every folder with a library.txt: default libraries first, then Custom Scenery
    packs in alphabetical order."""
    root = xplane_root(settings)
    if not root:
        return []
    out = []
    for origin, base in (("default", root / "Resources" / "default scenery"), ("custom", root / "Custom Scenery")):
        if not base.is_dir():
            continue
        for folder in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            try:
                if (folder / "library.txt").is_file():
                    out.append({"name": folder.name, "folder": folder, "origin": origin})
            except OSError:
                continue
    return out


def parse_library(path: Path) -> list[tuple]:
    """[(virtual path, file relative to the pack folder, flags)] for the placeable exports of one library.txt.
    flags: a string with p (private), d (deprecated), b (backup)."""
    out = []
    visibility = ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        words = line.split(None, 1)
        if not words:
            continue
        key = words[0]
        if key == "PRIVATE":
            visibility = "p"
        elif key == "PUBLIC":
            visibility = ""
        elif key in ("DEPRECATED", "SEMI_DEPRECATED"):
            visibility = "d"
        elif key in EXPORT_KEYS and len(words) == 2:
            skip = 2 if key in ("EXPORT_RATIO", "EXPORT_RATIO_SEASON") else 1
            if key.endswith("_SEASON") and key != "EXPORT_RATIO_SEASON":
                skip = 2
            parts = words[1].split(None, skip)
            if len(parts) <= skip:
                continue
            vpath, real = parts[skip - 1], parts[skip].strip()
            kind = ASSET_TYPES.get(os.path.splitext(vpath)[1].lower())
            if not kind:
                continue
            flags = visibility + ("b" if "BACKUP" in key else "")
            out.append((vpath.replace("\\", "/"), real.replace("\\", "/"), flags))
    return out


def _cache_path(settings: dict) -> Path:
    return project_path(settings["paths"].get("cache_dir", "cache")) / "library_index.json"


def _signature(folders: list[dict]) -> dict:
    sig = {}
    for f in folders:
        try:
            st = (f["folder"] / "library.txt").stat()
            sig[f"{f['origin']}/{f['name']}"] = [st.st_size, int(st.st_mtime)]
        except OSError:
            continue
    return sig


class LibraryIndex:
    """packs: [{name, origin, folder (relative to the X-Plane root), count, types, categories}];
    items: {virtual path: [[pack index, file, flags], ...]} in load order."""

    def __init__(self, root: Path, packs: list, items: dict):
        self.root = root
        self.packs = packs
        self.items = items

    @classmethod
    def build(cls, settings: dict, use_cache: bool = True) -> "LibraryIndex":
        root = xplane_root(settings)
        if not root:
            raise RuntimeError("X-Plane folder is not set; set it in Local settings first")
        folders = library_folders(settings)
        signature = _signature(folders)
        cache = _cache_path(settings)
        if use_cache and cache.is_file():
            try:
                data = json.loads(cache.read_text(encoding="utf-8"))
                if data.get("version") == INDEX_VERSION and data.get("signature") == signature \
                        and data.get("root") == str(root):
                    return cls(root, data["packs"], data["items"])
            except (OSError, ValueError):
                pass
        packs, items = [], {}
        for f in folders:
            entries = parse_library(f["folder"] / "library.txt")
            index = len(packs)
            types, categories = Counter(), Counter()
            seen = set()
            for vpath, real, flags in entries:
                items.setdefault(vpath, []).append([index, real, flags])
                if vpath not in seen:
                    seen.add(vpath)
                    kind = ASSET_TYPES[os.path.splitext(vpath)[1].lower()]
                    types[kind] += 1
                    categories[categorize(vpath, kind)] += 1
            packs.append({"name": f["name"], "origin": f["origin"],
                          "folder": f["folder"].relative_to(root).as_posix(),
                          "count": len(seen), "types": dict(types), "categories": dict(categories)})
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": INDEX_VERSION, "root": str(root), "signature": signature,
                                   "packs": packs, "items": items}), encoding="utf-8")
        os.replace(tmp, cache)
        log.info("Library index: %d libraries, %d virtual paths", len(packs), len(items))
        return cls(root, packs, items)

    # ---------- browsing ----------

    def primary(self, vpath: str):
        """The provider X-Plane would most likely use: the first non-backup export, else the first."""
        providers = self.items.get(vpath) or []
        for p in providers:
            if "b" not in p[2]:
                return p
        return providers[0] if providers else None

    def item_summary(self, vpath: str, pack: int | None = None) -> dict:
        providers = self.items[vpath]
        chosen = next((p for p in providers if p[0] == pack), None) if pack is not None else None
        chosen = chosen or self.primary(vpath)
        kind = ASSET_TYPES[os.path.splitext(vpath)[1].lower()]
        return {"path": vpath, "type": kind, "category": categorize(vpath, kind), "pack": chosen[0],
                "library": self.packs[chosen[0]]["name"],
                "libraries": sorted({self.packs[p[0]]["name"] for p in providers}),
                "variants": len(providers), "private": "p" in chosen[2], "deprecated": "d" in chosen[2]}

    def search(self, query: str = "", pack: int | None = None, kind: str = "", category: str = "",
               show_hidden: bool = False, offset: int = 0, limit: int = 200) -> dict:
        words = [w for w in re.split(r"\s+", query.lower().strip()) if w]
        hits = []
        for vpath, providers in self.items.items():
            if pack is not None and not any(p[0] == pack for p in providers):
                continue
            if words and not all(w in vpath.lower() for w in words):
                continue
            ext_kind = ASSET_TYPES[os.path.splitext(vpath)[1].lower()]
            if kind and ext_kind != kind:
                continue
            if category and categorize(vpath, ext_kind) != category:
                continue
            mine = [p for p in providers if pack is None or p[0] == pack]
            if not show_hidden and all(("p" in p[2] or "d" in p[2]) for p in mine):
                continue
            if pack is None and not show_hidden and all("b" in p[2] for p in providers) and \
                    len(providers) and any("b" not in q[2] for q in providers):
                continue
            hits.append(vpath)
        hits.sort(key=str.lower)
        return {"total": len(hits),
                "items": [self.item_summary(v, pack) for v in hits[offset:offset + limit]]}

    def resolve(self, vpath: str, pack: int | None = None) -> Path:
        """Absolute file for a virtual path (from the given library, else the primary provider)."""
        providers = self.items.get(vpath)
        if not providers:
            raise FileNotFoundError(f"{vpath} is not exported by any installed library")
        chosen = next((p for p in providers if p[0] == pack), None) if pack is not None else None
        chosen = chosen or self.primary(vpath)
        return self.root / self.packs[chosen[0]]["folder"] / chosen[1]

    def providers_of(self, vpath: str) -> list[str]:
        return sorted({self.packs[p[0]]["name"] for p in self.items.get(vpath, [])})

    def dependencies(self, vpaths) -> dict:
        """{"libraries": {library: [paths]}, "missing": [paths]} for the virtual paths a pack uses. Paths provided by
        X-Plane's default libraries are listed under "X-Plane 12 (default)"."""
        libraries, missing = {}, []
        for vpath in sorted(set(vpaths)):
            providers = self.items.get(vpath)
            if not providers:
                missing.append(vpath)
                continue
            if any(self.packs[p[0]]["origin"] == "default" for p in providers):
                name = "X-Plane 12 (default)"
            else:
                chosen = self.primary(vpath)
                name = self.packs[chosen[0]]["name"]
            libraries.setdefault(name, []).append(vpath)
        return {"libraries": libraries, "missing": missing}


_cached: dict = {}


def get_index(settings: dict, refresh: bool = False) -> LibraryIndex:
    """Process-wide index, rebuilt when a library.txt changes (checked by size + mtime) or refresh is asked for."""
    root = str(xplane_root(settings))
    signature = _signature(library_folders(settings))
    entry = _cached.get(root)
    if refresh or entry is None or entry[0] != signature:
        entry = _cached[root] = (signature, LibraryIndex.build(settings, use_cache=not refresh))
    return entry[1]
