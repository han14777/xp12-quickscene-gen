// Library browser: every installed X-Plane library (default + Custom Scenery) and the project's own models, with a
// 3D preview. openLibrary({types, owner, onPick, title}) opens it to pick an asset (types limits the file types,
// owner "job:<name>" / "apt:<name>" adds the project's models tab); without onPick it is a plain browser.
"use strict";

const Lib = {
  viewer: null,
  packs: [],
  opts: {},
  tab: "libs",
  offset: 0,
  items: [],
  total: 0,
  selected: null,
  timer: null,
};

function libStoreGet(key, fallback) {
  const v = store.get("cityscene.lib." + key);
  return v === null || v === undefined ? fallback : v;
}

async function loadLibraryPacks(refresh) {
  const r = await api("/api/library/packs" + (refresh ? "?refresh=1" : ""));
  Lib.packs = r.packs;
  Lib.categories = r.categories;
  Lib.types = r.types;
  Lib.totalPaths = r.total;
}

function renderLibFilters() {
  const keepPack = libStoreGet("pack", "");
  $("lib-pack").innerHTML = `<option value="">${esc(t("libAllPacks", { n: Lib.totalPaths || 0 }))}</option>` +
    Lib.packs.map((p) => `<option value="${p.index}">${esc((p.origin === "default" ? "X-Plane · " : "") + p.name)} (${p.count})</option>`).join("");
  $("lib-pack").value = Lib.packs.some((p) => String(p.index) === keepPack) ? keepPack : "";
  const allowed = Lib.opts.types && Lib.opts.types.length ? Lib.opts.types : Lib.types;
  $("lib-type").innerHTML = (allowed.length > 1 ? `<option value="">${esc(t("libAllTypes"))}</option>` : "") +
    allowed.map((x) => `<option value="${x}">${esc(t("type_" + x))}</option>`).join("");
  const keepType = libStoreGet("type", "");
  if (allowed.includes(keepType)) $("lib-type").value = keepType;
  $("lib-cat").innerHTML = `<option value="">${esc(t("libAllCats"))}</option>` +
    Lib.categories.map((c) => `<option value="${c}">${esc(t("cat_" + c))}</option>`).join("");
  $("lib-cat").value = libStoreGet("cat", "");
  $("lib-q").value = libStoreGet("q", "");
}

function libQuery(offset) {
  const p = new URLSearchParams({ q: $("lib-q").value.trim(), pack: $("lib-pack").value, type: $("lib-type").value,
    category: $("lib-cat").value, offset: String(offset), limit: "200", hidden: $("lib-hidden").checked ? "1" : "0" });
  return "/api/library/search?" + p.toString();
}

async function searchLibrary(more) {
  store.set("cityscene.lib.q", $("lib-q").value);
  store.set("cityscene.lib.pack", $("lib-pack").value);
  store.set("cityscene.lib.type", $("lib-type").value);
  store.set("cityscene.lib.cat", $("lib-cat").value);
  Lib.offset = more ? Lib.offset + 200 : 0;
  $("lib-count").textContent = t("libSearching");
  try {
    let r = await api(libQuery(Lib.offset));
    // a type filter limited by the caller but set to "all": filter on the client
    if (Lib.opts.types && Lib.opts.types.length && !$("lib-type").value) {
      r = { ...r, items: r.items.filter((i) => Lib.opts.types.includes(i.type)) };
    }
    Lib.items = more ? Lib.items.concat(r.items) : r.items;
    Lib.total = r.total;
    renderLibList();
  } catch (e) {
    $("lib-count").textContent = e.message;
  }
}

function libItemHtml(item, i) {
  const name = item.path.split("/").pop();
  const dir = item.path.slice(0, item.path.length - name.length);
  const sel = Lib.selected && Lib.selected.path === item.path ? " on" : "";
  return `<li class="lib-item${sel}" data-i="${i}" title="${esc(item.path)}">
    <span class="badge t-${item.type}">${item.type}</span>
    <span class="lib-name"><b>${esc(name)}</b><small>${esc(dir)}</small></span>
    <small class="lib-src">${esc(item.library || "")}${item.deprecated ? " · " + esc(t("libDeprecated")) : ""}</small></li>`;
}

function renderLibList() {
  $("lib-list").innerHTML = Lib.items.map(libItemHtml).join("") ||
    `<li class="empty">${esc(t(Lib.tab === "models" ? "libNoModels" : "libNothing"))}</li>`;
  $("lib-count").textContent = Lib.tab === "models" ? t("libModelsCount", { n: Lib.items.length })
    : t("libCount", { n: Lib.total, shown: Lib.items.length });
  $("lib-more").hidden = Lib.tab === "models" || Lib.items.length >= Lib.total;
  $("lib-list").querySelectorAll(".lib-item").forEach((li) => {
    li.onclick = () => selectLibItem(Lib.items[Number(li.dataset.i)]);
    li.ondblclick = () => { selectLibItem(Lib.items[Number(li.dataset.i)]); if (Lib.opts.onPick) useLibItem(); };
  });
}

function assetRef(item) {
  if (item.local) return `${Lib.opts.owner}:${item.path}`;
  return "lib:" + item.path;
}

async function selectLibItem(item) {
  Lib.selected = item;
  $("lib-list").querySelectorAll(".lib-item").forEach((li) => li.classList.toggle("on", Lib.items[Number(li.dataset.i)] === item));
  $("lib-use").disabled = false;
  $("lib-info").innerHTML = `<div class="lib-path">${esc(item.path)}</div>
    <div class="hint">${esc(t("type_" + item.type))}${item.library ? " · " + esc(t("libFrom", { libs: (item.libraries || [item.library]).join(", ") })) : " · " + esc(t("libLocal"))}</div>`;
  if (!Lib.viewer) Lib.viewer = new AssetViewer($("viewer"));
  Lib.viewer.message(t("libLoading"));
  try {
    const p = item.pack !== undefined && !item.local ? `&pack=${item.pack}` : "";
    const data = await api("/api/asset?ref=" + encodeURIComponent(assetRef(item)) + p);
    if (Lib.selected !== item) return;
    await Lib.viewer.show(data);
    const facts = [];
    if (data.file) facts.push(data.file);
    if (data.type === "fac") facts.push(t("facFacts", { type: data.facade_type, min: data.floors_min ?? "–", max: data.floors_max ?? "∞" }));
    if (data.type === "obj") facts.push(t("objFacts", { v: data.positions.length / 3, lights: data.lights.length }));
    if (data.type === "for") facts.push(t("forFacts", { n: data.trees.length, mesh: Object.keys(data.meshes).length }));
    if (facts.length) $("lib-info").insertAdjacentHTML("beforeend", `<div class="hint">${esc(facts.join(" · "))}</div>`);
  } catch (e) {
    Lib.viewer.clear();
    Lib.viewer.message(e.message);
  }
}

function useLibItem() {
  if (!Lib.selected || !Lib.opts.onPick) return;
  const pick = Lib.opts.onPick;
  const item = Lib.selected;
  closeLibrary();
  pick(item.path, item);
}

async function loadModels() {
  if (!Lib.opts.owner) return;
  try {
    const r = await api("/api/models?owner=" + encodeURIComponent(Lib.opts.owner));
    $("lib-models-dir").textContent = t("libModelsDir", { dir: r.dir });
    const allowed = Lib.opts.types && Lib.opts.types.length ? Lib.opts.types : null;
    Lib.items = r.files.map((f) => ({ path: f, type: f.split(".").pop().toLowerCase(), local: true }))
      .filter((i) => !allowed || allowed.includes(i.type));
    Lib.total = Lib.items.length;
    renderLibList();
  } catch (e) { $("lib-count").textContent = e.message; }
}

function showLibTab(tab) {
  Lib.tab = tab === "models" && Lib.opts.owner ? "models" : "libs";
  document.querySelectorAll(".lib-tabs .tab").forEach((b) => b.classList.toggle("on", b.dataset.libtab === Lib.tab));
  document.querySelector('.lib-tabs [data-libtab="models"]').hidden = !Lib.opts.owner;
  $("lib-filters").hidden = Lib.tab !== "libs";
  $("lib-models-bar").hidden = Lib.tab !== "models";
  Lib.items = [];
  if (Lib.tab === "models") loadModels(); else searchLibrary(false);
}

async function openLibrary(opts = {}) {
  Lib.opts = opts;
  Lib.selected = null;
  $("lib-modal").hidden = false;
  $("lib-title").textContent = opts.title || t("libTitle");
  $("lib-use").hidden = !opts.onPick;
  $("lib-use").disabled = true;
  $("lib-info").innerHTML = `<p class="hint">${esc(t("libPickHint"))}</p>`;
  if (Lib.viewer) { Lib.viewer.clear(); Lib.viewer.message(""); }
  try {
    if (!Lib.packs.length) {
      $("lib-count").textContent = t("libIndexing");
      await loadLibraryPacks(false);
    }
    renderLibFilters();
    showLibTab(opts.tab || (opts.owner && libStoreGet("tab", "libs") === "models" ? "models" : "libs"));
  } catch (e) {
    $("lib-count").textContent = e.message;
  }
}

function closeLibrary() {
  $("lib-modal").hidden = true;
}

$("open-library").onclick = () => openLibrary({ owner: currentOwner() });
$("lib-close").onclick = closeLibrary;
$("lib-modal").addEventListener("click", (e) => { if (e.target === $("lib-modal")) closeLibrary(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("lib-modal").hidden) closeLibrary(); });
$("lib-q").addEventListener("input", () => { clearTimeout(Lib.timer); Lib.timer = setTimeout(() => searchLibrary(false), 250); });
["lib-pack", "lib-type", "lib-cat", "lib-hidden"].forEach((id) => { $(id).onchange = () => searchLibrary(false); });
$("lib-more").onclick = () => searchLibrary(true);
$("lib-use").onclick = useLibItem;
$("lib-reset-view").onclick = () => Lib.viewer && Lib.viewer.resetView();
$("lib-copy").onclick = async () => {
  if (!Lib.selected) return;
  try { await navigator.clipboard.writeText(Lib.selected.path); say("libCopied", { path: Lib.selected.path }, "ok"); } catch (e) { say(null, { text: Lib.selected.path }); }
};
document.querySelectorAll(".lib-tabs .tab").forEach((b) => {
  b.onclick = () => { store.set("cityscene.lib.tab", b.dataset.libtab); showLibTab(b.dataset.libtab); };
});
$("lib-import").onclick = async () => {
  try {
    const r0 = await api("/api/pick", { body: { kind: "obj", title: t("importModel"), initial: "" } });
    if (!r0.path) return;
    $("lib-count").textContent = t("importing");
    const r = await api("/api/models/import", { body: { owner: Lib.opts.owner, path: r0.path,
      scale: parseFloat($("lib-import-scale").value) || 1, up: $("lib-import-up").value } });
    await loadModels();
    const item = Lib.items.find((i) => i.path === r.path);
    if (item) selectLibItem(item);
    const note = r.converted ? t("importConverted", { path: r.path, tris: r.triangles, mats: r.materials }) : t("importCopied", { path: r.path });
    say(null, { text: note + (r.warnings.length ? " · " + r.warnings.join(" · ") : "") + (r.missing.length ? " · " + t("importMissing", { list: r.missing.join(", ") }) : "") },
      r.warnings.length || r.missing.length ? "warn" : "ok");
  } catch (e) { sayError(e); $("lib-count").textContent = ""; }
};

// The project whose models folder the browser shows: the open city project or airport project
function currentOwner() {
  if (typeof currentMode === "function" && currentMode() === "airport") return Apt && Apt.name ? `apt:${Apt.name}` : null;
  return currentJob ? `job:${currentJob}` : null;
}

// "From library…" buttons next to inputs: data-for (input id), data-types (comma list), data-owner (job / apt)
document.addEventListener("click", (e) => {
  const b = e.target.closest("button.libpick");
  if (!b) return;
  e.preventDefault();
  const input = $(b.dataset.for);
  openLibrary({
    types: (b.dataset.types || "").split(",").filter(Boolean),
    owner: currentOwner(),
    title: t("libPickTitle"),
    onPick: (path) => { input.value = path; input.dispatchEvent(new Event("change", { bubbles: true })); },
  });
});
