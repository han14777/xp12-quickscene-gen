# QuickScene

**English** | [简体中文](README.zh-CN.md)

A scenery workbench for X-Plane 12, for **cities** and **airports**, in your browser:

- **City scenery**: draw an area and generate it from OpenStreetMap (building outlines and heights → X-Plane 12
  default facades → DSF that also hides the default autogen buildings), then refine it building by building:
  heights, a specific facade from any library, your own 3D models, extra objects, trees, ground polygons and lines
- **Airports**, following WED's design: start from X-Plane's own airport database, the Scenery Gateway, an installed
  scenery pack, OpenStreetMap or a blank runway; edit runways, pavements, markings, signs, stands, ATC taxi routes,
  lights, frequencies and 3D scenery on the map; validate; export apt.dat + DSF and install
- **Scenery library browser** with a 3D preview: every library installed in X-Plane (default libraries and everything
  in Custom Scenery such as OpenSceneryX, MisterX, SAM, RuScenery, …) and your own models
- **Import entries** so professional work fits in: X-Plane OBJ8 (XPlane2Blender), Wavefront `.obj`/`.mtl` and glTF
  `.gltf`/`.glb` (Blender, SketchUp, 3ds Max …) converted to OBJ8; the DSF items of any existing scenery pack (WED
  projects, Gateway airports, other add-ons); apt.dat airports from X-Plane, the Gateway or installed packs

QuickScene was called *xp12-cityscene-gen* / *CityScene* up to v0.1.1. Packs made by those versions still import.

## Download (Windows)

No Python needed: download the Windows zip from
[Releases](https://github.com/han14777/xp12-quickscene-gen/releases/latest), unzip it anywhere you can write to,
and double-click the exe (`QuickScene.exe`; `CityScene.exe` up to v0.1.1). The UI opens in your browser. On first
start DSFTool is downloaded from Laminar Research (files.x-plane.com, checked against a SHA-256) into the `tools`
folder, and X-Plane 12 is found automatically. Projects and generated scenery stay inside the unzipped folder.

The rest of this README covers running from source.

## Requirements

- Python 3.11+: `pip install -e ".[dev]"` (pyosmium, Pillow, numpy, shapely, pyproj …)
- X-Plane 12 (its location is read automatically from `%LOCALAPPDATA%\x-plane_install_12.txt`)
- `DSFTool.exe` from Laminar's [XPTools](https://developer.x-plane.com/tools/xptools/): downloaded automatically
  into `tools/` on first use, or set your own path under "Local settings" (saved to `config/local.yaml`, not committed)
- The browser loads Leaflet, leaflet-draw and three.js from public CDNs, and map tiles from OpenStreetMap / Esri

## Starting the UI

```
python -m quickscene gui        # or double-click start_gui.bat
```

English by default; switch with EN / 中文 next to the title. The top of the panel switches between **City scenery**
and **Airport**; **Library…** opens the scenery library at any time. The map's layer switcher (top right) offers
OpenStreetMap, satellite imagery, and your own imagery tile URL (Local settings).

## Scenery library and 3D preview

Every `library.txt` X-Plane can see is indexed (about 60 000 assets with a typical set of libraries; the index is
cached and refreshed when a library changes). Search by path, filter by library, type and category (buildings,
vegetation, vehicles, people, street furniture, airport, ground, lines …). Selecting an item shows it in 3D:

| Type | Preview |
|------|---------|
| `.obj` | the mesh with its texture (DDS decoded), lights as points; the nearest level of detail |
| `.fac` | type 1 facades built as a building from their wall segments; type 2 facades as textures on a box |
| `.for` | a group of trees: X-Plane 12's 3D tree meshes, or the classic crossed billboards |
| `.pol` / `.lin` | the texture on the ground at its real scale |
| `.agp` / `.str` | their objects placed as the file places them |

"Use this" puts the asset where you were choosing one; "Copy path" copies the library path. The **Project models**
tab lists the open project's own models and imports new ones (below).

Every pack lists the libraries it needs in its `README.txt` and in the result panel, and warns about assets no
installed library provides.

## Importing 3D models

"Import 3D model…" in the library's Project models tab (or "Browse…" when replacing a building) accepts:

- **X-Plane `.obj` / `.agp`**: copied into the project with the textures they name
- **Wavefront `.obj` + `.mtl`** and **glTF `.gltf` / `.glb`**: converted to OBJ8. Every material's image (or plain
  colour) is packed into one texture atlas, texture coordinates are remapped, node transforms applied and missing
  normals computed. Set the scale (0.01 for centimetres, 0.0254 for inches) and the up axis (Z for Z-up files).
  UVs that repeat outside 0..1 are wrapped and reported; bake textures to 0..1 for an exact result

Imported models live in `jobs/<project>/models/` (or `airports/<project>/models/`) and are copied into the pack.

## City scenery

The panel starts with **New project**, **Open project** and **Import pack**. For a new project:

1. Search for a place and draw rectangles or polygons (map tools at the top left)
2. Project settings: name, default height, render distance, OSM data source, and optionally **trees from
   OpenStreetMap** (single trees, tree rows, woods; tree types selectable from the library). Trees are kept off the
   generated buildings and roads
3. "Save area and generate": progress and logs appear as it runs; buildings show on the map coloured by height
4. Refine (below), then quit X-Plane and click "Install to X-Plane"

### Editing buildings

Click a building: keep it with a new height (other parts scale with it), a facade style or **a specific facade from
any library**; delete it; or replace it with a model (from the library or your own), with heading and east / north
offset. Edits are stored per OSM building in `jobs/<job>/overrides.json` and survive re-downloading OSM.
"Download reference for Blender" exports the building's blocks around its placement point as a Wavefront OBJ, to
model on top of it and export with [XPlane2Blender](https://github.com/X-Plane/XPlane2Blender).

### Scenery objects and details

Step 6 adds anything from the libraries or your own models: **objects** (click to place, keep clicking for more),
**facades**, **forests** (area, along a line, or one tree per point), **ground polygons** and **lines / object
strings**. Select an item on the map or in the list to drag it or its vertices, rotate (R / Shift+R), duplicate
(Ctrl+D), delete (Del) or undo (Ctrl+Z); objects can sit above the ground (`OBJECT_AGL`). Items are stored in
`jobs/<job>/placements.json`; "Save and regenerate" rebuilds the DSF without downloading.

"Import items from another scenery pack" reads the DSFs of any pack (decompiled with DSFTool) and adds the objects,
facades, forests, polygons and lines inside the area as editable items, keeping facade wall types, UV-mapped
polygons and curved lines; models inside that pack are copied into the project.

### Importing a pack made by QuickScene

Every pack carries its project in `<pack>/quickscene/` (area, settings, edits, placed items, cleaned OSM buildings),
so it can be turned back into a project: "Import pack" at the start, or `python -m quickscene import <pack folder>`.
The original pack stays as it is.

## Airports

Switch to **Airport**. **New airport** starts from:

| Source | What you get |
|--------|--------------|
| X-Plane's airport database | any of the ~38 000 airports X-Plane ships (Global Airports apt.dat, indexed once): runways, pavements, markings, signs, stands, ATC routes, flows |
| Scenery Gateway | the recommended scenery from gateway.x-plane.com, including its 3D objects and facades |
| An installed scenery pack | its apt.dat and, optionally, every object, facade, forest and polygon of its DSFs; models inside the pack are copied |
| OpenStreetMap | a draft: runways (ends named from `ref` or heading), taxiway centre lines with lights and pavement, aprons, stands facing away from the taxiway, windsock, tower, boundary, terminals and hangars as facades |
| Blank | one 1500 m runway at the centre of the map |

The editor works like WED: **tools** create each kind of element (runway, helipad, pavement, marking line, boundary,
taxi route, stand, windsock, beacon, tower viewpoint, sign, PAPI / VASI, and objects / facades / forests / ground /
lines from the library); the **contents** tree lists everything; **properties** edit the selection with apt.dat's
own value lists (surfaces, markings, approach lights, line and light types, stand types and aircraft classes, ICAO
width codes, airlines, active zones …). Click a point of a line or pavement to edit just that point and drag its
Bézier handles. Undo / redo, Ctrl+S, earlier saves (restore), and the ATC flow rows as text are available.

**Checks** run on every save, in the spirit of WED's validation: runway names, reciprocal pairs and headings,
lengths and widths, displaced thresholds, stands (names, types, aircraft, inside the boundary), taxi route network
(missing nodes, zero length, disconnected parts), frequencies, sign syntax, missing boundary / windsock / viewpoint.
Errors block the export; click a finding to jump to it.

**Export** writes `build/<pack>/Earth nav data/apt.dat` (format 1200; read back and counted) and a DSF per tile for
the scenery items (compiled with DSFTool and decompiled to check), copies the project's models, lists the libraries
needed, and can hide X-Plane's default objects / buildings / trees inside the boundary. **Install** links the pack
into Custom Scenery above Global Airports. Everything apt.dat holds survives import → edit → export, including
Bézier curves, ATC flows and truck routes.

## Command line

```
python -m quickscene new sydney_cbd --bbox 151.200,-33.875,151.215,-33.860 --render-distance 30
python -m quickscene build sydney_cbd            # fetch → clean → classify → build_dsf
python -m quickscene build sydney_cbd --steps clean,classify,build_dsf   # after changing settings, no re-download
python -m quickscene install sydney_cbd
python -m quickscene uninstall sydney_cbd
python -m quickscene list
python -m quickscene reference sydney_cbd way/123   # one building as a Wavefront OBJ for Blender
python -m quickscene import "X-Plane 12/Custom Scenery/CityScene_sydney_cbd"   # pack -> project
```

`--zone area.geojson` works too. `--osm-file file.osm.pbf` (or a download link) reads OSM from a file.

## OSM data from a file (BBBike, Geofabrik)

When the Overpass servers are busy, choose "OSM file (.osm.pbf) or download link" in the project settings and give a
local file or link: [BBBike](https://extract.bbbike.org/) (ready-made city files or a custom area; "Get this area from
BBBike…" fills in the drawn outline, "Check BBBike" picks up the link when it is ready) or
[Geofabrik](https://download.geofabrik.de/) (per country or state). The file is read with
[pyosmium](https://osmcode.org/pyosmium/).

## Render distance

X-Plane 12's default facades stop drawing at 8–18 km. Past that, merged low-poly blocks take over up to the render
distance (default 30 km, `--render-distance`, `dsf.render_distance_km`).

## City pipeline

| Step | What it does |
|------|--------------|
| fetch | Downloads `building`, `building:part` and `highway` (plus trees and woods when switched on) from Overpass in grid chunks, cached per project |
| clean | Assembles outlines and relations, keeps buildings whose centroid is in the area, splits `building:part`s, fills heights (`height` → `building:levels` × level height → default), cuts or drops buildings on roads, removes duplicates and overlaps |
| classify | Sorts buildings into high / mid / low rise and heritage, picks a default facade whose floor range fits (or the facade chosen in an edit) |
| build_dsf | One DSF per 1° tile: facades, models, placed items, OSM trees and rectangle exclusions for default objects and facades; compiled with DSFTool and decompiled to check nothing was dropped; distant blocks up to the render distance |

## Files

| Path | Contents |
|------|----------|
| `config/settings.yaml` | Global defaults: heights, cleaning, facades, exclusions, trees |
| `config/local.yaml` | Machine paths (X-Plane, DSFTool) and OSM servers, not committed |
| `jobs/<project>/` | City project: `job.json`, `zone.geojson`, `raw/`, `processed/`, `overrides.json`, `placements.json`, `models/`, `build.log` |
| `airports/<project>/` | Airport project: `airport.json` (the model), `models/`, `history/` (earlier saves) |
| `cache/` | Library index, Global Airports index, converted textures |
| `build/<pack>/` | Generated packs, linked into Custom Scenery on install |

## What install does

- Creates a folder link in `Custom Scenery/` to `build/<pack>/` (symlink, or a junction any user can create)
- Backs up `scenery_packs.ini` with a timestamp and inserts the pack's line just before `*GLOBAL_AIRPORTS*`,
  leaving every other line untouched (exclusions only affect packs listed below)
- Refuses to install or uninstall while X-Plane is running (it rewrites the ini when it quits)

## Known limitations

- Generated scenery has not yet been checked in the sim for every feature (trees, placed items, airport export)
- Buildings without OSM `height` / `building:levels` get the default height; X-Plane 12's generic facades have no
  single-storey style
- Previews are approximate: no animation, no night lighting, type 2 facades shown as textured boxes
- Model import packs all materials into one atlas (an OBJ8 has one texture); tiled textures are wrapped
- The airport editor shows Bézier curves and their handles but draws new lines and pavements with straight segments
  (switch a point to "curved" to add a handle)

## Building a release

```
pip install pyinstaller
powershell -ExecutionPolicy Bypass -File packaging/build_release.ps1
```

This writes `dist/QuickScene-<version>-windows-x64.zip`. Pushing a `v*` tag runs the same build on GitHub Actions.

## Tests

```
python -m pytest -q
```

No network access; the end-to-end tests compile real DSFs with DSFTool (skipped without it).

## License

Copyright (C) 2026 Hanyi Zhang

This program is free software: you can redistribute it and/or modify it under the terms of the
GNU General Public License as published by the Free Software Foundation, either version 3 of the License,
or (at your option) any later version. See [LICENSE](LICENSE).

The license covers this tool's source code only. Scenery it generates contains OpenStreetMap data (ODbL) and
references X-Plane libraries, which keep their own licences.

## Data and attribution

- Buildings, trees, OSM airport drafts: © OpenStreetMap contributors, ODbL 1.0. Keep the README.txt attribution
  inside any scenery you publish
- Facades and default assets: X-Plane 12 default library (Laminar Research)
- Third-party libraries you use stay under their authors' terms; check them before publishing scenery that needs them
- Airports from the Scenery Gateway / X-Plane's database: GPL v2+ scenery by the Gateway community
- Satellite imagery in the editor: Esri World Imagery, shown for orientation; check Esri's terms before deriving data
  from it (or use imagery you have rights to through the custom imagery URL)
