"""Run fetch -> clean -> classify -> build_dsf in order; shared by the CLI and the GUI.

Each run also logs to jobs/<name>/build.log; the on_log callback (used by the GUI) receives every line.
"""

import logging
import time
from datetime import datetime

from quickscene import build_dsf, classify, clean, fetch, osm_file
from quickscene.geo import area_km2
from quickscene.log import get_logger

STEPS = ["fetch", "clean", "classify", "build_dsf"]

log = get_logger("pipeline")


class _Callback(logging.Handler):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.setFormatter(logging.Formatter("[%(name)s] %(message)s"))

    def emit(self, record):
        self.fn(self.format(record))


def run(job, base_settings: dict, steps: list | None = None, force_fetch: bool = False,
        on_log=None, on_step=None) -> dict:
    """Returns {step: report}. Runs every step when steps is None."""
    settings = job.settings(base_settings)
    steps = steps or STEPS
    unknown = [s for s in steps if s not in STEPS]
    if unknown:
        raise ValueError(f"Unknown steps {unknown}; choose from {STEPS}")

    file_handler = logging.FileHandler(job.root / "build.log", mode="a", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s"))
    handlers = [file_handler] + ([_Callback(on_log)] if on_log else [])
    # Module loggers already print to the console; also route them to the log file and the callback
    loggers = [logging.getLogger(n) for n in ("fetch", "clean", "classify", "build_dsf", "pipeline")]
    for lg in loggers:
        for h in handlers:
            lg.addHandler(h)
    reports = {}
    try:
        area = area_km2(job.zones())
        log.info("Job %s (pack %s), area %.2f km², %s", job.name, job.scenery_name, area,
                 datetime.now().isoformat(timespec="seconds"))
        if area > settings["osm"]["max_area_km2"]:
            raise RuntimeError(f"Area {area:.0f} km² exceeds osm.max_area_km2 = {settings['osm']['max_area_km2']}; split it into smaller areas")
        for step in steps:
            if on_step:
                on_step(step)
            started = time.time()
            log.info("== %s ==", step)
            if step == "fetch" and job.source["type"] == "file":
                reports[step] = osm_file.fetch_file(job, settings, job.source["path"], force=force_fetch)
            elif step == "fetch":
                reports[step] = fetch.fetch(job, settings, force=force_fetch)
            elif step == "clean":
                reports[step] = clean.run(job, settings)
            elif step == "classify":
                reports[step] = classify.run(job, settings)
            else:
                reports[step] = build_dsf.run(job, settings)
            log.info("%s finished in %.1f s", step, time.time() - started)
    except Exception as exc:
        log.error("Failed: %s", exc)
        raise
    finally:
        for lg in loggers:
            for h in handlers:
                lg.removeHandler(h)
        file_handler.close()
    return reports
