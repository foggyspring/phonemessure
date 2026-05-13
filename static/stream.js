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
 *  send it as binary JPEG. Returns the work-canvas size used. */
S.sendFrame = async function (video, overlay, opts = {}) {
  if (!S.sock || S.sock.readyState !== 1) return null;
  if (!video.videoWidth) return null;

  // Match the overlay's "cover" rendering so server pixel coords ↔ overlay px.
  const targetW = opts.width || 640;
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

  // toBlob is async but cheaper than toDataURL.
  const blob = await new Promise((res) =>
    S._buf.toBlob(res, "image/jpeg", S.jpegQuality)
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

/** One-shot: send a frame, then issue a command, return a promise that
 *  resolves with the matching server event (or rejects on timeout). */
S.oneShot = async function (video, overlay, cmd, extra = {}, eventName = null, timeoutMs = 8000) {
  const evt = eventName || ({
    detect_aruco: "aruco",
    detect_yolo: "yolo",
    calib_start: "calib",
    calib_capture: "calib",
    calib_clear: "calib",
    calib_solve: "calib",
    intrinsics_status: "intrinsics",
  }[cmd] || "result");
  await S.sendFrame(video, overlay);
  return new Promise((resolve, reject) => {
    const to = setTimeout(() => { S._handlers.set(evt, (S._handlers.get(evt)||[]).filter(h => h !== handler)); reject(new Error("timeout")); }, timeoutMs);
    const handler = (msg) => {
      clearTimeout(to);
      S._handlers.set(evt, (S._handlers.get(evt)||[]).filter(h => h !== handler));
      resolve(msg);
    };
    S.on(evt, handler);
    S.send(cmd, extra);
  });
};
