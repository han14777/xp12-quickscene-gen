"""Command line: python -m quickscene <command>

  gui                                    start the browser map UI (default http://127.0.0.1:8765)
  new <name> (--bbox W,S,E,N | --zone file.geojson) [--scenery pack] [--default-height 12] [--render-distance 30]
      [--osm-file extract.osm.pbf | download link]   read OSM from a file (e.g. BBBike / Geofabrik) instead of Overpass
  build <name> [--steps fetch,clean,...] [--force-fetch]
  install <name> / uninstall <name>
  reference <name> <building_id> [-o file.obj]   one building as a Wavefront OBJ for Blender
  import <pack folder> [--name job] [--overwrite]  turn a scenery pack made by this tool back into a job
  list
"""

import argparse
import json
import sys

from shapely.geometry import box, mapping

from quickscene import install as installer
from quickscene import pipeline
from quickscene.config import Job, list_jobs, load_settings


def parse_bbox(text: str) -> dict:
    west, south, east, north = (float(v) for v in text.split(","))
    if not (west < east and south < north):
        raise argparse.ArgumentTypeError("--bbox must be west,south,east,north (lon,lat,lon,lat)")
    return mapping(box(west, south, east, north))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="quickscene", description="Quick X-Plane 12 city scenery from OpenStreetMap")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("gui", help="start the browser map UI")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")

    p = sub.add_parser("new", help="create a job (area)")
    p.add_argument("name")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--bbox", type=parse_bbox, help="west,south,east,north")
    group.add_argument("--zone", help="area GeoJSON file")
    p.add_argument("--scenery", help="scenery pack name (default CityScene_<name>)")
    p.add_argument("--default-height", type=float, help="default height in metres when OSM has none")
    p.add_argument("--render-distance", type=float, help="how far buildings stay visible, in km (default 30)")
    p.add_argument("--osm-file", help="read OSM data from this .osm.pbf file or download link instead of Overpass")
    p.add_argument("--overwrite", action="store_true")

    p = sub.add_parser("build", help="run the pipeline")
    p.add_argument("name")
    p.add_argument("--steps", help=f"comma separated, default all: {','.join(pipeline.STEPS)}")
    p.add_argument("--force-fetch", action="store_true", help="ignore the cache and re-download OSM")

    for cmd in ("install", "uninstall"):
        sub.add_parser(cmd).add_argument("name")
    sub.add_parser("list", help="list jobs")

    # Used by the map UI's "Browse..." buttons (quickscene/gui/picker.py): shows a native dialog, prints JSON
    p = sub.add_parser("pick", help="show a native file / folder dialog and print the choice as JSON (used by the UI)")
    p.add_argument("kind", choices=["folder", "exe", "obj", "osm"])
    p.add_argument("--title", default="")
    p.add_argument("--initial", default="")

    p = sub.add_parser("import", help="import a scenery pack made by this tool as a job")
    p.add_argument("pack", help="scenery pack folder (e.g. in Custom Scenery)")
    p.add_argument("--name", help="job name (default: the name stored in the pack)")
    p.add_argument("--overwrite", action="store_true", help="replace an existing job with the same name")

    p = sub.add_parser("reference", help="export one building as a Wavefront OBJ to model on in Blender")
    p.add_argument("name")
    p.add_argument("building_id", help="OSM building id, e.g. way/123")
    p.add_argument("-o", "--output", help="output file (default <building>_reference.obj)")

    args = parser.parse_args(argv)
    settings = load_settings()

    try:
        if args.command == "gui":
            from quickscene.gui.server import serve
            serve(args.port, open_browser=not args.no_browser)
        elif args.command == "new":
            if args.zone:
                with open(args.zone, encoding="utf-8") as f:
                    zone = json.load(f)
            else:
                zone = args.bbox
            options = {"heights": {"default_height_m": args.default_height}} if args.default_height else {}
            if args.render_distance:
                options["dsf"] = {"render_distance_km": args.render_distance}
            source = {"type": "file", "path": args.osm_file} if args.osm_file else None
            job = Job.create(args.name, zone, settings, args.scenery, options, overwrite=args.overwrite, source=source)
            print(f"Created job {job.name}: {job.root}")
        elif args.command == "build":
            steps = args.steps.split(",") if args.steps else None
            pipeline.run(Job.open(args.name, settings), settings, steps, force_fetch=args.force_fetch)
        elif args.command == "pick":
            from quickscene.gui.picker import child_main
            return child_main(args.kind, args.title, args.initial)
        elif args.command == "import":
            from pathlib import Path
            from quickscene.transfer import import_pack
            summary = import_pack(Path(args.pack), settings, args.name, overwrite=args.overwrite)
            print(f"Imported job {summary['name']} ({summary['kind']}): {summary['blocks']} blocks, "
                  f"{summary['edits']} edits, {summary['models']} models")
        elif args.command == "reference":
            from quickscene.overrides import reference_obj
            job = Job.open(args.name, settings)
            with open(job.processed_dir / "buildings_classified.geojson", encoding="utf-8") as f:
                features = json.load(f)["features"]
            text = reference_obj(features, args.building_id)
            out = args.output or f"{args.building_id.replace('/', '_')}_reference.obj"
            with open(out, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"Wrote {out}")
        elif args.command == "install":
            installer.install(Job.open(args.name, settings), settings)
        elif args.command == "uninstall":
            installer.uninstall(Job.open(args.name, settings), settings)
        elif args.command == "list":
            for name in list_jobs(settings):
                job = Job.open(name, settings)
                print(f"{name}\t{job.scenery_name}\t{'built' if (job.build_dir(settings) / 'Earth nav data').is_dir() else 'not built'}")
    except (RuntimeError, ValueError, FileNotFoundError, FileExistsError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0
