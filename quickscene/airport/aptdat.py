"""apt.dat (format 1200, read also 1000 / 1100) <-> the airport model (model.py).

Reading keeps everything: rows the model does not break down (ATC flows 1000-1110, truck routes 1206 / 1400 / 1401
and anything unknown) go to "raw" and are written back verbatim, in their original order relative to each other.
Bezier nodes (112 / 114 / 116) keep their control point. 50-56 frequencies (old 10 kHz units) are read as
1050-1056 (kHz). Writing follows WED's order: header, 1302 metadata, runways, pavements, linear features,
boundaries, viewpoint, beacon, windsocks, signs, light fixtures, startup locations, frequencies, raw ATC rows,
taxi route network, other raw rows; then 99 when writing a whole file.
"""

from quickscene.airport import model

HEADER_CODES = {"1": "land", "16": "sea", "17": "heli"}
NODE_CODES = {"111", "112", "113", "114", "115", "116"}
FLOW_CODES = {"1000", "1001", "1002", "1003", "1004", "1100", "1101", "1110"}


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def split_airports(text: str) -> list[list[str]]:
    """apt.dat text -> one list of rows per airport (header row first). File headers and 99 are dropped."""
    airports, current = [], None
    for line in text.splitlines():
        words = line.split(None, 1)
        if not words:
            continue
        if words[0] in HEADER_CODES:
            current = [line.rstrip()]
            airports.append(current)
        elif words[0] == "99":
            current = None
        elif current is not None:
            current.append(line.rstrip())
    return airports


def _node(words) -> tuple[dict, str]:
    code = words[0]
    lat, lon = _f(words[1]), _f(words[2])
    rest = words[3:]
    ctrl = None
    if code in ("112", "114", "116"):
        ctrl = [_f(words[4]), _f(words[3])]
        rest = words[5:]
    line = light = 0
    for v in rest:
        n = _i(v)
        if n >= 101:
            light = n
        elif not line:
            line = n
    return model.node(lon, lat, line, light, ctrl), code


def parse_airport(rows: list[str]) -> dict:
    """One airport block (from split_airports) -> airport model."""
    head = rows[0].split(None, 5)
    data = model.empty(head[4] if len(head) > 4 else "XXXX", head[5] if len(head) > 5 else "",
                       _f(head[1]) if len(head) > 1 else 0)
    data["header"]["type"] = HEADER_CODES.get(head[0], "land")
    chain = None          # the pavement / line / boundary receiving nodes
    ring: list = []
    last_startup = None
    last_edge = None
    for row in rows[1:]:
        words = row.split()
        code = words[0]
        if code in NODE_CODES and chain is not None:
            n, code = _node(words)
            ring.append(n)
            if code in ("113", "114", "115", "116"):
                if chain["kind"] == "lines":
                    chain["item"]["nodes"] = ring
                    chain["item"]["closed"] = code in ("113", "114")
                    chain = None
                else:
                    chain["item"]["rings"].append(ring)
                ring = []
            continue
        if chain is not None and code not in NODE_CODES:
            if ring:  # an unterminated chain: keep what was read
                if chain["kind"] == "lines":
                    chain["item"]["nodes"] = ring
                else:
                    chain["item"]["rings"].append(ring)
            chain, ring = None, []
        if code == "1302":
            parts = row.split(None, 2)
            if len(parts) >= 2:
                data["meta"][parts[1]] = parts[2] if len(parts) > 2 else ""
        elif code == "100" and len(words) >= 26:
            ends = []
            for k in (8, 17):
                e = words[k:k + 9]
                ends.append({"name": e[0], "lat": _f(e[1]), "lon": _f(e[2]), "displaced_m": _f(e[3]),
                             "blastpad_m": _f(e[4]), "markings": _i(e[5]), "approach_lights": _i(e[6]),
                             "tdz_lights": _i(e[7]), "reil": _i(e[8])})
            data["runways"].append({"id": model.new_id("rwy_"), "width_m": _f(words[1]), "surface": _i(words[2]),
                                    "shoulder": _i(words[3]), "smoothness": _f(words[4]),
                                    "centre_lights": _i(words[5]), "edge_lights": _i(words[6]),
                                    "distance_signs": _i(words[7]), "ends": ends})
        elif code == "101" and len(words) >= 9:
            data["water_runways"].append({
                "id": model.new_id("wrw_"), "width_m": _f(words[1]), "buoys": _i(words[2]),
                "ends": [{"name": words[3], "lat": _f(words[4]), "lon": _f(words[5])},
                         {"name": words[6], "lat": _f(words[7]), "lon": _f(words[8])}]})
        elif code == "102" and len(words) >= 12:
            data["helipads"].append({"id": model.new_id("hp_"), "name": words[1], "lat": _f(words[2]),
                                     "lon": _f(words[3]), "heading": _f(words[4]), "length_m": _f(words[5]),
                                     "width_m": _f(words[6]), "surface": _i(words[7]), "markings": _i(words[8]),
                                     "shoulder": _i(words[9]), "smoothness": _f(words[10]),
                                     "edge_lights": _i(words[11])})
        elif code in ("110", "120", "130"):
            if code == "110":
                parts = row.split(None, 4)
                item = {"id": model.new_id("pav_"), "surface": _i(parts[1]), "smoothness": _f(parts[2]),
                        "texture_heading": _f(parts[3]), "name": parts[4] if len(parts) > 4 else "", "rings": []}
                data["pavements"].append(item)
                chain = {"kind": "pavements", "item": item}
            elif code == "120":
                parts = row.split(None, 1)
                item = {"id": model.new_id("lin_"), "name": parts[1] if len(parts) > 1 else "", "closed": False,
                        "nodes": []}
                data["lines"].append(item)
                chain = {"kind": "lines", "item": item}
            else:
                parts = row.split(None, 1)
                item = {"id": model.new_id("bnd_"), "name": parts[1] if len(parts) > 1 else "", "rings": []}
                data["boundaries"].append(item)
                chain = {"kind": "boundaries", "item": item}
            ring = []
        elif code == "14" and len(words) >= 4:
            parts = row.split(None, 5)
            data["viewpoints"].append({"id": model.new_id("vp_"), "lat": _f(words[1]), "lon": _f(words[2]),
                                       "height_ft": _f(words[3]), "name": parts[5] if len(parts) > 5 else ""})
        elif code == "18" and len(words) >= 4:
            data["beacons"].append({"id": model.new_id("bcn_"), "lat": _f(words[1]), "lon": _f(words[2]),
                                    "type": _i(words[3])})
        elif code == "19" and len(words) >= 4:
            data["windsocks"].append({"id": model.new_id("ws_"), "lat": _f(words[1]), "lon": _f(words[2]),
                                      "lit": _i(words[3])})
        elif code == "20" and len(words) >= 7:
            parts = row.split(None, 6)
            data["signs"].append({"id": model.new_id("sgn_"), "lat": _f(words[1]), "lon": _f(words[2]),
                                  "heading": _f(words[3]), "size": _i(words[5]), "text": parts[6]})
        elif code == "21" and len(words) >= 6:
            parts = row.split(None, 7)
            data["light_fixtures"].append({"id": model.new_id("lf_"), "lat": _f(words[1]), "lon": _f(words[2]),
                                           "type": _i(words[3]), "heading": _f(words[4]), "angle": _f(words[5]),
                                           "runway": words[6] if len(words) > 6 else "",
                                           "description": parts[7] if len(parts) > 7 else ""})
        elif code == "1300" and len(words) >= 6:
            parts = row.split(None, 6)
            last_startup = {"id": model.new_id("st_"), "lat": _f(words[1]), "lon": _f(words[2]),
                            "heading": _f(words[3]), "type": words[4],
                            "categories": [c for c in words[5].split("|") if c],
                            "name": parts[6] if len(parts) > 6 else "", "width_code": "",
                            "operation": "none", "airlines": []}
            data["startups"].append(last_startup)
        elif code == "15" and len(words) >= 4:  # old ramp start: lat lon heading name
            parts = row.split(None, 4)
            last_startup = {"id": model.new_id("st_"), "lat": _f(words[1]), "lon": _f(words[2]),
                            "heading": _f(words[3]), "type": "misc", "categories": list(model.CATEGORIES[:4]),
                            "name": parts[4] if len(parts) > 4 else "", "width_code": "", "operation": "none",
                            "airlines": []}
            data["startups"].append(last_startup)
        elif code == "1301" and last_startup is not None and len(words) >= 3:
            last_startup["width_code"] = words[1]
            last_startup["operation"] = words[2]
            last_startup["airlines"] = words[3:]
        elif code in ("50", "51", "52", "53", "54", "55", "56", "1050", "1051", "1052", "1053", "1054", "1055",
                      "1056") and len(words) >= 2:
            parts = row.split(None, 2)
            num = _i(code)
            khz = _f(words[1]) * (10 if num < 1000 else 1)
            data["frequencies"].append({"id": model.new_id("frq_"),
                                        "type": model.FREQUENCY_TYPES[num + 1000 if num < 1000 else num],
                                        "mhz": round(khz / 1000, 3), "name": parts[2] if len(parts) > 2 else ""})
        elif code == "1200":
            pass
        elif code == "1201" and len(words) >= 5:
            parts = row.split(None, 5)
            data["taxi_nodes"].append({"id": _i(words[4]), "lat": _f(words[1]), "lon": _f(words[2]),
                                       "usage": words[3], "name": parts[5] if len(parts) > 5 else ""})
        elif code == "1202" and len(words) >= 4:
            parts = row.split(None, 5)
            last_edge = {"id": model.new_id("te_"), "a": _i(words[1]), "b": _i(words[2]),
                         "oneway": words[3] == "oneway", "kind": words[4] if len(words) > 4 else "taxiway",
                         "name": parts[5] if len(parts) > 5 else "", "active": []}
            data["taxi_edges"].append(last_edge)
        elif code == "1204" and last_edge is not None and len(words) >= 3:
            last_edge["active"].append({"type": words[1], "runways": words[2]})
        else:
            data["raw"].append(row)
    if chain is not None and ring:
        if chain["kind"] == "lines":
            chain["item"]["nodes"] = ring
        else:
            chain["item"]["rings"].append(ring)
    return data


def parse_text(text: str) -> list[dict]:
    return [parse_airport(rows) for rows in split_airports(text)]


# ---------- writing ----------

def _ll(lon, lat) -> str:
    return f"{lat:.8f} {lon:.8f}"


def _chain(rings: list, closed: bool = True) -> list[str]:
    out = []
    for ring in rings:
        for i, n in enumerate(ring):
            last = i == len(ring) - 1
            bez = n.get("ctrl")
            if last:
                code = ("114" if bez else "113") if closed else ("116" if bez else "115")
            else:
                code = "112" if bez else "111"
            row = f"{code} {_ll(n['lon'], n['lat'])}"
            if bez:
                row += f" {_ll(bez[0], bez[1])}"
            if n.get("line") or n.get("light"):
                row += f" {int(n.get('line') or 0)}"
                if n.get("light"):
                    row += f" {int(n['light'])}"
            out.append(row)
    return out


def _num(v) -> str:
    return f"{v:.2f}"


def write_airport(data: dict, include_raw: bool = True) -> list[str]:
    h = data["header"]
    rows = [f"{model.HEADER_TYPES.get(h['type'], 1)} {round(float(h['elevation_ft']))} 0 0 {h['icao']} {h['name']}"]
    rows += [f"1302 {k} {v}".rstrip() for k, v in data["meta"].items()]
    for r in data["runways"]:
        row = (f"100 {_num(r['width_m'])} {int(r['surface'])} {int(r['shoulder'])} {r['smoothness']:.2f} "
               f"{int(r['centre_lights'])} {int(r['edge_lights'])} {int(r['distance_signs'])}")
        for e in r["ends"]:
            row += (f" {e['name']} {_ll(e['lon'], e['lat'])} {_num(e['displaced_m'])} {_num(e['blastpad_m'])} "
                    f"{int(e['markings'])} {int(e['approach_lights'])} {int(e['tdz_lights'])} {int(e['reil'])}")
        rows.append(row)
    for r in data["water_runways"]:
        a, b = r["ends"]
        rows.append(f"101 {_num(r['width_m'])} {int(r['buoys'])} {a['name']} {_ll(a['lon'], a['lat'])} "
                    f"{b['name']} {_ll(b['lon'], b['lat'])}")
    for p in data["helipads"]:
        rows.append(f"102 {p['name']} {_ll(p['lon'], p['lat'])} {_num(p['heading'])} {_num(p['length_m'])} "
                    f"{_num(p['width_m'])} {int(p['surface'])} {int(p['markings'])} {int(p['shoulder'])} "
                    f"{p['smoothness']:.2f} {int(p['edge_lights'])}")
    for p in data["pavements"]:
        rows.append(f"110 {int(p['surface'])} {p['smoothness']:.2f} {_num(p['texture_heading'])} {p['name']}".rstrip())
        rows += _chain(p["rings"])
    for line in data["lines"]:
        rows.append(f"120 {line['name']}".rstrip())
        rows += _chain([line["nodes"]], closed=bool(line.get("closed")))
    for b in data["boundaries"]:
        rows.append(f"130 {b['name']}".rstrip())
        rows += _chain(b["rings"])
    for v in data["viewpoints"]:
        rows.append(f"14 {_ll(v['lon'], v['lat'])} {round(float(v['height_ft']))} 0 {v['name']}".rstrip())
    for b in data["beacons"]:
        rows.append(f"18 {_ll(b['lon'], b['lat'])} {int(b['type'])}")
    for w in data["windsocks"]:
        rows.append(f"19 {_ll(w['lon'], w['lat'])} {int(w['lit'])}")
    for s in data["signs"]:
        rows.append(f"20 {_ll(s['lon'], s['lat'])} {_num(s['heading'])} 0 {int(s['size'])} {s['text']}")
    for f in data["light_fixtures"]:
        rows.append(f"21 {_ll(f['lon'], f['lat'])} {int(f['type'])} {_num(f['heading'])} {_num(f['angle'])} "
                    f"{f.get('runway') or ''} {f.get('description') or ''}".rstrip())
    for s in data["startups"]:
        rows.append(f"1300 {_ll(s['lon'], s['lat'])} {_num(s['heading'])} {s['type']} "
                    f"{'|'.join(s['categories']) or 'props'} {s['name']}".rstrip())
        if s.get("width_code"):
            rows.append(f"1301 {s['width_code']} {s.get('operation') or 'none'} {' '.join(s.get('airlines') or [])}"
                        .rstrip())
    codes = {v: k for k, v in model.FREQUENCY_TYPES.items()}
    for f in data["frequencies"]:
        rows.append(f"{codes[f['type']]} {round(float(f['mhz']) * 1000)} {f['name']}".rstrip())
    raw = data.get("raw", []) if include_raw else []
    rows += [r for r in raw if r.split()[0] in FLOW_CODES]
    if data["taxi_nodes"]:
        rows.append("1200")
        for n in data["taxi_nodes"]:
            rows.append(f"1201 {_ll(n['lon'], n['lat'])} {n['usage']} {int(n['id'])} {n.get('name', '')}".rstrip())
        for e in data["taxi_edges"]:
            rows.append(f"1202 {int(e['a'])} {int(e['b'])} {'oneway' if e['oneway'] else 'twoway'} {e['kind']} "
                        f"{e.get('name', '')}".rstrip())
            for a in e.get("active") or []:
                rows.append(f"1204 {a['type']} {a['runways']}")
    rows += [r for r in raw if r.split()[0] not in FLOW_CODES]
    return rows


def write_file(airports: list[dict], agent: str = "QuickScene") -> str:
    lines = ["I", f"1200 Generated by {agent}", ""]
    for data in airports:
        lines += write_airport(data) + [""]
    lines.append("99")
    return "\n".join(lines) + "\n"
