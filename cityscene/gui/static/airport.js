// Airport editor, following WED's design: a hierarchy of everything in the airport, a property panel for the
// selection, map tools that create each kind of element, validation before export, and the scenery library for
// objects. The model is apt.dat element by element (cityscene/airport/model.py); DSF items use the same placed-item
// format as city projects. Projects start from X-Plane's airport database, the Scenery Gateway, an installed pack,
// OpenStreetMap or a blank runway.
"use strict";

const Apt = {
  name: null,
  data: null,
  findings: [],
  summary: { errors: 0, warnings: 0 },
  state: {},
  enums: null,
  selected: null,       // {list, id} (taxi nodes use their numeric id)
  dirty: false,
  undo: new UndoStack(30),
  layer: L.layerGroup(),
  layers: new Map(),     // "list:id" -> layer
  editor: null,
  tool: "select",
  startTab: "new",
  source: "xplane",
  results: [],
  packs: null,
};

const APT_COLORS = {
  runway: "#3a3f46", runwayLine: "#ffffff", pavement: "#8a8f98", boundary: "#7b3fb5", line: "#e8b500",
  lineWhite: "#f3f3f3", lightGreen: "#2fbf4a", lightBlue: "#3b7ddd", route: "#2f6fb0", routeRunway: "#d9302b",
  startup: { gate: "#2f6fb0", hangar: "#7b3fb5", tie_down: "#2e7d4f", misc: "#646a73" },
  windsock: "#f08a00", beacon: "#2e7d4f", tower: "#1f2328", sign: "#f2c200", light: "#d9302b", helipad: "#0b9e9e",
  selected: "#f2c200",
};
const LIST_ORDER = ["runways", "water_runways", "helipads", "pavements", "lines", "boundaries", "startups",
  "taxi_edges", "taxi_nodes", "windsocks", "beacons", "viewpoints", "signs", "light_fixtures", "frequencies", "placements"];
const POINT_LISTS = ["helipads", "startups", "windsocks", "beacons", "viewpoints", "signs", "light_fixtures"];
const TOOLS = [
  ["select", "🖱"], ["runways", "▬"], ["helipads", "H"], ["pavements", "⬠"], ["lines", "〰"], ["boundaries", "⬡"],
  ["taxi", "⇢"], ["startups", "✈"], ["windsocks", "⚑"], ["beacons", "✦"], ["viewpoints", "♜"], ["signs", "▭"],
  ["light_fixtures", "◉"], ["object", "▣"], ["fac", "🏢"], ["for", "🌳"], ["pol", "▦"], ["lin", "┄"],
];

// ---------- panel ----------
function aptPanelHtml() {
  return `
  <section id="apt-start">
    <div class="tabs" role="tablist">
      <button class="tab" data-apttab="new">${esc(t("aptNew"))}</button>
      <button class="tab" data-apttab="open">${esc(t("aptOpen"))}</button>
    </div>
    <div data-aptpanel="new">
      <div class="label-text">${esc(t("aptSource"))}</div>
      <div class="source-grid">
        ${["xplane", "gateway", "pack", "osm", "blank"].map((s) => `<label class="check"><input type="radio" name="apt-source" value="${s}"> <span>${esc(t("aptSrc_" + s))}</span></label>`).join("")}
      </div>
      <p class="hint" id="apt-source-hint"></p>
      <div id="apt-search-row"><input id="apt-q" placeholder="${esc(t("aptSearchPh"))}"></div>
      <ul id="apt-results" class="item-list compact"></ul>
      <div id="apt-blank-box" hidden>
        <div class="row three">
          <label><span>ICAO</span><input id="apt-blank-icao" placeholder="XXXX"></label>
          <label><span>${esc(t("aptElevation"))}</span><input id="apt-blank-elev" type="number" value="0"></label>
        </div>
        <label><span>${esc(t("aptAirportName"))}</span><input id="apt-blank-name"></label>
        <p class="hint">${esc(t("aptBlankHint"))}</p>
      </div>
      <label class="check" id="apt-dsf-row" hidden><input id="apt-include-dsf" type="checkbox" checked> <span>${esc(t("aptIncludeDsf"))}</span></label>
      <label><span>${esc(t("aptProjectName"))}</span><input id="apt-name" placeholder="YMML"></label>
      <button id="apt-create" class="primary">${esc(t("aptCreate"))}</button>
    </div>
    <div data-aptpanel="open" hidden><ul id="apt-list" class="item-list"></ul></div>
  </section>
  <section id="apt-editor" hidden>
    <div class="project-row">
      <div><div id="apt-title" class="project-title"></div><div id="apt-sub" class="hint"></div></div>
      <button id="apt-switch" class="ghost">${esc(t("switchProject"))}</button>
    </div>
    <div class="toolbar tools-grid" id="apt-tools">
      ${TOOLS.map(([k, icon]) => `<button data-tool="${k}" title="${esc(t("tool_" + k))}"><span class="ti">${icon}</span><span class="tl">${esc(t("tool_" + k))}</span></button>`).join("")}
    </div>
    <p id="apt-status" class="hint"></p>
    <div class="row wrap savebar">
      <button id="apt-save" class="primary">${esc(t("aptSave"))}</button>
      <button id="apt-undo" class="ghost" title="Ctrl+Z">↶</button>
      <button id="apt-redo" class="ghost" title="Ctrl+Y">↷</button>
      <span id="apt-dirty" class="hint"></span>
    </div>
    <details id="apt-props-box" class="step" open><summary><h2>${esc(t("aptProps"))}</h2></summary><div id="apt-props"><p class="hint">${esc(t("aptNothingSelected"))}</p></div></details>
    <details id="apt-tree-box" class="step" open><summary><h2>${esc(t("aptTree"))}</h2></summary>
      <input id="apt-tree-filter" class="small" placeholder="${esc(t("filterPh"))}"><div id="apt-tree"></div></details>
    <details id="apt-valid-box" class="step" open><summary><h2>${esc(t("aptValidation"))} <span id="apt-valid-flag"></span></h2></summary><ul id="apt-findings" class="item-list compact"></ul></details>
    <details id="apt-export-box" class="step" open><summary><h2>${esc(t("aptExport"))}</h2></summary>
      <label><span>${esc(t("sceneryName"))}</span><input id="apt-pack"></label>
      <div class="label-text">${esc(t("aptExclude"))}</div>
      <div class="row wrap">${["obj", "fac", "for"].map((k) => `<label class="check"><input type="checkbox" data-excl="${k}"> ${esc(t("excl_" + k))}</label>`).join("")}</div>
      <p class="hint">${esc(t("aptExcludeHint"))}</p>
      <div class="row wrap">
        <button id="apt-export" class="primary">${esc(t("aptExportBtn"))}</button>
        <button id="apt-install">${esc(t("install"))}</button>
        <button id="apt-uninstall" class="ghost">${esc(t("uninstall"))}</button>
      </div>
      <div id="apt-export-result"></div>
      <details class="sub-details"><summary>${esc(t("aptHistory"))}</summary><ul id="apt-history" class="item-list compact"></ul></details>
    </details>
  </section>`;
}

function buildAirportPanel() {
  $("airport-panel").innerHTML = aptPanelHtml();
  document.querySelectorAll("[data-apttab]").forEach((b) => { b.onclick = () => showAptTab(b.dataset.apttab); });
  document.querySelectorAll('input[name="apt-source"]').forEach((r) => {
    r.checked = r.value === Apt.source;
    r.onchange = () => { Apt.source = r.value; store.set("cityscene.aptSource", r.value); updateSourceUi(); };
  });
  let timer = null;
  $("apt-q").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(searchAirports, 250); });
  $("apt-q").addEventListener("keydown", (e) => { if (e.key === "Enter") searchAirports(); });
  $("apt-create").onclick = createAirport;
  $("apt-switch").onclick = closeAirport;
  $("apt-save").onclick = () => saveAirport().catch(sayError);
  $("apt-undo").onclick = () => undoApt(false);
  $("apt-redo").onclick = () => undoApt(true);
  $("apt-tree-filter").addEventListener("input", renderTree);
  $("apt-export").onclick = exportAirport;
  $("apt-install").onclick = () => installAirport(true);
  $("apt-uninstall").onclick = () => installAirport(false);
  $("apt-pack").onchange = () => { snapApt(); Apt.data.options.scenery_name = $("apt-pack").value.trim(); markDirty(); };
  document.querySelectorAll("[data-excl]").forEach((c) => {
    c.onchange = () => {
      snapApt();
      Apt.data.options.exclude = [...document.querySelectorAll("[data-excl]:checked")].map((x) => x.dataset.excl);
      markDirty();
    };
  });
  document.querySelectorAll("#apt-tools [data-tool]").forEach((b) => { b.onclick = () => setTool(b.dataset.tool); });
  showAptTab(Apt.startTab);
  updateSourceUi();
  if (Apt.data) showEditor();
}

function refreshAirportLanguage() {
  buildAirportPanel();
}

function showAptTab(tab) {
  Apt.startTab = tab;
  document.querySelectorAll("[data-apttab]").forEach((b) => b.classList.toggle("on", b.dataset.apttab === tab));
  document.querySelectorAll("[data-aptpanel]").forEach((p) => { p.hidden = p.dataset.aptpanel !== tab; });
  if (tab === "open") loadAirportList();
}

function updateSourceUi() {
  const s = Apt.source;
  $("apt-source-hint").textContent = t("aptSrcHint_" + s);
  $("apt-search-row").hidden = s === "blank";
  $("apt-blank-box").hidden = s !== "blank";
  $("apt-dsf-row").hidden = s !== "pack";
  $("apt-results").innerHTML = "";
  Apt.results = [];
  if (s === "pack") searchAirports();
  else if ($("apt-q").value.trim()) searchAirports();
}

async function searchAirports() {
  const q = $("apt-q").value.trim();
  const s = Apt.source;
  try {
    if (s === "pack") {
      if (!Apt.packs) { $("apt-results").innerHTML = `<li class="empty">${esc(t("aptLoadingPacks"))}</li>`; Apt.packs = await api("/api/airports/packs"); }
      const low = q.toLowerCase();
      Apt.results = Apt.packs.flatMap((p) => p.airports.map((a) => ({ ...a, pack: p.pack, path: p.path })))
        .filter((a) => !low || a.icao.toLowerCase().includes(low) || a.name.toLowerCase().includes(low) || a.pack.toLowerCase().includes(low)).slice(0, 60);
    } else if (s === "xplane") {
      Apt.results = q ? await api("/api/airports/search?q=" + encodeURIComponent(q)) : [];
    } else {
      Apt.results = q ? [{ icao: q.toUpperCase(), name: t("aptCodeOnly") }] : [];
    }
    renderResults();
  } catch (e) { sayError(e); }
}

function renderResults() {
  $("apt-results").innerHTML = Apt.results.map((a, i) => `<li data-i="${i}" class="${Apt.chosen === a ? "on" : ""}">
    <div class="item-text"><b>${esc(a.icao)}</b> ${esc(a.name || "")}<div class="hint">${esc(a.pack || (a.lat != null ? `${a.lat.toFixed(3)}, ${a.lon.toFixed(3)} · ${a.elevation_ft} ft` : ""))}</div></div></li>`).join("") ||
    ($("apt-q").value.trim() ? `<li class="empty">${esc(t("aptNoResults"))}</li>` : "");
  $("apt-results").querySelectorAll("li[data-i]").forEach((li) => {
    li.onclick = () => {
      Apt.chosen = Apt.results[Number(li.dataset.i)];
      if (!$("apt-name").value || $("apt-name").dataset.auto === "1") { $("apt-name").value = Apt.chosen.icao.replace(/[^\w-]/g, "_"); $("apt-name").dataset.auto = "1"; }
      if (Apt.chosen.lat != null) map.setView([Apt.chosen.lat, Apt.chosen.lon], 14);
      renderResults();
    };
  });
}

async function createAirport() {
  const name = $("apt-name").value.trim();
  const s = Apt.source;
  let source;
  if (s === "blank") {
    const c = map.getCenter();
    const icao = $("apt-blank-icao").value.trim().toUpperCase() || name;
    source = { type: "blank", icao, name: $("apt-blank-name").value.trim() || icao, lon: c.lng, lat: c.lat,
      elevation_ft: parseFloat($("apt-blank-elev").value) || 0 };
  } else {
    const chosen = Apt.chosen && Apt.results.includes(Apt.chosen) ? Apt.chosen : Apt.results[0];
    if (!chosen) return say("aptChooseFirst", {}, "warn");
    source = { type: s, icao: chosen.icao };
    if (s === "pack") Object.assign(source, { path: chosen.path, dsf: $("apt-include-dsf").checked });
  }
  if (!name) return say("needName", {}, "warn");
  const list = await api("/api/airports");
  const exists = list.some((a) => a.name === name);
  if (exists && !confirm(t("confirmOverwrite", { name }))) return;
  $("apt-create").disabled = true;
  say("aptCreating", {}, "");
  try {
    const r = await api("/api/airports", { body: { name, source, overwrite: exists } });
    await openAirport(r.name, true);
    const c = r.report.counts || {};
    say("aptCreated", { name: r.name, runways: c.runways || 0, items: r.report.placements || 0 }, "ok");
  } catch (e) { sayError(e); } finally { $("apt-create").disabled = false; }
}

async function loadAirportList() {
  try {
    const list = await api("/api/airports");
    $("apt-list").innerHTML = list.map((a) => `<li><div class="item-text"><b>${esc(a.name)}</b> ${esc(a.title)}<div class="hint">${esc(a.scenery_name || "")} · ${esc(t(a.built ? "stateBuilt" : "stateNotBuilt"))}</div></div>
      <div class="item-actions"><button class="ghost" data-open="${esc(a.name)}">${esc(t("openBtn"))}</button></div></li>`).join("") ||
      `<li class="empty">${esc(t("aptNoProjects"))}</li>`;
    $("apt-list").querySelectorAll("[data-open]").forEach((b) => { b.onclick = () => openAirport(b.dataset.open, true).catch(sayError); });
  } catch (e) { sayError(e); }
}

async function openAirport(name, fit) {
  if (!Apt.enums) Apt.enums = await api("/api/airports/enums");
  const r = await api(`/api/airports/${encodeURIComponent(name)}`);
  Apt.name = r.name;
  Apt.data = r.data;
  Apt.findings = r.findings;
  Apt.summary = r.summary;
  Apt.state = r;
  Apt.selected = null;
  Apt.dirty = false;
  Apt.undo.clear();
  store.set("cityscene.aptLast", name);
  showEditor();
  renderAirport();
  if (fit) fitAirport();
}

function fitAirport() {
  const pts = [];
  for (const r of Apt.data.runways) for (const e of r.ends) pts.push([e.lat, e.lon]);
  for (const b of Apt.data.boundaries) for (const n of b.rings[0] || []) pts.push([n.lat, n.lon]);
  if (!pts.length) for (const p of Apt.data.helipads.concat(Apt.data.startups)) pts.push([p.lat, p.lon]);
  if (pts.length) map.fitBounds(L.latLngBounds(pts).pad(0.1));
}

function closeAirport() {
  if (Apt.dirty && !confirm(t("aptDiscard"))) return;
  setTool("select");
  if (Apt.editor) { Apt.editor.remove(); Apt.editor = null; }
  Apt.name = null;
  Apt.data = null;
  Apt.dirty = false;
  Apt.layer.clearLayers();
  $("apt-editor").hidden = true;
  $("apt-start").hidden = false;
  showAptTab("open");
}

function showEditor() {
  $("apt-start").hidden = true;
  $("apt-editor").hidden = false;
  const h = Apt.data.header;
  $("apt-title").textContent = `${h.icao} · ${h.name}`;
  $("apt-sub").textContent = t("aptSub", { name: Apt.name, ft: h.elevation_ft });
  $("apt-pack").value = Apt.data.options.scenery_name || "";
  document.querySelectorAll("[data-excl]").forEach((c) => { c.checked = (Apt.data.options.exclude || []).includes(c.dataset.excl); });
  renderTree();
  renderFindings();
  renderProps();
  renderDirty();
  renderAptState();
  loadHistory();
}

// ---------- editing state ----------
function snapApt() { Apt.undo.push(Apt.data); }

function markDirty() {
  Apt.dirty = true;
  renderDirty();
}

function renderDirty() {
  $("apt-dirty").textContent = Apt.dirty ? t("unsaved") : t("aptSaved");
}

function undoApt(redo) {
  const state = redo ? Apt.undo.redo(Apt.data) : Apt.undo.undo(Apt.data);
  if (!state) return;
  Apt.data = state;
  markDirty();
  if (Apt.selected && !findItem(Apt.selected)) Apt.selected = null;
  renderAirport();
  renderProps();
}

function list(name) { return name === "placements" ? Apt.data.placements : Apt.data[name]; }

function findItem(sel) {
  if (!sel) return null;
  return (list(sel.list) || []).find((x) => x.id === sel.id) || null;
}

async function saveAirport() {
  const r = await api(`/api/airports/${encodeURIComponent(Apt.name)}`, { body: { data: Apt.data } });
  Apt.findings = r.findings;
  Apt.summary = r.summary;
  Apt.data.placements = r.placements;
  Apt.data.options = r.options;
  Apt.dirty = false;
  renderDirty();
  renderFindings();
  renderAirport();
  loadHistory();
  say("aptSavedMsg", { errors: r.summary.errors, warnings: r.summary.warnings }, r.summary.errors ? "warn" : "ok");
}

// ---------- map rendering ----------
function key(listName, id) { return `${listName}:${id}`; }

function runwayPolygon(r) {
  const [a, b] = r.ends;
  const brg = bearingDeg(a.lon, a.lat, b.lon, b.lat) * Math.PI / 180;
  const half = r.width_m / 2;
  const px = Math.cos(brg) * half, py = -Math.sin(brg) * half; // perpendicular (east, north)
  const c = (p, s) => { const [lon, lat] = offsetLonLat(p.lon, p.lat, s * px, s * py); return [lat, lon]; };
  return [c(a, 1), c(b, 1), c(b, -1), c(a, -1)];
}

function lineColor(nodes) {
  const n = nodes.find((x) => x.line) || nodes.find((x) => x.light) || {};
  if (n.line >= 20 && n.line < 30) return APT_COLORS.lineWhite;
  if (n.line) return APT_COLORS.line;
  if (n.light === 102 || n.light === 106) return APT_COLORS.lightBlue;
  return APT_COLORS.lightGreen;
}

function drawAptItem(listName, item) {
  const sel = Apt.selected && Apt.selected.list === listName && Apt.selected.id === item.id;
  const hl = sel ? APT_COLORS.selected : null;
  let layer = null;
  switch (listName) {
    case "runways": {
      const [a, b] = item.ends;
      layer = L.featureGroup([
        L.polygon(runwayPolygon(item), { color: hl || "#1f2328", weight: sel ? 3 : 1, fillColor: APT_COLORS.runway, fillOpacity: 0.85 }),
        L.polyline([[a.lat, a.lon], [b.lat, b.lon]], { color: APT_COLORS.runwayLine, weight: 1.5, dashArray: "10 8", interactive: false }),
        L.marker([a.lat, a.lon], { icon: L.divIcon({ className: "rwy-label", html: esc(a.name), iconSize: [30, 16] }), interactive: false }),
        L.marker([b.lat, b.lon], { icon: L.divIcon({ className: "rwy-label", html: esc(b.name), iconSize: [30, 16] }), interactive: false }),
      ]);
      break;
    }
    case "water_runways": {
      const r = { ...item, width_m: item.width_m };
      layer = L.polygon(runwayPolygon(r), { color: hl || "#2f6fb0", weight: 1, fillOpacity: 0.3 });
      break;
    }
    case "pavements":
      layer = L.polygon(item.rings.map((ring) => bezierPath(ring, true)), { color: hl || "#646a73", weight: sel ? 3 : 0.8, fillColor: APT_COLORS.pavement, fillOpacity: 0.55 });
      break;
    case "boundaries":
      layer = L.polygon(item.rings.map((ring) => bezierPath(ring, true)), { color: hl || APT_COLORS.boundary, weight: sel ? 3 : 2, dashArray: "8 6", fill: false });
      break;
    case "lines":
      layer = L.polyline(bezierPath(item.nodes, item.closed), { color: hl || lineColor(item.nodes), weight: sel ? 4 : 2 });
      break;
    case "taxi_edges": {
      const nodes = Apt.nodeIndex;
      const a = nodes.get(item.a), b = nodes.get(item.b);
      if (!a || !b) return null;
      layer = L.polyline([[a.lat, a.lon], [b.lat, b.lon]], { color: hl || (item.kind === "runway" ? APT_COLORS.routeRunway : APT_COLORS.route), weight: sel ? 5 : 2.5, opacity: 0.85, dashArray: item.oneway ? "6 4" : null });
      break;
    }
    case "taxi_nodes":
      layer = L.circleMarker([item.lat, item.lon], { radius: sel ? 6 : 3.5, color: hl || APT_COLORS.route, fillColor: "#fff", fillOpacity: 1, weight: 2 });
      break;
    case "startups":
      layer = L.marker([item.lat, item.lon], { icon: arrowIcon(hl || APT_COLORS.startup[item.type] || "#666", item.heading, "plane", sel ? 28 : 22), draggable: sel, keyboard: false });
      break;
    case "helipads":
      layer = L.marker([item.lat, item.lon], { icon: arrowIcon(hl || APT_COLORS.helipad, item.heading, "square", 20, "H"), draggable: sel, keyboard: false });
      break;
    case "windsocks":
      layer = L.marker([item.lat, item.lon], { icon: arrowIcon(hl || APT_COLORS.windsock, 0, "dot", 16), draggable: sel, keyboard: false });
      break;
    case "beacons":
      layer = L.marker([item.lat, item.lon], { icon: arrowIcon(hl || APT_COLORS.beacon, 0, "square", 16), draggable: sel, keyboard: false });
      break;
    case "viewpoints":
      layer = L.marker([item.lat, item.lon], { icon: arrowIcon(hl || APT_COLORS.tower, 0, "square", 18, "T"), draggable: sel, keyboard: false });
      break;
    case "signs":
      layer = L.marker([item.lat, item.lon], { icon: arrowIcon(hl || APT_COLORS.sign, item.heading, "sign", 18), draggable: sel, keyboard: false });
      break;
    case "light_fixtures":
      layer = L.marker([item.lat, item.lon], { icon: arrowIcon(hl || APT_COLORS.light, item.heading, "arrow", 16), draggable: sel, keyboard: false });
      break;
    case "placements": {
      const color = hl || placeColor(item);
      if (item.kind === "object") layer = L.marker([item.lat, item.lon], { icon: arrowIcon(color, item.heading, "arrow", sel ? 22 : 15), draggable: sel, keyboard: false });
      else if (item.kind === "line") layer = L.polyline(item.points.map((p) => [p[1], p[0]]), { color, weight: sel ? 4 : 2 });
      else layer = L.polygon(item.rings.map((r) => r.map((p) => [p[1], p[0]])), { color, weight: sel ? 3 : 1, fillOpacity: 0.35 });
      break;
    }
    default:
      return null;
  }
  if (layer.options && layer.options.draggable) {
    layer.on("dragstart", snapApt);
    layer.on("dragend", (e) => {
      const ll = e.target.getLatLng();
      item.lat = ll.lat;
      item.lon = ll.lng;
      markDirty();
      renderProps();
    });
  }
  layer.on("click", (e) => { L.DomEvent.stopPropagation(e); if (Apt.tool === "select") selectApt(listName, item.id); });
  const label = itemLabel(listName, item);
  if (label && layer.bindTooltip && listName !== "runways") layer.bindTooltip(label, { sticky: true });
  return layer;
}

function renderAirport() {
  Apt.layer.clearLayers();
  Apt.layers.clear();
  if (!Apt.data || currentMode() !== "airport") { Apt.layer.remove(); return; }
  Apt.layer.addTo(map);
  Apt.nodeIndex = new Map(Apt.data.taxi_nodes.map((n) => [n.id, n]));
  const showNodes = nodesVisible();
  const detail = detailVisible();
  Apt.nodesShown = showNodes;
  Apt.detailShown = detail;
  for (const listName of ["boundaries", "pavements", "water_runways", "runways", "lines", "placements", "taxi_edges", "taxi_nodes", ...POINT_LISTS]) {
    if (listName === "taxi_nodes" && !showNodes) continue;
    if (!detail && DETAIL_LISTS.includes(listName) && !(Apt.selected && Apt.selected.list === listName)) continue;
    for (const item of list(listName)) {
      const layer = drawAptItem(listName, item);
      if (!layer) continue;
      Apt.layers.set(key(listName, item.id), layer);
      Apt.layer.addLayer(layer);
    }
  }
  renderTree();
}

function redrawItem(listName, item) {
  const k = key(listName, item.id);
  const old = Apt.layers.get(k);
  if (old) Apt.layer.removeLayer(old);
  const layer = drawAptItem(listName, item);
  if (layer) { Apt.layers.set(k, layer); Apt.layer.addLayer(layer); }
  if (listName === "taxi_nodes") {
    for (const e of Apt.data.taxi_edges) if (e.a === item.id || e.b === item.id) redrawItem("taxi_edges", e);
  }
}

function nodesVisible() {
  return map.getZoom() >= 16 || Apt.tool === "taxi" || Boolean(Apt.selected && Apt.selected.list.startsWith("taxi"));
}

// Small markers (stands, signs, light fixtures) are drawn from zoom 15, taxi route nodes from zoom 16 (or while working
// on them); redraw only when that changes
const DETAIL_LISTS = ["startups", "signs", "light_fixtures"];

function detailVisible() { return map.getZoom() >= 15; }

map.on("zoomend", () => {
  if (Apt.data && currentMode() === "airport" && (nodesVisible() !== Apt.nodesShown || detailVisible() !== Apt.detailShown)) renderAirport();
});

// ---------- selection ----------
function selectApt(listName, id) {
  const prev = Apt.selected;
  Apt.selected = listName ? { list: listName, id } : null;
  if (Apt.editor) { Apt.editor.remove(); Apt.editor = null; }
  if (prev) { const it = findItem(prev); if (it) redrawItem(prev.list, it); }
  const item = findItem(Apt.selected);
  if (item) {
    redrawItem(listName, item);
    attachVertexEditor(listName, item);
  }
  renderProps();
  renderTree();
}

function nodeEditorOptions(item, rings, closed, min) {
  return {
    rings: () => rings(),
    get: (n) => [n.lat, n.lon],
    set: (n, lat, lng) => {
      if (n.ctrl) { n.ctrl = [n.ctrl[0] + (lng - n.lon), n.ctrl[1] + (lat - n.lat)]; }
      n.lat = lat;
      n.lon = lng;
    },
    make: (lat, lng, near) => ({ lon: lng, lat, ctrl: null, line: near.line || 0, light: near.light || 0 }),
    closed, min,
  };
}

function attachVertexEditor(listName, item) {
  let o = null;
  const live = () => {
    const layer = Apt.layers.get(key(listName, item.id));
    if (!layer) return;
    if (listName === "lines" && layer.setLatLngs) layer.setLatLngs(bezierPath(item.nodes, item.closed));
    else if ((listName === "pavements" || listName === "boundaries") && layer.setLatLngs) layer.setLatLngs(item.rings.map((r) => bezierPath(r, true)));
  };
  if (listName === "pavements" || listName === "boundaries") o = nodeEditorOptions(item, () => item.rings, true, 3);
  else if (listName === "lines") o = nodeEditorOptions(item, () => [item.nodes], item.closed, 2);
  else if (listName === "runways" || listName === "water_runways") {
    o = { rings: () => [item.ends], get: (e) => [e.lat, e.lon], set: (e, lat, lng) => { e.lat = lat; e.lon = lng; }, make: null, closed: false, min: 2, noInsert: true };
  } else if (listName === "taxi_edges") {
    const a = Apt.nodeIndex.get(item.a), b = Apt.nodeIndex.get(item.b);
    if (!a || !b) return;
    o = { rings: () => [[a, b]], get: (n) => [n.lat, n.lon], set: (n, lat, lng) => { n.lat = lat; n.lon = lng; }, make: null, closed: false, min: 2, noInsert: true };
  } else if (listName === "taxi_nodes") {
    o = { rings: () => [[item]], get: (n) => [n.lat, n.lon], set: (n, lat, lng) => { n.lat = lat; n.lon = lng; }, make: null, closed: false, min: 1, noInsert: true };
  } else if (listName === "placements" && item.kind !== "object") {
    const isLine = item.kind === "line";
    o = { rings: () => (isLine ? [item.points] : [item.rings[0]]), get: (p) => [p[1], p[0]], set: (p, lat, lng) => { p[0] = lng; p[1] = lat; },
      make: (lat, lng, near) => [lng, lat, ...near.slice(2)], closed: !isLine, min: isLine || placeExt(item) === "fac" ? 2 : 3 };
  }
  if (!o) return;
  Apt.editor = new VertexEditor(map, Object.assign(o, {
    onDrag: live,
    onChange: () => { markDirty(); redrawItem(listName, item); if (listName === "taxi_edges" || listName === "taxi_nodes") renderAirport(); renderProps(); },
  }));
  Apt.editor.layer.on("mousedown", snapApt);
}

function deleteSelectedApt() {
  const sel = Apt.selected;
  const item = findItem(sel);
  if (!item) return;
  snapApt();
  const arr = list(sel.list);
  arr.splice(arr.indexOf(item), 1);
  if (sel.list === "taxi_nodes") Apt.data.taxi_edges = Apt.data.taxi_edges.filter((e) => e.a !== item.id && e.b !== item.id);
  if (sel.list === "taxi_edges") {
    const used = new Set(Apt.data.taxi_edges.flatMap((e) => [e.a, e.b]));
    Apt.data.taxi_nodes = Apt.data.taxi_nodes.filter((n) => used.has(n.id) || (n.id !== item.a && n.id !== item.b));
  }
  markDirty();
  selectApt(null);
  renderAirport();
}

// ---------- labels and hierarchy ----------
function itemLabel(listName, it) {
  switch (listName) {
    case "runways": case "water_runways": return `${it.ends[0].name}/${it.ends[1].name}`;
    case "helipads": return it.name;
    case "pavements": case "boundaries": return it.name || t("unnamed");
    case "lines": return it.name || t("lineLabel", { n: it.nodes.length });
    case "startups": return it.name;
    case "signs": return it.text;
    case "viewpoints": return it.name || t("tool_viewpoints");
    case "frequencies": return `${it.type} ${Number(it.mhz).toFixed(3)} ${it.name || ""}`;
    case "taxi_edges": return `${it.name || "—"} (${it.kind})`;
    case "taxi_nodes": return `#${it.id} ${it.usage}${it.name ? " " + it.name : ""}`;
    case "light_fixtures": return (Apt.enums.light_fixtures.find((x) => x[0] === it.type) || [0, "?"])[1];
    case "placements": return placeName(it);
    case "windsocks": return t(it.lit ? "lit" : "unlit");
    case "beacons": return (Apt.enums.beacons.find((x) => x[0] === it.type) || [0, "?"])[1];
    default: return "";
  }
}

function renderTree() {
  if (!Apt.data || !$("apt-tree")) return;
  const f = ($("apt-tree-filter")?.value || "").trim().toLowerCase();
  const open = new Set(JSON.parse(store.get("cityscene.aptTreeOpen") || '["runways","startups"]'));
  $("apt-tree").innerHTML = LIST_ORDER.map((ln) => {
    const items = list(ln);
    const shown = items.filter((it) => !f || itemLabel(ln, it).toLowerCase().includes(f));
    const isOpen = open.has(ln) || (f && shown.length);
    const rows = isOpen ? shown.slice(0, 400).map((it) => {
      const on = Apt.selected && Apt.selected.list === ln && Apt.selected.id === it.id;
      return `<li class="${on ? "on" : ""}" data-list="${ln}" data-id="${esc(String(it.id))}">${esc(itemLabel(ln, it))}</li>`;
    }).join("") + (shown.length > 400 ? `<li class="hint">${esc(t("moreItems", { n: shown.length - 400 }))}</li>` : "") : "";
    const add = ln === "frequencies" ? `<button class="mini" data-add="frequencies">+</button>` : "";
    return `<div class="tree-group"><div class="tree-head" data-toggle="${ln}">${isOpen ? "▾" : "▸"} ${esc(t("list_" + ln))} <span class="hint">${items.length}</span>${add}</div>${rows ? `<ul>${rows}</ul>` : ""}</div>`;
  }).join("");
  $("apt-tree").querySelectorAll("[data-toggle]").forEach((h) => {
    h.onclick = (e) => {
      if (e.target.dataset.add) return;
      const ln = h.dataset.toggle;
      if (open.has(ln)) open.delete(ln); else open.add(ln);
      store.set("cityscene.aptTreeOpen", JSON.stringify([...open]));
      renderTree();
    };
  });
  $("apt-tree").querySelectorAll("li[data-list]").forEach((li) => {
    li.onclick = () => {
      const ln = li.dataset.list;
      const id = ln === "taxi_nodes" ? Number(li.dataset.id) : li.dataset.id;
      selectApt(ln, id);
      zoomToItem(ln, findItem({ list: ln, id }));
    };
  });
  $("apt-tree").querySelectorAll("[data-add]").forEach((b) => {
    b.onclick = (e) => {
      e.stopPropagation();
      snapApt();
      const fq = { id: "frq_" + Math.random().toString(16).slice(2, 10), type: "tower", mhz: 118.1, name: "Tower" };
      Apt.data.frequencies.push(fq);
      markDirty();
      selectApt("frequencies", fq.id);
    };
  });
}

function zoomToItem(ln, it) {
  if (!it) return;
  const layer = Apt.layers.get(key(ln, it.id));
  if (layer && layer.getBounds) map.fitBounds(layer.getBounds().pad(0.3), { maxZoom: 18 });
  else if (it.lat != null) map.setView([it.lat, it.lon], Math.max(map.getZoom(), 17));
}

// ---------- properties ----------
function enumOptions(table, value) {
  const rows = table.map((x) => (Array.isArray(x) ? x : [x, x]));
  const known = rows.some(([k]) => String(k) === String(value));
  return (known ? "" : `<option value="${esc(String(value))}" selected>${esc(String(value))}</option>`) +
    rows.map(([k, v]) => `<option value="${esc(String(k))}" ${String(k) === String(value) ? "selected" : ""}>${esc(String(v))}</option>`).join("");
}

// fields: [key, label key, type] with type num | int | text | bool | enum:<table> | list:<table> (multi)
const FIELDS = {
  runways: [["width_m", "f_width", "num"], ["surface", "f_surface", "enum:surfaces"], ["shoulder", "f_shoulder", "enum:shoulders"],
    ["smoothness", "f_smoothness", "num"], ["centre_lights", "f_centre_lights", "bool"], ["edge_lights", "f_edge_lights", "enum:edge_lights"],
    ["distance_signs", "f_distance_signs", "bool"]],
  runway_end: [["name", "f_end_name", "text"], ["displaced_m", "f_displaced", "num"], ["blastpad_m", "f_blastpad", "num"],
    ["markings", "f_markings", "enum:markings"], ["approach_lights", "f_approach", "enum:approach_lights"],
    ["tdz_lights", "f_tdz", "bool"], ["reil", "f_reil", "enum:reil"]],
  water_runways: [["width_m", "f_width", "num"], ["buoys", "f_buoys", "bool"]],
  helipads: [["name", "f_name", "text"], ["heading", "heading", "num"], ["length_m", "f_length", "num"], ["width_m", "f_width", "num"],
    ["surface", "f_surface", "enum:surfaces"], ["shoulder", "f_shoulder", "enum:shoulders"], ["edge_lights", "f_edge_lights", "enum:helipad_edge_lights"]],
  pavements: [["name", "f_name", "text"], ["surface", "f_surface", "enum:surfaces"], ["smoothness", "f_smoothness", "num"], ["texture_heading", "f_tex_heading", "num"]],
  lines: [["name", "f_name", "text"], ["closed", "f_closed", "bool"]],
  boundaries: [["name", "f_name", "text"]],
  startups: [["name", "f_name", "text"], ["heading", "heading", "num"], ["type", "f_startup_type", "enum:startup_types"],
    ["categories", "f_categories", "list:categories"], ["width_code", "f_width_code", "enum:width_codes_opt"],
    ["operation", "f_operation", "enum:operations"], ["airlines", "f_airlines", "words"]],
  windsocks: [["lit", "f_lit", "bool"]],
  beacons: [["type", "f_beacon", "enum:beacons"]],
  viewpoints: [["name", "f_name", "text"], ["height_ft", "f_height_ft", "num"]],
  signs: [["text", "f_sign_text", "text"], ["heading", "heading", "num"], ["size", "f_sign_size", "enum:sign_sizes"]],
  light_fixtures: [["type", "f_fixture", "enum:light_fixtures"], ["heading", "heading", "num"], ["angle", "f_angle", "num"], ["runway", "f_runway", "text"], ["description", "f_description", "text"]],
  frequencies: [["type", "f_freq_type", "enum:frequency_types"], ["mhz", "f_mhz", "num"], ["name", "f_name", "text"]],
  taxi_edges: [["name", "f_name", "text"], ["kind", "f_edge_kind", "enum:edge_kinds"], ["oneway", "f_oneway", "bool"]],
  taxi_nodes: [["usage", "f_usage", "enum:taxi_usage"], ["name", "f_name", "text"]],
};

function fieldHtml(obj, k, labelKey, type, path) {
  const v = obj[k];
  const id = `af-${path}`;
  const label = `<span>${esc(t(labelKey))}</span>`;
  if (type === "bool") return `<label class="check"><input id="${id}" data-path="${path}" data-type="bool" type="checkbox" ${v ? "checked" : ""}> ${esc(t(labelKey))}</label>`;
  if (type.startsWith("enum:")) {
    let table = Apt.enums[type.slice(5)];
    if (type === "enum:width_codes_opt") table = [["", "—"], ...Apt.enums.width_codes.map((x) => [x, x])];
    return `<label>${label}<select id="${id}" data-path="${path}" data-type="${typeof v === "number" ? "int" : "text"}">${enumOptions(table, v ?? "")}</select></label>`;
  }
  if (type.startsWith("list:")) {
    const table = Apt.enums[type.slice(5)];
    return `<div class="label-text">${esc(t(labelKey))}</div><div class="row wrap">${table.map((c) => `<label class="check"><input type="checkbox" data-path="${path}" data-type="multi" value="${c}" ${(v || []).includes(c) ? "checked" : ""}> ${esc(c)}</label>`).join("")}</div>`;
  }
  if (type === "words") return `<label>${label}<input id="${id}" data-path="${path}" data-type="words" value="${esc((v || []).join(" "))}"></label>`;
  const inputType = type === "num" || type === "int" ? "number" : "text";
  return `<label>${label}<input id="${id}" data-path="${path}" data-type="${type}" type="${inputType}" step="any" value="${esc(v ?? "")}"></label>`;
}

function setPath(root, path, value) {
  const parts = path.split(".");
  let o = root;
  for (const p of parts.slice(0, -1)) o = o[p];
  o[parts[parts.length - 1]] = value;
}

function renderProps() {
  const box = $("apt-props");
  if (!box || !Apt.data) return;
  const sel = Apt.selected;
  const item = findItem(sel);
  if (!item) {
    const h = Apt.data.header;
    box.innerHTML = `<div class="label-text">${esc(t("aptHeader"))}</div>` +
      `<label><span>ICAO</span><input data-hpath="icao" value="${esc(h.icao)}"></label>` +
      `<label><span>${esc(t("aptAirportName"))}</span><input data-hpath="name" value="${esc(h.name)}"></label>` +
      `<div class="row three"><label><span>${esc(t("aptElevation"))}</span><input data-hpath="elevation_ft" type="number" value="${esc(h.elevation_ft)}"></label>` +
      `<label><span>${esc(t("aptType"))}</span><select data-hpath="type">${enumOptions([["land", t("aptLand")], ["sea", t("aptSea")], ["heli", t("aptHeli")]], h.type)}</select></label></div>` +
      `<div class="label-text">${esc(t("aptMeta"))}</div><textarea id="apt-meta" rows="5">${esc(Object.entries(Apt.data.meta).map(([k, v]) => `${k} ${v}`).join("\n"))}</textarea>` +
      `<details class="sub-details"><summary>${esc(t("aptRaw", { n: Apt.data.raw.length }))}</summary>` +
      `<textarea id="apt-raw" rows="8" spellcheck="false">${esc(Apt.data.raw.join("\n"))}</textarea><p class="hint">${esc(t("aptRawHint"))}</p></details>` +
      `<p class="hint">${esc(t("aptNothingSelected"))}</p>`;
    box.querySelectorAll("[data-hpath]").forEach((el) => {
      el.onchange = () => {
        snapApt();
        const k = el.dataset.hpath;
        h[k] = k === "elevation_ft" ? parseFloat(el.value) || 0 : el.value.trim();
        markDirty();
        showEditor();
      };
    });
    $("apt-meta").onchange = () => {
      snapApt();
      const meta = {};
      for (const line of $("apt-meta").value.split("\n")) {
        const m = line.trim().match(/^(\S+)\s*(.*)$/);
        if (m) meta[m[1]] = m[2];
      }
      Apt.data.meta = meta;
      markDirty();
    };
    $("apt-raw").onchange = () => {
      snapApt();
      Apt.data.raw = $("apt-raw").value.split("\n").map((l) => l.trim()).filter((l) => /^\d+\s/.test(l));
      markDirty();
    };
    return;
  }
  const ln = sel.list;
  let html = `<div class="prop-head"><b>${esc(t("one_" + ln))}</b> ${esc(itemLabel(ln, item))}</div>`;
  if (ln === "placements") {
    html += `<p class="hint">${esc(item.path)}</p><label><span>${esc(t("placeAsset"))}</span><span class="path-row"><input data-path="path" data-type="text" value="${esc(item.path)}"><button class="libpick" data-for="af-pp" data-types="${placeTool(item) === "object" ? "obj,agp" : placeTool(item) === "lin" ? "lin,str" : placeTool(item)}">${esc(t("fromLibrary"))}</button></span></label><input id="af-pp" type="hidden">`;
    const ext = placeExt(item);
    const f = item.kind === "object" ? [["heading", "heading", "num"], ["agl", "placeAgl", "num"]]
      : ext === "fac" ? [["height", "placeHeight", "int"]] : ext === "for" ? [["density", "placeDensity", "int"]]
        : ext === "pol" ? [["heading", "placeTexHeading", "num"]] : ext === "lin" ? [["closed", "placeClosed", "bool"]] : [["spacing", "placeSpacing", "num"]];
    html += f.map(([k, l, ty]) => fieldHtml(item, k, l, ty, k)).join("");
    if (ext === "for") html += `<label><span>${esc(t("placeFill"))}</span><select data-path="fill" data-type="text">${enumOptions(["area", "line", "points"].map((x) => [x, t("fill_" + x)]), item.fill)}</select></label>`;
  } else {
    html += (FIELDS[ln] || []).map(([k, l, ty]) => fieldHtml(item, k, l, ty, k)).join("");
  }
  if (ln === "runways") {
    item.ends.forEach((e, i) => {
      html += `<div class="sub-form"><div class="label-text">${esc(t("runwayEnd", { name: e.name }))}</div>` +
        FIELDS.runway_end.map(([k, l, ty]) => fieldHtml(e, k, l, ty, `ends.${i}.${k}`)).join("") + "</div>";
    });
    const [a, b] = item.ends;
    const len = Math.hypot(...metresBetween(a.lon, a.lat, b.lon, b.lat));
    html += `<p class="hint">${esc(t("runwayInfo", { len: Math.round(len), brg: bearingDeg(a.lon, a.lat, b.lon, b.lat).toFixed(1) }))}</p>`;
  }
  if (ln === "water_runways") item.ends.forEach((e, i) => { html += fieldHtml(e, "name", "f_end_name", "text", `ends.${i}.name`); });
  if (ln === "lines" || ln === "pavements" || ln === "boundaries") {
    const nodes = ln === "lines" ? item.nodes : item.rings.flat();
    const lines = [...new Set(nodes.map((n) => n.line || 0))], lights = [...new Set(nodes.map((n) => n.light || 0))];
    html += `<label><span>${esc(t("f_line_type"))}</span><select data-nodes="line">${lines.length > 1 ? `<option value="" selected>${esc(t("mixed"))}</option>` : ""}${enumOptions(Apt.enums.line_types, lines.length > 1 ? "" : lines[0])}</select></label>`;
    html += `<label><span>${esc(t("f_light_type"))}</span><select data-nodes="light">${lights.length > 1 ? `<option value="" selected>${esc(t("mixed"))}</option>` : ""}${enumOptions(Apt.enums.light_types, lights.length > 1 ? "" : lights[0])}</select></label>`;
    const curves = nodes.filter((n) => n.ctrl).length;
    html += `<p class="hint">${esc(t("nodeInfo", { n: nodes.length, curves }))}</p>`;
    if (curves) html += `<button id="af-flatten" class="ghost">${esc(t("flattenCurves"))}</button>`;
  }
  if (ln === "taxi_edges") {
    html += `<div class="label-text">${esc(t("activeZones"))}</div>` + (item.active || []).map((a, i) =>
      `<div class="row"><select data-active="${i}" data-k="type">${enumOptions(Apt.enums.active_types, a.type)}</select><input data-active="${i}" data-k="runways" value="${esc(a.runways)}"><button class="mini" data-active-del="${i}">×</button></div>`).join("") +
      `<button class="mini" id="af-active-add">+ ${esc(t("activeAdd"))}</button>`;
  }
  const point = POINT_LISTS.includes(ln) || (ln === "placements" && item.kind === "object") || ln === "taxi_nodes";
  if (point) html += `<div class="row three"><label><span>${esc(t("lat"))}</span><input data-coord="lat" type="number" step="any" value="${item.lat}"></label><label><span>${esc(t("lon"))}</span><input data-coord="lon" type="number" step="any" value="${item.lon}"></label></div>`;
  const msgs = Apt.findings.filter((f) => f.ref && f.ref.list === ln && f.ref.id === item.id);
  if (msgs.length) html += `<ul class="finding-list">${msgs.map((f) => `<li class="${f.level}">${esc(f.message)}</li>`).join("")}</ul>`;
  html += `<div class="row wrap"><button id="af-dup">${esc(t("duplicate"))}</button><button id="af-del" class="danger">${esc(t("delete"))}</button><button id="af-zoom" class="ghost">${esc(t("zoomTo"))}</button><button id="af-close" class="ghost">${esc(t("deselect"))}</button></div>`;
  box.innerHTML = html;

  const apply = () => { markDirty(); redrawItem(ln, item); renderTree(); };
  box.querySelectorAll("[data-path]").forEach((el) => {
    el.onchange = () => {
      snapApt();
      const ty = el.dataset.type;
      let v = el.value;
      if (ty === "bool") v = el.checked ? 1 : 0;
      else if (ty === "num") v = parseFloat(v) || 0;
      else if (ty === "int") v = parseInt(v, 10) || 0;
      else if (ty === "words") v = v.split(/\s+/).filter(Boolean);
      else if (ty === "multi") v = [...box.querySelectorAll(`[data-path="${el.dataset.path}"][data-type="multi"]:checked`)].map((x) => x.value);
      if (["closed", "oneway", "buoys"].includes(el.dataset.path)) v = Boolean(v);
      if (ln === "placements" && el.dataset.path === "heading") v = ((v % 360) + 360) % 360;
      setPath(item, el.dataset.path, v);
      apply();
      renderProps();
    };
  });
  const pp = $("af-pp");
  if (pp) pp.addEventListener("change", () => { snapApt(); item.path = pp.value; apply(); renderProps(); });
  box.querySelectorAll("[data-nodes]").forEach((el) => {
    el.onchange = () => {
      if (el.value === "") return;
      snapApt();
      const nodes = ln === "lines" ? item.nodes : item.rings.flat();
      nodes.forEach((n) => { n[el.dataset.nodes] = parseInt(el.value, 10) || 0; });
      apply();
    };
  });
  box.querySelectorAll("[data-coord]").forEach((el) => {
    el.onchange = () => { snapApt(); item[el.dataset.coord] = parseFloat(el.value); apply(); if (ln === "taxi_nodes") renderAirport(); };
  });
  box.querySelectorAll("[data-active]").forEach((el) => {
    el.onchange = () => { snapApt(); item.active[Number(el.dataset.active)][el.dataset.k] = el.value.trim(); markDirty(); };
  });
  box.querySelectorAll("[data-active-del]").forEach((b) => { b.onclick = () => { snapApt(); item.active.splice(Number(b.dataset.activeDel), 1); markDirty(); renderProps(); }; });
  if ($("af-active-add")) $("af-active-add").onclick = () => { snapApt(); (item.active = item.active || []).push({ type: "departure", runways: Apt.data.runways[0] ? `${Apt.data.runways[0].ends[0].name},${Apt.data.runways[0].ends[1].name}` : "" }); markDirty(); renderProps(); };
  if ($("af-flatten")) $("af-flatten").onclick = () => { snapApt(); (ln === "lines" ? item.nodes : item.rings.flat()).forEach((n) => { n.ctrl = null; }); apply(); selectApt(ln, item.id); };
  $("af-del").onclick = deleteSelectedApt;
  $("af-dup").onclick = duplicateApt;
  $("af-zoom").onclick = () => zoomToItem(ln, item);
  $("af-close").onclick = () => selectApt(null);
}

function duplicateApt() {
  const sel = Apt.selected;
  const item = findItem(sel);
  if (!item || sel.list === "taxi_nodes" || sel.list === "taxi_edges") return;
  snapApt();
  const copy = JSON.parse(JSON.stringify(item));
  copy.id = "dup_" + Math.random().toString(16).slice(2, 10);
  const shiftNode = (n) => { const [lon, lat] = offsetLonLat(n.lon, n.lat, 10, 0); if (n.ctrl) n.ctrl = offsetLonLat(n.ctrl[0], n.ctrl[1], 10, 0); n.lon = lon; n.lat = lat; };
  const shiftPair = (p) => { const [lon, lat] = offsetLonLat(p[0], p[1], 10, 0); p[0] = lon; p[1] = lat; };
  if (copy.lat != null) shiftNode(copy);
  if (copy.ends) copy.ends.forEach(shiftNode);
  if (copy.rings && sel.list !== "placements") copy.rings.forEach((r) => r.forEach(shiftNode));
  if (copy.nodes) copy.nodes.forEach(shiftNode);
  if (sel.list === "placements") { if (copy.points) copy.points.forEach(shiftPair); if (copy.rings) copy.rings.forEach((r) => r.forEach(shiftPair)); }
  if (copy.name && sel.list === "startups") copy.name += " copy";
  list(sel.list).push(copy);
  markDirty();
  renderAirport();
  selectApt(sel.list, copy.id);
}

// ---------- findings ----------
function renderFindings() {
  if (!$("apt-findings")) return;
  const s = Apt.summary;
  $("apt-valid-flag").innerHTML = s.errors ? `<span class="bad-flag">${esc(t("nErrors", { n: s.errors }))}</span>` : `<span class="ok-flag">✓</span>`;
  const rows = Apt.findings.slice(0, 200).map((f, i) => `<li class="finding ${f.level}" data-i="${i}">${f.level === "error" ? "⛔" : "⚠"} ${esc(f.message)}</li>`);
  $("apt-findings").innerHTML = rows.join("") || `<li class="empty">${esc(t("aptAllGood"))}</li>`;
  $("apt-findings").querySelectorAll("li[data-i]").forEach((li) => {
    li.onclick = () => {
      const f = Apt.findings[Number(li.dataset.i)];
      if (f.ref && f.ref.list) { selectApt(f.ref.list, f.ref.id); zoomToItem(f.ref.list, findItem(f.ref)); }
    };
  });
}

// ---------- tools ----------
function setTool(tool) {
  cancelDraw();
  Apt.tool = tool;
  document.querySelectorAll("#apt-tools [data-tool]").forEach((b) => b.classList.toggle("on", b.dataset.tool === tool));
  if ($("apt-status")) $("apt-status").textContent = tool === "select" ? t("aptSelectHint") : t("toolHint_" + (["object", "fac", "for", "pol", "lin"].includes(tool) ? "asset" : tool));
  if (!Apt.data || tool === "select") return;
  if (["object", "fac", "for", "pol", "lin"].includes(tool)) {
    openLibrary({ types: PLACE_TYPES[tool], owner: currentOwner(), title: t("placePickTitle"), onPick: (path) => placeAsset(tool, path) });
    return;
  }
  runTool(tool);
}

function newId(prefix) { return prefix + Math.random().toString(16).slice(2, 10); }

function runTool(tool) {
  const finish = () => { setTool("select"); };
  if (tool === "runways") {
    drawShape("polyline", (lls) => {
      if (lls.length < 2) return finish();
      snapApt();
      const a = lls[0], b = lls[lls.length - 1];
      const brg = bearingDeg(a.lng, a.lat, b.lng, b.lat);
      const num = Math.round(brg / 10) % 36 || 36;
      const other = ((num + 18 - 1) % 36) + 1;
      const end = (name, ll) => ({ name, lon: ll.lng, lat: ll.lat, displaced_m: 0, blastpad_m: 0, markings: 3, approach_lights: 0, tdz_lights: 0, reil: 0 });
      const r = { id: newId("rwy_"), width_m: 45, surface: 1, shoulder: 0, smoothness: 0.25, centre_lights: 1, edge_lights: 3, distance_signs: 1,
        ends: [end(String(num).padStart(2, "0"), a), end(String(other).padStart(2, "0"), b)] };
      Apt.data.runways.push(r);
      markDirty(); renderAirport(); selectApt("runways", r.id); finish();
    }, { color: "#3a3f46", weight: 6 });
  } else if (tool === "pavements" || tool === "boundaries") {
    drawShape("polygon", (lls) => {
      snapApt();
      const ring = lls.map((ll) => ({ lon: ll.lng, lat: ll.lat, ctrl: null, line: 0, light: 0 }));
      const item = tool === "pavements" ? { id: newId("pav_"), name: "Pavement", surface: 1, smoothness: 0.25, texture_heading: 0, rings: [ring] }
        : { id: newId("bnd_"), name: "Airport boundary", rings: [ring] };
      Apt.data[tool].push(item);
      markDirty(); renderAirport(); selectApt(tool, item.id); finish();
    }, { color: tool === "pavements" ? "#646a73" : APT_COLORS.boundary });
  } else if (tool === "lines") {
    drawShape("polyline", (lls) => {
      snapApt();
      const item = { id: newId("lin_"), name: "", closed: false, nodes: lls.map((ll) => ({ lon: ll.lng, lat: ll.lat, ctrl: null, line: 1, light: 0 })) };
      Apt.data.lines.push(item);
      markDirty(); renderAirport(); selectApt("lines", item.id); finish();
    }, { color: APT_COLORS.line });
  } else if (tool === "taxi") {
    drawShape("polyline", (lls) => {
      snapApt();
      const nodes = Apt.data.taxi_nodes;
      let next = nodes.reduce((m, n) => Math.max(m, n.id), -1) + 1;
      const ids = lls.map((ll) => {
        const near = nodes.find((n) => Math.hypot(...metresBetween(n.lon, n.lat, ll.lng, ll.lat)) < 4);
        if (near) return near.id;
        const n = { id: next++, lon: ll.lng, lat: ll.lat, usage: "both", name: "" };
        nodes.push(n);
        return n.id;
      });
      let last = null;
      for (let i = 1; i < ids.length; i++) {
        if (ids[i] === ids[i - 1]) continue;
        last = { id: newId("te_"), a: ids[i - 1], b: ids[i], oneway: false, kind: "taxiway", name: "", active: [] };
        Apt.data.taxi_edges.push(last);
      }
      markDirty(); renderAirport(); if (last) selectApt("taxi_edges", last.id);
      runTool("taxi");
    }, { color: APT_COLORS.route, weight: 3 });
  } else {
    const make = {
      helipads: (ll) => ({ id: newId("hp_"), name: `H${Apt.data.helipads.length + 1}`, lon: ll.lng, lat: ll.lat, heading: 0, length_m: 20, width_m: 20, surface: 2, markings: 0, shoulder: 0, smoothness: 0.25, edge_lights: 1 }),
      startups: (ll) => ({ id: newId("st_"), name: `Stand ${Apt.data.startups.length + 1}`, lon: ll.lng, lat: ll.lat, heading: 0, type: "gate", categories: ["jets", "turboprops", "props"], width_code: "", operation: "none", airlines: [] }),
      windsocks: (ll) => ({ id: newId("ws_"), lon: ll.lng, lat: ll.lat, lit: 1 }),
      beacons: (ll) => ({ id: newId("bcn_"), lon: ll.lng, lat: ll.lat, type: 1 }),
      viewpoints: (ll) => ({ id: newId("vp_"), lon: ll.lng, lat: ll.lat, height_ft: 100, name: "Tower" }),
      signs: (ll) => ({ id: newId("sgn_"), lon: ll.lng, lat: ll.lat, heading: 0, size: 2, text: "{@Y}A" }),
      light_fixtures: (ll) => ({ id: newId("lf_"), lon: ll.lng, lat: ll.lat, type: 2, heading: 0, angle: 3, runway: "", description: "" }),
    }[tool];
    if (!make) return finish();
    drawShape("marker", ([ll]) => {
      snapApt();
      const item = make(ll);
      Apt.data[tool].push(item);
      markDirty();
      redrawItem(tool, item);
      selectApt(tool, item.id);
      runTool(tool);
    });
  }
}

function placeAsset(tool, path) {
  setTool("select");
  $("apt-status").textContent = t(tool === "object" ? "placeClickMap" : tool === "lin" ? "placeDrawLine" : "placeDrawShape", { name: path.split("/").pop() });
  Apt.tool = "asset";
  const add = (item) => {
    snapApt();
    Apt.data.placements.push(item);
    markDirty();
    redrawItem("placements", item);
  };
  if (tool === "object") {
    const next = () => drawShape("marker", ([ll]) => { add({ id: newId("pl_"), kind: "object", path, lon: ll.lng, lat: ll.lat, heading: 0 }); next(); });
    next();
    return;
  }
  drawShape(tool === "lin" ? "polyline" : "polygon", (lls) => {
    const pts = lls.map((ll) => [ll.lng, ll.lat]);
    const ext = path.split(".").pop().toLowerCase();
    const item = tool === "lin" ? { id: newId("pl_"), kind: "line", path, points: pts, closed: false, ...(ext === "str" ? { spacing: 10 } : {}) }
      : { id: newId("pl_"), kind: "polygon", path, rings: [pts], ...(tool === "fac" ? { height: 10 } : tool === "for" ? { density: 255, fill: "area" } : { heading: 0 }) };
    add(item);
    Apt.tool = "select";
    selectApt("placements", item.id);
  }, { color: PLACE_COLORS[tool] });
}

map.on("click", () => { if (currentMode() === "airport" && Apt.tool === "select" && Apt.selected && !activeDraw) selectApt(null); });

// ---------- export / install / history ----------
function renderAptState() {
  const s = Apt.state || {};
  if (!$("apt-install")) return;
  $("apt-install").disabled = !s.built || s.installed;
  $("apt-uninstall").disabled = !s.installed;
}

async function exportAirport() {
  try {
    if (Apt.dirty) await saveAirport();
    if (Apt.summary.errors) return say("aptFixErrors", { n: Apt.summary.errors }, "warn");
    say("aptExporting", {}, "");
    const r = await api(`/api/airports/${encodeURIComponent(Apt.name)}/export`, { body: {} });
    Apt.state = { ...Apt.state, ...r };
    renderAptState();
    const deps = r.report.dependencies;
    $("apt-export-result").innerHTML = `<pre class="log-small">${esc(r.log.join("\n"))}</pre>` +
      (deps ? `<div class="label-text">${esc(t("depsTitle"))}</div><ul class="dep-list">${Object.entries(deps.libraries).map(([n, p]) => `<li>${esc(n)} <span class="hint">(${p.length})</span></li>`).join("")}</ul>` +
        (deps.missing.length ? `<p class="warn-text">${esc(t("depsMissing", { n: deps.missing.length, list: deps.missing.slice(0, 5).join(", ") }))}</p>` : "") : "");
    say("aptExported", { path: r.report.pack }, "ok");
  } catch (e) { sayError(e); }
}

async function installAirport(on) {
  if (!on && !confirm(t("confirmUninstall"))) return;
  try {
    const r = await api(`/api/airports/${encodeURIComponent(Apt.name)}/${on ? "install" : "uninstall"}`, { body: {} });
    Apt.state = { ...Apt.state, ...r };
    renderAptState();
    say(on ? "installedMsg" : "uninstalledMsg", { link: r.link, ini: r.ini, backup: r.backup ? t("backupNote", { name: r.backup }) : "" }, "ok");
  } catch (e) { sayError(e); }
}

async function loadHistory() {
  if (!Apt.name || !$("apt-history")) return;
  try {
    const r = await api(`/api/airports/${encodeURIComponent(Apt.name)}/history`);
    $("apt-history").innerHTML = r.files.map((f) => `<li><span class="item-text">${esc(f.replace(/^airport-|\.json$/g, "").replace(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2}).*/, "$1-$2-$3 $4:$5:$6"))}</span><button class="mini" data-restore="${esc(f)}">${esc(t("restore"))}</button></li>`).join("") ||
      `<li class="empty">${esc(t("aptNoHistory"))}</li>`;
    $("apt-history").querySelectorAll("[data-restore]").forEach((b) => {
      b.onclick = async () => {
        if (!confirm(t("aptConfirmRestore"))) return;
        try {
          await api(`/api/airports/${encodeURIComponent(Apt.name)}/restore`, { body: { file: b.dataset.restore } });
          await openAirport(Apt.name, false);
          say("aptRestored", {}, "ok");
        } catch (e) { sayError(e); }
      };
    });
  } catch (e) { /* history is optional */ }
}

// ---------- keyboard ----------
document.addEventListener("keydown", (e) => {
  if (currentMode() !== "airport" || !Apt.data || !$("lib-modal").hidden) return;
  const typing = /INPUT|SELECT|TEXTAREA/.test(document.activeElement?.tagName || "");
  if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); saveAirport().catch(sayError); return; }
  if (typing) return;
  if ((e.ctrlKey || e.metaKey) && e.key === "z") { e.preventDefault(); undoApt(e.shiftKey); return; }
  if ((e.ctrlKey || e.metaKey) && e.key === "y") { e.preventDefault(); undoApt(true); return; }
  if ((e.ctrlKey || e.metaKey) && e.key === "d") { e.preventDefault(); duplicateApt(); return; }
  if (e.key === "Escape") { setTool("select"); selectApt(null); return; }
  if (!Apt.selected) return;
  if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); deleteSelectedApt(); }
  if (e.key === "r" || e.key === "R") {
    const item = findItem(Apt.selected);
    if (item && item.heading != null) {
      snapApt();
      item.heading = (((item.heading + (e.shiftKey ? -5 : 5)) % 360) + 360) % 360;
      markDirty(); redrawItem(Apt.selected.list, item); renderProps();
    }
  }
});

// ---------- mode hook ----------
function onModeChanged(m) {
  if (m === "airport") {
    if (!$("airport-panel").innerHTML) buildAirportPanel();
    renderAirport();
  } else {
    if (Apt.editor) { Apt.editor.remove(); Apt.editor = null; }
    Apt.layer.remove();
    if (Apt.tool !== "select") setTool("select");
  }
}

Apt.source = ["xplane", "gateway", "pack", "osm", "blank"].includes(store.get("cityscene.aptSource")) ? store.get("cityscene.aptSource") : "xplane";
