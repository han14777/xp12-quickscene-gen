// Browser map UI: draw an area -> save the job and generate in the background -> poll progress -> preview buildings
// -> edit single buildings (height / style / delete / custom model) -> install.
// UI text lives in i18n.js.
"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const STEPS = ["fetch", "clean", "classify", "build_dsf"];
// Height bands (metres), low to high
const HEIGHT_BANDS = [
  [0, "#9fb8c8", "< 10 m"],
  [10, "#7da7c9", "10–20 m"],
  [20, "#5b8fc4", "20–40 m"],
  [40, "#e0a13a", "40–60 m"],
  [60, "#d9622b", "60–150 m"],
  [150, "#a3242a", "≥ 150 m"],
];

let currentJob = null;
let currentDetail = null;
let logSince = 0;
let pollTimer = null;
let buildingsLayer = null;
let lastStatus = null;
let lastMessage = null;
// Per-building edits (jobs/<name>/overrides.json), OBJ files in the job's models folder, the selected building
let edits = {};
let modelFiles = [];
let modelsDir = "";
let selectedId = null;
const EDIT_COLORS = { edit: "#0b9e9e", delete: "#8a8f98", model: "#7b3fb5" };

// ---------- map ----------
const map = L.map("map", { zoomControl: true, preferCanvas: true, maxZoom: 21 }).setView([-37.8136, 144.9631], 13);
// Base maps: OSM, satellite imagery for tracing (Esri World Imagery), and an optional imagery URL from Local settings
const BASE_LAYERS = {
  osm: L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 21, maxNativeZoom: 19, attribution: "© OpenStreetMap contributors",
  }),
  satellite: L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 21, maxNativeZoom: 19, attribution: "Imagery © Esri, Maxar, Earthstar Geographics",
  }),
};
let layerControl = null;

function customImageryUrl() { return store.get("cityscene.imageryUrl") || ""; }

function buildLayerControl() {
  if (layerControl) map.removeControl(layerControl);
  if (BASE_LAYERS.custom) { map.removeLayer(BASE_LAYERS.custom); delete BASE_LAYERS.custom; }
  const url = customImageryUrl();
  if (url) BASE_LAYERS.custom = L.tileLayer(url, { maxZoom: 22, maxNativeZoom: 20, attribution: "Custom imagery" });
  const names = { osm: t("baseOsm"), satellite: t("baseSatellite"), custom: t("baseCustom") };
  const chosen = BASE_LAYERS[store.get("cityscene.baseLayer")] ? store.get("cityscene.baseLayer") : "osm";
  Object.entries(BASE_LAYERS).forEach(([k, layer]) => { if (k === chosen) layer.addTo(map); else map.removeLayer(layer); });
  layerControl = L.control.layers(Object.fromEntries(Object.entries(BASE_LAYERS).map(([k, l]) => [names[k], l])), {}, { position: "topright" }).addTo(map);
}

map.on("baselayerchange", (e) => {
  const key = Object.keys(BASE_LAYERS).find((k) => BASE_LAYERS[k] === e.layer);
  if (key) store.set("cityscene.baseLayer", key);
});

// ---------- panel layout: drag to resize, collapse, fold steps (remembered per browser) ----------
const PANEL_MIN = 280;
const PANEL_DEFAULT = 380;
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode etc. */ } },
};

function setPanelWidth(w) {
  const max = Math.max(PANEL_MIN, Math.round(window.innerWidth * 0.7));
  w = Math.min(max, Math.max(PANEL_MIN, Math.round(w)));
  document.body.style.setProperty("--panel-w", w + "px");
  map.invalidateSize();
  return w;
}

function updatePanelToggle() {
  const collapsed = document.body.classList.contains("panel-collapsed");
  $("panel-toggle").textContent = collapsed ? "›" : "‹";
  $("panel-toggle").title = t(collapsed ? "panelExpand" : "panelCollapse");
  $("resizer").title = collapsed ? "" : t("panelResize");
}

function setCollapsed(collapsed) {
  document.body.classList.toggle("panel-collapsed", collapsed);
  store.set("cityscene.panelCollapsed", collapsed ? "1" : "0");
  updatePanelToggle();
  map.invalidateSize();
}

setPanelWidth(Number(store.get("cityscene.panelWidth")) || PANEL_DEFAULT);

// ---------- mode: city / airport ----------
let mode = store.get("cityscene.mode") === "airport" ? "airport" : "city";

function currentMode() { return mode; }

function setMode(m) {
  mode = m === "airport" ? "airport" : "city";
  store.set("cityscene.mode", mode);
  document.querySelectorAll(".mode-tabs .tab").forEach((b) => b.classList.toggle("on", b.dataset.mode === mode));
  $("city-panel").hidden = mode !== "city";
  $("airport-panel").hidden = mode !== "airport";
  document.body.dataset.mode = mode;
  const cityLayers = [zoneLayer, buildingsLayer, typeof Place !== "undefined" ? Place.layer : null].filter(Boolean);
  if (mode === "city") {
    cityLayers.forEach((l) => l.addTo(map));
    if (drawControl) map.addControl(drawControl);
    if (buildingsLayer) legend.addTo(map);
  } else {
    cityLayers.forEach((l) => map.removeLayer(l));
    if (drawControl) map.removeControl(drawControl);
    map.removeControl(legend);
    if (typeof cancelDraw === "function") cancelDraw();
  }
  if (typeof onModeChanged === "function") onModeChanged(mode);
}

document.querySelectorAll(".mode-tabs .tab").forEach((b) => { b.onclick = () => setMode(b.dataset.mode); });
document.body.classList.toggle("panel-collapsed", store.get("cityscene.panelCollapsed") === "1");

$("panel-toggle").addEventListener("pointerdown", (e) => e.stopPropagation());
$("panel-toggle").addEventListener("dblclick", (e) => e.stopPropagation());
$("panel-toggle").onclick = () => setCollapsed(!document.body.classList.contains("panel-collapsed"));

$("resizer").addEventListener("pointerdown", (e) => {
  if (document.body.classList.contains("panel-collapsed")) return;
  const resizer = $("resizer");
  const left = $("panel").getBoundingClientRect().left;
  resizer.setPointerCapture(e.pointerId);
  resizer.classList.add("dragging");
  document.body.classList.add("resizing");
  const move = (ev) => setPanelWidth(ev.clientX - left);
  const up = (ev) => {
    resizer.removeEventListener("pointermove", move);
    resizer.removeEventListener("pointerup", up);
    resizer.classList.remove("dragging");
    document.body.classList.remove("resizing");
    store.set("cityscene.panelWidth", String(setPanelWidth(ev.clientX - left)));
  };
  resizer.addEventListener("pointermove", move);
  resizer.addEventListener("pointerup", up);
});

$("resizer").addEventListener("dblclick", () => {
  if (document.body.classList.contains("panel-collapsed")) return;
  store.set("cityscene.panelWidth", String(setPanelWidth(PANEL_DEFAULT)));
});

$("resizer").addEventListener("keydown", (e) => {
  if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
  const now = $("panel").getBoundingClientRect().width;
  store.set("cityscene.panelWidth", String(setPanelWidth(now + (e.key === "ArrowRight" ? 20 : -20))));
  e.preventDefault();
});

window.addEventListener("resize", () => setPanelWidth($("panel").getBoundingClientRect().width || PANEL_DEFAULT));

// Steps fold like "Local settings"; folded steps stay folded on the next visit
const closedSteps = new Set(JSON.parse(store.get("cityscene.closedSteps") || "[]"));
document.querySelectorAll("details.step").forEach((d) => {
  if (d.id !== "settings-box" && closedSteps.has(d.id)) d.open = false;
  d.addEventListener("toggle", () => {
    if (d.open) closedSteps.delete(d.id); else closedSteps.add(d.id);
    store.set("cityscene.closedSteps", JSON.stringify([...closedSteps]));
  });
});

const zoneLayer = new L.FeatureGroup().addTo(map);
let drawControl = null;

// leaflet-draw reads L.drawLocal when the toolbar is created, so the toolbar is rebuilt on language change
function addDrawControl() {
  if (drawControl) map.removeControl(drawControl);
  drawControl = new L.Control.Draw({
    position: "topleft",
    draw: {
      polyline: false, circle: false, marker: false, circlemarker: false,
      rectangle: { shapeOptions: { color: "#2f6fb0", weight: 2, fillOpacity: 0.08 } },
      polygon: { allowIntersection: false, shapeOptions: { color: "#2f6fb0", weight: 2, fillOpacity: 0.08 } },
    },
    edit: { featureGroup: zoneLayer },
  });
  if (mode === "city") map.addControl(drawControl);
}

// Shapes drawn with the area toolbar become the area; shapes drawn by the item and airport tools (editing.js
// drawShape, which sets activeDraw) are handled there
map.on(L.Draw.Event.CREATED, (e) => {
  if (typeof activeDraw !== "undefined" && activeDraw) return;
  zoneLayer.addLayer(e.layer);
  updateArea();
});
map.on(L.Draw.Event.EDITED, updateArea);
map.on(L.Draw.Event.DELETED, updateArea);

const legend = L.control({ position: "bottomright" });
// Buildings without OSM height (height_source default / default_small) are drawn white instead of by height
const NO_DATA_COLOR = "#ffffff";
const NO_DATA_OUTLINE = "#9aa0a8";

function noHeightData(p) {
  return String(p.height_source || "").startsWith("default");
}

function defaultHeight() {
  return currentDetail?.meta?.options?.heights?.default_height_m ?? lastStatus?.defaults?.default_height_m ?? 12;
}

legend.onAdd = () => {
  const div = L.DomUtil.create("div", "legend");
  div.innerHTML = `<i class="nodata" style="background:${NO_DATA_COLOR};border-color:${NO_DATA_OUTLINE}"></i>${esc(t("legendNoData", { h: defaultHeight() }))}<br>` +
    HEIGHT_BANDS.map(([, c, label]) => `<i style="background:${c}"></i>${label}`).join("<br>") +
    "<hr>" + ["edit", "delete", "model"].map((k) => `<i class="${k}" style="border-color:${EDIT_COLORS[k]}"></i>${t("legend_" + k)}`).join("<br>");
  return div;
};

function zoneArea() {
  let m2 = 0;
  zoneLayer.eachLayer((layer) => {
    const rings = layer.getLatLngs();
    m2 += L.GeometryUtil.geodesicArea(Array.isArray(rings[0]) ? rings[0] : rings);
  });
  return m2 / 1e6;
}

function updateArea() {
  const n = zoneLayer.getLayers().length;
  $("area").textContent = n ? t("zoneArea", { n, km2: zoneArea().toFixed(2) }) : t("noZone");
}

// ---------- language ----------
function refreshLanguage() {
  applyStaticText();
  buildLayerControl();
  if (typeof refreshAirportLanguage === "function") refreshAirportLanguage();
  if (typeof fillPlaceForm === "function") { fillPlaceForm(); renderPlaceList(); }
  addDrawControl();
  updateArea();
  if (lastStatus) renderStatus(lastStatus);
  if (currentDetail) render({ ...currentDetail, log: [] });
  if (lastMessage) say(lastMessage.key, lastMessage.vars, lastMessage.kind);
  renderJobList();
  renderPacks();
  updateMode();
  if (buildingsLayer) { map.removeControl(legend); legend.addTo(map); }
  renderEditCount();
  if (selectedId) fillEditForm();
  updatePanelToggle();
  renderServers();
  if (lastBbbike) showBbbike(lastBbbike.key, lastBbbike.cls, lastBbbike.vars);
}

document.querySelectorAll("#lang button").forEach((b) => {
  b.onclick = () => { setLang(b.dataset.lang); refreshLanguage(); };
});

// ---------- API ----------
async function api(path, options = {}) {
  const res = await fetch(path, {
    method: options.body ? "POST" : options.method || "GET",
    headers: options.body ? { "Content-Type": "application/json" } : {},
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

// Messages are kept as key + variables so they re-translate on language change; with key null, vars.text is shown as is (server errors)
function say(key, vars = {}, kind = "") {
  lastMessage = { key, vars, kind };
  $("message").textContent = key ? t(key, vars) : vars.text;
  $("message").className = kind;
}

const sayError = (e) => say(null, { text: e.message }, "err");

// ---------- settings ----------
function renderStatus(s) {
  const ok = s.xplane_ok && s.dsftool_ok;
  $("settings-flag").innerHTML = ok ? '<span class="ok-flag">✓</span>' : `<span class="bad-flag">${t("needsSetup")}</span>`;
}

async function loadStatus() {
  const s = await api("/api/status");
  lastStatus = s;
  $("xplane-root").value = s.xplane_root;
  $("dsftool").value = s.dsftool;
  renderStatus(s);
  initServers(s);
  $("settings-box").open = !(s.xplane_ok && s.dsftool_ok);
  if (!$("default-height").value) $("default-height").value = s.defaults.default_height_m;
  if (!$("render-distance").value) $("render-distance").value = s.defaults.render_distance_km;
  return s;
}

// ---------- Overpass servers: test, choose, order (saved right away) ----------
let servers = [];        // [{url, on}] in order; checked ones are used, top first
let shipped = [];
let serverTests = {};    // url -> test result

function initServers(s) {
  shipped = s.overpass.shipped;
  servers = s.overpass.known.map((url) => ({ url, on: s.overpass.active.includes(url) }));
  renderServers();
}

function serverState(url) {
  const r = serverTests[url];
  if (r === "testing") return ["", t("srvTesting")];
  if (!r) return ["", ""];
  if (r.ok) return ["ok", t("srvOk", { ms: r.ms })];
  if (r.error === "timeout") return ["bad", t("srvTimeout")];
  if ([429, 502, 503, 504].includes(r.status)) return ["warn", t("srvBusy", { status: r.status })];
  return ["bad", t("srvError", { error: r.error })];
}

function renderServers() {
  $("server-list").innerHTML = servers.map((srv, i) => {
    const [cls, text] = serverState(srv.url);
    let host = srv.url;
    try { host = new URL(srv.url).host; } catch (e) { /* keep the raw text */ }
    return `<li title="${esc(srv.url)}"><input type="checkbox" data-on="${i}" ${srv.on ? "checked" : ""}>` +
      `<span class="srv-host">${esc(host)}</span><span class="srv-state ${cls}">${esc(text)}</span>` +
      `<button class="ghost" data-up="${i}" title="${esc(t("moveUp"))}" ${i === 0 ? "disabled" : ""}>↑</button>` +
      (shipped.includes(srv.url) ? "" : `<button class="ghost" data-remove="${i}" title="${esc(t("removeServer"))}">×</button>`) +
      "</li>";
  }).join("");
  $("server-list").querySelectorAll("[data-on]").forEach((c) => {
    c.onchange = () => { servers[c.dataset.on].on = c.checked; saveServers(); };
  });
  $("server-list").querySelectorAll("[data-up]").forEach((b) => {
    b.onclick = () => { const i = Number(b.dataset.up); [servers[i - 1], servers[i]] = [servers[i], servers[i - 1]]; saveServers(); };
  });
  $("server-list").querySelectorAll("[data-remove]").forEach((b) => {
    b.onclick = () => { servers.splice(Number(b.dataset.remove), 1); saveServers(); };
  });
  $("servers-fastest").hidden = !Object.values(serverTests).some((r) => r && r.ok);
}

async function saveServers() {
  const urls = servers.filter((s) => s.on).map((s) => s.url);
  if (!urls.length) {
    say("needOneServer", {}, "warn");
    return loadStatus().catch(sayError);
  }
  try {
    await api("/api/overpass", { body: { urls } });
    renderServers();
    say("serversSaved", {}, "ok");
  } catch (e) { sayError(e); }
}

$("test-servers").onclick = async () => {
  const urls = servers.map((s) => s.url);
  urls.forEach((u) => { serverTests[u] = "testing"; });
  renderServers();
  $("test-servers").disabled = true;
  try {
    for (const r of await api("/api/overpass/test", { body: { urls } })) serverTests[r.url] = r;
  } catch (e) {
    urls.forEach((u) => { delete serverTests[u]; });
    sayError(e);
  } finally {
    $("test-servers").disabled = false;
    renderServers();
  }
};

// Working servers first, fastest on top, and only those checked; the others stay listed but unchecked
$("servers-fastest").onclick = () => {
  const ok = (s) => serverTests[s.url] && serverTests[s.url].ok;
  servers.sort((a, b) => (ok(b) - ok(a)) || ((ok(a) && ok(b)) ? serverTests[a.url].ms - serverTests[b.url].ms : 0));
  servers.forEach((s) => { s.on = Boolean(ok(s)); });
  saveServers();
};

$("server-add-btn").onclick = () => {
  const url = $("server-add").value.trim();
  if (!/^https?:\/\//.test(url)) return say("badServerUrl", {}, "warn");
  if (!servers.some((s) => s.url === url)) servers.push({ url, on: true });
  $("server-add").value = "";
  saveServers();
};

$("imagery-url").value = customImageryUrl();
$("imagery-url").onchange = () => {
  const v = $("imagery-url").value.trim();
  if (v && !/^https?:\/\/.+\{z\}.*/.test(v)) return say("badImageryUrl", {}, "warn");
  store.set("cityscene.imageryUrl", v);
  if (v) store.set("cityscene.baseLayer", "custom");
  buildLayerControl();
  say("imagerySaved", {}, "ok");
};

$("save-settings").onclick = async () => {
  try {
    await api("/api/settings", { body: { xplane_root: $("xplane-root").value, dsftool: $("dsftool").value } });
    const s = await loadStatus();
    const ok = s.xplane_ok && s.dsftool_ok;
    say(ok ? "settingsSaved" : "settingsInvalid", {}, ok ? "ok" : "warn");
  } catch (e) { sayError(e); }
};

// ---------- search ----------
$("search").addEventListener("keydown", async (e) => {
  if (e.key !== "Enter" || !e.target.value.trim()) return;
  try {
    const url = "https://nominatim.openstreetmap.org/search?format=json&limit=1&q=" + encodeURIComponent(e.target.value);
    const lang = LANG === "zh" ? "zh-CN,en" : "en";
    const [hit] = await (await fetch(url, { headers: { "Accept-Language": lang } })).json();
    if (!hit) return say("notFound", {}, "warn");
    const [s, n, w, ea] = hit.boundingbox.map(Number);
    map.fitBounds([[s, w], [n, ea]], { maxZoom: 15 });
    say(null, { text: hit.display_name });
  } catch (err) { say("searchFailed", { err: err.message }, "err"); }
});

$("clear-zone").onclick = () => { zoneLayer.clearLayers(); updateArea(); };

// ---------- OSM data source: Overpass servers or a local file / download link (e.g. from BBBike) ----------
function sourceType() {
  return document.querySelector('input[name="source"]:checked').value;
}

function setSource(source) {
  const type = source && source.type === "file" ? "file" : "overpass";
  document.querySelector(`input[name="source"][value="${type}"]`).checked = true;
  $("source-file").value = type === "file" ? source.path : "";
  $("source-file-box").hidden = type !== "file";
}

document.querySelectorAll('input[name="source"]').forEach((r) => {
  r.onchange = () => { $("source-file-box").hidden = sourceType() !== "file"; };
});

// BBBike: open its extract form with the area's polygon filled in (the user submits it there). The user then clicks
// "Check BBBike" whenever they like; that reads BBBike's public status page once and, when the file is ready, puts its
// link in the data source field. The request is remembered in this browser (BBBike's page lists the last 24 hours).
const BBBIKE_KEEP_MS = 24 * 3600 * 1000;
let lastBbbike = null;

function bbbikeRequest() {
  let req = null;
  try { req = JSON.parse(store.get("cityscene.bbbike") || "null"); } catch (e) { req = null; }
  return req && Date.now() - req.started < BBBIKE_KEEP_MS ? req : null;
}

function showBbbike(key, cls = "", vars = {}) {
  lastBbbike = key ? { key, cls, vars } : null;
  $("bbbike-track").hidden = !key;
  $("bbbike-state").textContent = key ? t(key, { name: bbbikeRequest()?.name || vars.name || "", ...vars }) : "";
  $("bbbike-state").className = cls;
  $("bbbike-check").hidden = !bbbikeRequest();
  $("bbbike-stop").hidden = !bbbikeRequest();
}

function stopBbbike() {
  store.set("cityscene.bbbike", "null");
  showBbbike(null);
}

async function checkBbbike() {
  const req = bbbikeRequest();
  if (!req) return showBbbike(null);
  $("bbbike-check").disabled = true;
  showBbbike("bbbikeChecking");
  const time = new Date().toLocaleTimeString();
  try {
    const s = await api(`/api/bbbike/status?name=${encodeURIComponent(req.name)}`);
    if (s.state === "ready") {
      document.querySelector('input[name="source"][value="file"]').checked = true;
      $("source-file-box").hidden = false;
      $("source-file").value = s.link;
      store.set("cityscene.bbbike", "null");
      showBbbike("bbbikeReady", "ok", { name: req.name });
      say("bbbikeReadyMsg", {}, "ok");
      return;
    }
    const key = s.state === "running" ? "bbbikeRunning" : s.state === "waiting" ? "bbbikeWaiting" : "bbbikeNotYet";
    showBbbike(key, s.state === "not_found" ? "warn" : "", { time });
  } catch (e) {
    showBbbike("bbbikeCheckFailed", "warn", { time });
  } finally {
    $("bbbike-check").disabled = false;
  }
}

$("bbbike").onclick = async () => {
  if (!zoneLayer.getLayers().length) return say("needZone", {}, "warn");
  // A random suffix makes the name unique on BBBike's public status page
  const base = (currentJob || $("job-name").value.trim() || "area").replace(/[^\w-]/g, "_");
  const name = `cityscene ${base} ${Math.random().toString(16).slice(2, 6)}`;
  // Open the tab first (inside the click), then point it at the form, so pop-up blockers allow it
  const tab = window.open("about:blank", "_blank");
  try {
    const r = await api("/api/bbbike/url", { body: { zone: zoneLayer.toGeoJSON(), name } });
    if (tab) tab.location = r.url; else window.open(r.url, "_blank");
    store.set("cityscene.bbbike", JSON.stringify({ name: r.name, started: Date.now() }));
    say("bbbikeOpened", {}, "ok");
    showBbbike("bbbikeSent");
  } catch (e) {
    if (tab) tab.close();
    sayError(e);
  }
};

$("bbbike-check").onclick = checkBbbike;
$("bbbike-stop").onclick = stopBbbike;

$("job-name").addEventListener("input", (e) => {
  $("scenery-name").placeholder = "CityScene_" + (e.target.value || "name");
});

// ---------- start: new project / open project / import pack ----------
let jobList = [];
let packList = [];
let startTab = ["new", "open", "import"].includes(store.get("cityscene.startTab")) ? store.get("cityscene.startTab") : "new";

function showTab(tab) {
  startTab = tab;
  store.set("cityscene.startTab", tab);
  document.querySelectorAll("#start-box .tab").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  document.querySelectorAll("#start-box .tab-panel").forEach((p) => { p.hidden = p.dataset.panel !== tab; });
  updateMode();
  if (tab === "open") loadJobs().catch(sayError);
  if (tab === "import") loadPacks();
}

document.querySelectorAll("#start-box .tab").forEach((b) => { b.onclick = () => showTab(b.dataset.tab); });

// The start chooser shows while no project is open; once one is, the project bar and its steps show instead
function updateMode() {
  const open = Boolean(currentJob);
  $("start-box").hidden = open;
  $("project-bar").hidden = !open;
  const editing = open || startTab === "new";
  $("area-box").hidden = !editing;
  $("job-box").hidden = !editing;
  $("new-names").hidden = open;
  $("build").textContent = t(open ? "rebuild" : "build");
  if (!open) for (const id of ["progress-box", "result-box", "edit-box", "place-box"]) $(id).hidden = true;
  $("project-name").textContent = currentJob || "";
  $("project-pack").textContent = currentDetail ? t("projectPack", { pack: currentDetail.meta.scenery_name }) : "";
}

function renderJobList() {
  $("job-list").innerHTML = jobList.length ? jobList.map((j) => `
    <li><div class="item-text"><b>${esc(j.name)}</b><div class="hint">${esc(j.scenery_name)} · ${esc(t(j.built ? "stateBuilt" : "stateNotBuilt"))}</div></div>
      <div class="item-actions"><button class="ghost" data-open="${esc(j.name)}">${esc(t("openBtn"))}</button></div></li>`).join("")
    : `<li class="empty">${esc(t("noJobs"))}</li>`;
  $("job-list").querySelectorAll("[data-open]").forEach((b) => { b.onclick = () => openJob(b.dataset.open, true).catch(sayError); });
}

async function loadJobs() {
  jobList = await api("/api/jobs");
  renderJobList();
}

function renderPacks() {
  $("pack-list").innerHTML = packList.length ? packList.map((p, i) => {
    const notes = [t("source_" + p.source)];
    if (p.kind === "legacy") notes.push(t("legacyTag"));
    if (p.existing_job) notes.push(t("alreadyImported", { name: p.existing_job }));
    const actions = p.existing_job
      ? `<button class="ghost" data-open="${esc(p.existing_job)}">${esc(t("openBtn"))}</button><button class="ghost" data-reimport="${i}">${esc(t("reimportBtn"))}</button>`
      : `<button data-import="${i}">${esc(t("importBtn"))}</button>`;
    return `<li><div class="item-text"><b>${esc(p.folder)}</b><div class="hint">${esc(notes.join(" · "))}</div></div><div class="item-actions">${actions}</div></li>`;
  }).join("") : `<li class="empty">${esc(t("importNone"))}</li>`;
  $("pack-list").querySelectorAll("[data-open]").forEach((b) => { b.onclick = () => openJob(b.dataset.open, true).catch(sayError); });
  $("pack-list").querySelectorAll("[data-import]").forEach((b) => {
    b.onclick = () => importPack(packList[b.dataset.import].path, "", false);
  });
  $("pack-list").querySelectorAll("[data-reimport]").forEach((b) => {
    const p = packList[b.dataset.reimport];
    b.onclick = () => { if (confirm(t("confirmReimport", { name: p.existing_job }))) importPack(p.path, p.existing_job, true); };
  });
}

async function loadPacks() {
  try {
    [packList, jobList] = await Promise.all([api("/api/packs"), api("/api/jobs")]);
    renderPacks();
  } catch (e) { sayError(e); }
}

function uniqueName(base) {
  const names = new Set(jobList.map((j) => j.name));
  let i = 2;
  while (names.has(`${base}_${i}`)) i++;
  return `${base}_${i}`;
}

// A pack already imported is offered as "Open"; importing a different pack whose job name is taken asks for a new name
async function importPack(path, name, overwrite) {
  if (!path) return say("importNeedPath", {}, "warn");
  say("importing", {}, "");
  try {
    const r = await api("/api/import", { body: { path, name, overwrite } });
    await openJob(r.name, true);
    say(r.kind === "legacy" ? "importedLegacy" : "imported", { name: r.name, blocks: r.blocks, edits: r.edits, models: r.models }, "ok");
  } catch (e) {
    // A job with this name exists, or another job already uses this pack's name: import under a new name
    const taken = !overwrite && ((e.message.match(/^Job (\S+) already exists/) || [])[1] ||
      (e.message.match(/already uses the pack name/) && (name || e.message.match(/^Job (\S+)/)[1])));
    if (!taken) return sayError(e);
    const chosen = prompt(t("importNameTaken", { name: taken }), uniqueName(taken));
    if (chosen && chosen.trim()) return importPack(path, chosen.trim(), false);
    say(null, { text: "" });
  }
}

$("import-btn").onclick = () => importPack($("import-path").value.trim(), $("import-name").value.trim(), false);

$("switch-project").onclick = () => {
  clearInterval(pollTimer);
  currentJob = null;
  currentDetail = null;
  selectedId = null;
  edits = {};
  zoneLayer.clearLayers();
  updateArea();
  if (buildingsLayer) { map.removeLayer(buildingsLayer); buildingsLayer = null; }
  map.removeControl(legend);
  $("edit-form").hidden = true;
  $("job-name").value = "";
  $("scenery-name").value = "";
  setSource(null);
  setExtras(null);
  clearPlacements();
  $("log").textContent = "";
  say(null, { text: "" });
  showTab(startTab);
};

async function openJob(name, fit) {
  currentJob = name;
  selectedId = null;
  $("edit-form").hidden = true;
  logSince = 0;
  $("log").textContent = "";
  const d = await api(`/api/jobs/${name}`);
  $("job-name").value = d.meta.name;
  $("scenery-name").value = d.meta.scenery_name;
  const dh = d.meta.options?.heights?.default_height_m;
  if (dh) $("default-height").value = dh;
  const rd = d.meta.options?.dsf?.render_distance_km;
  if (rd) $("render-distance").value = rd;
  setSource(d.meta.source);
  setExtras(d.meta.options?.extras);
  zoneLayer.clearLayers();
  L.geoJSON(d.zone).eachLayer((l) => zoneLayer.addLayer(l));
  updateArea();
  if (fit && zoneLayer.getLayers().length) map.fitBounds(zoneLayer.getBounds());
  render(d);
  updateMode();
  if (d.run.state === "running") startPolling();
  else if (d.built) loadBuildings();
  loadPlacements();
}

$("build").onclick = async () => {
  const name = currentJob || $("job-name").value.trim();
  if (!name) return say("needName", {}, "warn");
  if (!zoneLayer.getLayers().length) return say("needZone", {}, "warn");
  const area = zoneArea();
  if (area > 25 && !confirm(t("confirmLarge", { km2: area.toFixed(1) }))) return;
  await loadJobs();
  const existing = jobList.some((j) => j.name === name);
  if (existing && name !== currentJob && !confirm(t("confirmOverwrite", { name }))) return;
  const sceneryName = currentJob ? currentDetail.meta.scenery_name : $("scenery-name").value.trim();
  const source = sourceType() === "file" ? { type: "file", path: $("source-file").value.trim() } : { type: "overpass" };
  if (source.type === "file" && !source.path) return say("needSourceFile", {}, "warn");
  try {
    const options = {};
    const dh = parseFloat($("default-height").value);
    if (dh > 0) options.heights = { default_height_m: dh };
    const rd = parseFloat($("render-distance").value);
    if (rd > 0) options.dsf = { render_distance_km: rd };
    const extras = readExtras();
    if (extras) options.extras = extras;
    await api("/api/jobs", { body: {
      name, scenery_name: sceneryName, zone: zoneLayer.toGeoJSON(), options, source, overwrite: existing,
    } });
    await loadJobs();
    currentJob = name;
    updateMode();
    logSince = 0;
    $("log").textContent = "";
    await api(`/api/jobs/${name}/build`, { body: { force_fetch: $("force-fetch").checked } });
    say("started", {}, "ok");
    startPolling();
  } catch (e) { sayError(e); }
};

function startPolling() {
  clearInterval(pollTimer);
  pollTimer = setInterval(poll, 1000);
  poll();
}

async function poll() {
  if (!currentJob) return;
  try {
    const d = await api(`/api/jobs/${currentJob}?since=${logSince}`);
    render(d);
    if (d.run.state !== "running") {
      clearInterval(pollTimer);
      if (d.run.state === "done") { say("done", {}, "ok"); loadBuildings(); }
      if (d.run.state === "error") say("failed", { err: d.run.error }, "err");
    }
  } catch (e) { sayError(e); clearInterval(pollTimer); }
}

function render(d) {
  currentDetail = d;
  const running = d.run.state === "running";
  $("progress-box").hidden = d.run.state === "idle" && !d.built;
  $("build").disabled = running;
  if (d.log.length) {
    $("log").textContent += d.log.join("\n") + "\n";
    $("log").scrollTop = $("log").scrollHeight;
    logSince = d.log_total;
  }
  const at = STEPS.indexOf(d.run.step);
  document.querySelectorAll("#steps li").forEach((li, i) => {
    li.className = "";
    if (d.run.state === "done" || (d.run.state === "idle" && d.built)) li.className = "done";
    else if (at >= 0 && i < at) li.className = "done";
    else if (i === at) li.className = d.run.state === "error" ? "failed" : "active";
  });
  $("result-box").hidden = !d.built || running;
  $("edit-box").hidden = !d.built || running;
  $("place-box").hidden = running;
  renderDeps(d.reports.build && d.reports.build.dependencies);
  $("install").disabled = d.installed;
  $("uninstall").disabled = !d.installed;
  const c = d.reports.clean, k = d.reports.classify, b = d.reports.build;
  const rows = [];
  if (c) {
    rows.push([t("sumBuildings"), `${c.buildings} / ${c.kept_blocks}`]);
    rows.push([t("sumMax"), `${c.max_height_m} m`]);
    const hs = c.height_sources || {};
    const total = Object.values(hs).reduce((a, v) => a + v, 0) || 1;
    const real = (hs.osm_height || 0) + (hs.osm_part_height || 0) + (hs.osm_levels || 0);
    rows.push([t("sumOsmHeight"), t("sumOsmHeightVal", { pct: Math.round(100 * real / total) })]);
  }
  if (k) rows.push([t("sumCategories"), ["high_rise", "mid_rise", "low_rise"].map((x) => k.buildings_by_category[x] || 0).join(" / ")]);
  if (b) rows.push([t("sumTiles"), Object.keys(b.tiles).join(", ")]);
  rows.push([t("sumStatus"), d.installed ? t("installed") : t("notInstalled")]);
  $("summary").innerHTML = rows.map(([k2, v]) => `<dt>${esc(k2)}</dt><dd>${esc(v)}</dd>`).join("");
}

function heightColor(h) {
  let color = HEIGHT_BANDS[0][1];
  for (const [min, c] of HEIGHT_BANDS) if (h >= min) color = c;
  return color;
}

function editKind(e) {
  if (!e) return null;
  if (e.delete) return "delete";
  if (e.model) return "model";
  return "edit";
}

function buildingStyle(f) {
  const p = f.properties;
  const kind = editKind(edits[p.building_id]);
  const style = noHeightData(p)
    ? { color: NO_DATA_OUTLINE, fillColor: NO_DATA_COLOR, weight: 0.6, fillOpacity: 0.55, dashArray: null }
    : { color: heightColor(p.height_m), fillColor: heightColor(p.height_m), weight: 0.6, fillOpacity: 0.65, dashArray: null };
  if (kind === "edit") Object.assign(style, { color: EDIT_COLORS.edit, weight: 2.5 });
  if (kind === "delete") Object.assign(style, { color: EDIT_COLORS.delete, fillOpacity: 0.08, weight: 1, dashArray: "3 3" });
  if (kind === "model") Object.assign(style, { color: EDIT_COLORS.model, fillColor: EDIT_COLORS.model, weight: 1.5, fillOpacity: 0.7 });
  if (p.building_id === selectedId) Object.assign(style, { color: "#f2c200", weight: 3 });
  return style;
}

async function loadEdits() {
  const o = await api(`/api/jobs/${currentJob}/overrides`);
  edits = o.buildings;
  modelFiles = o.models;
  modelsDir = o.models_dir;
  $("model-files").innerHTML = modelFiles.map((m) => `<option value="${esc(m)}">`).join("");
  renderEditCount();
}

async function loadBuildings() {
  if (buildingsLayer) { map.removeLayer(buildingsLayer); buildingsLayer = null; }
  if (!currentJob) return;
  await loadEdits();
  if (!$("show-buildings").checked) { map.removeControl(legend); return; }
  const data = await api(`/api/jobs/${currentJob}/buildings`);
  buildingsLayer = L.geoJSON(data, {
    style: buildingStyle,
    onEachFeature: (f, layer) => {
      const p = f.properties;
      layer.bindTooltip(() => {
        const kind = editKind(edits[p.building_id]);
        const height = noHeightData(p) ? esc(t("tipNoData", { h: p.height_m })) : `${p.height_m} m (${esc(p.height_source)})`;
        return `${p.name ? esc(p.name) + "<br>" : ""}${height}<br>${esc(p.facade_style)}` +
          (kind ? `<br><b>${esc(t("legend_" + kind))}</b>` : "");
      });
      layer.on("click", (e) => { L.DomEvent.stopPropagation(e); selectBuilding(p.building_id); });
    },
  }).addTo(map);
  legend.addTo(map);
  if (data.total > data.features.length) say("previewLimited", { shown: data.features.length, total: data.total }, "warn");
}

$("show-buildings").onchange = loadBuildings;

// ---------- editing single buildings ----------
function selectedBlocks() {
  const blocks = [];
  if (buildingsLayer) buildingsLayer.eachLayer((l) => { if (l.feature.properties.building_id === selectedId) blocks.push(l.feature.properties); });
  return blocks;
}

function selectBuilding(id) {
  selectedId = id;
  if (buildingsLayer) buildingsLayer.setStyle(buildingStyle);
  $("edit-form").hidden = false;
  fillEditForm();
}

function fillEditForm() {
  const blocks = selectedBlocks();
  const tallest = blocks.length ? Math.max(...blocks.map((b) => b.height_m)) : 0;
  const name = blocks.find((b) => b.name)?.name;
  const osm = selectedId.match(/^(way|relation)\/(\d+)/);
  const link = osm ? `<a href="https://www.openstreetmap.org/${osm[1]}/${osm[2]}" target="_blank" rel="noopener">${esc(selectedId)}</a>` : esc(selectedId);
  $("edit-title").innerHTML = `<b>${esc(name || t("unnamed"))}</b> <small>${link}</small><br>` +
    `<small>${esc(t("editCurrent", { h: tallest, n: blocks.length, style: blocks[0]?.facade_style ?? "?" }))}</small>`;
  const e = edits[selectedId] || {};
  const kind = editKind(e) || "keep";
  $("edit-action").value = kind === "edit" ? "keep" : kind;
  $("edit-height").value = e.height_m ?? "";
  $("edit-height").placeholder = tallest;
  $("edit-style").value = e.facade ? "custom" : e.style ?? "";
  $("edit-facade").value = e.facade ?? "";
  $("edit-facade-row").hidden = $("edit-style").value !== "custom";
  $("edit-model-path").value = e.model?.path ?? "";
  $("edit-heading").value = e.model?.heading ?? 0;
  $("edit-east").value = e.model?.offset_e_m ?? 0;
  $("edit-north").value = e.model?.offset_n_m ?? 0;
  $("models-hint").textContent = t("modelsHint", { dir: modelsDir, n: modelFiles.length });
  showActionFields();
}

function showActionFields() {
  const action = $("edit-action").value;
  $("edit-keep").hidden = action !== "keep";
  $("edit-model").hidden = action !== "model";
}

$("edit-action").onchange = showActionFields;
$("edit-style").onchange = () => { $("edit-facade-row").hidden = $("edit-style").value !== "custom"; };

function renderEditCount() {
  const counts = { edit: 0, delete: 0, model: 0 };
  for (const e of Object.values(edits)) counts[editKind(e)]++;
  const total = counts.edit + counts.delete + counts.model;
  $("edit-count").textContent = total ? t("editCount", { total, ...counts }) : t("noEdits");
}

async function saveEdit(edit) {
  try {
    const r = await api(`/api/jobs/${currentJob}/overrides`, { body: { building_id: selectedId, edit } });
    edits = r.buildings;
    if (buildingsLayer) buildingsLayer.setStyle(buildingStyle);
    renderEditCount();
    fillEditForm();
    say("editSaved", {}, "ok");
  } catch (e) { sayError(e); }
}

$("edit-save").onclick = () => {
  const action = $("edit-action").value;
  if (action === "delete") return saveEdit({ delete: true });
  if (action === "model") {
    const path = $("edit-model-path").value.trim().replace(/\\/g, "/");
    // Not in the models folder: it can only be a library path, so make sure that is meant
    if (path && !modelFiles.includes(path) && !confirm(t("confirmLibraryPath", { path }))) return;
    return saveEdit({ model: {
      path, heading: parseFloat($("edit-heading").value) || 0,
      offset_e_m: parseFloat($("edit-east").value) || 0, offset_n_m: parseFloat($("edit-north").value) || 0,
    } });
  }
  const style = $("edit-style").value;
  if (style === "custom" && !$("edit-facade").value.trim()) return say("needFacade", {}, "warn");
  return saveEdit({ height_m: $("edit-height").value || null, style: style && style !== "custom" ? style : null,
    facade: style === "custom" ? $("edit-facade").value.trim() : null });
};

$("edit-reset").onclick = () => saveEdit(null);

// ---------- "Browse..." buttons: the local server opens the system's file dialog ----------
async function useModelFile(path) {
  const r = await api(`/api/jobs/${currentJob}/models`, { body: { path } });
  $("edit-model-path").value = r.path;
  await loadEdits();
  $("models-hint").textContent = t("modelsHint", { dir: modelsDir, n: modelFiles.length });
  if (r.missing.length) say("modelMissing", { path: r.path, list: r.missing.join(", ") }, "warn");
  else if (r.copied.length) say("modelCopied", { path: r.path, n: r.copied.length - 1 }, "ok");
}

document.querySelectorAll("button.browse").forEach((b) => {
  b.onclick = async (e) => {
    e.preventDefault();
    const input = $(b.dataset.for);
    const kind = b.dataset.pick;
    b.disabled = true;
    say("pickOpen", {}, "");
    try {
      const initial = kind === "obj" ? modelsDir : input.value;
      const r = await api("/api/pick", { body: { kind, title: t("pickTitle_" + b.dataset.for), initial } });
      say(null, { text: "" });
      if (!r.path) return;
      if (kind === "obj") await useModelFile(r.path);
      else input.value = r.path;
    } catch (err) { sayError(err); } finally { b.disabled = false; }
  };
});

$("edit-reference").onclick = async () => {
  try {
    const res = await fetch(`/api/jobs/${currentJob}/reference?id=${encodeURIComponent(selectedId)}`);
    if (!res.ok) throw new Error((await res.json()).error || res.statusText);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(await res.blob());
    a.download = selectedId.replace(/[^\w#-]/g, "_") + "_reference.obj";
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (e) { sayError(e); }
};

$("apply-edits").onclick = async () => {
  try {
    logSince = 0;
    $("log").textContent = "";
    await api(`/api/jobs/${currentJob}/build`, { body: { steps: ["classify", "build_dsf"] } });
    say("started", {}, "ok");
    startPolling();
  } catch (e) { sayError(e); }
};

$("install").onclick = async () => {
  try {
    const r = await api(`/api/jobs/${currentJob}/install`, { body: {} });
    const backup = r.backup ? t("backupNote", { name: r.backup }) : "";
    say("installedMsg", { link: r.link, ini: r.ini, backup }, "ok");
    openJob(currentJob, false);
  } catch (e) { sayError(e); }
};

$("uninstall").onclick = async () => {
  if (!confirm(t("confirmUninstall"))) return;
  try {
    await api(`/api/jobs/${currentJob}/uninstall`, { body: {} });
    say("uninstalledMsg", {}, "ok");
    openJob(currentJob, false);
  } catch (e) { sayError(e); }
};

applyStaticText();
buildLayerControl();
updatePanelToggle();
addDrawControl();
updateArea();
loadStatus().catch(sayError);
showTab(startTab);
if (bbbikeRequest()) { $("source-file-box").hidden = false; showBbbike("bbbikeSent"); }
// the other scripts (viewer, library, placements, airport) load after this one
window.addEventListener("load", () => { if (!currentJob) setExtras(null); setMode(mode); });

// ---------- trees from OSM (settings section extras) ----------
const EXTRA_DEFAULTS = {
  tree_asset: "lib/vegetation/trees/deciduous/maple_medium.for",
  tree_row_asset: "lib/vegetation/trees/deciduous/oak_medium.for",
  wood_asset: "lib/vegetation/forests/mixed/temperate.for",
};

function setExtras(x) {
  x = x || {};
  $("extra-trees").checked = Boolean(x.trees);
  $("extra-rows").checked = Boolean(x.tree_rows);
  $("extra-woods").checked = Boolean(x.woods);
  $("extra-tree-asset").value = x.tree_asset || EXTRA_DEFAULTS.tree_asset;
  $("extra-row-asset").value = x.tree_row_asset || EXTRA_DEFAULTS.tree_row_asset;
  $("extra-wood-asset").value = x.wood_asset || EXTRA_DEFAULTS.wood_asset;
}

function readExtras() {
  const x = { trees: $("extra-trees").checked, tree_rows: $("extra-rows").checked, woods: $("extra-woods").checked };
  for (const [key, id] of [["tree_asset", "extra-tree-asset"], ["tree_row_asset", "extra-row-asset"], ["wood_asset", "extra-wood-asset"]]) {
    const v = $(id).value.trim();
    if (v && v !== EXTRA_DEFAULTS[key]) x[key] = v;
  }
  return x.trees || x.tree_rows || x.woods || Object.keys(x).length > 3 ? x : null;
}

// ---------- libraries the generated pack needs ----------
function renderDeps(deps) {
  const box = $("deps");
  if (!deps) { box.innerHTML = ""; return; }
  const libs = Object.entries(deps.libraries || {});
  box.innerHTML = `<div class="label-text">${esc(t("depsTitle"))}</div><ul class="dep-list">` +
    libs.map(([name, paths]) => `<li>${esc(name)} <span class="hint">(${paths.length})</span></li>`).join("") + "</ul>" +
    (deps.missing && deps.missing.length ? `<p class="warn-text">${esc(t("depsMissing", { n: deps.missing.length, list: deps.missing.slice(0, 5).join(", ") }))}</p>` : "");
}
