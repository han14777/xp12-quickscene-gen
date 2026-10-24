// 3D preview of X-Plane assets (three.js). Takes the JSON from /api/asset (quickscene/assets.py) and builds:
// OBJ8 meshes (first LOD, textures, lights as points), type 1 facades as a box cut from their wall segments, forests
// as a few trees (3D meshes or crossed billboards), draped polygons and lines as textured ground, object strings
// and AGPs from their child objects. Coordinates are OBJ8's: +X east, +Y up, +Z south (same handedness as three.js).
"use strict";

class AssetViewer {
  constructor(container) {
    this.container = container;
    this.ok = typeof THREE !== "undefined";
    if (!this.ok) return;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    this.renderer.outputEncoding = THREE.sRGBEncoding;
    container.appendChild(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(45, 1, 0.05, 20000);
    this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8f98, 0.85));
    const sun = new THREE.DirectionalLight(0xffffff, 0.75);
    sun.position.set(0.6, 1, 0.4);
    this.scene.add(sun);
    this.grid = new THREE.GridHelper(100, 20, 0x8a8f98, 0xc8c8c8);
    this.grid.material.transparent = true;
    this.grid.material.opacity = 0.35;
    this.scene.add(this.grid);
    this.group = new THREE.Group();
    this.scene.add(this.group);
    this.loader = new THREE.TextureLoader();
    this.textures = new Map();
    this.token = 0;
    new ResizeObserver(() => this.resize()).observe(container);
    this.resize();
    const loop = () => {
      requestAnimationFrame(loop);
      if (!this.container.offsetParent) return;
      this.controls.update();
      this.renderer.render(this.scene, this.camera);
    };
    loop();
  }

  resize() {
    if (!this.ok) return;
    const w = this.container.clientWidth || 300, h = this.container.clientHeight || 240;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  texture(token, repeat) {
    if (!token) return null;
    const key = token + (repeat ? "|r" : "");
    if (!this.textures.has(key)) {
      const tex = this.loader.load("/api/texture?t=" + encodeURIComponent(token));
      tex.encoding = THREE.sRGBEncoding;
      tex.anisotropy = 4;
      if (repeat) tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
      this.textures.set(key, tex);
    }
    return this.textures.get(key);
  }

  material(token, opts = {}) {
    return new THREE.MeshStandardMaterial({
      map: this.texture(token, opts.repeat), color: token ? 0xffffff : 0xbfc4ca, side: THREE.DoubleSide,
      alphaTest: 0.35, transparent: false, roughness: 0.85, metalness: 0.0,
      polygonOffset: Boolean(opts.ground), polygonOffsetFactor: opts.ground ? -1 : 0,
    });
  }

  clear() {
    for (const child of [...this.group.children]) {
      this.group.remove(child);
      child.traverse?.((o) => { o.geometry?.dispose?.(); });
    }
  }

  message(text) {
    const el = document.getElementById("viewer-msg");
    if (el) { el.textContent = text || ""; el.hidden = !text; }
  }

  async show(asset) {
    if (!this.ok) { this.message("3D preview needs WebGL / three.js"); return; }
    const token = ++this.token;
    this.clear();
    this.message("");
    try {
      const obj = await this.build(asset, token);
      if (token !== this.token) return;
      if (obj) this.group.add(obj);
      this.frame();
    } catch (e) {
      if (token === this.token) this.message(e.message);
    }
  }

  frame() {
    const box = new THREE.Box3().setFromObject(this.group);
    if (box.isEmpty()) return;
    const size = box.getSize(new THREE.Vector3());
    const centre = box.getCenter(new THREE.Vector3());
    const r = Math.max(size.x, size.y, size.z, 1);
    this.controls.target.copy(centre);
    this.camera.position.set(centre.x + r * 1.1, centre.y + r * 0.8, centre.z + r * 1.4);
    this.camera.near = r / 500;
    this.camera.far = r * 100;
    this.camera.updateProjectionMatrix();
    const g = Math.pow(10, Math.ceil(Math.log10(r * 2)));
    this.grid.scale.setScalar(g / 100);
  }

  resetView() { this.frame(); }

  async build(a, token) {
    switch (a.type) {
      case "obj": return this.buildObj(a);
      case "fac": return this.buildFac(a);
      case "for": return this.buildFor(a);
      case "pol": return this.buildPol(a);
      case "lin": return this.buildLin(a);
      case "str": return this.buildStr(a, token);
      case "agp": return this.buildAgp(a, token);
      default: throw new Error("No preview for this type");
    }
  }

  buildObj(a) {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(a.positions, 3));
    geo.setAttribute("normal", new THREE.Float32BufferAttribute(a.normals, 3));
    geo.setAttribute("uv", new THREE.Float32BufferAttribute(a.uvs, 2));
    const index = [];
    const mats = [this.material(a.textures.main), this.material(a.textures.draped || a.textures.main, { ground: true })];
    for (const g of a.groups) {
      const start = index.length;
      for (let i = g.start; i < g.start + g.count; i++) index.push(a.indices[i]);
      geo.addGroup(start, g.count, g.texture === "draped" ? 1 : 0);
    }
    geo.setIndex(index);
    const out = new THREE.Group();
    out.add(new THREE.Mesh(geo, mats));
    if (a.lights && a.lights.length) {
      const lg = new THREE.BufferGeometry();
      lg.setAttribute("position", new THREE.Float32BufferAttribute(a.lights.flat(), 3));
      out.add(new THREE.Points(lg, new THREE.PointsMaterial({ color: 0xffd34d, size: 0.35 })));
    }
    if (!a.groups.length) this.message("No triangles in the first level of detail");
    return out;
  }

  // ----- facades -----
  static spans(list, total, sizePx, scale) {
    // segments [a, b] in texture units -> [[metres, u0, u1]]
    return list.map(([s0, s1]) => [Math.abs(s1 - s0) / sizePx * scale, s0 / sizePx, s1 / sizePx]).filter((s) => s[0] > 0.01);
  }

  static fill(head, mid, tail, length) {
    // head segments, then mid repeated, then tail, fitting length (metres)
    const out = [...head];
    const sum = (l) => l.reduce((t, s) => t + s[0], 0);
    let used = sum(head);
    const tailLen = sum(tail);
    if (mid.length && sum(mid) > 0.05) {
      let i = 0;
      while (used + mid[i % mid.length][0] + tailLen <= length + 0.01 && out.length < 400) {
        out.push(mid[i % mid.length]);
        used += mid[i % mid.length][0];
        i++;
      }
    }
    return out.concat(tail);
  }

  buildFac(a) {
    const out = new THREE.Group();
    const W = 24, D = 16;
    const floors = Math.max(a.floors_min || 0, 4);
    const corners = [[-W / 2, D / 2], [W / 2, D / 2], [W / 2, -D / 2], [-W / 2, -D / 2]];
    if (a.facade_type === 1 && a.walls.length) {
      const mat = this.material(a.textures.wall);
      const pos = [], uv = [], nor = [];
      let height = 0;
      for (let k = 0; k < 4; k++) {
        const w = a.walls[k % a.walls.length];
        const [tw, th] = w.tex_size;
        const [sx, sy] = w.scale;
        const [x0, z0] = corners[k], [x1, z1] = corners[(k + 1) % 4];
        const len = Math.hypot(x1 - x0, z1 - z0);
        const hs = AssetViewer.fill(AssetViewer.spans(w.left, 0, tw, sx), AssetViewer.spans(w.center, 0, tw, sx), AssetViewer.spans(w.right, 0, tw, sx), len);
        const mid = AssetViewer.spans(w.middle, 0, th, sy);
        const floorH = mid.length ? mid.reduce((t, s) => t + s[0], 0) / mid.length : 3.2;
        const vs = AssetViewer.fill(AssetViewer.spans(w.bottom, 0, th, sy), mid, AssetViewer.spans(w.top, 0, th, sy), floors * floorH);
        const hTotal = hs.reduce((t, s) => t + s[0], 0) || len;
        const nx = (z1 - z0) / len, nz = -(x1 - x0) / len;
        let along = 0;
        for (const [hw, u0, u1] of hs) {
          const f0 = along / hTotal, f1 = (along + hw) / hTotal;
          along += hw;
          const ax = x0 + (x1 - x0) * f0, az = z0 + (z1 - z0) * f0, bx = x0 + (x1 - x0) * f1, bz = z0 + (z1 - z0) * f1;
          let up = 0;
          for (const [vh, v0, v1] of vs) {
            pos.push(ax, up, az, bx, up, bz, bx, up + vh, bz, ax, up, az, bx, up + vh, bz, ax, up + vh, az);
            uv.push(u0, v0, u1, v0, u1, v1, u0, v0, u1, v1, u0, v1);
            for (let i = 0; i < 6; i++) nor.push(nx, 0, nz);
            up += vh;
          }
          height = Math.max(height, up);
        }
      }
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
      geo.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
      geo.setAttribute("normal", new THREE.Float32BufferAttribute(nor, 3));
      out.add(new THREE.Mesh(geo, mat));
      const roof = new THREE.Mesh(new THREE.PlaneGeometry(W, D), new THREE.MeshStandardMaterial({ color: 0x9a9da2, side: THREE.DoubleSide }));
      roof.rotation.x = -Math.PI / 2;
      roof.position.y = height;
      out.add(roof);
    } else {
      const h = a.roof_height || floors * 3.2;
      const box = new THREE.Mesh(new THREE.BoxGeometry(W, h, D), [
        this.material(a.textures.wall), this.material(a.textures.wall), this.material(a.textures.roof),
        this.material(a.textures.roof), this.material(a.textures.wall), this.material(a.textures.wall)]);
      box.position.y = h / 2;
      out.add(box);
      this.message(t("viewerFacade2"));
    }
    return out;
  }

  // ----- forests -----
  buildFor(a) {
    const out = new THREE.Group();
    const trees = a.trees.length ? a.trees : [];
    const spacing = Math.max(4, (a.spacing && a.spacing[0]) || 10);
    const n = 3;
    let k = 0;
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        const tree = trees[k++ % Math.max(1, trees.length)];
        if (!tree) break;
        const h = (tree.min_h + tree.max_h) / 2 || 10;
        const x = (i - 1) * spacing + (Math.sin(k * 12.9) * spacing) / 4;
        const z = (j - 1) * spacing + (Math.cos(k * 78.2) * spacing) / 4;
        const mesh = tree.meshes.map((m) => a.meshes[m]).find(Boolean);
        if (mesh && a.textures.mesh) {
          const geo = new THREE.BufferGeometry();
          geo.setAttribute("position", new THREE.Float32BufferAttribute(mesh.positions, 3));
          geo.setAttribute("normal", new THREE.Float32BufferAttribute(mesh.normals, 3));
          geo.setAttribute("uv", new THREE.Float32BufferAttribute(mesh.uvs, 2));
          geo.setIndex(mesh.indices);
          const m3 = new THREE.Mesh(geo, this.material(a.textures.mesh));
          geo.computeBoundingBox();
          const top = geo.boundingBox.max.y || 1;
          m3.scale.setScalar(h / top);
          m3.position.set(x, 0, z);
          out.add(m3);
        } else {
          const [sx, sy] = a.scale;
          const w = h * (tree.w / Math.max(1, tree.h));
          const u0 = tree.s / sx, u1 = (tree.s + tree.w) / sx, v0 = tree.t / sy, v1 = (tree.t + tree.h) / sy;
          for (const rot of [0, Math.PI / 2]) {
            const geo = new THREE.PlaneGeometry(w, h);
            geo.setAttribute("uv", new THREE.Float32BufferAttribute([u0, v1, u1, v1, u0, v0, u1, v0], 2));
            const q = new THREE.Mesh(geo, this.material(a.textures.billboard));
            q.position.set(x, h / 2, z);
            q.rotation.y = rot;
            out.add(q);
          }
        }
      }
    }
    if (!trees.length) this.message(t("viewerNoTrees"));
    return out;
  }

  buildPol(a) {
    const [sx, sy] = a.scale;
    const size = Math.max(sx, sy, 10) * 2;
    const geo = new THREE.PlaneGeometry(size, size);
    const reps = a.nowrap ? [1, 1] : [size / Math.max(sx, 0.1), size / Math.max(sy, 0.1)];
    geo.setAttribute("uv", new THREE.Float32BufferAttribute([0, reps[1], reps[0], reps[1], 0, 0, reps[0], 0], 2));
    const m = new THREE.Mesh(geo, this.material(a.texture, { repeat: !a.nowrap, ground: true }));
    m.rotation.x = -Math.PI / 2;
    return m;
  }

  buildLin(a) {
    const [tw] = a.tex_size;
    const layer = a.layers[0] || [0, 0, tw / 2, tw];
    const [, left, , right] = layer;
    const width = Math.max(0.2, Math.abs(right - left) / tw * a.scale[0]);
    const length = Math.max(20, width * 8);
    const geo = new THREE.PlaneGeometry(width, length);
    const reps = length / Math.max(0.5, a.scale[1]);
    const u0 = left / tw, u1 = right / tw;
    geo.setAttribute("uv", new THREE.Float32BufferAttribute([u0, reps, u1, reps, u0, 0, u1, 0], 2));
    const m = new THREE.Mesh(geo, this.material(a.texture, { repeat: true, ground: true }));
    m.rotation.x = -Math.PI / 2;
    return m;
  }

  async child(ref) {
    if (!ref) return null;
    const data = await api("/api/asset?ref=" + encodeURIComponent(ref));
    return data.type === "obj" ? this.buildObj(data) : null;
  }

  async buildStr(a, token) {
    const out = new THREE.Group();
    const obj = await this.child(a.children[0]?.ref);
    if (!obj || token !== this.token) return out;
    const step = a.offset || 5;
    for (let i = 0; i < 6; i++) {
      const c = obj.clone();
      c.position.x = (i - 2.5) * step;
      out.add(c);
    }
    return out;
  }

  async buildAgp(a, token) {
    const out = new THREE.Group();
    if (a.tile && a.texture) {
      const [x0, z0, x1, z1] = a.tile;
      const geo = new THREE.PlaneGeometry(Math.abs(x1 - x0), Math.abs(z1 - z0));
      const [u0, v0, u1, v1] = a.tile_uv;
      geo.setAttribute("uv", new THREE.Float32BufferAttribute([u0, v1, u1, v1, u0, v0, u1, v0], 2));
      const g = new THREE.Mesh(geo, this.material(a.texture, { ground: true }));
      g.rotation.x = -Math.PI / 2;
      g.position.set((x0 + x1) / 2, 0.01, (z0 + z1) / 2);
      out.add(g);
    }
    const cache = new Map();
    for (const p of a.placements.slice(0, 200)) {
      const ref = a.children[p.object]?.ref;
      if (!cache.has(ref)) cache.set(ref, await this.child(ref).catch(() => null));
      if (token !== this.token) return out;
      const obj = cache.get(ref);
      if (!obj) continue;
      const c = obj.clone();
      c.position.set(p.x, 0, p.z);
      c.rotation.y = -p.heading * Math.PI / 180;
      out.add(c);
    }
    return out;
  }
}
