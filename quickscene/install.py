"""Install / uninstall: link build/<pack> into X-Plane's Custom Scenery and register it in scenery_packs.ini.

Ported from melbourne-cbd-xp12 pipeline/install.py:
- Nothing is copied. A directory symlink is tried first (needs Windows Developer Mode or admin), then a
  directory junction (any user can create one). An existing link to this pack is left alone; anything else
  at that path (a link elsewhere or a real folder) aborts instead of being overwritten
- scenery_packs.ini: a timestamped backup is made before every change; only this pack's line is inserted or
  removed, all other lines and line endings stay as they were. The line goes before the first line containing
  any text from install.insert_before (default *GLOBAL_AIRPORTS*, i.e. above all third-party overlays), or
  before the first SCENERY_PACK line if none matches
- X-Plane rewrites scenery_packs.ini when it quits, so installing is refused while it is running
"""

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from quickscene.config import xplane_root
from quickscene.log import get_logger

log = get_logger("install")


def xplane_running() -> bool:
    if sys.platform != "win32":
        return False
    result = subprocess.run(["tasklist", "/FI", "IMAGENAME eq X-Plane.exe", "/NH"],
                            capture_output=True, text=True, errors="replace")
    return "X-Plane.exe" in result.stdout


def _same_target(link: Path, target: Path) -> bool:
    try:
        return link.resolve() == target.resolve()
    except OSError:
        return False


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(path))


def ensure_link(link: Path, target: Path) -> str:
    """Returns exists / symlink / junction."""
    if _is_link(link) or link.exists():
        if _is_link(link) and _same_target(link, target):
            return "exists"
        if _is_link(link):
            # Typically installed from another copy of this tool (e.g. a release folder) and then imported here
            raise RuntimeError(f"{link} is already installed from {link.resolve()}; uninstall it there (or remove "
                               "that link in Custom Scenery) before installing this copy")
        raise RuntimeError(f"{link} exists and is not a link to {target}; stopped to avoid overwriting it, please check by hand")
    try:
        os.symlink(target, link, target_is_directory=True)
        return "symlink"
    except OSError as exc:
        if sys.platform != "win32":
            raise RuntimeError(f"Could not create the folder link: {exc}") from exc
    import _winapi

    try:
        _winapi.CreateJunction(str(target.resolve()), str(link))
    except OSError as exc:
        raise RuntimeError(f"Could not create the folder link: {exc}. Enable Windows Developer Mode or run as administrator") from exc
    return "junction"


def _folder(line: str) -> str:
    return line.split(" ", 1)[-1].strip().rstrip("/")


def insert_entry(ini_text: str, entry: str, insert_before: list) -> tuple[str, str]:
    """Returns (new text, result): present / inserted / top. Inserts one line and leaves the rest untouched."""
    newline = "\r\n" if "\r\n" in ini_text else "\n"
    lines = ini_text.split(newline)
    folder = _folder(entry)
    if any(line.startswith("SCENERY_PACK") and _folder(line) == folder for line in lines):
        return ini_text, "present"
    for marker in insert_before:
        for i, line in enumerate(lines):
            if line.startswith("SCENERY_PACK") and marker in line:
                return newline.join(lines[:i] + [entry] + lines[i:]), "inserted"
    first = next((i for i, line in enumerate(lines) if line.startswith("SCENERY_PACK")), None)
    if first is None:
        end = len(lines) - 1 if lines and lines[-1] == "" else len(lines)
        first = end
    return newline.join(lines[:first] + [entry] + lines[first:]), "top"


def remove_entry(ini_text: str, entry: str) -> tuple[str, bool]:
    newline = "\r\n" if "\r\n" in ini_text else "\n"
    lines = ini_text.split(newline)
    folder = _folder(entry)
    kept = [line for line in lines if not (line.startswith("SCENERY_PACK") and _folder(line) == folder)]
    return newline.join(kept), len(kept) != len(lines)


def _paths(settings: dict) -> tuple[Path, Path]:
    root = xplane_root(settings)
    if root is None:
        raise RuntimeError("X-Plane 12 not found; set xplane.root in the settings")
    custom = root / "Custom Scenery"
    ini = custom / "scenery_packs.ini"
    if not ini.is_file():
        raise RuntimeError(f"{ini} not found; start X-Plane 12 once to create it")
    return custom, ini


def _write_ini(ini: Path, text: str) -> Path:
    backup = ini.with_name(f"scenery_packs.ini.bak-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(ini, backup)
    ini.write_bytes(text.encode("utf-8"))
    return backup


def install(job, settings: dict) -> dict:
    build_dir = job.build_dir(settings)
    if not (build_dir / "Earth nav data").is_dir():
        raise RuntimeError(f"Scenery pack {build_dir} not found; generate it first")
    if xplane_running():
        raise RuntimeError("X-Plane is running and rewrites scenery_packs.ini when it quits; quit X-Plane before installing")
    custom, ini = _paths(settings)
    name = job.scenery_name
    link_result = ensure_link(custom / name, build_dir)
    log.info("Folder link: %s (%s -> %s)", link_result, custom / name, build_dir)

    original = ini.read_bytes().decode("utf-8")
    updated, result = insert_entry(original, f"SCENERY_PACK Custom Scenery/{name}/", settings["install"]["insert_before"])
    backup = None
    if result == "present":
        log.info("scenery_packs.ini already lists this pack; left unchanged")
    else:
        backup = _write_ini(ini, updated).name
        log.info("scenery_packs.ini: %s (backup %s)", "inserted" if result == "inserted" else "added at the top", backup)
    return {"link": link_result, "ini": result, "backup": backup}


def uninstall(job, settings: dict) -> dict:
    if xplane_running():
        raise RuntimeError("X-Plane is running; quit it before uninstalling")
    custom, ini = _paths(settings)
    name = job.scenery_name
    link = custom / name
    link_result = "absent"
    if _is_link(link):
        if not _same_target(link, job.build_dir(settings)):
            raise RuntimeError(f"{link} points elsewhere; not removed")
        # On Windows both directory symlinks and junctions are removed with rmdir; only the link goes, not the target
        os.rmdir(link) if sys.platform == "win32" else link.unlink()
        link_result = "removed"
    elif link.exists():
        raise RuntimeError(f"{link} is a real folder, not a link; not removed")
    original = ini.read_bytes().decode("utf-8")
    updated, removed = remove_entry(original, f"SCENERY_PACK Custom Scenery/{name}/")
    backup = _write_ini(ini, updated).name if removed else None
    log.info("Uninstalled %s: link %s, ini entry %s", name, link_result, f"removed (backup {backup})" if removed else "not present")
    return {"link": link_result, "ini_removed": removed, "backup": backup}
