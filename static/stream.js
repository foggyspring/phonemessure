/* phonemessure — WebSocket frame transport
 *
 * Captures the current camera frame into an offscreen canvas matched to the
 * overlay's geometry (same "cover" crop the user sees), JPEG-encodes it,
 * and ships it to the server over /ws/{sid}. Inbound JSON events are
 * dispatched to handlers registered with `on(event, handler)`.
 *
 * The server only keeps the most recent frame per session, so we don't
 * worry about back-pressure: drop old frames, send fresh ones at ~6 fps.
 */

const S = (window.PM_STREAM = window.PM_STREAM || {});

S.sock = null;
S.sid = null;
S.connected = false;
S._handlers = new Map();   // event -> [fn]
S._timer = null;
S._sendBusy = false;
S.fps = 6;
S.jpegQuality = 0.72;
S.streaming = false;
S._buf = document.createElement("canvas");

S.on = function (event, fn) {
  if (!S._handlers.has(event)) S._handlers.set(event, []);
  S._handlers.get(event).push(fn);
};

S._dispatch = function (msg) {
  const arr = S._handlers.get(msg.event);
  if (!arr) return;
  for (const fn of arr) {
    try { fn(msg); } catch (e) { console.error(e); }
  }
};

S.off = function (event, fn) {
  const arr = S._handlers.get(event);
  if (!arr) return;
  S._handlers.set(event, arr.filter(h => h !== fn));
};

S.connect = function (sid) {
  S.sid = sid;
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${proto}//${location.host}/ws/${encodeURIComponent(sid)}`;
  const sock = new WebSocket(url);
  sock.binaryType = "arraybuffer";

  sock.onopen = () => {
    S.connected = true;
    S._dispatch({ event: "open" });
  };
  sock.onclose = () => {
    S.connected = false;
    S._dispatch({ event: "close" });
    // Reconnect after 1.5s
    if (S.sid) setTimeout(() => S.connect(S.sid), 1500);
  };
  sock.onerror = (e) => {
    S._dispatch({ event: "error", reason: "websocket error" });
  };
  sock.onmessage = (ev) => {
    if (typeof ev.data !== "string") return;
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    S._dispatch(msg);
  };

  S.sock = sock;
};

S.send = function (cmd, extra) {
  if (!S.sock || S.sock.readyState !== 1) return false;
  S.sock.send(JSON.stringify({ cmd, ...(extra || {}) }));
  return true;
};

/** Capture one frame from `video` matched to `overlay`'s aspect/size and
 *  send it as binary JPEG. Returns the work-canvas size used.
 *
 *  `opts.width`   — output width in pixels (default 640 for the live loop;
 *                   1024 for refine taps; 1280 for ArUco detection bursts)
 *  `opts.quality` — JPEG quality 0..1 (default S.jpegQuality = 0.72) */
S.sendFrame = async function (video, overlay, opts = {}) {
  if (!S.sock || S.sock.readyState !== 1) return null;
  if (!video.videoWidth) return null;

  // Match the overlay's "cover" rendering so server pixel coords ↔ overlay px.
  const targetW = opts.width || 640;
  const quality = opts.quality != null ? opts.quality : S.jpegQuality;
  const aspect = overlay.clientWidth / overlay.clientHeight;
  const W = targetW;
  const H = Math.round(W / aspect);

  if (S._buf.width !== W || S._buf.height !== H) {
    S._buf.width = W; S._buf.height = H;
  }
  const ctx = S._buf.getContext("2d");
  const vw = video.videoWidth, vh = video.videoHeight;
  const arO = W / H, arV = vw / vh;
  let sx = 0, sy = 0, sw = vw, sh = vh;
  if (arV > arO) { sw = vh * arO; sx = (vw - sw) / 2; }
  else            { sh = vw / arO; sy = (vh - sh) / 2; }
  ctx.drawImage(video, sx, sy, sw, sh, 0, 0, W, H);

  const blob = await new Promise((res) =>
    S._buf.toBlob(res, "image/jpeg", quality)
  );
  if (!blob) return null;
  const buf = await blob.arrayBuffer();
  if (S.sock.readyState === 1) S.sock.send(buf);
  return { width: W, height: H };
};

/** Start a continuous frame loop at `fps`. The work-canvas size is the
 *  reference for all server-returned pixel coords. */
S.startStream = function (video, overlay) {
  if (S.streaming) return;
  S.streaming = true;
  const period = 1000 / S.fps;
  const tick = async () => {
    if (!S.streaming) return;
    if (!S._sendBusy && S.connected) {
      S._sendBusy = true;
      try { await S.sendFrame(video, overlay); } catch {}
      S._sendBusy = false;
    }
    S._timer = setTimeout(tick, period);
  };
  tick();
};

S.stopStream = function () {
  S.streaming = false;
  if (S._timer) { clearTimeout(S._timer); S._timer = null; }
};

const _EVT_FOR = {
  detect_aruco: "aruco",
  detect_yolo: "yolo",
  refine_point: "refine",
  calib_start: "calib",
  calib_capture: "calib",
  calib_clear: "calib",
  calib_solve: "calib",
  intrinsics_status: "intrinsics",
};

/** One-shot: send a frame, then issue a command, return a promise that
 *  resolves with the matching server event (or rejects on timeout).
 *
 *  `opts.width` / `opts.quality` override the frame size & JPEG quality for
 *  this call only — used for high-resolution ArUco detection. */
S.oneShot = async function (video, overlay, cmd, extra = {}, eventName = null,
                            timeoutMs = 8000, opts = {}) {
  const evt = eventName || _EVT_FOR[cmd] || "result";
  await S.sendFrame(video, overlay, opts);
  return new Promise((resolve, reject) => {
    const to = setTimeout(() => { S.off(evt, handler); reject(new Error("timeout")); }, timeoutMs);
    const handler = (msg) => {
      clearTimeout(to);
      S.off(evt, handler);
      resolve(msg);
    };
    S.on(evt, handler);
    S.send(cmd, extra);
  });
};

/** Burst-send N high-quality frames at the same resolution (so the server's
 *  averaging buffer is full of hi-res samples), then run the command.
 *
 *  Used by the "Detect markers" button to drop ArUco corner noise as 1/√N. */
S.burstThenCmd = async function (video, overlay, n, cmd, extra = {}, opts = {}) {
  const evt = _EVT_FOR[cmd] || "result";
  // Pause the regular low-res stream so we don't interleave different
  // resolutions in the server's buffer (which would invalidate the
  // averaging window).
  const wasStreaming = S.streaming;
  S.stopStream();
  try {
    for (let i = 0; i < n; i++) {
      await S.sendFrame(video, overlay, opts);
      // 80 ms between bursts — phone camera updates at ~30 fps so this
      // gives us decorrelated samples without ridiculous bandwidth.
      await new Promise(r => setTimeout(r, 80));
    }
    return await new Promise((resolve, reject) => {
      const to = setTimeout(() => { S.off(evt, handler); reject(new Error("timeout")); }, 10000);
      const handler = (msg) => { clearTimeout(to); S.off(evt, handler); resolve(msg); };
      S.on(evt, handler);
      S.send(cmd, extra);
    });
  } finally {
    if (wasStreaming) S.startStream(video, overlay);
  }
};

/** Convenience wrapper for refine_point.
 *  Sends a frame at a fixed width (1024 px) — high enough for sub-pixel
 *  Sobel to bite, low enough not to blow up bandwidth per tap — then asks
 *  the server to snap the touch position to the nearest strong edge. */
S.refinePoint = async function (video, overlay, p_overlay) {
  const W = 1024;
  const k = W / overlay.clientWidth;  // overlay-px × k = frame-px
  const x = p_overlay.x * k;
  const y = p_overlay.y * k;
  try {
    const msg = await S.oneShot(video, overlay, "refine_point",
      { x, y, radius: 22 }, "refine", 1500, { width: W, quality: 0.9 });
    if (!msg.ok || !msg.refined) return null;
    return { x: msg.x / k, y: msg.y / k, gradient: msg.gradient };
  } catch { return null; }
};
