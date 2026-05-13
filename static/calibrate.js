/* phonemessure — calibration
 *
 * Two paths:
 *
 *  A. tap-the-edge (reference object of known length)
 *     User picks a preset or custom mm value, taps the two endpoints of the
 *     reference edge in the live frame; the ratio between mm and pixel
 *     distance becomes our scale. Quick but assumes (a) the object lies in
 *     the same plane as whatever else you'll measure, and (b) the camera is
 *     roughly perpendicular to that plane.
 *
 *  B. sheet (4-corner fiducial)
 *     Print /api/aruco-sheet.pdf on A4, place it in view, run blob detection
 *     on the captured frame, pick the 4 large dark squares, sort them by
 *     image position, and solve for a full 3×3 homography from image pixels
 *     to mm-on-the-sheet. After this the sheet's whole plane is correct even
 *     at oblique angles.
 */

const C = (window.PM_CAL = window.PM_CAL || {});

// Sheet geometry — keep in sync with app/aruco_pdf.py
C.SHEET = {
  W_MM: 160.0,  // distance between marker centers, long axis
  H_MM: 247.0,  // short axis
};

// ─── linear algebra (just enough) ────────────────────────────────────

function solve(A, b) {
  // Gaussian elimination with partial pivoting on a small square system.
  const n = b.length;
  const M = A.map((row, i) => [...row, b[i]]);
  for (let i = 0; i < n; i++) {
    let piv = i;
    for (let r = i + 1; r < n; r++) {
      if (Math.abs(M[r][i]) > Math.abs(M[piv][i])) piv = r;
    }
    if (Math.abs(M[piv][i]) < 1e-12) return null;
    if (piv !== i) [M[i], M[piv]] = [M[piv], M[i]];
    for (let r = i + 1; r < n; r++) {
      const f = M[r][i] / M[i][i];
      for (let c = i; c <= n; c++) M[r][c] -= f * M[i][c];
    }
  }
  const x = new Array(n).fill(0);
  for (let i = n - 1; i >= 0; i--) {
    let s = M[i][n];
    for (let j = i + 1; j < n; j++) s -= M[i][j] * x[j];
    x[i] = s / M[i][i];
  }
  return x;
}

/**
 * Solve homography H such that
 *    (X, Y, 1)^T  ~  H * (x, y, 1)^T
 * given 4 src→dst correspondences. Returns 9 numbers (row-major) with
 * H[8] = 1, or null on degeneracy.
 */
C.homographyFromQuad = function (src, dst) {
  if (src.length !== 4 || dst.length !== 4) return null;
  const A = [];
  const b = [];
  for (let i = 0; i < 4; i++) {
    const { x, y } = src[i];
    const { x: X, y: Y } = dst[i];
    A.push([x, y, 1, 0, 0, 0, -X * x, -X * y]);
    b.push(X);
    A.push([0, 0, 0, x, y, 1, -Y * x, -Y * y]);
    b.push(Y);
  }
  const h = solve(A, b);
  if (!h) return null;
  return [h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7], 1];
};

// ─── reference-object (tap-the-edge) ─────────────────────────────────

C.scaleCalibration = function (p1, p2, mm) {
  const px = Math.hypot(p2.x - p1.x, p2.y - p1.y);
  if (px < 5 || !(mm > 0)) return null;
  return { kind: "scale", mmPerPx: mm / px };
};

// ─── sheet fiducial detection ────────────────────────────────────────

/** Grab the current camera frame into a downscaled work canvas.
 *  Returns { canvas, scale } where scale converts work-canvas pixels back
 *  to overlay-canvas pixels (which is what the measurement engine uses). */
C.grabWorkFrame = function (video, overlay, maxW = 720) {
  const vw = video.videoWidth, vh = video.videoHeight;
  if (!vw || !vh) return null;
  // We render the video stream "object-fit: cover" into the overlay. To make
  // detection coordinates align with the overlay we use overlay-aspect.
  const ow = overlay.width, oh = overlay.height;
  const scale = Math.min(maxW / ow, 1);
  const W = Math.round(ow * scale);
  const H = Math.round(oh * scale);

  const c = document.createElement("canvas");
  c.width = W; c.height = H;
  const ctx = c.getContext("2d", { willReadFrequently: true });

  // Match the cover-style transform that the <video> element uses.
  const arO = ow / oh, arV = vw / vh;
  let sx = 0, sy = 0, sw = vw, sh = vh;
  if (arV > arO) {
    sw = vh * arO;
    sx = (vw - sw) / 2;
  } else {
    sh = vw / arO;
    sy = (vh - sh) / 2;
  }
  ctx.drawImage(video, sx, sy, sw, sh, 0, 0, W, H);
  return { canvas: c, ctx, scale };
};

/** Threshold + connected-component blob extraction. Returns blobs sorted
 *  by area descending. Each blob has { area, cx, cy, x0, y0, x1, y1 }
 *  in work-canvas pixel coordinates. */
C.findDarkBlobs = function (workCanvas) {
  const W = workCanvas.width, H = workCanvas.height;
  const ctx = workCanvas.getContext("2d");
  const img = ctx.getImageData(0, 0, W, H);
  const px = img.data;

  // Build a luminance + threshold pass. Otsu on a histogram.
  const hist = new Uint32Array(256);
  const lum = new Uint8Array(W * H);
  for (let i = 0, j = 0; i < px.length; i += 4, j++) {
    const y = (px[i] * 299 + px[i + 1] * 587 + px[i + 2] * 114) / 1000 | 0;
    lum[j] = y;
    hist[y]++;
  }
  // Otsu's method
  let sum = 0;
  for (let i = 0; i < 256; i++) sum += i * hist[i];
  let sumB = 0, wB = 0, maxVar = 0, thr = 128;
  const total = W * H;
  for (let t = 0; t < 256; t++) {
    wB += hist[t];
    if (!wB) continue;
    const wF = total - wB;
    if (!wF) break;
    sumB += t * hist[t];
    const mB = sumB / wB;
    const mF = (sum - sumB) / wF;
    const v = wB * wF * (mB - mF) * (mB - mF);
    if (v > maxVar) { maxVar = v; thr = t; }
  }
  // Bias darker (markers are solid black on white)
  thr = Math.max(40, Math.min(thr - 10, 200));

  // BFS connected components of dark pixels
  const bin = new Uint8Array(W * H);
  for (let i = 0; i < lum.length; i++) bin[i] = lum[i] < thr ? 1 : 0;

  const seen = new Uint8Array(W * H);
  const blobs = [];
  const stack = new Int32Array(W * H);
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const idx = y * W + x;
      if (!bin[idx] || seen[idx]) continue;
      let sp = 0;
      stack[sp++] = idx;
      seen[idx] = 1;
      let area = 0, sxs = 0, sys = 0;
      let x0 = x, y0 = y, x1 = x, y1 = y;
      while (sp > 0) {
        const cur = stack[--sp];
        const cx = cur % W, cy = (cur / W) | 0;
        area++;
        sxs += cx; sys += cy;
        if (cx < x0) x0 = cx; if (cx > x1) x1 = cx;
        if (cy < y0) y0 = cy; if (cy > y1) y1 = cy;
        // 4-neighbour
        if (cx > 0)     { const n = cur - 1; if (bin[n] && !seen[n]) { seen[n]=1; stack[sp++]=n; } }
        if (cx < W - 1) { const n = cur + 1; if (bin[n] && !seen[n]) { seen[n]=1; stack[sp++]=n; } }
        if (cy > 0)     { const n = cur - W; if (bin[n] && !seen[n]) { seen[n]=1; stack[sp++]=n; } }
        if (cy < H - 1) { const n = cur + W; if (bin[n] && !seen[n]) { seen[n]=1; stack[sp++]=n; } }
      }
      if (area < 80) continue;
      blobs.push({
        area, cx: sxs / area, cy: sys / area,
        x0, y0, x1, y1,
        w: x1 - x0 + 1, h: y1 - y0 + 1,
      });
    }
  }
  blobs.sort((a, b) => b.area - a.area);
  return blobs;
};

/** Pick the four corner-marker blobs out of a set of candidates.
 *  Heuristic: square-ish, large, roughly the same size, and forming a
 *  quadrilateral that covers most of the image. */
C.pickFourMarkers = function (blobs, imgW, imgH) {
  // Filter: square-ish, filled-ness, not too small relative to image
  const minArea = (imgW * imgH) * 0.0005;  // ≥ ~0.05% of image
  const cands = blobs.filter(b => {
    if (b.area < minArea) return false;
    const ratio = b.w / b.h;
    if (ratio < 0.6 || ratio > 1.6) return false;
    const filled = b.area / (b.w * b.h);
    return filled > 0.55; // solid squares
  }).slice(0, 12);

  if (cands.length < 4) return null;

  // Among top candidates, find 4 whose sizes are similar (within ±30%).
  // Greedy: start from largest, accept others within tolerance.
  const seed = cands[0];
  const close = cands.filter(b => b.area >= seed.area * 0.45 && b.area <= seed.area * 1.6);
  if (close.length < 4) return null;
  const four = close.slice(0, 4);

  // Sort into TL/TR/BR/BL by image position.
  // Use sum (x+y) for TL (min) / BR (max), diff (x-y) for TR (max) / BL (min)
  const byDiag = [...four].sort((a, b) => (a.cx + a.cy) - (b.cx + b.cy));
  const TL = byDiag[0];
  const BR = byDiag[3];
  const remaining = [byDiag[1], byDiag[2]];
  const TR = remaining[0].cx > remaining[1].cx ? remaining[0] : remaining[1];
  const BL = remaining[0] === TR ? remaining[1] : remaining[0];

  return { TL, TR, BR, BL };
};

/** End-to-end: from a video element, return a homography calibration or
 *  null with a reason string in `.reason`. */
C.detectSheet = function (video, overlay) {
  const grab = C.grabWorkFrame(video, overlay);
  if (!grab) return { ok: false, reason: "camera not ready" };

  const blobs = C.findDarkBlobs(grab.canvas);
  const corners = C.pickFourMarkers(blobs, grab.canvas.width, grab.canvas.height);
  if (!corners) return { ok: false, reason: "couldn't find 4 corner markers" };

  // Convert work-canvas coords back to overlay coords.
  const s = 1 / grab.scale;
  const toOverlay = (b) => ({ x: b.cx * s, y: b.cy * s });

  const src = [
    toOverlay(corners.TL),
    toOverlay(corners.TR),
    toOverlay(corners.BR),
    toOverlay(corners.BL),
  ];
  const dst = [
    { x: 0,             y: 0 },
    { x: C.SHEET.W_MM,  y: 0 },
    { x: C.SHEET.W_MM,  y: C.SHEET.H_MM },
    { x: 0,             y: C.SHEET.H_MM },
  ];
  const H = C.homographyFromQuad(src, dst);
  if (!H) return { ok: false, reason: "homography solve failed" };

  return {
    ok: true,
    calibration: { kind: "homography", H, refName: "sheet", srcCorners: src },
  };
};
