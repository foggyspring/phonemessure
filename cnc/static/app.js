import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { STLLoader } from "three/addons/loaders/STLLoader.js";

// ───────────────────────── state ─────────────────────────
const state = {
  file: null,          // File object the user uploaded
  fileBytes: null,     // ArrayBuffer of that file
  geometry: null,      // parsed geometry metrics from /api/parse
  shop: null,          // /api/materials response
  lastPayload: null,   // last /api/quote result (for the PDF button)
  mesh: null,          // current THREE mesh in the scene
};

const $ = (id) => document.getElementById(id);

// ───────────────────────── 3D viewer ─────────────────────────
let scene, camera, renderer, controls;

function initViewer() {
  const host = $("viewer");
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0d1117);

  camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100000);
  camera.position.set(120, 90, 160);

  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio);
  host.appendChild(renderer.domElement);

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;

  scene.add(new THREE.HemisphereLight(0xffffff, 0x202a36, 1.05));
  const key = new THREE.DirectionalLight(0xffffff, 1.1);
  key.position.set(1, 1.4, 1);
  scene.add(key);
  const fill = new THREE.DirectionalLight(0x88aaff, 0.4);
  fill.position.set(-1, -0.5, -1);
  scene.add(fill);

  const grid = new THREE.GridHelper(400, 40, 0x30363d, 0x21262d);
  grid.position.y = -0.01;
  scene.add(grid);

  resize();
  window.addEventListener("resize", resize);
  animate();
}

function resize() {
  const host = $("viewer");
  const w = host.clientWidth || 600;
  const h = host.clientHeight || 420;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}

function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}

function clearMesh() {
  if (state.mesh) {
    scene.remove(state.mesh);
    state.mesh.geometry?.dispose?.();
    state.mesh.material?.dispose?.();
    state.mesh = null;
  }
}

function frameObject(obj) {
  const box = new THREE.Box3().setFromObject(obj);
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  obj.position.sub(center); // recenter at origin

  const maxDim = Math.max(size.x, size.y, size.z) || 50;
  const dist = maxDim * 2.4;
  camera.position.set(dist * 0.7, dist * 0.55, dist);
  camera.near = maxDim / 100;
  camera.far = maxDim * 100;
  camera.updateProjectionMatrix();
  controls.target.set(0, 0, 0);
  controls.update();

  const grid = scene.children.find((c) => c.type === "GridHelper");
  if (grid) {
    grid.position.y = -size.y / 2 - 1;
    grid.scale.setScalar(Math.max(1, maxDim / 200));
  }
}

function showMesh(geometry) {
  clearMesh();
  geometry.computeVertexNormals();
  const mat = new THREE.MeshStandardMaterial({
    color: 0xb6c2cf, metalness: 0.65, roughness: 0.35, flatShading: false,
  });
  const mesh = new THREE.Mesh(geometry, mat);
  const edges = new THREE.LineSegments(
    new THREE.EdgesGeometry(geometry, 35),
    new THREE.LineBasicMaterial({ color: 0x1f6feb, transparent: true, opacity: 0.25 })
  );
  mesh.add(edges);
  scene.add(mesh);
  state.mesh = mesh;
  frameObject(mesh);
  $("drop").classList.add("loaded");
}

function showBBox(dims) {
  // STEP with no client geometry: show the bounding box as a wireframe.
  clearMesh();
  const [x, y, z] = dims;
  const geo = new THREE.BoxGeometry(x, y, z);
  const mesh = new THREE.Mesh(
    geo,
    new THREE.MeshStandardMaterial({ color: 0x1f6feb, transparent: true, opacity: 0.12 })
  );
  const edges = new THREE.LineSegments(
    new THREE.EdgesGeometry(geo),
    new THREE.LineBasicMaterial({ color: 0x1f6feb })
  );
  mesh.add(edges);
  scene.add(mesh);
  state.mesh = mesh;
  frameObject(mesh);
  $("drop").classList.add("loaded");
}

// ───────────────────────── upload flow ─────────────────────────
function b64ToArrayBuffer(b64) {
  const bin = atob(b64);
  const len = bin.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
}

async function handleFile(file) {
  if (!file) return;
  clearError();
  state.file = file;
  state.fileBytes = await file.arrayBuffer();
  $("viewer-meta").innerHTML = `解析中 parsing <b>${file.name}</b> …`;

  const fd = new FormData();
  fd.append("file", file);
  let res;
  try {
    res = await fetch("/api/parse", { method: "POST", body: fd });
  } catch (e) {
    showError("网络错误 network error: " + e.message);
    return;
  }
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))).detail || res.statusText;
    // STEP without kernel etc. -> let the user fall back to manual dims.
    showError(detail);
    $("manual-box").classList.remove("hidden");
    state.geometry = null;
    refreshQuoteEnabled();
    return;
  }
  const data = await res.json();
  state.geometry = data.geometry;
  $("manual-box").classList.add("hidden");

  // Render: native STL locally; tessellated STEP from server; else bbox.
  const ext = file.name.toLowerCase().split(".").pop();
  const loader = new STLLoader();
  try {
    if (ext === "stl") {
      showMesh(loader.parse(state.fileBytes));
    } else if (data.preview_stl_b64) {
      showMesh(loader.parse(b64ToArrayBuffer(data.preview_stl_b64)));
    } else {
      showBBox(data.geometry.dims_mm);
    }
  } catch (e) {
    showBBox(data.geometry.dims_mm);
  }

  const g = data.geometry;
  $("viewer-meta").innerHTML =
    `<b>${file.name}</b> · ${data.source_format.toUpperCase()} · ` +
    `外形 ${g.dims_mm.map((v) => v.toFixed(1)).join(" × ")} mm · ` +
    `体积 ${g.volume_cm3} cm³ · 表面积 ${g.area_cm2} cm² · ` +
    `复杂度 ${(g.complexity * 100).toFixed(0)}%`;
  refreshQuoteEnabled();
}

// ───────────────────────── materials / form ─────────────────────────
async function loadShop() {
  const res = await fetch("/api/materials");
  state.shop = await res.json();

  const matSel = $("material");
  matSel.innerHTML = "";
  for (const [k, m] of Object.entries(state.shop.materials)) {
    const o = document.createElement("option");
    o.value = k;
    o.textContent = m.label;
    matSel.appendChild(o);
  }
  const machSel = $("machine");
  for (const [k, mc] of Object.entries(state.shop.machines)) {
    const o = document.createElement("option");
    o.value = k;
    o.textContent = `${mc.label} (¥${mc.rate_cny_per_hour}/h)`;
    machSel.appendChild(o);
  }
  matSel.addEventListener("change", refreshFinishes);
  refreshFinishes();
}

function refreshFinishes() {
  const mat = state.shop.materials[$("material").value];
  const finSel = $("finish");
  const prev = finSel.value;
  finSel.innerHTML = "";
  for (const key of mat.finish_ok) {
    const o = document.createElement("option");
    o.value = key;
    o.textContent = state.shop.finishes[key]?.label || key;
    finSel.appendChild(o);
  }
  if ([...finSel.options].some((o) => o.value === prev)) finSel.value = prev;
}

// ───────────────────────── holes table ─────────────────────────
function addHoleRow(d = 6, depth = 10, count = 1, threaded = false) {
  const tb = $("holes-table").querySelector("tbody");
  const tr = document.createElement("tr");
  tr.innerHTML = `
    <td><input type="number" class="h-d" min="0" step="0.1" value="${d}"></td>
    <td><input type="number" class="h-depth" min="0" step="0.1" value="${depth}"></td>
    <td><input type="number" class="h-count" min="1" step="1" value="${count}"></td>
    <td style="text-align:center"><input type="checkbox" class="h-thread" ${threaded ? "checked" : ""}></td>
    <td><button class="row-del">×</button></td>`;
  tr.querySelector(".row-del").addEventListener("click", () => tr.remove());
  tb.appendChild(tr);
}

function collectHoles() {
  const rows = [...$("holes-table").querySelectorAll("tbody tr")];
  return rows.map((tr) => ({
    diameter_mm: parseFloat(tr.querySelector(".h-d").value) || 0,
    depth_mm: parseFloat(tr.querySelector(".h-depth").value) || 0,
    count: parseInt(tr.querySelector(".h-count").value) || 1,
    threaded: tr.querySelector(".h-thread").checked,
  })).filter((h) => h.diameter_mm > 0);
}

// ───────────────────────── quote ─────────────────────────
function manualDims() {
  const l = parseFloat($("m-l").value);
  const w = parseFloat($("m-w").value);
  const h = parseFloat($("m-h").value);
  const v = parseFloat($("m-v").value);
  if (l > 0 && w > 0 && h > 0) {
    return { length_mm: l, width_mm: w, height_mm: h, volume_mm3: v > 0 ? v : null };
  }
  return null;
}

function refreshQuoteEnabled() {
  const ok = state.geometry !== null || manualDims() !== null;
  $("quote-btn").disabled = !ok;
}

function buildParams() {
  const p = {
    part_name: state.file ? state.file.name.replace(/\.[^.]+$/, "") : "part",
    material: $("material").value,
    quantity: parseInt($("quantity").value) || 1,
    finish: $("finish").value,
    machine: $("machine").value || null,
    tight_tolerance: $("tight").checked,
    requires_5axis: $("fiveaxis").checked,
    rush: $("rush").checked,
    holes: collectHoles(),
  };
  const mw = parseFloat($("minwall").value);
  if (mw > 0) p.min_wall_mm = mw;
  if (!state.file) {
    const md = manualDims();
    if (md) p.manual_dims = md;
  }
  return p;
}

async function getQuote() {
  clearError();
  const btn = $("quote-btn");
  btn.disabled = true;
  btn.textContent = "计算中 …";
  try {
    const fd = new FormData();
    fd.append("params", JSON.stringify(buildParams()));
    if (state.file) fd.append("file", state.file);
    const res = await fetch("/api/quote", { method: "POST", body: fd });
    if (!res.ok) {
      const detail = (await res.json().catch(() => ({}))).detail || res.statusText;
      showError("报价失败: " + detail);
      return;
    }
    state.lastPayload = await res.json();
    renderResult(state.lastPayload);
    loadHistory();
  } catch (e) {
    showError("网络错误: " + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "生成报价 Get Quote";
    refreshQuoteEnabled();
  }
}

// ───────────────────────── render result ─────────────────────────
function kv(rows) {
  return rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("");
}
const money = (v, cur = "CNY") => (cur === "CNY" ? "¥" : cur + " ") + Number(v).toFixed(2);

function renderResult(p) {
  const cur = p.quote.currency;
  const g = p.geometry, pl = p.plan, q = p.quote, r = q.requested;

  $("geo-table").innerHTML = kv([
    ["外形 Bounding box", g.dims_mm.map((v) => v.toFixed(1)).join(" × ") + " mm"],
    ["体积 Volume", g.volume_cm3 + " cm³"],
    ["表面积 Surface", g.area_cm2 + " cm²"],
    ["毛坯填充 Fill", g.bbox_fill_pct != null ? g.bbox_fill_pct + " %" : "—"],
    ["复杂度 Complexity", (g.complexity * 100).toFixed(0) + " %"],
    ["三角面 Triangles", g.triangles || "—"],
  ]);

  $("plan-table").innerHTML = kv([
    ["机床 Machine", pl.machine_label],
    ["毛坯 Stock", [pl.stock.length_mm, pl.stock.width_mm, pl.stock.height_mm].map((v) => v.toFixed(1)).join(" × ") + " mm"],
    ["去除体积 Removed", pl.removed_volume_cm3 + " cm³"],
    ["有效 MRR", pl.effective_mrr_cm3_min + " cm³/min"],
    ["装夹 Setups", pl.setups],
    ["刀具 Tools", pl.tools],
    ["开粗 Roughing", pl.times.roughing_min.toFixed(1) + " min"],
    ["精加工 Finishing", pl.times.finishing_min.toFixed(1) + " min"],
    ["钻孔 Drilling", pl.times.drilling_min.toFixed(1) + " min"],
    ["攻丝 Tapping", pl.times.tapping_min.toFixed(1) + " min"],
    ["单件机时 Cycle", "<b>" + pl.times.per_part_min.toFixed(1) + " min</b>"],
    ["一次性编程 One-time", q.one_time_cny.toFixed(0) + " 分钟摊销"],
  ]);

  $("bignum").textContent = money(r.unit_price_cny, cur);
  $("cost-table").innerHTML = kv([
    ["材料费 Material", money(r.material_cny, cur)],
    ["加工费 Machining", money(r.machining_cny, cur)],
    ["表面处理 Finishing", money(r.finish_variable_cny, cur)],
    ["编程摊销 Setup/ea", money(r.amortized_one_time_cny, cur)],
    ["单件成本 Unit cost", money(r.unit_cost_cny, cur)],
    [`利润率 Margin ${(r.margin * 100).toFixed(0)}%`, ""],
    ["数量 Qty", r.quantity],
    ["<b>批量总价 Total</b>", "<b>" + money(r.line_total_cny, cur) + "</b>"],
  ]);

  let rows = `<tr><th>数量 Qty</th><th>单价 Unit</th><th>总价 Total</th></tr>`;
  for (const t of q.tiers) {
    const active = t.quantity === r.quantity ? ' class="active"' : "";
    rows += `<tr${active}><td>${t.quantity}</td><td>${money(t.unit_price_cny, cur)}</td><td>${money(t.line_total_cny, cur)}</td></tr>`;
  }
  $("tier-table").innerHTML = rows;

  const notes = [...(p.warnings || []), ...(q.notes || []), ...(pl.notes || [])];
  $("warn-list").innerHTML = notes.map((n) => `<li>${n}</li>`).join("") || "<li>无 None</li>";
  if (q.lead_days) {
    $("warn-list").innerHTML += `<li>交期 Lead time: <b>${q.lead_days}</b> 天${q.rush ? " (加急)" : ""}</li>`;
  }

  $("result").classList.remove("hidden");
  $("result").scrollIntoView({ behavior: "smooth", block: "start" });
}

// ───────────────────────── history ─────────────────────────
async function loadHistory() {
  let rows;
  try {
    rows = (await (await fetch("/api/quotes?limit=12")).json()).quotes || [];
  } catch { return; }
  const tb = $("hist-table").querySelector("tbody");
  $("hist-empty").classList.toggle("hidden", rows.length > 0);
  tb.innerHTML = "";
  for (const r of rows) {
    const tr = document.createElement("tr");
    const when = (r.created_at || "").replace("T", " ").slice(5, 16);
    tr.innerHTML =
      `<td>${r.part_name || "part"}<div class="h-id">${r.id}</div></td>` +
      `<td>${r.material}×${r.quantity}<br><span class="muted tiny">${when}</span></td>` +
      `<td class="h-price">${money(r.unit_price, r.currency || "CNY")}</td>` +
      `<td class="h-pdf"><a href="/api/quotes/${r.id}/pdf" target="_blank">PDF</a></td>`;
    tr.addEventListener("click", (e) => {
      if (e.target.tagName === "A") return; // let the PDF link work
      reopenQuote(r.id);
    });
    tb.appendChild(tr);
  }
}

async function reopenQuote(id) {
  try {
    const res = await fetch(`/api/quotes/${id}`);
    if (!res.ok) return;
    state.lastPayload = await res.json();
    renderResult(state.lastPayload);
  } catch { /* ignore */ }
}

async function downloadPdf() {
  if (!state.lastPayload) return;
  const btn = $("pdf-btn");
  btn.disabled = true;
  btn.textContent = "生成中 …";
  try {
    const res = await fetch("/api/quote/pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state.lastPayload),
    });
    if (!res.ok) { showError("PDF 失败: " + res.statusText); return; }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = (state.lastPayload.input.part_name || "quote") + "_quote.pdf";
    a.click();
    URL.revokeObjectURL(url);
  } finally {
    btn.disabled = false;
    btn.textContent = "下载 PDF 报价单";
  }
}

// ───────────────────────── errors / health ─────────────────────────
function showError(msg) {
  const el = $("error");
  el.textContent = msg;
  el.classList.remove("hidden");
}
function clearError() { $("error").classList.add("hidden"); }

async function loadHealth() {
  try {
    const h = await (await fetch("/api/health")).json();
    $("health").innerHTML = h.brep_kernel
      ? '<span class="ok">● OCCT 内核就绪</span> STEP/IGES 可解析'
      : '<span class="no">● 无 OCCT 内核</span> 仅 STL 自动解析，STEP 请手动输入尺寸';
  } catch { /* ignore */ }
}

// ───────────────────────── wire-up ─────────────────────────
function wireUploads() {
  const drop = $("drop");
  const input = $("file");
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
  loadShop();
  loadHealth();
  loadHistory();
  $("add-hole").addEventListener("click", () => addHoleRow());
  $("quote-btn").addEventListener("click", getQuote);
  $("pdf-btn").addEventListener("click", downloadPdf);
  $("hist-refresh").addEventListener("click", loadHistory);
  ["m-l", "m-w", "m-h"].forEach((id) => $(id).addEventListener("input", refreshQuoteEnabled));
}

main();
