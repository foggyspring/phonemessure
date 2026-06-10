import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { STLLoader } from "three/addons/loaders/STLLoader.js";

// ───────────────────────── state ─────────────────────────
const state = {
  file: null,
  fileBytes: null,
  geometry: null,
  shop: null,
  lastPayload: null,
  mesh: null,
  bbox: null,
  hasQuoted: false,
  lastPrice: 0,
  token: null,
  user: null,
  leadTime: null,   // selected lead-time tier key (null → standard default)
  fx: { rate: 1, symbol: "¥", currency: "CNY" },
  compare: false,
};

const $ = (id) => document.getElementById(id);
// Escape user-controlled strings (filenames, part names) before innerHTML.
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Shared fetch wrapper. Resolves to { res, detail, authFailed }:
//  - detail: error message from the JSON body's `detail` (or statusText) when !res.ok
//  - authFailed: true when an authed call got 401/403 — the session is dropped
//    (logout + login modal) and the caller should simply return.
// Note: when !res.ok the body has already been consumed; use `detail`, not res.json().
// Network errors reject like fetch() — callers keep their own try/catch.
async function apiFetch(url, options, { authed = false } = {}) {
  const res = await fetch(url, options);
  if (authed && (res.status === 401 || res.status === 403)) {
    logout(); openLogin();
    return { res, authFailed: true };
  }
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))).detail || res.statusText;
    return { res, detail };
  }
  return { res };
}

// Material → render look (color + metalness/roughness) for a believable preview.
const MAT_LOOK = {
  AL: { color: 0xc7ced6, metal: 0.7, rough: 0.35 },
  SUS: { color: 0x9aa3ad, metal: 0.85, rough: 0.3 },
  BRASS: { color: 0xd9b25b, metal: 0.9, rough: 0.3 },
  COPPER: { color: 0xc9763f, metal: 0.9, rough: 0.32 },
  TITANIUM: { color: 0x8f9499, metal: 0.8, rough: 0.4 },
  POM: { color: 0xe9edf1, metal: 0.0, rough: 0.75 },
  ABS: { color: 0x2c2f33, metal: 0.0, rough: 0.85 },
  PA: { color: 0xe6e2d3, metal: 0.0, rough: 0.8 },
};
function lookForMaterial(key) {
  if (!key) return { color: 0xb6c2cf, metal: 0.65, rough: 0.4 };
  if (key.startsWith("AL")) return MAT_LOOK.AL;
  if (key.startsWith("SUS")) return MAT_LOOK.SUS;
  if (key.startsWith("BRASS")) return MAT_LOOK.BRASS;
  if (key.startsWith("COPPER")) return { color: 0xc9763f, metal: 0.9, rough: 0.32 };
  if (key.startsWith("TITAN")) return MAT_LOOK.TITANIUM;
  if (key === "POM") return MAT_LOOK.POM;
  if (key === "ABS") return MAT_LOOK.ABS;
  if (key.startsWith("PA")) return MAT_LOOK.PA;
  return { color: 0xb6c2cf, metal: 0.65, rough: 0.4 };
}

// ───────────────────────── 3D viewer ─────────────────────────
let scene, camera, renderer, controls;
// Render-on-demand: the rAF loop keeps ticking but only calls renderer.render()
// when something changed (controls interaction/damping/autoRotate, or an
// explicit invalidate() after scene mutations such as mesh load or recolor).
let needsRender = true;
function invalidate() { needsRender = true; }

function initViewer() {
  const host = $("viewer");
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0c1015);

  camera = new THREE.PerspectiveCamera(45, 1, 0.1, 200000);
  camera.position.set(120, 90, 160);

  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  host.appendChild(renderer.domElement);

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.autoRotateSpeed = 1.6;
  controls.addEventListener("change", invalidate);

  scene.add(new THREE.HemisphereLight(0xffffff, 0x1a222c, 1.0));
  const key = new THREE.DirectionalLight(0xffffff, 1.15);
  key.position.set(1, 1.4, 1);
  scene.add(key);
  const fill = new THREE.DirectionalLight(0x88aaff, 0.45);
  fill.position.set(-1, -0.4, -1);
  scene.add(fill);
  const rim = new THREE.DirectionalLight(0xffffff, 0.5);
  rim.position.set(0, 1, -1.5);
  scene.add(rim);

  const grid = new THREE.GridHelper(400, 40, 0x2f3a45, 0x1c242c);
  grid.name = "grid";
  scene.add(grid);

  resize();
  window.addEventListener("resize", resize);
  animate();
}

function resize() {
  const host = $("viewer");
  const w = host.clientWidth || 600;
  const h = host.clientHeight || 460;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  invalidate();
}

function animate() {
  requestAnimationFrame(animate);
  // update() returns true while the camera moves (user input, damping, autoRotate)
  if (controls.update()) needsRender = true;
  if (!needsRender) return;
  needsRender = false;
  renderer.render(scene, camera);
}

function disposeMaterial(m) {
  if (!m) return;
  if (Array.isArray(m)) { m.forEach(disposeMaterial); return; }
  m.map?.dispose?.();   // e.g. the CanvasTexture behind makeLabel() sprites
  m.dispose?.();
}

function disposeObj(o) {
  if (!o) return;
  scene.remove(o);
  o.traverse?.((c) => { c.geometry?.dispose?.(); disposeMaterial(c.material); });
  o.geometry?.dispose?.();
  disposeMaterial(o.material);
}

function makeLabel(text) {
  const cv = document.createElement("canvas");
  cv.width = 256; cv.height = 64;
  const ctx = cv.getContext("2d");
  ctx.fillStyle = "rgba(12,16,21,0.85)";
  ctx.fillRect(0, 0, cv.width, cv.height);
  ctx.strokeStyle = "#2f81f7"; ctx.lineWidth = 3; ctx.strokeRect(2, 2, cv.width - 4, cv.height - 4);
  ctx.fillStyle = "#e6edf3"; ctx.font = "bold 30px sans-serif";
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText(text, cv.width / 2, cv.height / 2);
  const tex = new THREE.CanvasTexture(cv);
  const spr = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
  spr.scale.set(0.18, 0.045, 1);
  return spr;
}

function buildBBox(dims) {
  const [x, y, z] = dims;
  const g = new THREE.Group();
  const box = new THREE.BoxGeometry(x, y, z);
  const edges = new THREE.LineSegments(
    new THREE.EdgesGeometry(box),
    new THREE.LineBasicMaterial({ color: 0x2f81f7, transparent: true, opacity: 0.7 })
  );
  g.add(edges);
  box.dispose();
  // Dimension labels scaled to the model.
  const s = Math.max(x, y, z);
  const lbls = [
    [`L ${x.toFixed(1)}`, [0, -y / 2 - s * 0.06, z / 2]],
    [`W ${y.toFixed(1)}`, [x / 2, -y / 2 - s * 0.06, 0]],
    [`H ${z.toFixed(1)}`, [x / 2 + s * 0.06, 0, z / 2]],
  ];
  for (const [t, p] of lbls) {
    const sp = makeLabel(t);
    sp.scale.set(s * 0.42, s * 0.105, 1);
    sp.position.set(p[0], p[1], p[2]);
    g.add(sp);
  }
  return g;
}

function frameDims(dims) {
  const maxDim = Math.max(...dims) || 50;
  const dist = maxDim * 2.4;
  camera.position.set(dist * 0.75, dist * 0.55, dist);
  camera.near = maxDim / 200;
  camera.far = maxDim * 200;
  camera.updateProjectionMatrix();
  controls.target.set(0, 0, 0);
  controls.update();
  const grid = scene.getObjectByName("grid");
  if (grid) { grid.position.y = -dims[1] / 2 - maxDim * 0.02; grid.scale.setScalar(Math.max(1, maxDim / 200)); }
  invalidate();
}

function renderGeometry(dims, meshGeometry, materialKey) {
  disposeObj(state.mesh); state.mesh = null;
  disposeObj(state.bbox); state.bbox = null;

  if (meshGeometry) {
    meshGeometry.center();
    meshGeometry.computeVertexNormals();
    const look = lookForMaterial(materialKey);
    const mesh = new THREE.Mesh(
      meshGeometry,
      new THREE.MeshStandardMaterial({ color: look.color, metalness: look.metal, roughness: look.rough })
    );
    mesh.add(new THREE.LineSegments(
      new THREE.EdgesGeometry(meshGeometry, 35),
      new THREE.LineBasicMaterial({ color: 0x0d1117, transparent: true, opacity: 0.25 })
    ));
    scene.add(mesh);
    state.mesh = mesh;
  }

  state.bbox = buildBBox(dims);
  state.bbox.visible = $("toggle-bbox").classList.contains("active");
  scene.add(state.bbox);

  frameDims(dims);
  invalidate();
  $("drop").classList.add("loaded");
  $("viewer-toolbar").hidden = false;
}

function recolorMesh(materialKey) {
  if (!state.mesh) return;
  const look = lookForMaterial(materialKey);
  state.mesh.material.color.setHex(look.color);
  state.mesh.material.metalness = look.metal;
  state.mesh.material.roughness = look.rough;
  invalidate();
}

// ───────────────────────── upload flow ─────────────────────────
function b64ToArrayBuffer(b64) {
  const bin = atob(b64), len = bin.length, bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
}

function handleFiles(fileList) {
  const files = [...(fileList || [])];
  if (!files.length) return;
  if (files.length === 1) { handleFile(files[0]); return; }
  batchQuote(files);
}

async function batchQuote(files) {
  state.batchFiles = Object.fromEntries(files.map((f) => [f.name, f]));
  const card = $("batch-card");
  card.classList.remove("hidden");
  $("batch-agg").textContent = `批量报价中（${files.length} 个文件）…`;
  $("batch-table").innerHTML = "";
  try {
    const fd = new FormData();
    fd.append("params", JSON.stringify(buildParams(false)));
    files.forEach((f) => fd.append("files", f));
    const res = await fetch("/api/quote/batch", { method: "POST", body: fd });
    const d = await res.json();
    if (!res.ok) { $("batch-agg").textContent = "批量报价失败：" + (d.detail || res.statusText); return; }
    renderBatch(d);
    card.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (e) {
    $("batch-agg").textContent = "网络错误：" + e.message;
  }
}

const RISK_LABEL = { high: "高", medium: "中", low: "低", ok: "无" };

function renderBatch(d) {
  const a = d.aggregate;
  const topup = a.min_order_topup_cny > 0 ? `（含起订补差 ¥${fmtNum(a.min_order_topup_cny)}）` : "";
  $("batch-agg").innerHTML =
    `${a.n_ok}/${a.n_parts} 件成功 · 净额 <b>¥${fmtNum(a.net_total_cny)}</b>${topup}` +
    ` · 含税 <b>¥${fmtNum(a.total_incl_tax_cny)}</b> · 最长交期 ${a.lead_days} 天` +
    ` · 总重 ${a.order_weight_kg} kg · 预估运费 ¥${fmtNum(a.shipping_cny)}${a.crated ? "（含木箱）" : ""}` +
    (a.risk_counts.high + a.risk_counts.medium > 0
      ? ` · <span class="warn-mark">风险 高${a.risk_counts.high}/中${a.risk_counts.medium}，建议复核：${a.needs_review.map(esc).join("、")}</span>`
      : ` · 无明显工艺风险`);
  let rows = `<tr><th>零件</th><th>单价</th><th>数量</th><th>小计</th><th>交期</th><th>风险</th><th>置信</th></tr>`;
  for (const x of d.parts) {
    rows += x.ok
      ? `<tr class="batch-row" data-file="${esc(x.file)}"><td>${esc(x.file)}</td>` +
        `<td>¥${fmtNum(x.unit_price_cny)}</td><td>${x.quantity}</td><td>¥${fmtNum(x.line_net_cny)}</td>` +
        `<td>${x.lead_days} 天</td><td>${RISK_LABEL[x.risk] || x.risk}</td><td>${x.confidence}</td></tr>`
      : `<tr><td>${esc(x.file)}</td><td colspan="6" class="muted">失败：${esc(x.error)}</td></tr>`;
  }
  $("batch-table").innerHTML = rows;
  $("batch-table").querySelectorAll(".batch-row").forEach((tr) =>
    tr.addEventListener("click", () => {
      const f = (state.batchFiles || {})[tr.dataset.file];
      if (f) handleFile(f);            // load the part into the single-part flow
    }));
}

async function handleFile(file) {
  if (!file) return;
  state.file = file;
  state.fileBytes = await file.arrayBuffer();
  $("viewer-spinner").classList.remove("hidden");
  $("viewer-meta").innerHTML = `解析中 <b>${esc(file.name)}</b> …`;

  let r;
  try {
    const fd = new FormData();
    fd.append("file", file);
    r = await apiFetch("/api/parse", { method: "POST", body: fd });
  } catch (e) {
    $("viewer-spinner").classList.add("hidden");
    toast("网络错误：" + e.message, "err");
    state.fileBytes = null;
    return;
  }
  $("viewer-spinner").classList.add("hidden");

  if (!r.res.ok) {
    toast(r.detail, "err", 6000);
    $("manual-box").classList.remove("hidden");
    state.geometry = null;
    state.fileBytes = null;
    refreshQuoteEnabled();
    return;
  }
  const data = await r.res.json();
  state.geometry = data.geometry;
  $("manual-box").classList.add("hidden");

  const ext = file.name.toLowerCase().split(".").pop();
  const loader = new STLLoader();
  let geom = null;
  try {
    if (ext === "stl") geom = loader.parse(state.fileBytes);
    else if (data.preview_stl_b64) geom = loader.parse(b64ToArrayBuffer(data.preview_stl_b64));
  } catch { geom = null; }
  state.fileBytes = null;   // free the upload buffer (up to 60 MB); only loader.parse needed it
  renderGeometry(data.geometry.dims_mm, geom, $("material").value);

  const g = data.geometry;
  $("viewer-meta").innerHTML =
    `<b>${esc(file.name)}</b> · ${data.source_format.toUpperCase()}` +
    `<br><span class="chip">外形 ${g.dims_mm.map((v) => v.toFixed(1)).join(" × ")} mm</span>` +
    `<span class="chip">体积 ${g.volume_cm3} cm³</span>` +
    `<span class="chip">表面积 ${g.area_cm2} cm²</span>` +
    `<span class="chip">复杂度 ${(g.complexity * 100).toFixed(0)}%</span>`;
  refreshQuoteEnabled();
  toast("解析完成，可以生成报价了", "ok");
  if (state.hasQuoted) scheduleLiveQuote();
}

// ───────────────────────── materials / form ─────────────────────────
async function loadShop() {
  const prevMat = $("material").value, prevFin = $("finish").value, prevMach = $("machine").value;
  const res = await fetch("/api/materials");
  state.shop = await res.json();

  const matSel = $("material");
  matSel.innerHTML = "";
  for (const [k, m] of Object.entries(state.shop.materials)) {
    const o = document.createElement("option");
    o.value = k; o.textContent = m.label;
    matSel.appendChild(o);
  }
  if (prevMat && state.shop.materials[prevMat]) matSel.value = prevMat;

  const machSel = $("machine");
  machSel.innerHTML = '<option value="">自动 Auto</option>';
  for (const [k, mc] of Object.entries(state.shop.machines)) {
    const o = document.createElement("option");
    o.value = k; o.textContent = `${mc.label} (¥${mc.rate_cny_per_hour}/h)`;
    machSel.appendChild(o);
  }
  if (prevMach) machSel.value = prevMach;

  if (!matSel._wired) {
    matSel.addEventListener("change", () => { refreshFinishes(); updateMatPrice(); recolorMesh(matSel.value); });
    matSel._wired = true;
  }
  refreshFinishes(prevFin);
  updateMatPrice();
}

function refreshFinishes(prefer) {
  const mat = state.shop.materials[$("material").value];
  const finSel = $("finish");
  const prev = prefer || finSel.value;
  finSel.innerHTML = "";
  for (const key of mat.finish_ok) {
    const o = document.createElement("option");
    o.value = key; o.textContent = state.shop.finishes[key]?.label || key;
    finSel.appendChild(o);
  }
  if ([...finSel.options].some((o) => o.value === prev)) finSel.value = prev;
}

function updateMatPrice() {
  const m = state.shop?.materials[$("material").value];
  if (!m) { $("mat-price").textContent = ""; return; }
  const live = m.price_source && m.price_source !== "static";
  const src = m.price_source === "manual override" ? "手动改价"
    : live ? "实时 " + m.price_source : "静态参考价";
  $("mat-price").textContent = `料价 ¥${m.price_cny_per_kg}/kg · 密度 ${m.density_g_cm3} g/cm³ · ${src}`;
}

// ───────────────────────── holes table ─────────────────────────
function addHoleRow(d = 6, depth = 10, count = 1, threaded = false) {
  const tb = $("holes-table").querySelector("tbody");
  const tr = document.createElement("tr");
  tr.innerHTML =
    `<td><input type="number" class="h-d" min="0" step="0.1" value="${d}"></td>` +
    `<td><input type="number" class="h-depth" min="0" step="0.1" value="${depth}"></td>` +
    `<td><input type="number" class="h-count" min="1" step="1" value="${count}"></td>` +
    `<td style="text-align:center"><input type="checkbox" class="h-thread" ${threaded ? "checked" : ""}></td>` +
    `<td><button class="row-del" title="删除">×</button></td>`;
  tr.querySelector(".row-del").addEventListener("click", () => { tr.remove(); scheduleLiveQuote(); });
  tr.querySelectorAll("input").forEach((i) => i.addEventListener("input", scheduleLiveQuote));
  tb.appendChild(tr);
  scheduleLiveQuote();
}

function collectHoles() {
  return [...$("holes-table").querySelectorAll("tbody tr")].map((tr) => ({
    diameter_mm: parseFloat(tr.querySelector(".h-d").value) || 0,
    depth_mm: parseFloat(tr.querySelector(".h-depth").value) || 0,
    count: parseInt(tr.querySelector(".h-count").value) || 1,
    threaded: tr.querySelector(".h-thread").checked,
  })).filter((h) => h.diameter_mm > 0);
}

// ───────────────────────── quote ─────────────────────────
function manualDims() {
  const l = parseFloat($("m-l").value), w = parseFloat($("m-w").value),
        h = parseFloat($("m-h").value), v = parseFloat($("m-v").value);
  if (l > 0 && w > 0 && h > 0)
    return { length_mm: l, width_mm: w, height_mm: h, volume_mm3: v > 0 ? v : null };
  return null;
}

function refreshQuoteEnabled() {
  $("quote-btn").disabled = !(state.geometry !== null || manualDims() !== null);
}

function buildParams(save) {
  const p = {
    part_name: state.file ? state.file.name.replace(/\.[^.]+$/, "") : "part",
    material: $("material").value,
    quantity: parseInt($("quantity").value) || 1,
    finish: $("finish").value,
    machine: $("machine").value || null,
    tolerance: $("tolerance").value,
    surface_finish: $("surface_finish").value,
    currency: $("currency").value,
    compare: state.compare || false,
    addons: [...document.querySelectorAll("#addons input:checked")].map((c) => c.dataset.addon),
    requires_5axis: $("fiveaxis").checked,
    lead_time: state.leadTime,
    backend: $("backend").value,
    units: $("units").value,
    customer: $("customer").value.trim(),
    holes: collectHoles(),
    save,
  };
  const mw = parseFloat($("minwall").value);
  if (mw > 0) p.min_wall_mm = mw;
  if (!state.file) { const md = manualDims(); if (md) p.manual_dims = md; }
  return p;
}

let liveTimer = null;
function scheduleLiveQuote() {
  if (!state.hasQuoted) return;
  if (!(state.geometry || manualDims())) return;
  clearTimeout(liveTimer);
  liveTimer = setTimeout(() => requestQuote(false), 420);
}

let quoteSeq = 0;   // discard out-of-order responses: a slow earlier quote must
                    // not overwrite a newer one (stale price + stale PDF export)
async function requestQuote(save) {
  if (!(state.geometry || manualDims())) return;
  const seq = ++quoteSeq;
  const btn = $("quote-btn");
  if (save) btn.classList.add("loading");
  try {
    const fd = new FormData();
    fd.append("params", JSON.stringify(buildParams(save)));
    if (state.file) fd.append("file", state.file);
    const { res, detail } = await apiFetch("/api/quote", { method: "POST", body: fd });
    if (seq !== quoteSeq) return;          // a newer request superseded this one
    if (!res.ok) {
      toast("报价失败：" + detail, "err", 6000);
      return;
    }
    const payload = await res.json();
    if (seq !== quoteSeq) return;
    state.lastPayload = payload;
    state.hasQuoted = true;
    renderResult(state.lastPayload, !save);
    $("live-hint").hidden = false;
    if (save) { loadHistory(); toast("报价已生成并存入历史 · " + (state.lastPayload.id || ""), "ok"); }
  } catch (e) {
    toast("网络错误：" + e.message, "err");
  } finally {
    btn.classList.remove("loading");
    refreshQuoteEnabled();
  }
}

// ───────────────────────── render result ─────────────────────────
const fmtNum = (v) => Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const money = (v, cur = "CNY") => {
  const fx = state.fx || { rate: 1, symbol: "¥" };
  return fx.symbol + fmtNum(Number(v) * fx.rate);
};
// Stored amounts are always CNY; history rows must not be converted by whatever
// display FX the last rendered quote happened to set.
const moneyCNY = (v) => "¥" + fmtNum(Number(v));
function kv(rows) { return rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join(""); }

function countUp(el, from, to, cur) {
  const t0 = performance.now(), dur = 480;
  function step(t) {
    const k = Math.min(1, (t - t0) / dur);
    const e = 1 - Math.pow(1 - k, 3);
    el.textContent = money(from + (to - from) * e, cur);
    if (k < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

function renderResult(p, isLive) {
  state.fx = p.fx || { rate: 1, symbol: "¥", currency: "CNY" };
  const cur = p.quote.currency;
  const g = p.geometry, pl = p.plan, q = p.quote, r = q.requested;

  $("geo-table").innerHTML = kv([
    ["外形 Bounding box", g.dims_mm.map((v) => v.toFixed(1)).join(" × ") + " mm"],
    ["体积 Volume", g.volume_cm3 + " cm³"],
    ["表面积 Surface", g.area_cm2 + " cm²"],
    ["毛坯填充 Fill", g.bbox_fill_pct != null ? g.bbox_fill_pct + " %" : "—"],
    ["重量 Weight", g.part_weight_g != null ? (g.part_weight_g / 1000).toFixed(3) + " kg" : "—"],
    ["复杂度 Complexity", (g.complexity * 100).toFixed(0) + " %"],
    ...(g.min_wall_mm != null ? [["最小壁厚 Min wall",
        `${g.min_wall_mm.toFixed(2)} mm${g.min_wall_auto ? ' <span class="muted tiny">(自动检测)</span>' : ""}`]] : []),
    ...(g.holes_auto && g.holes_detected && g.holes_detected.length ? [["识别孔 Holes",
        g.holes_detected.map((h) => `${h.count}×Ø${h.diameter_mm}${h.through ? "通" : "盲"}`).join("、") +
        ' <span class="muted tiny">(自动识别)</span>']] : []),
    ["三角面 Triangles", g.triangles || "—"],
  ]);

  $("plan-table").innerHTML = kv([
    ["机床 Machine", pl.machine_label],
    ["毛坯 Stock", [pl.stock.length_mm, pl.stock.width_mm, pl.stock.height_mm].map((v) => v.toFixed(1)).join(" × ") + " mm"],
    ["去除体积 Removed", pl.removed_volume_cm3 + " cm³"],
    ["有效 MRR", pl.effective_mrr_cm3_min + " cm³/min"],
    ["装夹 / 刀具", `${pl.setups} setups · ${pl.tools} tools`],
    ["开粗 Roughing", pl.times.roughing_min.toFixed(1) + " min"],
    ["精加工 Finishing", pl.times.finishing_min.toFixed(1) + " min"],
    ["钻孔 Drilling", pl.times.drilling_min.toFixed(1) + " min"],
    ["攻丝 Tapping", pl.times.tapping_min.toFixed(1) + " min"],
    ["单件机时 Cycle", "<b>" + pl.times.per_part_min.toFixed(1) + " min</b>"],
  ]);

  countUp($("bignum"), state.lastPrice || 0, r.unit_price_cny, cur);
  state.lastPrice = r.unit_price_cny;
  const taxPct = ((q.tax_rate || 0) * 100).toFixed(0);
  const grand = q.total_incl_tax_cny != null ? q.total_incl_tax_cny : r.line_total_cny;
  const valid = q.valid_until ? ` · 有效期至 ${q.valid_until}` : "";
  const minNote = q.min_order_topup_cny > 0
    ? ` · <span class="warn-mark">已按起订额 ${money(q.min_order_cny || q.net_total_cny, cur)} 计，补差 ${money(q.min_order_topup_cny, cur)}</span>`
    : "";
  $("bignum-sub").innerHTML =
    `× ${r.quantity} 件 · 净额 ${money(q.net_total_cny != null ? q.net_total_cny : r.line_total_cny, cur)}` +
    ` · <b>含税 ${money(grand, cur)}</b>${q.tax_rate ? ` (${q.tax_label || "税"} ${taxPct}%)` : ""}` +
    ` · 交期 ${q.lead_days} 天${q.delivery_date ? `（约 ${q.delivery_date} 交付）` : ""}${valid}${minNote}`;
  const pw = $("price-why");
  if (pw) {
    const drv = p.price_drivers;
    pw.innerHTML = drv && drv.summary ? `<span class="muted tiny">${drv.summary}</span>` : "";
  }
  renderConfidence(p);
  renderLogistics(p);
  renderCompare(p);
  renderLeadOptions(q, isLive);
  renderMaterialSuggestions(p);
  const psrc = p.input?.price_source;
  const srcTag = psrc && psrc !== "static"
    ? ` <span class="muted tiny">(${psrc === "manual override" ? "改价" : psrc.split(" ")[0]})</span>` : "";
  const matRows = q.scrap_credit_cny > 0
    ? [[`材料毛重 Material${srcTag}`, money(q.material_gross_cny, cur)],
       ["废料抵扣 Scrap credit", "−" + money(q.scrap_credit_cny, cur)],
       ["材料净费 Material net", money(r.material_cny, cur)]]
    : [[`材料费 Material${srcTag}`, money(r.material_cny, cur)]];
  $("cost-table").innerHTML = kv([
    ...matRows,
    ["加工费 Machining", money(r.machining_cny, cur)],
    ["表面处理 Finishing", money(r.finish_variable_cny, cur)],
    ...(r.addon_per_part_cny > 0 ? [["去毛刺/增项 Post-process", money(r.addon_per_part_cny, cur)]] : []),
    ["编程摊销 Setup/ea", money(r.amortized_one_time_cny, cur)],
    ["单件成本 Unit cost", money(r.unit_cost_cny, cur)],
    [`利润率 Margin`, (r.margin * 100).toFixed(0) + " %"],
  ]);

  const base = q.tiers.length ? q.tiers[0].unit_price_cny : r.unit_price_cny;
  let rows = `<tr><th>数量 Qty</th><th>单价 Unit</th><th>总价 Total</th></tr>`;
  for (const t of q.tiers) {
    const active = t.quantity === r.quantity ? ' class="active"' : "";
    const save = base > 0 && t.unit_price_cny < base
      ? `<span class="save-pct">-${(100 * (1 - t.unit_price_cny / base)).toFixed(0)}%</span>` : "";
    rows += `<tr${active}><td>${t.quantity}</td><td>${money(t.unit_price_cny, cur)}${save}</td><td>${money(t.line_total_cny, cur)}</td></tr>`;
  }
  $("tier-table").innerHTML = rows;

  // Structured DFM findings (graded), then lighter cost/process notes.
  const SEV = { high: { c: "warn", t: "高" }, medium: { c: "warn", t: "中" },
                low: { c: "info", t: "低" }, info: { c: "info", t: "" } };
  const dfm = p.dfm || [];
  const hasRisk = dfm.some((d) => d.severity === "high" || d.severity === "medium");
  const dfmHtml = dfm.map((d) => {
    const s = SEV[d.severity] || SEV.info;
    const badge = s.t ? `<span class="sev-badge ${d.severity}">${s.t}</span>` : "";
    return `<li class="${s.c}"><span>${badge}<b>${esc(d.title)}</b> — ${esc(d.detail)}
      <span class="muted tiny">建议：${esc(d.suggestion)}</span></span></li>`;
  }).join("");
  const sum = p.dfm_summary;
  const sumHtml = sum
    ? `<li class="dfm-sum ${sum.level}"><span><b>制造风险概览</b> — ${esc(sum.headline)}` +
      `<span class="muted tiny">（高 ${sum.counts.high} · 中 ${sum.counts.medium} · 低 ${sum.counts.low}）</span></span></li>`
    : "";
  const notes = [...(q.notes || []), ...(pl.notes || [])];
  const noteHtml = notes.map((n) => `<li class="info"><span class="w-ico">·</span><span class="muted">${esc(n)}</span></li>`).join("");
  $("warn-list").innerHTML = (sumHtml + dfmHtml + noteHtml) ||
    `<li class="info"><span>无明显可加工性风险 No DFM flags</span></li>`;
  // set unconditionally: a risk-free follow-up quote must clear the old suffix
  $("warn-card").querySelector("h3").textContent =
    hasRisk ? "工艺提示 Notes & DFM（有风险）" : "工艺提示 Notes & DFM";

  const assume = p.assumptions || [];
  const aCard = $("assume-card");
  if (aCard) {
    aCard.classList.toggle("hidden", !assume.length);
    $("assume-list").innerHTML = assume.map((a) =>
      `<li class="info"><span class="w-ico">·</span><span class="muted">${esc(a)}</span></li>`).join("");
  }

  renderEstimator(p);
  renderCalibration(p, isLive);
  $("result-badge").hidden = !isLive;
  const resEl = $("result");
  const wasHidden = resEl.classList.contains("hidden");
  resEl.classList.remove("hidden");
  if (wasHidden) { resEl.classList.add("reveal"); resEl.scrollIntoView({ behavior: "smooth", block: "start" }); }
}

// ───────────────────────── AI copilot ─────────────────────────
const aiState = { history: [], busy: false, sid: null };

function openAIPanel() { $("ai-panel").classList.remove("hidden"); $("ai-text").focus(); }
function closeAIPanel() { $("ai-panel").classList.add("hidden"); }

async function loadAIStatus() {
  try {
    const s = await (await fetch("/api/ai/status")).json();
    const el = $("ai-status");
    el.textContent = s.live ? `已接入 ${s.provider}` : "离线模拟助手（上线接入真实 LLM）";
    el.classList.toggle("live", !!s.live);
  } catch { $("ai-status").textContent = "状态未知"; }
  try {
    const t = await (await fetch("/api/ai/tools")).json();
    const n = (t.tools || []).length;
    $("ai-foot").innerHTML =
      `可调用 ${n} 项工具（报价/对比/DFM/改价等）；改价等写操作需管理员审批。<br>` +
      "注意：AI 结果仅供参考，重要报价请人工复核；勿在对话中输入敏感信息。";
  } catch { /* ignore */ }
}

function appendAIMsg(role, text) {
  const d = document.createElement("div");
  d.className = "ai-msg " + (role === "user" ? "ai-user" : "ai-bot");
  d.textContent = text;
  $("ai-messages").appendChild(d);
  $("ai-messages").scrollTop = $("ai-messages").scrollHeight;
  return d;
}

function appendAITyping() {
  const d = document.createElement("div");
  d.className = "ai-msg ai-bot ai-typing";
  d.textContent = "助手思考中…";
  $("ai-messages").appendChild(d);
  $("ai-messages").scrollTop = $("ai-messages").scrollHeight;
  return d;
}

async function sendAI() {
  const text = $("ai-text").value.trim();
  if (!text || aiState.busy) return;
  $("ai-text").value = "";
  // research mode owns the conversation until the flow ends or user exits
  if (aiState.research) return sendResearch(text);
  aiState.busy = true;
  appendAIMsg("user", text);
  const typing = appendAITyping();
  try {
    const fd = new FormData();
    fd.append("message", text);
    fd.append("params", JSON.stringify(buildParams(false)));
    fd.append("history", JSON.stringify(aiState.history));
    if (state.file) fd.append("file", state.file);
    const r = await fetch("/api/ai/chat", { method: "POST", headers: authHeaders(), body: fd });
    const d = await r.json();
    typing.remove();
    (d.actions || []).forEach(renderAIAction);
    if (d.reply) appendAIMsg("bot", d.reply);
    if (d.pending) renderAIPending(d.pending);
    aiState.history = d.history || aiState.history;
    saveAISession();
  } catch (e) {
    typing.remove(); appendAIMsg("bot", "网络错误：" + e.message);
  } finally { aiState.busy = false; }
}

// ──────────────── guided research flow (analytical partner) ────────────────
function startResearch() {
  aiState.research = { state: {} };
  openAIPanel();
  appendAIMsg("bot", "已进入研究模式：我会按 需求澄清 → 几何/DFM → 选材 → 批量/交期 → 决策简报 引导分析。随时回复“退出研究”返回自由问答。");
  sendResearch("开始研究");
}

function renderResearchProgress(prog) {
  const el = $("ai-progress");
  if (!el) return;
  if (!prog) { el.classList.add("hidden"); el.innerHTML = ""; return; }
  el.classList.remove("hidden");
  el.innerHTML = prog.stages.map((s, i) =>
    `<span class="ai-stage${s.done ? " done" : ""}${i + 1 === prog.current ? " on" : ""}">${esc(s.label)}</span>`
  ).join('<span class="ai-stage-sep">→</span>');
}

function renderNextSteps(steps) {
  if (!steps || !steps.length) return;
  const d = document.createElement("div");
  d.className = "ai-msg ai-bot ai-next";
  d.innerHTML = `<span class="muted tiny">下一步建议：</span>` + steps.map((s) =>
    `<button class="ai-chip" data-research-q="${esc(s.message)}">${esc(s.label)}</button>`).join(" ");
  $("ai-messages").appendChild(d);
  $("ai-messages").scrollTop = $("ai-messages").scrollHeight;
}

async function sendResearch(text) {
  if (aiState.busy) return;
  if (/退出研究|退出|exit/i.test(text)) {
    aiState.research = null; renderResearchProgress(null);
    appendAIMsg("bot", "已退出研究模式，回到自由问答。");
    return;
  }
  aiState.busy = true;
  appendAIMsg("user", text);
  const typing = appendAITyping();
  try {
    const fd = new FormData();
    fd.append("message", text);
    fd.append("params", JSON.stringify(buildParams(false)));
    fd.append("state", JSON.stringify(aiState.research.state || {}));
    if (state.file) fd.append("file", state.file);
    const r = await fetch("/api/ai/research", { method: "POST", headers: authHeaders(), body: fd });
    const d = await r.json();
    typing.remove();
    if (!r.ok) { appendAIMsg("bot", "研究步骤失败：" + (d.detail || r.statusText)); return; }
    aiState.research.state = d.state || {};
    renderResearchProgress(d.progress);
    appendAIMsg("bot", d.reply || "");
    renderNextSteps(d.next_steps);
    // Bridge research turns into the chat history: the conversation persists
    // with the session, and post-research free chat (a real LLM at launch)
    // keeps the study context instead of going amnesiac about the brief.
    aiState.history.push({ role: "user", content: text },
                         { role: "assistant", content: d.reply || "" });
    saveAISession();
    if (d.done) {
      aiState.research = null;   // flow complete; chips still work via data-research-q
    }
  } catch (e) {
    typing.remove(); appendAIMsg("bot", "网络错误：" + e.message);
  } finally { aiState.busy = false; }
}

const AI_TOOL_META = {
  get_quote: { label: "报价" }, compare_materials: { label: "材料对比" },
  suggest_cheaper_material: { label: "更省建议" }, analyze_dfm: { label: "DFM 分析" },
  list_materials: { label: "材料列表" }, set_price: { label: "改价" },
  record_actual_time: { label: "录入实测工时" }, explain_quote: { label: "成本解释" },
};

function renderAIAction(a) {
  const m = AI_TOOL_META[a.tool] || { label: a.tool };
  const args = Object.entries(a.arguments || {}).map(([k, v]) => `${k}=${v}`).join(" ");
  const card = document.createElement("div");
  card.className = "ai-action" + (a.error ? " err" : "");
  card.innerHTML =
    `<div class="ai-action-head"><b>${esc(m.label)}</b>` +
    (args ? ` <span class="ai-args">${esc(args)}</span>` : "") +
    (a.error ? ` <span class="ai-err">错误 ${esc(a.error)}</span>` : ` <span class="ai-ok">完成</span>`) +
    `</div><div class="ai-action-body">${esc(a.summary || "")}</div>`;
  if (a.tool === "get_quote" && a.arguments && !a.error) {
    const btn = document.createElement("button");
    btn.className = "ai-apply"; btn.textContent = "应用到表单并报价";
    btn.addEventListener("click", () => applyAIQuote(a.arguments));
    card.appendChild(btn);
  }
  $("ai-messages").appendChild(card);
  $("ai-messages").scrollTop = $("ai-messages").scrollHeight;
}

function renderAIPending(pending) {
  const m = AI_TOOL_META[pending.tool] || { label: pending.tool };
  const args = Object.entries(pending.arguments || {}).map(([k, v]) => `${k}=${v}`).join(" ");
  const card = document.createElement("div");
  card.className = "ai-action pending";
  card.innerHTML =
    `<div class="ai-action-head"><b>待确认：${esc(m.label)}</b></div>` +
    `<div class="ai-action-body">${esc(pending.description || "")}<br><span class="ai-args">${esc(args)}</span></div>`;
  const row = document.createElement("div");
  row.className = "ai-approve-row";
  const ok = document.createElement("button"); ok.className = "ai-confirm"; ok.textContent = "确认执行";
  const no = document.createElement("button"); no.className = "ai-reject"; no.textContent = "取消";
  ok.addEventListener("click", () => { approveAI(pending); card.remove(); });
  no.addEventListener("click", () => { card.remove(); appendAIMsg("bot", "已取消该操作。"); });
  row.append(ok, no); card.appendChild(row);
  $("ai-messages").appendChild(card);
  $("ai-messages").scrollTop = $("ai-messages").scrollHeight;
}

async function approveAI(pending) {
  if (!state.token) { toast("写操作需管理员登录", "err"); openLogin(); return; }
  try {
    const { res, detail, authFailed } = await apiFetch("/api/ai/approve", {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ tool: pending.tool, arguments: pending.arguments, params: buildParams(false) }),
    }, { authed: true });
    if (authFailed) return;
    if (!res.ok) { appendAIMsg("bot", "执行失败：" + detail); return; }
    const d = await res.json();
    appendAIMsg("bot", d.summary || (d.ok ? "已执行" : "执行失败"));
    if (d.ok) { loadShop(); if (state.hasQuoted) requestQuote(false); }
  } catch (e) { appendAIMsg("bot", "网络错误：" + e.message); }
}

function applyAIQuote(args) {
  if (args.material) {
    $("material").value = args.material; recolorMesh(args.material);
    refreshFinishes();   // material change re-scopes valid finishes; a stale
  }                      // list silently no-ops args.finish or 400s the quote
  if (args.quantity) $("quantity").value = args.quantity;
  if (args.tolerance) $("tolerance").value = args.tolerance;
  if (args.surface_finish) $("surface_finish").value = args.surface_finish;
  if (args.finish) $("finish").value = args.finish;
  updateMatPrice();
  requestQuote(true);
  toast("已按 AI 建议更新报价", "ok");
}

async function aiAnalyze() {
  if (!state.file && !manualDims()) { toast("请先上传零件或填写尺寸", "err"); return; }
  openAIPanel();
  appendAIMsg("user", "全面分析这个零件");
  const typing = appendAITyping();
  try {
    const fd = new FormData();
    fd.append("params", JSON.stringify(buildParams(false)));
    if (state.file) fd.append("file", state.file);
    const d = await (await fetch("/api/ai/analyze", { method: "POST", body: fd })).json();
    typing.remove();
    (d.sections || []).forEach((s) =>
      renderAIAction({ tool: s.tool, arguments: {}, summary: s.summary, error: s.error }));
    if (d.recommendation) appendAIMsg("bot", "综合建议：\n" + d.recommendation);
  } catch (e) { typing.remove(); appendAIMsg("bot", "分析失败：" + e.message); }
}

function newChat() {
  aiState.history = []; aiState.sid = null;
  $("ai-messages").innerHTML = '<div class="ai-msg ai-bot">已开始新对话。试试“分析这个零件”或“SUS304 50件多少钱”。</div>';
}

async function saveAISession() {
  try {
    if (!aiState.sid) aiState.sid = "ai_" + Date.now().toString(36);
    const title = (aiState.history.find((m) => m.role === "user")?.content || "对话").slice(0, 40);
    await fetch("/api/ai/session", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: aiState.sid, title, messages: aiState.history }),
    });
  } catch { /* best-effort */ }
}

function initAI() {
  $("ai-fab").addEventListener("click", openAIPanel);
  $("ai-analyze-btn").addEventListener("click", aiAnalyze);
  $("ask-why-btn").addEventListener("click", () => { $("ai-text").value = "为什么是这个价格？"; openAIPanel(); sendAI(); });
  $("ai-close").addEventListener("click", closeAIPanel);
  $("ai-new").addEventListener("click", newChat);
  $("ai-send").addEventListener("click", sendAI);
  $("ai-text").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendAI(); }
  });
  $("ai-messages").addEventListener("click", (e) => {
    const chip = e.target.closest(".ai-chip");
    if (!chip) return;
    if (chip.id === "ai-research-btn") { startResearch(); return; }
    const rq = chip.dataset.researchQ;
    if (rq) {
      if (/重新开始|重新研究/.test(rq)) { startResearch(); return; }
      if (aiState.research) { sendResearch(rq); }
      else { $("ai-text").value = rq; sendAI(); }   // post-brief chips → free chat
      return;
    }
    if (chip.dataset.q) { $("ai-text").value = chip.dataset.q; sendAI(); }
  });
  loadAIStatus();
}

// ───────────────────────── material comparison ─────────────────────────
function renderCompare(p) {
  const el = $("compare-wrap");
  const rows = p.material_comparison;
  state.compare = false;                 // one-shot; don't slow later quotes
  if (!rows) { el.innerHTML = ""; return; }
  el.innerHTML = `<div class="lead-title" style="margin-top:12px">材料对比 Material comparison（按单价）</div>` +
    `<table class="tiers"><tr><th>材料</th><th>单价</th><th>密度</th><th>可加工性</th></tr>` +
    rows.map((r) => `<tr${r.key === $("material").value ? ' class="active"' : ""}>
      <td>${esc(r.label.split(" ")[0])}</td><td>${money(r.unit_price_cny)}</td>
      <td>${r.density_g_cm3}</td><td>×${r.machinability}</td></tr>`).join("") + `</table>`;
}

// ───────────────────────── logistics ─────────────────────────
function renderLogistics(p) {
  const el = $("logistics");
  if (!el) return;
  const L = p.logistics;
  if (!L) { el.innerHTML = ""; return; }
  const crate = L.crated ? `，含木箱 ${money(L.crate_cny)}` : "";
  let h = `<span class="muted tiny">预估运费 Shipping（${L.order_weight_kg} kg${crate}）：${money(L.shipping_cny)}</span>`;
  if (!L.meets_min_order)
    h += ` <span class="min-order">未达最小起订额 ${money(L.min_order_cny)}，差 ${money(L.shortfall_cny)}</span>`;
  el.innerHTML = h;
}

// ───────────────────────── confidence ─────────────────────────
function renderConfidence(p) {
  const el = $("conf-badge");
  const c = p.confidence;
  if (!c) { el.innerHTML = ""; return; }
  const label = { high: "高 High", medium: "中 Medium", low: "低 Low" }[c.level] || c.level;
  const reasons = c.reasons.length ? "影响：" + c.reasons.map(esc).join("、") : "无明显不确定因素";
  const cur = (p.quote && p.quote.currency) || "CNY";
  const rng = c.price_range_cny
    ? ` <span class="muted tiny">参考区间 ${money(c.price_range_cny.low, cur)}–${money(c.price_range_cny.high, cur)} (±${(c.price_range_cny.band_pct*100).toFixed(0)}%)</span>`
    : "";
  el.innerHTML = `<span class="conf-badge ${c.level}" title="${esc(reasons)}">报价置信度 ${c.score}/100 · ${label}</span>` + rng;
}

// ───────────────────────── material suggestions ─────────────────────────
function renderMaterialSuggestions(p) {
  const el = $("mat-suggest");
  const s = p.material_suggestions || [];
  if (!s.length) { el.innerHTML = ""; return; }
  el.innerHTML = `<span class="muted tiny">更省材料（如性能允许 if properties allow）：</span> ` +
    s.map((m) => {
      const weak = m.strength_ok === false;
      const title = weak ? ` title="${esc(m.strength_hint || "")}"` : "";
      const warn = weak ? ' <span class="warn-mark">强度↓</span>' : "";
      return `<button class="suggest-chip${weak ? " weak" : ""}" data-mat="${esc(m.key)}"${title}>` +
             `${esc(m.label.split(" ")[0])} ↓${m.savings_pct}%${warn}</button>`;
    }).join(" ");
  el.querySelectorAll(".suggest-chip").forEach((b) => b.addEventListener("click", () => {
    $("material").value = b.dataset.mat;
    refreshFinishes();   // re-scope finishes or the old material's pick can 400
    updateMatPrice(); recolorMesh(b.dataset.mat); requestQuote(true);
  }));
}

// ───────────────────────── lead-time options ─────────────────────────
function renderLeadOptions(q, isLive) {
  const el = $("lead-opts");
  const opts = q.lead_time_options || [];
  if (!opts.length) { el.innerHTML = ""; return; }
  // Chips stay clickable on live renders too — a chip click re-renders live
  // (isLive=true), so disabling here made tier selection one-shot.
  el.innerHTML = `<div class="lead-title">交期选项 Delivery</div>` +
    `<div class="lead-chips">` + opts.map((o) =>
      `<button class="lead-chip${o.selected ? " on" : ""}" data-lead="${esc(o.key)}">
         <span class="lead-name">${esc(o.label)}</span>
         <span class="lead-days">${o.days} 天${o.delivery_date ? ` · ${o.delivery_date}` : ""}</span>
         <span class="lead-price">${money(o.unit_price_cny, q.currency)}/件</span>
       </button>`).join("") + `</div>`;
  el.querySelectorAll(".lead-chip").forEach((b) => b.addEventListener("click", () => {
    state.leadTime = b.dataset.lead;
    requestQuote(false);   // live re-quote with the chosen tier
  }));
}

// ───────────────────────── calibration (admin) ─────────────────────────
function renderCalibration(p, isLive) {
  const card = $("cal-card");
  const cal = p.plan?.calibration;
  // factor note for everyone (shows when calibration is active)
  if (cal && cal.factor && cal.factor !== 1) {
    $("plan-table").insertAdjacentHTML("beforeend",
      `<tr><td>实测校准 Calibration</td><td>×${cal.factor} (n=${cal.n})</td></tr>`);
  }
  // entry panel only for a logged-in admin on a saved quote (has id)
  const canCalibrate = state.user && !isLive && p.id;
  card.classList.toggle("hidden", !canCalibrate);
  if (canCalibrate) {
    $("cal-actual").value = "";
    $("cal-status").textContent = cal && cal.n
      ? `当前因子 ×${cal.factor}（${cal.n} 样本）` : "尚无校准样本";
  }
}

async function submitCalibration() {
  if (!state.lastPayload?.id) return;
  const actual = parseFloat($("cal-actual").value);
  if (!(actual > 0)) { toast("请输入有效的实测分钟", "err"); return; }
  const btn = $("cal-submit"); btn.classList.add("loading");
  try {
    const { res, detail, authFailed } = await apiFetch("/api/calibration/actual", {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ quote_id: state.lastPayload.id, actual_min: actual }),
    }, { authed: true });
    if (authFailed) return;
    if (!res.ok) { toast("提交失败：" + detail, "err"); return; }
    const d = await res.json();
    const f = d.factors?.[d.material];
    toast("已记录实测，材料因子 " + (f ? `×${f.factor} (n=${f.n})` : "样本不足"), "ok");
    requestQuote(false);   // re-quote to reflect the updated factor
  } catch (e) {
    toast("网络错误：" + e.message, "err");
  } finally { btn.classList.remove("loading"); }
}

// ───────────────────────── history ─────────────────────────
async function loadHistory() {
  let rows;
  try { rows = (await (await fetch("/api/quotes?limit=12")).json()).quotes || []; } catch { return; }
  const tb = $("hist-table").querySelector("tbody");
  $("hist-empty").classList.toggle("hidden", rows.length > 0);
  tb.innerHTML = "";
  for (const r of rows) {
    const tr = document.createElement("tr");
    const when = (r.created_at || "").replace("T", " ").slice(5, 16);
    tr.innerHTML =
      `<td>${esc(r.part_name || "part")}<div class="h-id">${esc(r.id)}</div></td>` +
      `<td>${r.material} × ${r.quantity}<br><span class="muted tiny">${when}</span></td>` +
      `<td class="h-price">${moneyCNY(r.unit_price)}</td>` +
      `<td class="h-pdf"><a href="/api/quotes/${r.id}/pdf" target="_blank" rel="noopener">PDF</a></td>`;
    tr.addEventListener("click", (e) => { if (e.target.tagName !== "A") reopenQuote(r.id); });
    tb.appendChild(tr);
  }
}

async function reopenQuote(id) {
  try {
    const res = await fetch(`/api/quotes/${id}`);
    if (!res.ok) return;
    state.lastPayload = await res.json();
    state.lastPrice = 0;
    renderResult(state.lastPayload, false);
    toast("已载入历史报价 " + id, "info");
  } catch { /* ignore */ }
}

async function downloadPdf() {
  if (!state.lastPayload) return;
  const btn = $("pdf-btn");
  btn.classList.add("loading");
  try {
    const res = await fetch("/api/quote/pdf", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state.lastPayload),
    });
    if (!res.ok) { toast("PDF 生成失败", "err"); return; }
    const blob = await res.blob(), url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = (state.lastPayload.input.part_name || "quote") + "_quote.pdf";
    a.click();
    URL.revokeObjectURL(url);
    toast("PDF 已下载", "ok");
  } finally { btn.classList.remove("loading"); }
}

// ───────────────────────── auth ─────────────────────────
function authHeaders(extra) {
  const h = extra ? { ...extra } : {};
  if (state.token) h["Authorization"] = "Bearer " + state.token;
  return h;
}

function setAuthUI() {
  const chip = $("user-chip"), logout = $("logout-btn");
  if (state.user) {
    chip.textContent = state.user;
    chip.classList.remove("hidden");
    logout.classList.remove("hidden");
  } else {
    chip.classList.add("hidden");
    logout.classList.add("hidden");
  }
}

async function loadMe() {
  state.token = localStorage.getItem("cnc_token") || null;
  if (!state.token) { state.user = null; setAuthUI(); return; }
  try {
    const r = await fetch("/api/me", { headers: authHeaders() });
    if (r.ok) { state.user = (await r.json()).username; }
    else { state.token = null; localStorage.removeItem("cnc_token"); state.user = null; }
  } catch { state.user = null; }
  setAuthUI();
}

function openLogin() {
  $("login-error").classList.add("hidden");
  $("login-pass").value = "";
  $("login-modal").classList.remove("hidden");
  $("login-pass").focus();
}
function closeLogin() { $("login-modal").classList.add("hidden"); }

async function doLogin() {
  const username = $("login-user").value.trim();
  const password = $("login-pass").value;
  const btn = $("login-submit"); btn.classList.add("loading");
  try {
    const r = await fetch("/api/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!r.ok) {
      const d = (await r.json().catch(() => ({}))).detail || "登录失败";
      const e = $("login-error"); e.textContent = d; e.classList.remove("hidden");
      return;
    }
    const data = await r.json();
    state.token = data.token; state.user = data.username;
    localStorage.setItem("cnc_token", data.token);
    setAuthUI(); closeLogin(); toast("已登录 " + data.username, "ok");
    openAdmin();   // continue to the panel they were after
  } catch (e) {
    toast("网络错误：" + e.message, "err");
  } finally { btn.classList.remove("loading"); }
}

function logout() {
  state.token = null; state.user = null;
  localStorage.removeItem("cnc_token");
  setAuthUI(); toast("已退出", "info");
}

// ───────────────────────── admin price modal ─────────────────────────
const BIZ_LABELS = {
  margin: "利润率", tax_rate: "增值税率", tight_tolerance_margin_bonus: "精密公差溢价",
  deburr_base_cny: "去毛刺起步 ¥", deburr_per_dm2_cny: "去毛刺 ¥/dm²",
  packaging_cny: "包装费 ¥", shipping_cny_per_kg: "运费 ¥/kg", min_order_cny: "最小起订 ¥",
  quote_valid_days: "报价有效期(天)", daily_capacity_hours: "日产能(机时/天)",
  tool_wear_cny_per_hour: "刀具消耗 ¥/h", crate_threshold_kg: "木箱阈值(kg)", crate_cny: "木箱费 ¥",
};
// Friendly labels + edit step for the per-material/finish/machine fields.
const FIELD_META = {
  price_cny_per_kg: ["料价 ¥/kg", 0.5], machinability: ["可加工性", 0.05],
  density_g_cm3: ["密度 g/cm³", 0.01], form_factor: ["毛坯系数", 0.05],
  scrap_credit_frac: ["废料抵扣率", 0.05], tensile_mpa: ["抗拉强度 MPa", 5],
  stock_lead_days: ["备料周期(天)", 1], rate_cny_per_hour: ["时租 ¥/h", 5],
  base_mrr_cm3_min: ["基础 MRR", 1], max_axes: ["最大轴数", 1],
  setup_cny: ["起步 ¥", 1], per_dm2_cny: ["¥/dm²", 0.5], min_cny: ["保底 ¥", 1],
  lead_days: ["外协周期(天)", 1],
};

function adminRow(grid, label, kind, key, field, value, step) {
  const d = document.createElement("div"); d.className = "admin-row";
  d.innerHTML = `<label>${esc(label)}<input type="number" step="${step}" ` +
    `data-kind="${kind}" data-key="${esc(key)}" data-field="${field}" value="${value}"></label>`;
  grid.appendChild(d);
}

async function openAdmin() {
  if (!state.token) { openLogin(); return; }
  let cfg;
  try {
    const { res, authFailed } = await apiFetch("/api/admin/config", { headers: authHeaders() }, { authed: true });
    if (authFailed) return;
    cfg = await res.json();
  } catch { toast("加载配置失败", "err"); return; }
  // Render every overridable field the config returns (label excluded), so the
  // panel always reflects the full maintainable set without UI edits.
  const fieldRows = (grid, kind, entries, shortLabel) => {
    grid.innerHTML = "";
    for (const [k, obj] of Object.entries(entries || {})) {
      const name = shortLabel ? obj.label.split(" ")[0] : obj.label;
      for (const [field, val] of Object.entries(obj)) {
        if (field === "label" || val == null) continue;
        const [fl, step] = FIELD_META[field] || [field, 0.5];
        adminRow(grid, `${name} · ${fl}`, kind, k, field, val, step);
      }
    }
  };
  fieldRows($("admin-materials"), "material", cfg.materials, true);
  fieldRows($("admin-machines"), "machine", cfg.machines, false);
  fieldRows($("admin-finishes"), "finish", cfg.finishes || {}, true);
  const bg = $("admin-business"); bg.innerHTML = "";
  for (const [k, v] of Object.entries(cfg.business || {}))
    adminRow(bg, BIZ_LABELS[k] || k, "business", "", k, v, k.includes("rate") || k === "margin" ? 0.01 : 1);
  // tier arrays (lead / tolerance / surface / addons) → field path
  const tg = $("admin-tiers"); tg.innerHTML = "";
  const tierLbl = { lead_time_tiers: "交期", tolerance_classes: "公差", surface_classes: "表面", addons: "增项" };
  for (const [arr, list] of Object.entries(cfg.tiers || {})) {
    for (const el of list) {
      for (const [sub, val] of Object.entries(el)) {
        if (sub === "key" || sub === "label" || val == null) continue;
        adminRow(tg, `${tierLbl[arr]}·${el.label.split(" ")[0]}·${sub}`, "business", "",
          `${arr}.${el.key}.${sub}`, val, 0.01);
      }
    }
  }
  const cp = $("admin-capp"); cp.innerHTML = "";
  for (const [k, v] of Object.entries(cfg.capp || {})) adminRow(cp, k, "capp", "", k, v, 0.5);
  // cutting per selected material
  state.adminCutting = cfg.cutting || {};
  const sel = $("admin-cut-mat"); sel.innerHTML = "";
  for (const m of Object.keys(state.adminCutting)) { const o = document.createElement("option"); o.value = m; o.textContent = m; sel.appendChild(o); }
  sel.onchange = renderAdminCutting; renderAdminCutting();
  renderSkills();
  $("admin-modal").classList.remove("hidden");
}

function renderAdminCutting() {
  const cg = $("admin-cutting"); cg.innerHTML = "";
  const mat = $("admin-cut-mat").value;
  for (const [k, v] of Object.entries((state.adminCutting || {})[mat] || {}))
    adminRow(cg, k, "cutting", mat, k, v, 0.01);
}
function closeAdmin() { $("admin-modal").classList.add("hidden"); }

// ───────────────────────── skills library ─────────────────────────
const SKILL_KIND_LABEL = { action: "动作", analyze: "分析", knowledge: "知识" };

// Save one field (or several) on a skill, then re-render the list.
async function skillPut(payload) {
  try {
    const { res, detail, authFailed } = await apiFetch("/api/admin/skills", {
      method: "PUT", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
    }, { authed: true });
    if (authFailed) { closeAdmin(); toast("登录已过期，请重新登录", "err"); return false; }
    if (!res.ok) { toast("保存失败：" + detail, "err"); return false; }
    return true;
  } catch (e) { toast("保存失败：" + e.message, "err"); return false; }
}

// Split a triggers input on commas (ASCII + 、 + Chinese comma) into a clean array.
const splitTriggers = (s) => String(s || "").split(/[,，、]/).map((t) => t.trim()).filter(Boolean);

async function renderSkills() {
  const wrap = $("admin-skills");
  if (!wrap) return;
  let data;
  try {
    const { res, authFailed } = await apiFetch("/api/admin/skills", { headers: authHeaders() }, { authed: true });
    if (authFailed) return;
    data = await res.json();
  } catch { toast("加载技能库失败", "err"); return; }
  wrap.innerHTML = "";
  for (const sk of data.skills || []) {
    const row = document.createElement("div");
    row.className = "skill-row";
    const kindCls = sk.kind || "knowledge";
    const kindLbl = SKILL_KIND_LABEL[kindCls] || kindCls;
    const originLbl = sk.builtin ? "内置" : "自定义";
    const originCls = sk.builtin ? "builtin" : "custom";
    row.innerHTML =
      `<div class="skill-head">` +
        `<span class="skill-name">${esc(sk.name || sk.key)}</span>` +
        `<span class="skill-badge ${esc(kindCls)}">${esc(kindLbl)}</span>` +
        `<span class="skill-origin ${originCls}">${originLbl}</span>` +
        `<label class="skill-en"><input type="checkbox" data-en ${sk.enabled ? "checked" : ""}>启用</label>` +
        `<button class="ghost-btn small skill-del" data-del>${sk.builtin ? "恢复默认" : "删除"}</button>` +
      `</div>` +
      `<label class="skill-field">触发词<input type="text" data-trig value="${esc((sk.triggers || []).join("，"))}"></label>` +
      (kindCls === "knowledge"
        ? `<label class="skill-field">回答<textarea data-resp rows="3">${esc(sk.response || "")}</textarea></label>`
        : "");
    // toggle enabled
    row.querySelector("[data-en]").addEventListener("change", async (e) => {
      if (await skillPut({ key: sk.key, enabled: e.target.checked })) toast("已更新", "ok");
      else renderSkills();
    });
    // edit triggers (commit on blur)
    row.querySelector("[data-trig]").addEventListener("change", async (e) => {
      if (await skillPut({ key: sk.key, triggers: splitTriggers(e.target.value) })) toast("已更新触发词", "ok");
    });
    // edit response (knowledge only)
    const resp = row.querySelector("[data-resp]");
    if (resp) resp.addEventListener("change", async (e) => {
      if (await skillPut({ key: sk.key, response: e.target.value })) toast("已更新回答", "ok");
    });
    // delete / revert
    row.querySelector("[data-del]").addEventListener("click", async () => {
      const verb = sk.builtin ? "恢复默认" : "删除";
      if (!confirm(`确定${verb}「${sk.name || sk.key}」？`)) return;
      try {
        const { res, detail, authFailed } = await apiFetch("/api/admin/skills/" + encodeURIComponent(sk.key),
          { method: "DELETE", headers: authHeaders() }, { authed: true });
        if (authFailed) { closeAdmin(); toast("登录已过期，请重新登录", "err"); return; }
        if (!res.ok) { toast("操作失败：" + detail, "err"); return; }
        toast(verb + "成功", "ok"); renderSkills();
      } catch (e) { toast("操作失败：" + e.message, "err"); }
    });
    wrap.appendChild(row);
  }
}

// Inline "new custom skill" form, appended to the skills pane.
function openSkillAddForm() {
  const wrap = $("admin-skills");
  if (!wrap || wrap.querySelector(".skill-add-form")) return;
  const form = document.createElement("div");
  form.className = "skill-row skill-add-form";
  form.innerHTML =
    `<div class="skill-head"><span class="skill-name">新增自定义技能</span></div>` +
    `<label class="skill-field">标识 key<input type="text" data-f="key" placeholder="custom_xxx"></label>` +
    `<label class="skill-field">名称<input type="text" data-f="name"></label>` +
    `<label class="skill-field">类型<select data-f="kind"><option value="knowledge">知识</option><option value="action">动作</option></select></label>` +
    `<label class="skill-field">触发词<input type="text" data-f="triggers" placeholder="用逗号分隔"></label>` +
    `<label class="skill-field skill-resp">回答<textarea data-f="response" rows="3"></textarea></label>` +
    `<label class="skill-field skill-act" style="display:none">动作 action<input type="text" data-f="action" placeholder="tool name"></label>` +
    `<div class="skill-head"><button class="primary small" data-save>保存</button><button class="ghost-btn small" data-cancel>取消</button></div>`;
  const kindSel = form.querySelector('[data-f="kind"]');
  kindSel.addEventListener("change", () => {
    const isAct = kindSel.value === "action";
    form.querySelector(".skill-resp").style.display = isAct ? "none" : "";
    form.querySelector(".skill-act").style.display = isAct ? "" : "none";
  });
  form.querySelector("[data-cancel]").addEventListener("click", () => form.remove());
  form.querySelector("[data-save]").addEventListener("click", async () => {
    const val = (f) => form.querySelector(`[data-f="${f}"]`).value.trim();
    const kind = val("kind");
    const payload = { key: val("key"), name: val("name"), kind, triggers: splitTriggers(val("triggers")) };
    if (!payload.key || !payload.name || !payload.triggers.length) { toast("请填写 标识/名称/触发词", "err"); return; }
    if (kind === "knowledge") {
      if (!val("response")) { toast("知识类需要填写回答", "err"); return; }
      payload.response = val("response");
    } else {
      if (!val("action")) { toast("动作类需要填写 action", "err"); return; }
      payload.action = val("action");
    }
    if (await skillPut(payload)) { toast("已新增技能", "ok"); renderSkills(); }
  });
  wrap.appendChild(form);
}

async function saveAdmin() {
  const inputs = [...$("admin-modal").querySelectorAll("input[data-kind]")];
  const changed = inputs.filter((i) => {
    const orig = i.getAttribute("value");
    return parseFloat(i.value) !== parseFloat(orig);
  });
  if (!changed.length) { toast("没有改动", "info"); closeAdmin(); return; }
  $("admin-save").classList.add("loading");
  try {
    const failures = [];
    for (const i of changed) {
      const { res, detail, authFailed } = await apiFetch("/api/admin/price", {
        method: "PUT", headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ kind: i.dataset.kind, key: i.dataset.key, field: i.dataset.field, value: parseFloat(i.value) }),
      }, { authed: true });
      if (authFailed) {
        closeAdmin(); toast("登录已过期，请重新登录", "err");
        return;
      }
      if (!res.ok)   // a 400 (e.g. out-of-range value) must not toast as success
        failures.push(`${i.dataset.key || i.dataset.kind}/${i.dataset.field}: ${detail}`);
    }
    await loadShop();
    closeAdmin();
    if (failures.length)
      toast(`已更新 ${changed.length - failures.length} 项，${failures.length} 项被拒绝：${failures.join("；")}`, "err", 8000);
    else
      toast(`已更新 ${changed.length} 项价格`, "ok");
    if (state.hasQuoted) requestQuote(false);
  } catch (e) {
    toast("保存失败：" + e.message, "err");
  } finally { $("admin-save").classList.remove("loading"); }
}

// ───────────────────────── toast / errors / health ─────────────────────────
function toast(msg, kind = "info", ttl = 3200) {
  const wrap = $("toast-wrap");
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  // esc(): server error details echo user input (e.g. the uploaded *filename*
  // in "unsupported file type") — an innerHTML sink here would be reflected XSS.
  el.innerHTML = `<span class="t-dot"></span><span>${esc(String(msg))}</span>`;
  wrap.appendChild(el);
  setTimeout(() => { el.classList.add("fade-out"); setTimeout(() => el.remove(), 350); }, ttl);
}

async function loadBackends() {
  try {
    const d = await (await fetch("/api/backends")).json();
    const sel = $("backend"), av = d.available || {};
    [...sel.options].forEach((o) => {
      if (o.value === "toolpath" && !av.toolpath) { o.disabled = true; o.textContent += "（未安装）"; }
    });
    const tiers = [];
    if (av.toolpath) tiers.push(av.surface_dropcutter ? "刀路仿真+曲面drop-cutter" : "刀路仿真");
    if (av.freecad) tiers.push("FreeCAD CAM");
    $("backend-hint").textContent = tiers.length
      ? "可用高精度后端：" + tiers.join(" / ")
      : "仅解析快算可用（pip install trimesh shapely 开启刀路仿真）";
  } catch { /* ignore */ }
}

const BACKEND_LABEL = {
  toolpath: "刀路仿真 Toolpath", analytic: "解析 Analytic", freecad: "FreeCAD CAM",
};
function renderEstimator(p) {
  const used = p.estimator?.used;
  const b = $("estimator-badge");
  if (!used) { b.hidden = true; return; }
  b.hidden = false;
  b.textContent = "工时来源：" + (BACKEND_LABEL[used] || used);
  b.className = "badge " + (used === "analytic" ? "info-badge" : "tp-badge");

  const steps = p.plan?.process_steps || [];
  const routing = steps.length
    ? `<div class="ops-title">工艺路线 Process routing</div>` +
      `<table class="routing"><tr><th>#</th><th>工序 Step</th><th>说明</th><th>工时</th><th>范围</th></tr>` +
      steps.map((s) => `<tr><td>${s.step}</td><td>${s.name}</td><td class="muted">${s.detail}</td>` +
        `<td>${s.minutes > 0 ? s.minutes.toFixed(2) + " min" : "—"}</td>` +
        `<td class="tiny muted">${s.scope}</td></tr>`).join("") + `</table>`
    : "";

  const ops = p.plan?.operations || [];
  const el = $("ops-detail");
  if (!ops.length && !routing) { el.innerHTML = ""; return; }
  if (!ops.length) { el.innerHTML = routing; return; }
  const fmt = (o) => {
    if (o.op === "roughing") return `开粗 · Z分层 ${o.levels} 层 · 刀路 ${(o.path_len_mm / 1000).toFixed(2)} m @ ${o.feed_mm_min}mm/min → ${o.minutes}min`;
    if (o.op === "finishing") {
      const surf = o.surface_method === "drop-cutter"
        ? `曲面(drop-cutter ×${o.surface_factor})` : "光面(光栅)";
      return `精加工 · 等高 ${o.levels} 层 · 壁 ${(o.wall_len_mm / 1000).toFixed(2)}m + ${surf} ${(o.raster_len_mm / 1000).toFixed(2)}m → ${o.minutes}min`;
    }
    if (o.op === "drilling") return `钻孔 ${o.holes} 个（螺纹 ${o.threaded}）· 总深 ${o.total_depth_mm}mm → 钻 ${o.drill_minutes} / 攻 ${o.tap_minutes}min`;
    return JSON.stringify(o);
  };
  el.innerHTML = routing +
    `<div class="ops-title">刀路仿真明细 Toolpath detail</div>` +
    ops.map((o) => `<div class="ops-row">${fmt(o)}</div>`).join("");
}

async function loadHealth() {
  try {
    const h = await (await fetch("/api/health")).json();
    $("health").innerHTML = h.brep_kernel
      ? '<span class="dot ok"></span> OCCT 就绪 · STEP/IGES 可解析'
      : '<span class="dot no"></span> 仅 STL 自动解析 · STEP 请手动尺寸';
  } catch { $("health").innerHTML = '<span class="dot no"></span> 离线'; }
}

// ───────────────────────── viewer toolbar ─────────────────────────
function wireToolbar() {
  $("toggle-bbox").addEventListener("click", (e) => {
    e.currentTarget.classList.toggle("active");
    if (state.bbox) state.bbox.visible = e.currentTarget.classList.contains("active");
    invalidate();
  });
  $("toggle-spin").addEventListener("click", (e) => {
    e.currentTarget.classList.toggle("active");
    controls.autoRotate = e.currentTarget.classList.contains("active");
  });
  $("reset-view").addEventListener("click", () => { if (state.geometry) frameDims(state.geometry.dims_mm); });
  // The drop-hint overlay is hidden once .dropzone.loaded — this stays clickable.
  $("reupload").addEventListener("click", () => $("file").click());
}

// ───────────────────────── uploads ─────────────────────────
function wireUploads() {
  const drop = $("drop"), input = $("file");
  $("drop-hint").addEventListener("click", () => input.click());
  input.addEventListener("change", (e) => handleFiles(e.target.files));
  ["dragenter", "dragover"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("drag"); }));
  drop.addEventListener("drop", (e) => handleFiles(e.dataTransfer.files));
}

function main() {
  initViewer();
  wireUploads();
  wireToolbar();
  loadShop();
  loadHealth();
  loadHistory();
  loadBackends();
  loadMe();
  initAI();

  $("add-hole").addEventListener("click", () => addHoleRow());
  $("quote-btn").addEventListener("click", () => requestQuote(true));
  $("compare-btn").addEventListener("click", () => { state.compare = true; requestQuote(true); });
  $("pdf-btn").addEventListener("click", downloadPdf);
  $("hist-refresh").addEventListener("click", loadHistory);
  $("admin-btn").addEventListener("click", openAdmin);
  $("admin-close").addEventListener("click", closeAdmin);
  document.querySelectorAll(".admin-tab").forEach((t) => t.addEventListener("click", () => {
    const name = t.dataset.tab;
    document.querySelectorAll(".admin-tab").forEach((x) => x.classList.toggle("on", x === t));
    document.querySelectorAll(".admin-pane").forEach((p) => p.classList.toggle("on", p.dataset.pane === name));
    if (name === "skills") renderSkills();
  }));
  $("skill-add").addEventListener("click", openSkillAddForm);
  $("admin-save").addEventListener("click", saveAdmin);
  $("admin-modal").addEventListener("click", (e) => { if (e.target.id === "admin-modal") closeAdmin(); });
  $("logout-btn").addEventListener("click", logout);
  $("login-close").addEventListener("click", closeLogin);
  $("login-submit").addEventListener("click", doLogin);
  $("login-modal").addEventListener("click", (e) => { if (e.target.id === "login-modal") closeLogin(); });
  $("login-pass").addEventListener("keydown", (e) => { if (e.key === "Enter") doLogin(); });
  $("cal-submit").addEventListener("click", submitCalibration);

  // Live re-quote on any parameter change (once a first quote exists).
  ["quantity", "finish", "machine", "minwall", "tolerance", "surface_finish", "currency", "fiveaxis", "backend", "units", "customer"].forEach((id) =>
    $(id).addEventListener("change", scheduleLiveQuote));
  $("material").addEventListener("change", scheduleLiveQuote);
  $("addons").addEventListener("change", scheduleLiveQuote);
  ["m-l", "m-w", "m-h", "m-v"].forEach((id) =>
    $(id).addEventListener("input", () => { refreshQuoteEnabled(); scheduleLiveQuote(); }));
}

main();
