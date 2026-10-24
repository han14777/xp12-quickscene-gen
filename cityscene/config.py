"""Settings and job folders.

- config/settings.yaml: global defaults; config/local.yaml (not in git): machine paths, overriding the same keys
- A job lives in jobs/<name>/: job.json (name, scenery pack name, options), zone.geojson (the area),
  raw/ (Overpass cache), processed/ (intermediate results), overrides.json (per-building edits), models/ (custom
  OBJs); the generated pack goes to build/<pack name>/
"""

import copy
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

if getattr(sys, "frozen", False):
    # Packaged build (PyInstaller): bundled files sit in sys._MEIPASS; jobs, build output, local.yaml and the
    # downloaded DSFTool go next to the executable
    BUNDLE_ROOT = Path(sys._MEIPASS)
    PROJECT_ROOT = Path(sys.executable).resolve().parent
else:
    BUNDLE_ROOT = PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = BUNDLE_ROOT / "config" / "settings.yaml"
LOCAL_PATH = PROJECT_ROOT / "config" / "local.yaml"
# Where DSFTool is downloaded to when tools.dsftool is empty
BUNDLED_DSFTOOL = PROJECT_ROOT / "tools" / "DSFTool.exe"

# Job names become folder names, and pack names appear in Custom Scenery and scenery_packs.ini: keep them plain
_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
# Settings sections a job's options may override
OPTION_SECTIONS = ("heights", "classification", "clean", "roads", "dsf", "extras")


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_settings(path: Path = SETTINGS_PATH, local_path: Path | None = LOCAL_PATH) -> dict:
    """settings.yaml with config/local.yaml on top; local_path=None gives the shipped defaults only."""
    with open(path, encoding="utf-8") as f:
        settings = yaml.safe_load(f)
    if local_path is not None and local_path.exists():
        with open(local_path, encoding="utf-8") as f:
            settings = deep_merge(settings, yaml.safe_load(f) or {})
    return settings


def save_local(values: dict, local_path: Path = LOCAL_PATH) -> None:
    """Merge machine paths into config/local.yaml."""
    current = {}
    if local_path.exists():
        with open(local_path, encoding="utf-8") as f:
            current = yaml.safe_load(f) or {}
    local_path.parent.mkdir(parents=True, exist_ok=True)
    with open(local_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(deep_merge(current, values), f, allow_unicode=True, sort_keys=False)


def project_path(relative: str) -> Path:
    path = Path(relative)
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def xplane_root(settings: dict) -> Path | None:
    """xplane.root from settings; when empty, the first valid line of %LOCALAPPDATA%/x-plane_install_12.txt (written by the X-Plane 12 installer)."""
    configured = settings.get("xplane", {}).get("root")
    if configured:
        return Path(configured)
    marker = Path(os.environ.get("LOCALAPPDATA", "")) / "x-plane_install_12.txt"
    if marker.is_file():
        for line in marker.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip() and Path(line.strip()).is_dir():
                return Path(line.strip())
    return None


def dsftool_path(settings: dict) -> Path | None:
    """tools.dsftool from settings; when empty, tools/DSFTool.exe (where tools_setup downloads it)."""
    configured = settings.get("tools", {}).get("dsftool")
    if configured:
        return Path(configured) if Path(configured).is_file() else None
    return BUNDLED_DSFTOOL if BUNDLED_DSFTOOL.is_file() else None


def check_name(name: str) -> str:
    if not _NAME_RE.match(name or ""):
        raise ValueError(f"Names may only use letters, digits, underscores and hyphens (1-64 characters): {name!r}")
    return name


@dataclass
class Job:
    """One scenery job."""

    name: str
    root: Path

    @classmethod
    def open(cls, name: str, settings: dict) -> "Job":
        job = cls(check_name(name), project_path(settings["paths"]["jobs_dir"]) / name)
        if not job.meta_path.exists():
            raise FileNotFoundError(f"Job {name} does not exist ({job.meta_path})")
        return job

    @classmethod
    def create(cls, name: str, zone: dict, settings: dict, scenery_name: str | None = None,
               options: dict | None = None, overwrite: bool = False, source: dict | None = None) -> "Job":
        """zone is GeoJSON (Feature / FeatureCollection / Polygon / MultiPolygon). source is where OSM data comes
        from: {"type": "overpass"} (default) or {"type": "file", "path": file path or download link}."""
        job = cls(check_name(name), project_path(settings["paths"]["jobs_dir"]) / name)
        if job.meta_path.exists() and not overwrite:
            raise FileExistsError(f"Job {name} already exists")
        job.root.mkdir(parents=True, exist_ok=True)
        meta = job.meta if job.meta_path.exists() else {"created": datetime.now().isoformat(timespec="seconds")}
        meta.update({
            "name": name,
            "scenery_name": check_name(scenery_name or f"CityScene_{name}"),
            "options": options or {},
        })
        if source is not None:
            meta["source"] = check_source(source)
        job.write_meta(meta)
        job.zone_path.write_text(json.dumps(normalize_zone(zone), ensure_ascii=False, indent=1), encoding="utf-8")
        return job

    @property
    def meta_path(self) -> Path:
        return self.root / "job.json"

    @property
    def zone_path(self) -> Path:
        return self.root / "zone.geojson"

    @property
    def raw_dir(self) -> Path:
        return self.root / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.root / "processed"

    @property
    def overrides_path(self) -> Path:
        """Per-building edits (cityscene/overrides.py)."""
        return self.root / "overrides.json"

    @property
    def models_dir(self) -> Path:
        """Custom OBJ models (and their textures) that replace buildings."""
        return self.root / "models"

    @property
    def meta(self) -> dict:
        return json.loads(self.meta_path.read_text(encoding="utf-8"))

    def write_meta(self, meta: dict) -> None:
        self.meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    @property
    def source(self) -> dict:
        return self.meta.get("source") or {"type": "overpass"}

    @property
    def scenery_name(self) -> str:
        return self.meta["scenery_name"]

    def build_dir(self, settings: dict) -> Path:
        return project_path(settings["paths"]["build_dir"]) / self.scenery_name

    def settings(self, base: dict) -> dict:
        """Global settings with this job's options applied (only sections in OPTION_SECTIONS can be overridden)."""
        options = {k: v for k, v in self.meta.get("options", {}).items() if k in OPTION_SECTIONS}
        return deep_merge(base, options)

    def zones(self) -> list:
        """The area as a list of Features, each with a unique properties.id."""
        return json.loads(self.zone_path.read_text(encoding="utf-8"))["features"]


def check_source(source: dict) -> dict:
    kind = (source or {}).get("type", "overpass")
    if kind == "overpass":
        return {"type": "overpass"}
    if kind == "file":
        path = str(source.get("path") or "").strip().strip('"')
        if not path:
            raise ValueError("Choose an OSM file (.osm.pbf) or paste a download link")
        return {"type": "file", "path": path}
    raise ValueError(f"Unknown data source {kind!r}")


def normalize_zone(zone: dict) -> dict:
    """Normalise GeoJSON to a FeatureCollection of Polygon / MultiPolygon features numbered zone_1, zone_2, ..."""
    if zone.get("type") == "FeatureCollection":
        features = zone["features"]
    elif zone.get("type") == "Feature":
        features = [zone]
    else:
        features = [{"type": "Feature", "properties": {}, "geometry": zone}]
    out = []
    for feature in features:
        geometry = feature.get("geometry") or {}
        if geometry.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        out.append({"type": "Feature", "properties": {"id": f"zone_{len(out) + 1}"}, "geometry": geometry})
    if not out:
        raise ValueError("The area contains no polygons")
    return {"type": "FeatureCollection", "features": out}


def list_jobs(settings: dict) -> list:
    jobs_dir = project_path(settings["paths"]["jobs_dir"])
    if not jobs_dir.is_dir():
        return []
    return sorted(p.name for p in jobs_dir.iterdir() if (p / "job.json").is_file())
