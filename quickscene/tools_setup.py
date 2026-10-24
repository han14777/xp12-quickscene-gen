"""Fetch DSFTool on first use: when tools.dsftool is empty, DSFTool.exe is extracted from Laminar's official
XPTools zip (tools.dsftool_url) into tools/ so a fresh install needs no manual setup. Nothing is redistributed;
the file comes straight from files.x-plane.com.
"""

import hashlib
import io
import zipfile
from pathlib import Path

import requests

from quickscene import config
from quickscene.log import get_logger

log = get_logger("setup")


def download_dsftool(url: str, target: Path, sha256: str = "", timeout_s: float = 60) -> Path:
    log.info("Downloading DSFTool from %s", url)
    response = requests.get(url, timeout=timeout_s)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        members = [n for n in archive.namelist() if n.replace("\\", "/").lower().endswith("/dsftool.exe")
                   or n.lower() == "dsftool.exe"]
        if not members:
            raise RuntimeError(f"DSFTool.exe not found in {url}")
        data = archive.read(members[0])
    if sha256 and hashlib.sha256(data).hexdigest() != sha256.lower():
        raise RuntimeError(f"DSFTool.exe from {url} does not match tools.dsftool_sha256")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(target)
    log.info("DSFTool saved to %s", target)
    return target


def ensure_dsftool(settings: dict) -> Path:
    """Return a usable DSFTool, downloading it when tools.dsftool is empty and tools/DSFTool.exe is missing."""
    found = config.dsftool_path(settings)
    if found:
        return found
    tools = settings.get("tools", {})
    if tools.get("dsftool"):
        raise RuntimeError(f"DSFTool not found at {tools['dsftool']}; fix tools.dsftool in the settings "
                           "or clear it to download DSFTool automatically")
    url = tools.get("dsftool_url")
    if not url:
        raise RuntimeError("DSFTool not found; set tools.dsftool in the settings (DSFTool.exe from XPTools)")
    try:
        return download_dsftool(url, config.BUNDLED_DSFTOOL, tools.get("dsftool_sha256", ""))
    except (requests.RequestException, zipfile.BadZipFile, OSError) as exc:
        raise RuntimeError(f"Could not download DSFTool ({exc}); download XPTools from "
                           "https://developer.x-plane.com/tools/xptools/ and set tools.dsftool in the settings") from exc
