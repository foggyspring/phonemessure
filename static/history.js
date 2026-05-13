/* phonemessure — measurement history, exports, snapshot rendering */

const H = (window.PM_HIST = window.PM_HIST || {});

H.items = [];           // [{ id, ts, name, note, mode, label, fields, shape }]
H.sessionId = null;
H.STORAGE_KEY = "pm.history.v1";
H.CAL_KEY = "pm.calibration.v1";

H.load = function () {
  try {
    H.items = JSON.parse(localStorage.getItem(H.STORAGE_KEY) || "[]");
  } catch { H.items = []; }
};

H.save = function () {
  try { localStorage.setItem(H.STORAGE_KEY, JSON.stringify(H.items)); } catch {}
};

H.add = function (entry) {
  entry.id = entry.id || Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  entry.ts = entry.ts || new Date().toISOString();
  H.items.push(entry);
  H.save();
  if (H.sessionId) {
    fetch(`/api/session/${H.sessionId}/history`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(entry),
    }).catch(() => {});
  }
  return entry;
};

H.remove = function (id) {
  H.items = H.items.filter(x => x.id !== id);
  H.save();
};

H.clear = function () {
  H.items = [];
  H.save();
  if (H.sessionId) {
    fetch(`/api/session/${H.sessionId}/history`, { method: "DELETE" }).catch(() => {});
  }
};

// ─── exports ─────────────────────────────────────────────────────────

H.toCSV = function () {
  const rows = [["id", "timestamp", "name", "mode", "value", "note"]];
  for (const it of H.items) {
    rows.push([
      it.id,
      it.ts,
      (it.name || "").replace(/[\r\n,"]/g, " "),
      it.mode,
      (it.label || "").replace(/[\r\n,"]/g, " "),
      (it.note || "").replace(/[\r\n,"]/g, " "),
    ]);
  }
  return rows.map(r => r.map(c => `"${String(c)}"`).join(",")).join("\n");
};

H.toJSON = function () {
  return JSON.stringify({ exported_at: new Date().toISOString(), items: H.items }, null, 2);
};

H.download = function (filename, mime, content) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};

// ─── snapshot (camera frame + burned-in annotations) ─────────────────

H.snapshot = function (video, overlay, gridcv) {
  const W = overlay.width, height = overlay.height;
  const c = document.createElement("canvas");
  c.width = W; c.height = height;
  const ctx = c.getContext("2d");

  // 1. video frame, "cover" cropped to match the overlay
  const vw = video.videoWidth, vh = video.videoHeight;
  if (vw && vh) {
    const arO = W / height, arV = vw / vh;
    let sx = 0, sy = 0, sw = vw, sh = vh;
    if (arV > arO) { sw = vh * arO; sx = (vw - sw) / 2; }
    else            { sh = vw / arO; sy = (vh - sh) / 2; }
    ctx.drawImage(video, sx, sy, sw, sh, 0, 0, W, height);
  } else {
    ctx.fillStyle = "#000"; ctx.fillRect(0, 0, W, height);
  }

  // 2. grid (faint)
  if (gridcv) {
    ctx.globalAlpha = 0.55;
    ctx.drawImage(gridcv, 0, 0);
    ctx.globalAlpha = 1;
  }

  // 3. annotations
  ctx.drawImage(overlay, 0, 0);

  // 4. footer with calibration info & timestamp
  const cal = window.PM.state.calibration;
  const calTxt = !cal ? "uncalibrated"
    : cal.kind === "scale" ? `scale: ${(1 / cal.mmPerPx).toFixed(2)} px/mm`
    : "homography (sheet)";
  ctx.fillStyle = "rgba(11, 15, 20, 0.8)";
  ctx.fillRect(0, height - 32, W, 32);
  ctx.fillStyle = "#22d3ee";
  ctx.font = "12px ui-monospace, SF Mono, monospace";
  ctx.textBaseline = "middle";
  ctx.textAlign = "left";
  ctx.fillText(`phonemessure · ${calTxt}`, 10, height - 16);
  ctx.textAlign = "right";
  ctx.fillStyle = "#94a3b8";
  ctx.fillText(new Date().toLocaleString(), W - 10, height - 16);

  return c.toDataURL("image/png");
};

// ─── render list into DOM ────────────────────────────────────────────

H.renderInto = function (container) {
  if (!H.items.length) {
    container.innerHTML = '<p class="hint">No measurements yet.</p>';
    return;
  }
  container.innerHTML = "";
  for (const it of [...H.items].reverse()) {
    const div = document.createElement("div");
    div.className = "hist-item";
    const left = document.createElement("div");
    left.innerHTML = `
      <div class="h-name">${escapeHTML(it.name || it.mode)}</div>
      <div class="h-val">${escapeHTML(it.label || "—")}</div>
      ${it.note ? `<div class="h-note">${escapeHTML(it.note)}</div>` : ""}
    `;
    const del = document.createElement("button");
    del.className = "h-del"; del.textContent = "×"; del.title = "Delete";
    del.onclick = () => { H.remove(it.id); H.renderInto(container); };
    div.append(left, del);
    container.appendChild(div);
  }
};

function escapeHTML(s) {
  return String(s).replace(/[&<>"']/g, m => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[m]));
}
