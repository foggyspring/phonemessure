/* phonemessure — calibration (client side)
 *
 * Two paths, both produce the same `calibration` object that measure.js
 * consumes (`{kind: "scale", mmPerPx}` or `{kind: "homography", H}`):
 *
 *  A. tap-the-edge — pure client, unchanged.
 *
 *  B. sheet (real ArUco) — the server runs cv2.aruco on a streamed frame
 *     and returns a homography in *work-canvas* pixel coordinates. We
 *     compose that with the work→overlay scale so the resulting H maps
 *     overlay pixels (which is the coord space measure.js works in)
 *     straight to millimetres on the sheet.
 *
 *  C. auto-reference (YOLO-World) — server returns detections + each one's
 *     mm-per-pixel. We let the user pick one to apply as a scale
 *     calibration.
 */

const C = (window.PM_CAL = window.PM_CAL || {});

// Sheet geometry — kept in sync with app/inference/aruco.py
C.SHEET = { W_MM: 160.0, H_MM: 247.0 };

// ─── tap-the-edge ────────────────────────────────────────────────────

C.scaleCalibration = function (p1, p2, mm) {
  const px = Math.hypot(p2.x - p1.x, p2.y - p1.y);
  if (px < 5 || !(mm > 0)) return null;
  return { kind: "scale", mmPerPx: mm / px };
};

// ─── helpers for server-side results ────────────────────────────────

/** Compose a 3×3 work→mm homography with a uniform pixel scale s so that
 *  the result maps `overlay-pixels × s = work-pixels` directly into mm. */
function _composeScale(H, s) {
  // H @ diag(s, s, 1)
  return [
    H[0] * s, H[1] * s, H[2],
    H[3] * s, H[4] * s, H[5],
    H[6] * s, H[7] * s, H[8],
  ];
}

/** Build a calibration object from a server `aruco` event. */
C.calibrationFromAruco = function (msg, overlayWidthPx) {
  if (!msg || !msg.ok || !msg.pose) return null;
  const pose = msg.pose;
  const [workW, _workH] = pose.image_size;
  const s = workW / overlayWidthPx;  // overlay-px * s = work-px
  const Hwork = pose.H;              // 9 numbers, work-px -> mm
  const H = _composeScale(Hwork, s);
  return {
    kind: "homography",
    H,
    refName: "sheet",
    sourceMarkers: pose.marker_centres,
    workSize: pose.image_size,
    undistorted: !!msg.undistorted,
  };
};

/** Convert a YOLO detection (work-canvas coords) into:
 *    - a `scale` calibration (mm/px in *overlay* coords),
 *    - the detection's overlay-coord bbox (so we can draw a confirmation). */
C.calibrationFromYolo = function (det, msg, overlayWidthPx) {
  if (!det || det.mm_per_px == null) return null;
  const [workW, _workH] = msg.image_size;
  const s = workW / overlayWidthPx;       // overlay-px * s = work-px
  const mmPerPxOverlay = det.mm_per_px * s;
  const [x1, y1, x2, y2] = det.box;
  const oBox = [x1 / s, y1 / s, x2 / s, y2 / s];
  return {
    kind: "scale",
    mmPerPx: mmPerPxOverlay,
    refName: det.label,
    refScore: det.score,
    refBox: oBox,
  };
};
