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
}

function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}

function disposeObj(o) {
  if (!o) return;
  scene.remove(o);
  o.traverse?.((c) => { c.geometry?.dispose?.(); c.material?.dispose?.(); });
  o.geometry?.dispose?.();
  o.material?.dispose?.();
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
  $("drop").classList.add("loaded");
  $("viewer-toolbar").hidden = false;
}

function recolorMesh(materialKey) {
  if (!state.mesh) return;
  const look = lookForMaterial(materialKey);
  state.mesh.material.color.setHex(look.color);
  state.mesh.material.metalness = look.metal;
  state.mesh.material.roughness = look.rough;
}

// ───────────────────────── upload flow ─────────────────────────
function b64ToArrayBuffer(b64) {
  const bin = atob(b64), len = bin.length, bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
}

async function handleFile(file) {
  if (!file) return;
  state.file = file;
  state.fileBytes = await file.arrayBuffer();
  $("viewer-spinner").classList.remove("hidden");
  $("viewer-meta").innerHTML = `解析中 <b>${esc(file.name)}</b> …`;

  let res;
  try {
    const fd = new FormData();
    fd.append("file", file);
    res = await fetch("/api/parse", { method: "POST", body: fd });
  } catch (e) {
    $("viewer-spinner").classList.add("hidden");
    toast("网络错误：" + e.message, "err");
    return;
  }
  $("viewer-spinner").classList.add("hidden");

  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))).detail || res.statusText;
    toast(detail, "err", 6000);
    $("manual-box").classList.remove("hidden");
    state.geometry = null;
    refreshQuoteEnabled();
    return;
  }
  const data = await res.json();
  state.geometry = data.geometry;
  $("manual-box").classList.add("hidden");

  const ext = file.name.toLowerCase().split(".").pop();
  const loader = new STLLoader();
  let geom = null;
  try {
    if (ext === "stl") geom = loader.parse(state.fileBytes);
    else if (data.preview_stl_b64) geom = loader.parse(b64ToArrayBuffer(data.preview_stl_b64));
  } catch { geom = null; }
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
    `<td><button class="row-del" title="删除">✕</button></td>`;
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

async function requestQuote(save) {
  if (!(state.geometry || manualDims())) return;
  const btn = $("quote-btn");
  if (save) btn.classList.add("loading");
  try {
    const fd = new FormData();
    fd.append("params", JSON.stringify(buildParams(save)));
    if (state.file) fd.append("file", state.file);
    const res = await fetch("/api/quote", { method: "POST", body: fd });
    if (!res.ok) {
      const detail = (await res.json().catch(() => ({}))).detail || res.statusText;
      toast("报价失败：" + detail, "err", 6000);
      return;
    }
    state.lastPayload = await res.json();
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

const DFM_RULES = [
  { re: /风险|变形|断丝|过小|振动|啄钻|peck/i, level: "warn", ico: "⚠️" },
  { re: /无法|不可|超限|必须/i, level: "danger", ico: "⛔" },
];
function classifyDFM(text) {
  for (const r of DFM_RULES) if (r.re.test(text)) return r;
  return { level: "info", ico: "ℹ️" };
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
  $("bignum-sub").innerHTML =
    `× ${r.quantity} 件 · 净额 ${money(q.net_total_cny != null ? q.net_total_cny : r.line_total_cny, cur)}` +
    ` · <b>含税 ${money(grand, cur)}</b>${q.tax_rate ? ` (${q.tax_label || "税"} ${taxPct}%)` : ""}` +
    ` · 交期 ${q.lead_days} 天${valid}`;
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
  const SEV = { high: { c: "warn", ico: "⛔", t: "高" }, medium: { c: "warn", ico: "⚠️", t: "中" },
                low: { c: "info", ico: "ℹ️", t: "低" }, info: { c: "info", ico: "💡", t: "" } };
  const dfm = p.dfm || [];
  const hasRisk = dfm.some((d) => d.severity === "high" || d.severity === "medium");
  const dfmHtml = dfm.map((d) => {
    const s = SEV[d.severity] || SEV.info;
    const badge = s.t ? `<span class="sev-badge ${d.severity}">${s.t}</span>` : "";
    return `<li class="${s.c}"><span class="w-ico">${s.ico}</span><span>${badge}<b>${esc(d.title)}</b> — ${esc(d.detail)}
      <span class="muted tiny">建议：${esc(d.suggestion)}</span></span></li>`;
  }).join("");
  const notes = [...(q.notes || []), ...(pl.notes || [])];
  const noteHtml = notes.map((n) => `<li class="info"><span class="w-ico">·</span><span class="muted">${esc(n)}</span></li>`).join("");
  $("warn-list").innerHTML = (dfmHtml + noteHtml) ||
    `<li class="info"><span class="w-ico">✅</span><span>无明显可加工性风险 No DFM flags</span></li>`;
  if (dfm.length) $("warn-card").querySelector("h3").textContent =
    hasRisk ? "工艺提示 Notes & DFM ⚠" : "工艺提示 Notes & DFM";

  renderEstimator(p);
  renderCalibration(p, isLive);
  $("result-badge").hidden = !isLive;
  const resEl = $("result");
  const wasHidden = resEl.classList.contains("hidden");
  resEl.classList.remove("hidden");
  if (wasHidden) { resEl.classList.add("reveal"); resEl.scrollIntoView({ behavior: "smooth", block: "start" }); }
}

// ───────────────────────── AI copilot ─────────────────────────
const aiState = { history: [], busy: false };

function openAIPanel() { $("ai-panel").classList.remove("hidden"); $("ai-text").focus(); }
function closeAIPanel() { $("ai-panel").classList.add("hidden"); }

async function loadAIStatus() {
  try {
    const s = await (await fetch("/api/ai/status")).json();
    const el = $("ai-status");
    el.textContent = s.live ? `已接入 ${s.provider}` : "离线模拟助手（上线接入真实 LLM）";
    el.classList.toggle("live", !!s.live);
  } catch { $("ai-status").textContent = "状态未知"; }
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
  aiState.busy = true; $("ai-text").value = "";
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
    aiState.history = d.history || aiState.history;
  } catch (e) {
    typing.remove(); appendAIMsg("bot", "网络错误：" + e.message);
  } finally { aiState.busy = false; }
}

function renderAIAction(a) {
  // simple line for now; rich action cards arrive in the next iteration
  appendAIMsg("bot", `🔧 ${a.tool}：${a.summary}`);
}

function initAI() {
  $("ai-fab").addEventListener("click", openAIPanel);
  $("ai-close").addEventListener("click", closeAIPanel);
  $("ai-send").addEventListener("click", sendAI);
  $("ai-text").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendAI(); }
  });
  $("ai-messages").addEventListener("click", (e) => {
    const chip = e.target.closest(".ai-chip");
    if (chip) { $("ai-text").value = chip.dataset.q; sendAI(); }
  });
  loadAIStatus();
}

// ───────────────────────── material comparison ─────────────────────────
function renderCompare(p) {
  const el = $("compare-wrap");
  const rows = p.material_comparison;
  state.compare = false;                 // one-shot; don't slow later quotes
  if (!rows) { el.innerHTML = ""; return; }
  const cur = (state.fx || {}).symbol || "¥";
  const cheapest = rows[0] && rows[0].key;
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
  let h = `<span class="muted tiny">预估运费 Shipping（${L.order_weight_kg} kg）：${money(L.shipping_cny)}</span>`;
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
  el.innerHTML = `<span class="conf-badge ${c.level}" title="${esc(reasons)}">报价置信度 ${c.score}/100 · ${label}</span>`;
}

// ───────────────────────── material suggestions ─────────────────────────
function renderMaterialSuggestions(p) {
  const el = $("mat-suggest");
  const s = p.material_suggestions || [];
  if (!s.length) { el.innerHTML = ""; return; }
  el.innerHTML = `<span class="muted tiny">更省材料（如性能允许 if properties allow）：</span> ` +
    s.map((m) => `<button class="suggest-chip" data-mat="${esc(m.key)}">${esc(m.label.split(" ")[0])} ↓${m.savings_pct}%</button>`).join(" ");
  el.querySelectorAll(".suggest-chip").forEach((b) => b.addEventListener("click", () => {
    $("material").value = b.dataset.mat;
    updateMatPrice(); recolorMesh(b.dataset.mat); requestQuote(true);
  }));
}

// ───────────────────────── lead-time options ─────────────────────────
function renderLeadOptions(q, isLive) {
  const el = $("lead-opts");
  const opts = q.lead_time_options || [];
  if (!opts.length) { el.innerHTML = ""; return; }
  el.innerHTML = `<div class="lead-title">交期选项 Delivery</div>` +
    `<div class="lead-chips">` + opts.map((o) =>
      `<button class="lead-chip${o.selected ? " on" : ""}" data-lead="${esc(o.key)}"${isLive ? " disabled" : ""}>
         <span class="lead-name">${esc(o.label)}</span>
         <span class="lead-days">${o.days} 天</span>
         <span class="lead-price">${money(o.unit_price_cny, q.currency)}/件</span>
       </button>`).join("") + `</div>`;
  el.querySelectorAll(".lead-chip").forEach((b) => b.addEventListener("click", () => {
    const key = b.dataset.lead;
    state.leadTime = key;
    requestQuote(false);   // re-quote with the chosen tier (saved quote)
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
    const r = await fetch("/api/calibration/actual", {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ quote_id: state.lastPayload.id, actual_min: actual }),
    });
    if (r.status === 401 || r.status === 403) { logout(); openLogin(); return; }
    if (!r.ok) { toast("提交失败：" + ((await r.json().catch(() => ({}))).detail || r.status), "err"); return; }
    const d = await r.json();
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
      `<td class="h-price">${money(r.unit_price, r.currency || "CNY")}</td>` +
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
    chip.textContent = "👤 " + state.user;
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
function openAdmin() {
  if (!state.token) { openLogin(); return; }
  if (!state.shop) return;
  const mg = $("admin-materials"); mg.innerHTML = "";
  for (const [k, m] of Object.entries(state.shop.materials)) {
    const d = document.createElement("div"); d.className = "admin-row";
    d.innerHTML = `<label>${m.label}<input type="number" min="0" step="0.5" data-kind="material" data-key="${k}" data-field="price_cny_per_kg" value="${m.price_cny_per_kg}"></label>`;
    mg.appendChild(d);
  }
  const cg = $("admin-machines"); cg.innerHTML = "";
  for (const [k, mc] of Object.entries(state.shop.machines)) {
    const d = document.createElement("div"); d.className = "admin-row";
    d.innerHTML = `<label>${mc.label}<input type="number" min="0" step="5" data-kind="machine" data-key="${k}" data-field="rate_cny_per_hour" value="${mc.rate_cny_per_hour}"></label>`;
    cg.appendChild(d);
  }
  $("admin-modal").classList.remove("hidden");
}
function closeAdmin() { $("admin-modal").classList.add("hidden"); }

async function saveAdmin() {
  const inputs = [...$("admin-modal").querySelectorAll("input[data-kind]")];
  const changed = inputs.filter((i) => {
    const orig = i.getAttribute("value");
    return parseFloat(i.value) !== parseFloat(orig);
  });
  if (!changed.length) { toast("没有改动", "info"); closeAdmin(); return; }
  $("admin-save").classList.add("loading");
  try {
    for (const i of changed) {
      const r = await fetch("/api/admin/price", {
        method: "PUT", headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ kind: i.dataset.kind, key: i.dataset.key, field: i.dataset.field, value: parseFloat(i.value) }),
      });
      if (r.status === 401 || r.status === 403) {
        closeAdmin(); logout(); toast("登录已过期，请重新登录", "err"); openLogin();
        return;
      }
    }
    await loadShop();
    closeAdmin();
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
  const ico = kind === "ok" ? "✅" : kind === "err" ? "⛔" : "ℹ️";
  el.innerHTML = `<span>${ico}</span><span>${msg}</span>`;
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

  const ops = p.plan?.operations || [];
  const el = $("ops-detail");
  if (!ops.length) { el.innerHTML = ""; return; }
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
  el.innerHTML = `<div class="ops-title">刀路仿真明细 Toolpath detail</div>` +
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
  });
  $("toggle-spin").addEventListener("click", (e) => {
    e.currentTarget.classList.toggle("active");
    controls.autoRotate = e.currentTarget.classList.contains("active");
  });
  $("reset-view").addEventListener("click", () => { if (state.geometry) frameDims(state.geometry.dims_mm); });
}

// ───────────────────────── uploads ─────────────────────────
function wireUploads() {
  const drop = $("drop"), input = $("file");
  $("drop-hint").addEventListener("click", () => input.click());
  input.addEventListener("change", (e) => handleFile(e.target.files[0]));
  ["dragenter", "dragover"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("drag"); }));
  drop.addEventListener("drop", (e) => handleFile(e.dataTransfer.files[0]));
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
