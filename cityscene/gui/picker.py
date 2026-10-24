"""Native file / folder dialogs for the map UI. The browser cannot reveal local paths, so the local server opens the
operating system's dialog on this machine and returns the chosen path.

The dialog runs in a child process (`cityscene pick ...`, or `CityScene.exe pick ...` in the packaged build) with
tkinter on that process's main thread: Tk objects created on one thread and released on another crash the whole
process on Windows, and the server handles requests on worker threads. One dialog at a time; it is kept on top
so it does not open behind the browser.
"""

import json
import subprocess
import sys
import threading
from pathlib import Path

KINDS = {
    "folder": None,
    "exe": [("Programs", "*.exe"), ("All files", "*.*")],
    "obj": [("3D models", "*.obj *.agp *.gltf *.glb"), ("X-Plane objects", "*.obj *.agp"),
            ("glTF", "*.gltf *.glb"), ("All files", "*.*")],
    "osm": [("OSM data", "*.pbf *.osm *.bz2"), ("All files", "*.*")],
}
_lock = threading.Lock()


def _initial_dir(initial: str) -> str | None:
    if not initial:
        return None
    path = Path(initial.strip().strip('"'))
    for candidate in (path, path.parent):
        if candidate.is_dir():
            return str(candidate)
    return None


def show_dialog(kind: str, title: str = "", initial: str = "") -> str | None:
    """Runs in the child process: show the dialog on the main thread and return the chosen path or None."""
    import tkinter
    from tkinter import filedialog

    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update()
    try:
        options = {"parent": root, "title": title or None, "initialdir": _initial_dir(initial)}
        if kind == "folder":
            chosen = filedialog.askdirectory(mustexist=True, **options)
        else:
            chosen = filedialog.askopenfilename(filetypes=KINDS[kind], **options)
    finally:
        root.destroy()
    return str(Path(chosen)) if chosen else None


def child_main(kind: str, title: str, initial: str) -> int:
    """Entry of `cityscene pick`: prints one JSON line {"path": ...} or {"error": ...}."""
    try:
        print(json.dumps({"path": show_dialog(kind, title, initial)}))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
    return 0


def _command() -> list:
    if getattr(sys, "frozen", False):
        return [sys.executable, "pick"]
    return [sys.executable, "-m", "cityscene", "pick"]


def pick(kind: str, title: str = "", initial: str = "") -> str | None:
    """Show a dialog and return the chosen path, or None when cancelled."""
    if kind not in KINDS:
        raise ValueError(f"Unknown dialog kind {kind!r}")
    if not _lock.acquire(blocking=False):
        raise RuntimeError("A file dialog is already open; finish it first")
    try:
        result = subprocess.run([*_command(), kind, "--title", title, "--initial", initial],
                                capture_output=True, text=True, encoding="utf-8", errors="replace")
    finally:
        _lock.release()
    lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
    reply = json.loads(lines[-1]) if lines else {"error": (result.stderr or "no reply").strip()[-300:]}
    if "error" in reply:
        raise RuntimeError(f"Could not open a file dialog ({reply['error']}); type the path instead")
    return reply.get("path")
