// Map editing helpers shared by placed items (placements.js) and the airport editor (airport.js):
// - VertexEditor: drag vertices, click a midpoint handle to insert, right-click (or Alt+click) a vertex to delete
// - drawShape: one-off drawing with leaflet-draw's handlers (marker / polyline / polygon), Esc cancels
// - UndoStack: JSON snapshots with undo / redo
// - arrowIcon: a marker icon rotated to a heading; bezierPath: apt.dat Bezier chains as polylines for display
"use strict";

class VertexEditor {
  // rings(): arrays of points; get(p) -> [lat, lng]; set(p, lat, lng) moves p; make(lat, lng, neighbour) -> new point
  // closed: whether the last point joins the first; min: fewest points per ring; noInsert: no midpoint handles;
  // onDrag(): while dragging; onChange(): after every change; onSelect(p, ring, index): a vertex was clicked
  // (null when cleared). Bezier points: getCtrl(p) -> [lat, lng] or null and setCtrl(p, lat, lng) show the selected
  // vertex's control handle and its mirror, both draggable.
  constructor(mapRef, o) {
    this.map = mapRef;
    this.o = Object.assign({ closed: true, min: 2, onChange: () => {} }, o);
    this.layer = L.layerGroup().addTo(mapRef);
    this.selected = null;
    this.render();
  }

  select(p) {
    this.selected = p;
    this.render();
    if (this.o.onSelect) {
      const rings = this.o.rings();
      const ri = rings.findIndex((r) => r.includes(p));
      this.o.onSelect(p, ri, ri >= 0 ? rings[ri].indexOf(p) : -1);
    }
  }

  render() {
    this.layer.clearLayers();
    const rings = this.o.rings();
    if (this.selected && !rings.some((r) => r.includes(this.selected))) this.selected = null;
    rings.forEach((ring, ri) => {
      ring.forEach((p, i) => {
        const on = p === this.selected;
        const m = L.marker(this.o.get(p), { draggable: true, icon: VertexEditor.icon(on ? "s" : "v"), zIndexOffset: on ? 1100 : 1000, keyboard: false });
        m.on("drag", (e) => { const ll = e.target.getLatLng(); this.o.set(p, ll.lat, ll.lng); this.o.onDrag && this.o.onDrag(); });
        m.on("dragend", () => { this.o.onChange(); this.render(); });
        const remove = (e) => {
          L.DomEvent.stop(e);
          if (ring.length <= this.o.min) return;
          ring.splice(i, 1);
          if (p === this.selected) this.selected = null;
          this.o.onChange();
          this.render();
        };
        m.on("contextmenu", remove);
        m.on("click", (e) => {
          if (e.originalEvent.altKey) return remove(e);
          L.DomEvent.stop(e);
          if (this.o.onSelect || this.o.getCtrl) this.select(on ? null : p);
        });
        this.layer.addLayer(m);
        if (on && this.o.getCtrl) this.renderCtrl(p);
        const last = i === ring.length - 1;
        if (this.o.noInsert || (last && !this.o.closed)) return;
        const q = ring[(i + 1) % ring.length];
        const a = this.o.get(p), b = this.o.get(q);
        const mid = L.marker([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], { icon: VertexEditor.icon("m"), keyboard: false });
        mid.on("click", (e) => {
          L.DomEvent.stop(e);
          const ll = e.target.getLatLng();
          ring.splice(i + 1, 0, this.o.make(ll.lat, ll.lng, p));
          this.o.onChange();
          this.render();
        });
        this.layer.addLayer(mid);
      });
    });
  }

  renderCtrl(p) {
    const c = this.o.getCtrl(p);
    if (!c) return;
    const at = this.o.get(p);
    const mirror = () => { const c2 = this.o.getCtrl(p); return [2 * at[0] - c2[0], 2 * at[1] - c2[1]]; };
    const guide = L.polyline([mirror(), at, c], { color: "#f2a900", weight: 1, dashArray: "3 3", interactive: false });
    this.layer.addLayer(guide);
    const handle = (pos, isMirror) => {
      const h = L.marker(pos, { draggable: true, icon: VertexEditor.icon("c"), zIndexOffset: 1200, keyboard: false });
      h.on("drag", (e) => {
        const ll = e.target.getLatLng();
        const lat = isMirror ? 2 * at[0] - ll.lat : ll.lat;
        const lng = isMirror ? 2 * at[1] - ll.lng : ll.lng;
        this.o.setCtrl(p, lat, lng);
        guide.setLatLngs([mirror(), at, this.o.getCtrl(p)]);
        this.o.onDrag && this.o.onDrag();
      });
      h.on("dragend", () => { this.o.onChange(); this.render(); });
      this.layer.addLayer(h);
    };
    handle(c, false);
    handle(mirror(), true);
  }

  remove() { this.map.removeLayer(this.layer); }

  static icon(kind) {
    const cls = { v: "vx-handle", s: "vx-handle vx-on", m: "vx-mid", c: "vx-ctrl" }[kind];
    return L.divIcon({ className: cls, iconSize: kind === "m" ? [9, 9] : kind === "c" ? [9, 9] : [11, 11] });
  }
}

let activeDraw = null;

function cancelDraw() {
  if (activeDraw) { activeDraw.disable(); activeDraw = null; }
}

// kind: marker | polyline | polygon; done(latlngs) gets [L.LatLng] (one for a marker)
function drawShape(kind, done, style = {}) {
  cancelDraw();
  const shape = Object.assign({ color: "#f2a900", weight: 3 }, style);
  const handler = kind === "marker" ? new L.Draw.Marker(map, { icon: VertexEditor.icon("v") })
    : kind === "polyline" ? new L.Draw.Polyline(map, { shapeOptions: shape })
      : new L.Draw.Polygon(map, { allowIntersection: true, shapeOptions: shape });
  const onCreated = (e) => {
    map.off(L.Draw.Event.CREATED, onCreated);
    if (activeDraw !== handler) return;
    activeDraw = null;
    const layer = e.layer;
    if (kind === "marker") done([layer.getLatLng()]);
    else {
      const lls = layer.getLatLngs();
      done(Array.isArray(lls[0]) ? lls[0] : lls);
    }
  };
  map.on(L.Draw.Event.CREATED, onCreated);
  handler.on("disabled", () => { setTimeout(() => map.off(L.Draw.Event.CREATED, onCreated), 0); });
  handler.enable();
  activeDraw = handler;
  return handler;
}

document.addEventListener("keydown", (e) => { if (e.key === "Escape") cancelDraw(); });

class UndoStack {
  constructor(limit = 60) { this.limit = limit; this.past = []; this.future = []; }
  push(state) {
    const json = JSON.stringify(state);
    if (this.past[this.past.length - 1] === json) return;
    this.past.push(json);
    if (this.past.length > this.limit) this.past.shift();
    this.future = [];
  }
  undo(current) {
    if (!this.past.length) return null;
    this.future.push(JSON.stringify(current));
    return JSON.parse(this.past.pop());
  }
  redo(current) {
    if (!this.future.length) return null;
    this.past.push(JSON.stringify(current));
    return JSON.parse(this.future.pop());
  }
  clear() { this.past = []; this.future = []; }
}

// A marker icon: shape "arrow" (pointing to heading), "dot", "square", "plane", with a colour and optional label
function arrowIcon(color, heading = 0, shape = "arrow", size = 22, label = "") {
  const h = Number(heading) || 0;
  const svg = {
    arrow: `<path d="M11 1 L18 19 L11 14 L4 19 Z" fill="${color}" stroke="#1f2328" stroke-width="1"/>`,
    plane: `<path d="M11 1 C12 1 12.5 3 12.5 6 L20 11 L20 13 L12.5 10.5 L12 16 L15 18 L15 20 L11 19 L7 20 L7 18 L10 16 L9.5 10.5 L2 13 L2 11 L9.5 6 C9.5 3 10 1 11 1 Z" fill="${color}" stroke="#1f2328" stroke-width=".8"/>`,
    dot: `<circle cx="11" cy="11" r="6" fill="${color}" stroke="#1f2328" stroke-width="1.2"/>`,
    square: `<rect x="5" y="5" width="12" height="12" fill="${color}" stroke="#1f2328" stroke-width="1.2"/>`,
    sign: `<rect x="3" y="7" width="16" height="8" rx="1" fill="${color}" stroke="#1f2328" stroke-width="1"/><path d="M11 15 L11 20" stroke="#1f2328"/>`,
  }[shape] || "";
  const rotate = shape === "dot" || shape === "square" ? 0 : h;
  const text = label ? `<span class="ic-label">${esc(label)}</span>` : "";
  return L.divIcon({
    className: "rot-icon", iconSize: [size, size], iconAnchor: [size / 2, size / 2],
    html: `<svg width="${size}" height="${size}" viewBox="0 0 22 22" style="transform:rotate(${rotate}deg)">${svg}</svg>${text}`,
  });
}

// apt.dat chains: node.ctrl is the Bezier control point after the node; the segment from a to b is a cubic curve
// a -> a.ctrl -> mirror(b.ctrl about b) -> b (quadratic when only one end has a control point)
function bezierPath(nodes, closed, steps = 8) {
  const out = [];
  const n = nodes.length;
  if (!n) return out;
  const segs = closed ? n : n - 1;
  out.push([nodes[0].lat, nodes[0].lon]);
  for (let i = 0; i < segs; i++) {
    const a = nodes[i], b = nodes[(i + 1) % n];
    const c1 = a.ctrl ? [a.ctrl[0], a.ctrl[1]] : null;
    const c2 = b.ctrl ? [2 * b.lon - b.ctrl[0], 2 * b.lat - b.ctrl[1]] : null;
    if (!c1 && !c2) { out.push([b.lat, b.lon]); continue; }
    const p0 = [a.lon, a.lat], p3 = [b.lon, b.lat];
    const p1 = c1 || c2, p2 = c2 || c1;
    for (let k = 1; k <= steps; k++) {
      const tt = k / steps, u = 1 - tt;
      const x = u * u * u * p0[0] + 3 * u * u * tt * p1[0] + 3 * u * tt * tt * p2[0] + tt * tt * tt * p3[0];
      const y = u * u * u * p0[1] + 3 * u * u * tt * p1[1] + 3 * u * tt * tt * p2[1] + tt * tt * tt * p3[1];
      out.push([y, x]);
    }
  }
  return out;
}

// Metres east / north between two lon/lat points, and the inverse; bearing in degrees from true north
function metresBetween(lon1, lat1, lon2, lat2) {
  const k = Math.PI / 180, R = 6371008.8;
  return [(lon2 - lon1) * k * Math.cos(((lat1 + lat2) / 2) * k) * R, (lat2 - lat1) * k * R];
}

function offsetLonLat(lon, lat, east, north) {
  const k = Math.PI / 180, R = 6371008.8;
  return [lon + east / (R * Math.cos(lat * k) * k), lat + north / (R * k)];
}

function bearingDeg(lon1, lat1, lon2, lat2) {
  const [e, n] = metresBetween(lon1, lat1, lon2, lat2);
  return (Math.atan2(e, n) * 180 / Math.PI + 360) % 360;
}
