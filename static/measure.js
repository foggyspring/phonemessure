/* phonemessure — measurement engine
 *
 * Holds the active shape being drawn, the list of completed shapes for this
 * frame, and the routines that project image-space pixels into millimeters.
 *
 * Two calibration models are supported:
 *   1. "scale"      — one mm-per-pixel number from a tap-the-edge reference.
 *                     Accurate only on the same plane as the reference and
 *                     when the camera is roughly perpendicular to it.
 *   2. "homography" — full 3×3 plane-to-plane matrix from the four-corner
 *                     calibration sheet. Handles tilted/angled shots.
 */

const M = (window.PM = window.PM || {});

M.state = {
  mode: "line",
  shapes: [],         // completed shapes
  current: null,      // shape under construction
  drag: null,         // { shapeIdx, ptIdx } when editing an existing point
  calibration: null,  // { kind: "scale", mmPerPx } | { kind: "homography", H, refName }
  decimals: 1,
};

// ─── calibration math ────────────────────────────────────────────────

M.toMM = function (p) {
  const cal = M.state.calibration;
  if (!cal) return null;
  if (cal.kind === "scale") {
    return { x: p.x * cal.mmPerPx, y: p.y * cal.mmPerPx };
  }
  // homography: [x', y', w']^T = H * [x, y, 1]^T
  const H = cal.H;
  const w = H[6] * p.x + H[7] * p.y + H[8];
  return {
    x: (H[0] * p.x + H[1] * p.y + H[2]) / w,
    y: (H[3] * p.x + H[4] * p.y + H[5]) / w,
  };
};

M.distMM = function (a, b) {
  const A = M.toMM(a), B = M.toMM(b);
  if (!A || !B) return null;
  return Math.hypot(B.x - A.x, B.y - A.y);
};

M.fmtMM = function (mm) {
  if (mm == null || !isFinite(mm)) return "—";
  const d = M.state.decimals;
  if (mm >= 1000) return (mm / 1000).toFixed(d + 1) + " m";
  if (mm >= 10)   return mm.toFixed(d) + " mm";
  return mm.toFixed(d + 1) + " mm";
};

M.fmtArea = function (mm2) {
  if (mm2 == null || !isFinite(mm2)) return "—";
  const d = M.state.decimals;
  if (mm2 >= 1e6) return (mm2 / 1e6).toFixed(d + 1) + " m²";
  if (mm2 >= 100) return (mm2 / 100).toFixed(d) + " cm²";
  return mm2.toFixed(d) + " mm²";
};

// ─── shape factories ─────────────────────────────────────────────────

M.startShape = function (mode, p) {
  if (mode === "line")    return { mode, pts: [p, p] };
  if (mode === "poly")    return { mode, pts: [p], open: true };
  if (mode === "polygon") return { mode, pts: [p], open: true };
  if (mode === "rect")    return { mode, pts: [p, p] }; // diag corners
  if (mode === "circle")  return { mode, pts: [p] };
  return null;
};

M.continueShape = function (s, p) {
  // tap-to-add for poly/polygon; drag for line/rect/circle handled elsewhere
  if (s.mode === "poly" || s.mode === "polygon") {
    s.pts.push(p);
  } else if (s.mode === "circle" && s.pts.length < 3) {
    s.pts.push(p);
  }
};

M.finishShape = function (s) {
  if (!s) return null;
  if ((s.mode === "poly" || s.mode === "polygon") && s.pts.length < 2) return null;
  if (s.mode === "circle" && s.pts.length < 3) return null;
  if (s.mode === "polygon") s.open = false;
  return s;
};

// ─── measurement computation per shape ───────────────────────────────

function _circleFrom3(p1, p2, p3) {
  const ax = p1.x, ay = p1.y, bx = p2.x, by = p2.y, cx = p3.x, cy = p3.y;
  const d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by));
  if (Math.abs(d) < 1e-9) return null;
  const ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d;
  const uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d;
  const r = Math.hypot(ax - ux, ay - uy);
  return { cx: ux, cy: uy, r };
}

M.computeShape = function (s) {
  const out = { mode: s.mode, label: "", lines: [] };
  if (!s) return out;

  if (s.mode === "line") {
    const d = M.distMM(s.pts[0], s.pts[1]);
    out.label = M.fmtMM(d);
    out.distance_mm = d;
  } else if (s.mode === "poly") {
    let total = 0;
    for (let i = 0; i < s.pts.length - 1; i++) {
      const seg = M.distMM(s.pts[i], s.pts[i + 1]);
      if (seg != null) total += seg;
      out.lines.push(M.fmtMM(seg));
    }
    out.label = M.fmtMM(total);
    out.length_mm = total;
  } else if (s.mode === "polygon") {
    let per = 0;
    const mmPts = s.pts.map(M.toMM);
    if (!mmPts.includes(null)) {
      for (let i = 0; i < mmPts.length; i++) {
        const a = mmPts[i], b = mmPts[(i + 1) % mmPts.length];
        per += Math.hypot(b.x - a.x, b.y - a.y);
      }
      // shoelace area
      let area2 = 0;
      for (let i = 0; i < mmPts.length; i++) {
        const a = mmPts[i], b = mmPts[(i + 1) % mmPts.length];
        area2 += a.x * b.y - b.x * a.y;
      }
      out.perimeter_mm = per;
      out.area_mm2 = Math.abs(area2) / 2;
      out.label = `${M.fmtMM(per)}  ·  ${M.fmtArea(out.area_mm2)}`;
    }
  } else if (s.mode === "rect") {
    const [p1, p2] = s.pts;
    const a = M.toMM(p1), b = M.toMM(p2);
    if (a && b) {
      const w = Math.abs(b.x - a.x), h = Math.abs(b.y - a.y);
      out.width_mm = w;
      out.height_mm = h;
      out.area_mm2 = w * h;
      out.label = `${M.fmtMM(w)} × ${M.fmtMM(h)}  ·  ${M.fmtArea(w * h)}`;
    }
  } else if (s.mode === "circle") {
    if (s.pts.length >= 2) {
      // partial: 2-pt diameter
      const d = M.distMM(s.pts[0], s.pts[1]);
      out.label = `Ø ${M.fmtMM(d || 0)}`;
      out.diameter_mm = d;
    }
    if (s.pts.length === 3) {
      const c = _circleFrom3(s.pts[0], s.pts[1], s.pts[2]);
      if (c) {
        const center = { x: c.cx, y: c.cy };
        const edge = { x: c.cx + c.r, y: c.cy };
        const dmm = 2 * (M.distMM(center, edge) || 0);
        out._circle = c;
        out.diameter_mm = dmm;
        out.circumference_mm = Math.PI * dmm;
        out.area_mm2 = Math.PI * (dmm / 2) ** 2;
        out.label = `Ø ${M.fmtMM(dmm)}  ·  ${M.fmtMM(Math.PI * dmm)}  ·  ${M.fmtArea(out.area_mm2)}`;
      }
    }
  }
  return out;
};

// ─── drawing ─────────────────────────────────────────────────────────

M.drawAll = function (ctx) {
  ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
  for (const s of M.state.shapes) M._drawShape(ctx, s, false);
  if (M.state.current) M._drawShape(ctx, M.state.current, true);
};

M._drawShape = function (ctx, s, active) {
  const C = M.state.calibration ? "#22d3ee" : "#94a3b8";
  ctx.strokeStyle = C;
  ctx.fillStyle = active ? "rgba(34, 211, 238, 0.12)" : "rgba(34, 211, 238, 0.06)";
  ctx.lineWidth = 2;
  ctx.font = "600 14px ui-monospace, SF Mono, Menlo, monospace";

  if (s.mode === "line") {
    ctx.beginPath();
    ctx.moveTo(s.pts[0].x, s.pts[0].y);
    ctx.lineTo(s.pts[1].x, s.pts[1].y);
    ctx.stroke();
    M._endpointDots(ctx, s.pts);
    M._tagAtMid(ctx, s.pts[0], s.pts[1], M.computeShape(s).label);
  } else if (s.mode === "poly") {
    ctx.beginPath();
    s.pts.forEach((p, i) => i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y));
    ctx.stroke();
    M._endpointDots(ctx, s.pts);
    const r = M.computeShape(s);
    if (s.pts.length >= 2) M._tagAt(ctx, s.pts[s.pts.length - 1], r.label);
  } else if (s.mode === "polygon") {
    ctx.beginPath();
    s.pts.forEach((p, i) => i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y));
    if (!s.open) ctx.closePath();
    ctx.fill();
    ctx.stroke();
    M._endpointDots(ctx, s.pts);
    const r = M.computeShape(s);
    if (s.pts.length >= 3) {
      const cx = s.pts.reduce((a, p) => a + p.x, 0) / s.pts.length;
      const cy = s.pts.reduce((a, p) => a + p.y, 0) / s.pts.length;
      M._tagAt(ctx, { x: cx, y: cy }, r.label);
    }
  } else if (s.mode === "rect") {
    const [a, b] = s.pts;
    const x = Math.min(a.x, b.x), y = Math.min(a.y, b.y);
    const w = Math.abs(b.x - a.x), h = Math.abs(b.y - a.y);
    ctx.fillRect(x, y, w, h);
    ctx.strokeRect(x, y, w, h);
    M._endpointDots(ctx, s.pts);
    M._tagAt(ctx, { x: x + w / 2, y: y + h / 2 }, M.computeShape(s).label);
  } else if (s.mode === "circle") {
    M._endpointDots(ctx, s.pts);
    if (s.pts.length === 2) {
      const [a, b] = s.pts;
      const r = Math.hypot(b.x - a.x, b.y - a.y) / 2;
      const cx = (a.x + b.x) / 2, cy = (a.y + b.y) / 2;
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      M._tagAt(ctx, { x: cx, y: cy }, M.computeShape(s).label);
    } else if (s.pts.length === 3) {
      const r = M.computeShape(s);
      if (r._circle) {
        ctx.beginPath();
        ctx.arc(r._circle.cx, r._circle.cy, r._circle.r, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
        M._tagAt(ctx, { x: r._circle.cx, y: r._circle.cy }, r.label);
      }
    }
  }
};

M._endpointDots = function (ctx, pts) {
  ctx.save();
  ctx.fillStyle = "#22d3ee";
  ctx.strokeStyle = "#0b0f14";
  ctx.lineWidth = 2;
  for (const p of pts) {
    ctx.beginPath();
    ctx.arc(p.x, p.y, 8, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }
  ctx.restore();
};

M._tagAtMid = function (ctx, a, b, text) {
  M._tagAt(ctx, { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }, text);
};

M._tagAt = function (ctx, p, text) {
  if (!text) return;
  ctx.save();
  const pad = 6;
  const m = ctx.measureText(text);
  const w = m.width + pad * 2;
  const h = 22;
  const x = p.x - w / 2;
  const y = p.y - h - 12;
  ctx.fillStyle = "rgba(11,15,20,0.85)";
  ctx.strokeStyle = "#22d3ee";
  ctx.lineWidth = 1;
  M._roundRect(ctx, x, y, w, h, 6);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = "#22d3ee";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, p.x, y + h / 2);
  ctx.restore();
};

M._roundRect = function (ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
};

// ─── point picking / hit-test for drag ───────────────────────────────

M.hitTest = function (p, hitRadius = 22) {
  for (let i = M.state.shapes.length - 1; i >= 0; i--) {
    const s = M.state.shapes[i];
    for (let j = 0; j < s.pts.length; j++) {
      const q = s.pts[j];
      if (Math.hypot(q.x - p.x, q.y - p.y) <= hitRadius) {
        return { shapeIdx: i, ptIdx: j };
      }
    }
  }
  if (M.state.current) {
    for (let j = 0; j < M.state.current.pts.length; j++) {
      const q = M.state.current.pts[j];
      if (Math.hypot(q.x - p.x, q.y - p.y) <= hitRadius) {
        return { shapeIdx: -1, ptIdx: j };
      }
    }
  }
  return null;
};

M.movePoint = function (drag, p) {
  const shape = drag.shapeIdx === -1
    ? M.state.current
    : M.state.shapes[drag.shapeIdx];
  if (shape) shape.pts[drag.ptIdx] = p;
};

M.undo = function () {
  if (M.state.current && M.state.current.pts.length > 1) {
    M.state.current.pts.pop();
    if (M.state.current.pts.length === 0) M.state.current = null;
    return;
  }
  if (M.state.shapes.length) M.state.shapes.pop();
};

M.clear = function () {
  M.state.shapes = [];
  M.state.current = null;
  M.state.drag = null;
};
