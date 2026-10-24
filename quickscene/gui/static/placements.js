// Placed scenery items of a city project (jobs/<name>/placements.json, quickscene/placements.py): objects, facades,
// forests, draped polygons, lines and object strings from any installed library or the project's own models.
// Pick an asset, then click (objects; keeps placing until Esc) or draw (shapes) on the map. Select an item on the map
// or in the list to edit it: drag it or its vertices, change its values, delete (Del), rotate (R / Shift+R),
// duplicate; Ctrl+Z / Ctrl+Y undo and redo. Save writes the list; Save and apply also regenerates the DSF.
"use strict";

const Place = {
  items: [],
  selected: null,
  layer: L.layerGroup(),
  layers: new Map(),
  editor: null,
  dirty: false,
  undo: new UndoStack(),
};
const PLACE_COLORS = { object: "#7b3fb5", fac: "#d9622b", for: "#2e7d4f", pol: "#4f7fa8", lin: "#e0b000", str: "#b3261e" };
const PLACE_TYPES = { object: ["obj", "agp"], fac: ["fac"], for: ["for"], pol: ["pol"], lin: ["lin", "str"] };

function placeExt(item) { return item.path.split(".").pop().toLowerCase(); }
function placeTool(item) { return item.kind === "object" ? "object" : placeExt(item) === "str" ? "lin" : placeExt(item); }
function placeColor(item) { return PLACE_COLORS[item.kind === "object" ? "object" : placeExt(item)] || "#666"; }
function placeName(item) { return item.name || item.path.split("/").pop(); }
function newPlaceId() { return Math.random().toString(16).slice(2, 12); }

function placeVisible() { return Boolean(currentJob) && !$("place-box").hidden; }

async function loadPlacements() {
  if (!currentJob) return;
  try {
    const r = await api(`/api/jobs/${currentJob}/placements`);
    Place.items = r.items;
    Place.undo.clear();
    Place.dirty = false;
    selectPlace(null);
    renderPlacements();
  } catch (e) { sayError(e); }
}

function clearPlacements() {
  Place.items = [];
  Place.dirty = false;
  selectPlace(null);
  renderPlacements();
}

function changedPlacements() {
  Place.dirty = true;
  renderPlaceList();
}

function snapshot() { Place.undo.push(Place.items); }

function itemLatLngs(item) {
  if (item.kind === "object") return [[item.lat, item.lon]];
  const pts = item.kind === "line" ? item.points : item.rings[0];
  return pts.map((p) => [p[1], p[0]]);
}

function drawItem(item) {
  const color = placeColor(item);
  const sel = Place.selected === item.id;
  let layer;
  if (item.kind === "object") {
    layer = L.marker([item.lat, item.lon], { icon: arrowIcon(sel ? "#f2c200" : color, item.heading, "arrow", sel ? 24 : 18), draggable: sel, keyboard: false });
    layer.on("dragstart", snapshot);
    layer.on("dragend", (e) => { const ll = e.target.getLatLng(); item.lat = ll.lat; item.lon = ll.lng; changedPlacements(); fillPlaceForm(); });
  } else if (item.kind === "line" || (placeExt(item) === "for" && item.fill === "line")) {
    layer = L.polyline(itemLatLngs(item), { color: sel ? "#f2c200" : color, weight: sel ? 4 : 3, dashArray: placeExt(item) === "str" ? "6 4" : null });
  } else if (placeExt(item) === "for" && item.fill === "points") {
    layer = L.layerGroup(itemLatLngs(item).map((ll) => L.circleMarker(ll, { radius: 4, color, fillOpacity: 0.7, weight: 1 })));
  } else {
    const rings = item.rings.map((r) => r.map((p) => [p[1], p[0]]));
    layer = L.polygon(rings, { color: sel ? "#f2c200" : color, weight: sel ? 3 : 1.5, fillOpacity: placeExt(item) === "for" ? 0.25 : 0.4 });
  }
  layer.on("click", (e) => { L.DomEvent.stopPropagation(e); selectPlace(item.id); });
  if (layer.bindTooltip) layer.bindTooltip(placeName(item), { sticky: true });
  return layer;
}

function renderPlacements() {
  Place.layer.clearLayers();
  Place.layers.clear();
  if (!currentJob) { Place.layer.remove(); renderPlaceList(); return; }
  Place.layer.addTo(map);
  for (const item of Place.items) {
    const layer = drawItem(item);
    Place.layers.set(item.id, layer);
    Place.layer.addLayer(layer);
  }
  renderPlaceList();
}

function refreshItem(item) {
  const old = Place.layers.get(item.id);
  if (old) Place.layer.removeLayer(old);
  const layer = drawItem(item);
  Place.layers.set(item.id, layer);
  Place.layer.addLayer(layer);
}

function renderPlaceList() {
  const f = $("place-filter").value.trim().toLowerCase();
  const shown = Place.items.filter((i) => !f || i.path.toLowerCase().includes(f) || (i.name || "").toLowerCase().includes(f));
  $("place-count").textContent = t("placeCount", { n: Place.items.length }) + (Place.dirty ? " · " + t("unsaved") : "");
  $("place-list").innerHTML = shown.slice(0, 300).map((i) => `<li data-id="${esc(i.id)}" class="${Place.selected === i.id ? "on" : ""}">
    <span class="badge" style="background:${placeColor(i)}">${esc(placeExt(i))}</span>
    <span class="item-text" title="${esc(i.path)}">${esc(placeName(i))}</span></li>`).join("") ||
    `<li class="empty">${esc(t("placeNone"))}</li>`;
  $("place-list").querySelectorAll("li[data-id]").forEach((li) => {
    li.onclick = () => { selectPlace(li.dataset.id); const it = Place.items.find((x) => x.id === li.dataset.id); if (it) map.panTo(itemLatLngs(it)[0]); };
  });
}

function selectPlace(id) {
  const prev = Place.selected;
  Place.selected = id;
  if (Place.editor) { Place.editor.remove(); Place.editor = null; }
  const item = Place.items.find((i) => i.id === id);
  for (const pid of [prev, id]) {
    const it = Place.items.find((i) => i.id === pid);
    if (it) refreshItem(it);
  }
  if (item && item.kind !== "object" && !(placeExt(item) === "for" && item.fill === "points" && item.rings[0].length > 200)) {
    const isLine = item.kind === "line";
    Place.editor = new VertexEditor(map, {
      rings: () => (isLine ? [item.points] : [item.rings[0]]),
      get: (p) => [p[1], p[0]],
      set: (p, lat, lng) => { p[0] = lng; p[1] = lat; },
      make: (lat, lng, near) => [lng, lat, ...near.slice(2)],
      closed: !isLine && !(placeExt(item) === "for" && item.fill !== "area"),
      min: isLine || placeExt(item) === "fac" ? 2 : placeExt(item) === "for" && item.fill === "points" ? 1 : 3,
      onDrag: () => { const l = Place.layers.get(item.id); if (l && l.setLatLngs) l.setLatLngs(isLine || item.fill === "line" ? itemLatLngs(item) : item.rings.map((r) => r.map((p) => [p[1], p[0]]))); },
      onChange: () => { changedPlacements(); refreshItem(item); },
    });
    Place.editor.layer.on("mousedown", snapshot);
  }
  fillPlaceForm();
  renderPlaceList();
}

function num(v, d = 0) { const n = parseFloat(v); return Number.isFinite(n) ? n : d; }

function fillPlaceForm() {
  const item = Place.items.find((i) => i.id === Place.selected);
  const form = $("place-form");
  form.hidden = !item;
  if (!item) return;
  const ext = placeExt(item);
  const rows = [`<div class="lib-path">${esc(item.path)}</div>`,
    `<label><span>${esc(t("placeAsset"))}</span><span class="path-row"><input id="pf-path" value="${esc(item.path)}"><button class="libpick" data-for="pf-path" data-types="${PLACE_TYPES[placeTool(item)].join(",")}">${esc(t("fromLibrary"))}</button></span></label>`];
  if (item.kind === "object") {
    rows.push(`<div class="row three"><label><span>${esc(t("heading"))}</span><input id="pf-heading" type="number" step="1" value="${item.heading}"></label>
      <label><span>${esc(t("placeAgl"))}</span><input id="pf-agl" type="number" step="0.1" min="0" value="${item.agl || 0}"></label></div>
      <div class="row"><button data-rot="-15">⟲ 15°</button><button data-rot="15">⟳ 15°</button><button data-rot="90">90°</button></div>`);
  } else if (ext === "fac") {
    rows.push(`<label><span>${esc(t("placeHeight"))}</span><input id="pf-height" type="number" min="1" max="500" step="1" value="${item.height}"></label>`);
  } else if (ext === "for") {
    rows.push(`<div class="row three"><label><span>${esc(t("placeDensity"))}</span><input id="pf-density" type="number" min="0" max="255" value="${item.density}"></label>
      <label><span>${esc(t("placeFill"))}</span><select id="pf-fill">${["area", "line", "points"].map((f) => `<option value="${f}" ${item.fill === f ? "selected" : ""}>${esc(t("fill_" + f))}</option>`).join("")}</select></label></div>`);
  } else if (ext === "pol") {
    rows.push(`<label><span>${esc(t("placeTexHeading"))}</span><input id="pf-heading" type="number" step="1" value="${item.heading || 0}"></label>`);
  } else if (ext === "lin") {
    rows.push(`<label class="check"><input id="pf-closed" type="checkbox" ${item.closed ? "checked" : ""}> ${esc(t("placeClosed"))}</label>`);
  } else if (ext === "str") {
    rows.push(`<label><span>${esc(t("placeSpacing"))}</span><input id="pf-spacing" type="number" min="0.5" step="0.5" value="${item.spacing || 10}"></label>`);
  }
  rows.push(`<div class="row wrap"><button id="pf-dup">${esc(t("duplicate"))}</button><button id="pf-del" class="danger">${esc(t("delete"))}</button><button id="pf-close" class="ghost">${esc(t("deselect"))}</button></div>
    <p class="hint">${esc(t(item.kind === "object" ? "placeObjHint" : "placeShapeHint"))}</p>`);
  form.innerHTML = rows.join("");
  const bind = (id, fn) => { const el = $(id); if (el) el.onchange = () => { snapshot(); fn(el); changedPlacements(); refreshItem(item); fillPlaceForm(); }; };
  bind("pf-path", (el) => {
    const v = el.value.trim();
    const ok = PLACE_TYPES[placeTool(item)].includes(v.split(".").pop().toLowerCase());
    if (ok) item.path = v; else say("placeWrongType", {}, "warn");
  });
  bind("pf-heading", (el) => { item.heading = ((num(el.value) % 360) + 360) % 360; });
  bind("pf-agl", (el) => { item.agl = Math.max(0, num(el.value)); });
  bind("pf-height", (el) => { item.height = Math.min(500, Math.max(1, Math.round(num(el.value, 10)))); });
  bind("pf-density", (el) => { item.density = Math.min(255, Math.max(0, Math.round(num(el.value, 255)))); });
  bind("pf-fill", (el) => { item.fill = el.value; selectPlace(item.id); });
  bind("pf-closed", (el) => { item.closed = el.checked; });
  bind("pf-spacing", (el) => { item.spacing = Math.max(0.5, num(el.value, 10)); });
  form.querySelectorAll("[data-rot]").forEach((b) => { b.onclick = () => rotateSelected(Number(b.dataset.rot)); });
  $("pf-dup").onclick = duplicateSelected;
  $("pf-del").onclick = deleteSelected;
  $("pf-close").onclick = () => selectPlace(null);
}

function rotateSelected(deg) {
  const item = Place.items.find((i) => i.id === Place.selected);
  if (!item || item.kind !== "object") return;
  snapshot();
  item.heading = (((item.heading + deg) % 360) + 360) % 360;
  changedPlacements();
  refreshItem(item);
  fillPlaceForm();
}

function deleteSelected() {
  const i = Place.items.findIndex((x) => x.id === Place.selected);
  if (i < 0) return;
  snapshot();
  Place.items.splice(i, 1);
  changedPlacements();
  selectPlace(null);
  renderPlacements();
}

function duplicateSelected() {
  const item = Place.items.find((i) => i.id === Place.selected);
  if (!item) return;
  snapshot();
  const copy = JSON.parse(JSON.stringify(item));
  copy.id = newPlaceId();
  const shift = (p) => { const [lon, lat] = offsetLonLat(p[0], p[1], 8, 0); p[0] = lon; p[1] = lat; };
  if (copy.kind === "object") { const [lon, lat] = offsetLonLat(copy.lon, copy.lat, 8, 0); copy.lon = lon; copy.lat = lat; }
  else if (copy.kind === "line") copy.points.forEach(shift);
  else copy.rings.forEach((r) => r.forEach(shift));
  Place.items.push(copy);
  changedPlacements();
  renderPlacements();
  selectPlace(copy.id);
}

function startPlacing(tool, path) {
  const setStatus = (key, vars) => { $("place-status").textContent = key ? t(key, vars) : ""; };
  if (tool === "object") {
    const next = () => {
      setStatus("placeClickMap", { name: path.split("/").pop() });
      drawShape("marker", ([ll]) => {
        snapshot();
        const item = { id: newPlaceId(), kind: "object", path, lon: ll.lng, lat: ll.lat, heading: 0 };
        Place.items.push(item);
        changedPlacements();
        refreshItem(item);
        next();
      });
    };
    next();
    return;
  }
  const line = tool === "lin";
  setStatus(line ? "placeDrawLine" : "placeDrawShape", { name: path.split("/").pop() });
  drawShape(line ? "polyline" : "polygon", (lls) => {
    setStatus(null);
    snapshot();
    const pts = lls.map((ll) => [ll.lng, ll.lat]);
    const ext = path.split(".").pop().toLowerCase();
    const item = line ? { id: newPlaceId(), kind: "line", path, points: pts, closed: false, ...(ext === "str" ? { spacing: 10 } : {}) }
      : { id: newPlaceId(), kind: "polygon", path, rings: [pts],
        ...(tool === "fac" ? { height: 10 } : tool === "for" ? { density: 255, fill: "area" } : { heading: 0 }) };
    Place.items.push(item);
    changedPlacements();
    refreshItem(item);
    selectPlace(item.id);
  }, { color: PLACE_COLORS[tool] });
}

document.querySelectorAll("#place-tools [data-place]").forEach((b) => {
  b.onclick = () => {
    const tool = b.dataset.place;
    openLibrary({
      types: PLACE_TYPES[tool], owner: currentOwner(), title: t("placePickTitle"),
      onPick: (path) => startPlacing(tool, path),
    });
  };
});

$("place-filter").addEventListener("input", renderPlaceList);

async function savePlacements() {
  const r = await api(`/api/jobs/${currentJob}/placements`, { body: { items: Place.items } });
  Place.items = r.items;
  Place.dirty = false;
  renderPlacements();
  if (Place.selected) selectPlace(Place.selected);
  say("placeSaved", { n: r.items.length }, "ok");
}

$("place-save").onclick = () => savePlacements().catch(sayError);
$("place-apply").onclick = async () => {
  try {
    await savePlacements();
    logSince = 0;
    $("log").textContent = "";
    await api(`/api/jobs/${currentJob}/build`, { body: { steps: ["classify", "build_dsf"] } });
    say("started", {}, "ok");
    startPolling();
  } catch (e) { sayError(e); }
};
$("place-import").onclick = async () => {
  const path = $("place-import-path").value.trim();
  if (!path) return say("importNeedPath", {}, "warn");
  if (Place.dirty) await savePlacements().catch(sayError);
  say("placeImporting", {}, "");
  try {
    const r = await api(`/api/jobs/${currentJob}/import-dsf`, { body: { path } });
    Place.items = r.items;
    renderPlacements();
    say("placeImported", { n: r.added, skipped: r.report.outside || 0 }, "ok");
  } catch (e) { sayError(e); }
};

document.addEventListener("keydown", (e) => {
  if (currentMode() !== "city" || !placeVisible()) return;
  const typing = /INPUT|SELECT|TEXTAREA/.test(document.activeElement?.tagName || "");
  if ((e.ctrlKey || e.metaKey) && (e.key === "z" || e.key === "y") && !typing) {
    const state = e.key === "z" && !e.shiftKey ? Place.undo.undo(Place.items) : Place.undo.redo(Place.items);
    if (state) { Place.items = state; Place.dirty = true; selectPlace(null); renderPlacements(); }
    e.preventDefault();
    return;
  }
  if (typing || !Place.selected) return;
  if (e.key === "Delete" || e.key === "Backspace") { deleteSelected(); e.preventDefault(); }
  if (e.key === "r" || e.key === "R") rotateSelected(e.shiftKey ? -15 : 15);
  if ((e.ctrlKey || e.metaKey) && e.key === "d") { duplicateSelected(); e.preventDefault(); }
});

window.addEventListener("beforeunload", (e) => {
  if (Place.dirty || (typeof Apt !== "undefined" && Apt.dirty)) { e.preventDefault(); e.returnValue = ""; }
});
