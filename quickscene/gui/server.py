"""Local server for the browser map UI (standard library only): listens on 127.0.0.1 only, serves the static
page and a JSON API, and runs the pipeline in a background thread.

API:
  GET  /api/status                      machine paths, whether X-Plane is running, default options
  POST /api/settings                    {xplane_root, dsftool} saved to config/local.yaml
  POST /api/overpass                    {urls} the Overpass servers to use, in order (saved to config/local.yaml)
  POST /api/overpass/test               {urls} test each server with a tiny query
  GET  /api/jobs                        job list
  POST /api/jobs                        {name, scenery_name, zone, options, source, overwrite} create / replace a job
  GET  /api/jobs/<name>?since=n         job details, area, run state, log lines from n on
  GET  /api/jobs/<name>/buildings       classified buildings (map preview)
  POST /api/jobs/<name>/build           {force_fetch, steps} run the pipeline in the background (one job at a time)
  GET  /api/jobs/<name>/overrides       per-building edits and the OBJ files in the job's models folder
  POST /api/jobs/<name>/overrides       {building_id, edit} store one building's edit (empty edit removes it)
  GET  /api/jobs/<name>/reference?id=   one building as a Wavefront OBJ for Blender (download)
  POST /api/jobs/<name>/install | uninstall
  GET  /api/packs                       scenery packs made by this tool that can be imported as jobs
  POST /api/bbbike/url                  {zone, name} BBBike extract form link with the area's polygon filled in
  GET  /api/bbbike/status?name=         state of that extract on BBBike's status page (waiting / running / ready + link)
  POST /api/pick                        {kind: folder | exe | obj, title, initial} native file dialog -> {path}
  POST /api/jobs/<name>/models          {path} copy an OBJ (and its textures) into the job's models folder
  POST /api/import                      {path, name, overwrite} import a pack as a job
Library, previews, model import, placed items and airport projects: see routes.py.
"""

import json
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from quickscene import install as installer
from quickscene import bbbike, fetch, overrides, pipeline, transfer
from quickscene.gui import picker, routes
from quickscene.config import Job, dsftool_path, list_jobs, load_settings, normalize_zone, save_local, xplane_root
from quickscene.log import get_logger
from quickscene.tools_setup import ensure_dsftool

log = get_logger("gui")

STATIC = Path(__file__).resolve().parent / "static"
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}
# Maximum blocks returned for the preview; more makes the browser sluggish
PREVIEW_LIMIT = 30000

_runs: dict[str, dict] = {}
_run_lock = threading.Lock()


def _run_state(name: str) -> dict:
    return _runs.setdefault(name, {"state": "idle", "step": None, "log": [], "error": None})


def _start_build(name: str, force_fetch: bool, steps: list | None = None) -> None:
    if not _run_lock.acquire(blocking=False):
        running = next((n for n, r in _runs.items() if r["state"] == "running"), "?")
        raise RuntimeError(f"Job {running} is running; wait for it to finish")
    state = _run_state(name)
    state.update(state="running", step=None, log=[], error=None)

    def work():
        try:
            settings = load_settings()
            job = Job.open(name, settings)
            pipeline.run(job, settings, steps, force_fetch=force_fetch, on_log=state["log"].append,
                         on_step=lambda s: state.update(step=s))
            state["state"] = "done"
        except Exception as exc:  # background thread: hand the error to the UI
            state.update(state="error", error=str(exc))
            state["log"].append(f"[gui] Failed: {exc}")
            log.error("%s", traceback.format_exc())
        finally:
            _run_lock.release()

    threading.Thread(target=work, daemon=True).start()


def _installed(job: Job, settings: dict) -> bool:
    root = xplane_root(settings)
    # Only a link to this copy's build output counts (the same pack may be installed from another copy)
    link = root / "Custom Scenery" / job.scenery_name if root else None
    return bool(link) and link.exists() and installer._same_target(link, job.build_dir(settings))


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def job_detail(name: str, since: int) -> dict:
    settings = load_settings()
    job = Job.open(name, settings)
    processed = job.processed_dir
    state = _run_state(name)
    return {
        "meta": job.meta,
        "zone": json.loads(job.zone_path.read_text(encoding="utf-8")),
        "built": (job.build_dir(settings) / "Earth nav data").is_dir(),
        "installed": _installed(job, settings),
        "build_dir": str(job.build_dir(settings)),
        "run": {k: state[k] for k in ("state", "step", "error")},
        "log": state["log"][since:],
        "log_total": len(state["log"]),
        "reports": {k: _read_json(processed / f"{k}_report.json") for k in ("clean", "classify", "build")},
    }


def buildings_preview(name: str) -> dict:
    settings = load_settings()
    path = Job.open(name, settings).processed_dir / "buildings_classified.geojson"
    data = _read_json(path) or {"features": []}
    features = data["features"][:PREVIEW_LIMIT]
    keep = ("id", "building_id", "name", "height_m", "height_source", "category", "facade_style", "building")
    for f in features:
        f["properties"] = {k: f["properties"].get(k) for k in keep}
    return {"type": "FeatureCollection", "features": features, "total": len(data["features"])}


def job_overrides(name: str) -> dict:
    job = Job.open(name, load_settings())
    models = []
    if job.models_dir.is_dir():
        models = sorted(p.relative_to(job.models_dir).as_posix() for p in job.models_dir.rglob("*")
                        if p.suffix.lower() in (".obj", ".agp"))
    return {"buildings": overrides.load(job), "models": models, "models_dir": str(job.models_dir)}


def reference(name: str, building_id: str) -> str:
    job = Job.open(name, load_settings())
    data = _read_json(job.processed_dir / "buildings_classified.geojson") or {"features": []}
    return overrides.reference_obj(data["features"], building_id)


def status() -> dict:
    settings = load_settings()
    root = xplane_root(settings)
    tool = dsftool_path(settings)
    active = fetch.overpass_urls(settings["osm"]["overpass_url"])
    shipped = fetch.overpass_urls(load_settings(local_path=None)["osm"]["overpass_url"])
    return {
        "overpass": {"active": active, "shipped": shipped,
                     "known": active + [u for u in shipped if u not in active]},
        "xplane_root": str(root) if root else "",
        "xplane_ok": bool(root and (root / "Custom Scenery").is_dir()),
        "dsftool": str(tool) if tool else settings["tools"].get("dsftool", ""),
        "dsftool_ok": tool is not None,
        "xplane_running": installer.xplane_running(),
        "defaults": {
            "default_height_m": settings["heights"]["default_height_m"],
            "level_height_m": settings["heights"]["level_height_m"],
            "render_distance_km": settings["dsf"]["render_distance_km"],
            "max_area_km2": settings["osm"]["max_area_km2"],
        },
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "quickscene/0.1"

    def log_message(self, fmt, *args):  # polling is frequent, so skip access logs
        pass

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _result(self, result) -> None:
        if isinstance(result, tuple) and isinstance(result[0], bytes):
            return self._send(200, result[0], result[1])
        return self._json(result)

    def _error(self, exc: Exception, code: int = 400) -> None:
        self._json({"error": str(exc)}, code)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}") if length else {}

    def _same_origin(self) -> bool:
        """Reject write requests from other sites' pages (this server is only for its own page)."""
        origin = self.headers.get("Origin")
        return origin is None or urlparse(origin).netloc == self.headers.get("Host")

    def do_GET(self):
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        try:
            found, result = routes.dispatch("GET", url.path, parse_qs(url.query), {}, load_settings())
            if found:
                return self._result(result)
            if not parts:
                return self._static("index.html")
            if parts[0] == "static" and len(parts) == 2:
                return self._static(parts[1])
            if parts == ["api", "status"]:
                return self._json(status())
            if parts == ["api", "packs"]:
                return self._json(transfer.find_packs(load_settings()))
            if parts == ["api", "bbbike", "status"]:
                return self._json(bbbike.status(parse_qs(url.query).get("name", [""])[0]))
            if parts == ["api", "jobs"]:
                settings = load_settings()
                jobs = []
                for name in list_jobs(settings):
                    job = Job.open(name, settings)
                    jobs.append({"name": name, "scenery_name": job.scenery_name,
                                 "built": (job.build_dir(settings) / "Earth nav data").is_dir(),
                                 "state": _run_state(name)["state"]})
                return self._json(jobs)
            if parts[:2] == ["api", "jobs"] and len(parts) == 3:
                since = int(parse_qs(url.query).get("since", ["0"])[0])
                return self._json(job_detail(parts[2], since))
            if parts[:2] == ["api", "jobs"] and len(parts) == 4 and parts[3] == "buildings":
                return self._json(buildings_preview(parts[2]))
            if parts[:2] == ["api", "jobs"] and len(parts) == 4 and parts[3] == "overrides":
                return self._json(job_overrides(parts[2]))
            if parts[:2] == ["api", "jobs"] and len(parts) == 4 and parts[3] == "reference":
                building_id = parse_qs(url.query).get("id", [""])[0]
                body = reference(parts[2], building_id).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Disposition",
                                 f'attachment; filename="{building_id.replace("/", "_")}_reference.obj"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return None
            self._json({"error": "not found"}, 404)
        except FileNotFoundError as exc:
            self._error(exc, 404)
        except (ValueError, RuntimeError) as exc:
            self._error(exc)
        except Exception as exc:
            log.error("%s", traceback.format_exc())
            self._error(exc, 500)

    def do_POST(self):
        if not self._same_origin():
            return self._json({"error": "forbidden"}, 403)
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        try:
            body = self._body()
            settings = load_settings()
            found, result = routes.dispatch("POST", url.path, parse_qs(url.query), body, settings)
            if found:
                return self._result(result)
            if parts == ["api", "settings"]:
                save_local({"xplane": {"root": body.get("xplane_root", "").strip()},
                            "tools": {"dsftool": body.get("dsftool", "").strip()}})
                return self._json(status())
            if parts == ["api", "bbbike", "url"]:
                zones = normalize_zone(body.get("zone") or {})["features"]
                name = str(body.get("name") or "quickscene").strip()[:60]
                return self._json({"url": bbbike.form_url(zones, settings["osm"]["bbox_buffer_m"], name), "name": name})
            if parts == ["api", "overpass"]:
                save_local({"osm": {"overpass_url": fetch.clean_server_list(body.get("urls"))}})
                return self._json(status())
            if parts == ["api", "overpass", "test"]:
                return self._json(fetch.test_servers(fetch.clean_server_list(body.get("urls"))))
            if parts == ["api", "pick"]:
                return self._json({"path": picker.pick(str(body.get("kind")), str(body.get("title") or ""),
                                                       str(body.get("initial") or ""))})
            if parts == ["api", "import"]:
                if _run_lock.locked():
                    raise RuntimeError("A job is running; wait for it to finish")
                return self._json(transfer.import_pack(Path(str(body.get("path") or "").strip().strip('"')), settings,
                                                       str(body.get("name") or "").strip() or None,
                                                       overwrite=bool(body.get("overwrite"))))
            if parts == ["api", "jobs"]:
                job = Job.create(body["name"], body["zone"], settings, body.get("scenery_name") or None,
                                 body.get("options") or {}, overwrite=bool(body.get("overwrite")),
                                 source=body.get("source"))
                return self._json({"name": job.name})
            if parts[:2] == ["api", "jobs"] and len(parts) == 4:
                name, action = parts[2], parts[3]
                if action == "build":
                    Job.open(name, settings)
                    _start_build(name, bool(body.get("force_fetch")), body.get("steps") or None)
                    return self._json({"started": True})
                if action == "models":
                    return self._json(overrides.import_model(Job.open(name, settings), Path(str(body.get("path") or "")),
                                                             float(body.get("scale") or 1), str(body.get("up") or "y")))
                if action == "overrides":
                    job = Job.open(name, settings)
                    building_id = str(body.get("building_id") or "")
                    if not building_id:
                        raise ValueError("building_id is required")
                    return self._json({"buildings": overrides.set_edit(job, building_id, body.get("edit"),
                                                                       job.settings(settings))})
                if action == "install":
                    return self._json(installer.install(Job.open(name, settings), settings))
                if action == "uninstall":
                    return self._json(installer.uninstall(Job.open(name, settings), settings))
            self._json({"error": "not found"}, 404)
        except (RuntimeError, ValueError, KeyError, FileExistsError, FileNotFoundError) as exc:
            self._error(exc)
        except Exception as exc:
            log.error("%s", traceback.format_exc())
            self._error(exc, 500)

    def _static(self, name: str) -> None:
        path = (STATIC / name).resolve()
        if path.parent != STATIC or not path.is_file():
            return self._json({"error": "not found"}, 404)
        self._send(200, path.read_bytes(), CONTENT_TYPES.get(path.suffix, "application/octet-stream"))


def serve(port: int = 8765, open_browser: bool = True) -> None:
    url = f"http://127.0.0.1:{port}/"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        # Most likely the UI is already running (e.g. the launcher was double-clicked twice): just show it
        log.warning("Port %d is in use; opening the UI that is already running", port)
        if open_browser:
            webbrowser.open(url)
        return
    try:
        ensure_dsftool(load_settings())
    except RuntimeError as exc:
        log.warning("%s", exc)
    log.info("UI running at %s (Ctrl+C to quit)", url)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
