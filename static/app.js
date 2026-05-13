/* phonemessure — main wiring */
(() => {
  const PM = window.PM;
  const PM_CAL = window.PM_CAL;
  const PM_HIST = window.PM_HIST;
  const S = window.PM_STREAM;

  const $ = (id) => document.getElementById(id);

  // ─── elements ───────────────────────────────────────────────────────
  const video    = $("cam");
  const overlay  = $("overlay");
  const gridcv   = $("gridcv");
  const loupe    = $("loupe");
  const readout  = $("readout");
  const readoutVal = $("readout-val");
  const readoutMode = $("readout-mode");
  const calStatus = $("cal-status");
  const detectStatus = $("detect-status");
  const yoloStatus = $("yolo-status");
  const yoloList = $("yolo-list");
  const intrStatus = $("intr-status");
  const intrMono = $("intr-mono");
  const deviceMono = $("device-mono");
  const deviceChip = $("device-chip");
  const connDot = $("conn-dot");
  const connLabel = $("conn-label");
  const histList = $("hist-list");
  const sessIdEl = $("sess-id");

  // ─── state ──────────────────────────────────────────────────────────
  const ui = {
    facing: "environment",
    stream: null,
    showGrid: false,
    gridMM: 10,
    loupeOn: true,
    calPending: null,        // scale-from-taps in progress
    intrCaptures: 0,
  };

  // ─── boot ───────────────────────────────────────────────────────────
  PM_HIST.load();
  loadLocalCalibration();
  initSession().then(loadInferenceStatus);

  $("boot-go").addEventListener("click", startCamera);

  async function initSession() {
    let sid = localStorage.getItem("pm.sessionId");
    if (!sid) {
      try {
        const r = await fetch("/api/session/new");
        const j = await r.json();
        sid = j.sessionId;
        localStorage.setItem("pm.sessionId", sid);
      } catch { sid = "local"; }
    }
    PM_HIST.sessionId = sid;
    if (sessIdEl) sessIdEl.textContent = sid;
    // open the websocket; frames only start streaming when needed
    S.connect(sid);
  }

  async function loadInferenceStatus() {
    try {
      const r = await fetch("/api/inference/status");
      const j = await r.json();
      const dev = (j.device || "cpu").toUpperCase();
      deviceMono.textContent = dev + (j.aruco.ok ? " · aruco OK" : " · aruco —") + (j.yolo.ok ? " · yolo OK" : " · yolo —");
      deviceChip.textContent = dev;
      deviceChip.classList.remove("mps", "cpu");
      deviceChip.classList.add(dev === "MPS" ? "mps" : "cpu");
      deviceChip.hidden = false;
    } catch {}
  }

  S.on("open", () => {
    connDot.classList.remove("off");
    connLabel.textContent = "phonemessure";
  });
  S.on("close", () => {
    connDot.classList.add("off");
    connLabel.textContent = "reconnecting…";
  });

  function loadLocalCalibration() {
    try {
      const raw = localStorage.getItem(PM_HIST.CAL_KEY);
      if (raw) PM.state.calibration = JSON.parse(raw);
    } catch {}
  }
  function persistCalibration() {
    try {
      if (PM.state.calibration) {
        localStorage.setItem(PM_HIST.CAL_KEY, JSON.stringify(PM.state.calibration));
      } else {
        localStorage.removeItem(PM_HIST.CAL_KEY);
      }
    } catch {}
    if (PM_HIST.sessionId) {
      fetch(`/api/session/${PM_HIST.sessionId}/calibration`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(PM.state.calibration),
      }).catch(() => {});
    }
    refreshReadout();
  }

  // ─── camera ─────────────────────────────────────────────────────────
  async function startCamera() {
    const err = $("boot-err");
    err.hidden = true;
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error("getUserMedia not available — open this page over HTTPS.");
      }
      if (ui.stream) ui.stream.getTracks().forEach(t => t.stop());
      ui.stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: { ideal: ui.facing },
          width:  { ideal: 1920 },
          height: { ideal: 1080 },
        },
        audio: false,
      });
      video.srcObject = ui.stream;
      await video.play();
      $("boot").hidden = true;
      $("app").hidden = false;
      sizeCanvases();
      // start the upstream frame loop; server only ever holds the latest frame
      S.startStream(video, overlay);
    } catch (e) {
      err.textContent = (e && e.message) || String(e);
      err.hidden = false;
    }
  }

  function sizeCanvases() {
    const dpr = window.devicePixelRatio || 1;
    const w = window.innerWidth, h = window.innerHeight;
    for (const c of [overlay, gridcv]) {
      c.width = Math.round(w * dpr);
      c.height = Math.round(h * dpr);
      c.style.width = w + "px";
      c.style.height = h + "px";
      c.getContext("2d").setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    redraw();
  }
  window.addEventListener("resize", sizeCanvases);
  window.addEventListener("orientationchange", () => setTimeout(sizeCanvases, 250));

  $("btn-flip").addEventListener("click", () => {
    ui.facing = ui.facing === "environment" ? "user" : "environment";
    startCamera();
  });

  // ─── pointer handling on overlay ────────────────────────────────────
  function ptFrom(ev) {
    const rect = overlay.getBoundingClientRect();
    const t = ev.touches ? ev.touches[0] : ev;
    return { x: t.clientX - rect.left, y: t.clientY - rect.top };
  }

  let calTapBuf = [];

  overlay.addEventListener("pointerdown", (ev) => {
    ev.preventDefault();
    overlay.setPointerCapture(ev.pointerId);
    const p = ptFrom(ev);

    if (ui.calPending) {
      calTapBuf.push(p);
      drawCalTaps();
      showLoupe(p, true);
      if (calTapBuf.length === 2) {
        const mm = ui.calPending.mm;
        const cal = PM_CAL.scaleCalibration(calTapBuf[0], calTapBuf[1], mm);
        if (cal) {
          PM.state.calibration = cal;
          persistCalibration();
          calStatus.textContent = `OK — ${(1/cal.mmPerPx).toFixed(2)} px/mm`;
          calStatus.className = "status ok";
        } else {
          calStatus.textContent = "Taps too close together — try again.";
          calStatus.className = "status err";
        }
        ui.calPending = null;
        calTapBuf = [];
        hideLoupe();
        redrawGrid();
        redraw();
      }
      return;
    }

    const hit = PM.hitTest(p);
    if (hit) {
      PM.state.drag = hit;
      if (ui.loupeOn) showLoupe(p, false);
      return;
    }

    if (!PM.state.current) {
      PM.state.current = PM.startShape(PM.state.mode, p);
      if (PM.state.mode === "line" || PM.state.mode === "rect") {
        PM.state.drag = { shapeIdx: -1, ptIdx: 1 };
      }
    } else {
      PM.continueShape(PM.state.current, p);
      if (PM.state.mode === "circle" && PM.state.current.pts.length === 3) {
        PM.state.shapes.push(PM.finishShape(PM.state.current));
        PM.state.current = null;
      }
    }
    if (ui.loupeOn) showLoupe(p, false);
    redraw();
  });

  overlay.addEventListener("pointermove", (ev) => {
    const p = ptFrom(ev);
    if (PM.state.drag) {
      PM.movePoint(PM.state.drag, p);
      if (ui.loupeOn) showLoupe(p, !!ui.calPending);
      redraw();
    }
  });

  function endPointer() {
    if (PM.state.drag) {
      PM.state.drag = null;
      const c = PM.state.current;
      if (c && (c.mode === "line" || c.mode === "rect")) {
        PM.state.shapes.push(c);
        PM.state.current = null;
      }
    }
    hideLoupe();
    redraw();
  }
  overlay.addEventListener("pointerup", endPointer);
  overlay.addEventListener("pointercancel", endPointer);

  // ─── magnifier ──────────────────────────────────────────────────────
  function showLoupe(p, isCal) {
    if (!ui.loupeOn) return;
    const size = 140, zoom = 2.5;
    loupe.width = size; loupe.height = size;
    loupe.style.width = size + "px"; loupe.style.height = size + "px";
    loupe.style.left = Math.max(8, Math.min(window.innerWidth - size - 8, p.x - size / 2)) + "px";
    const topY = p.y - size - 60;
    loupe.style.top = (topY > 60 ? topY : p.y + 40) + "px";
    loupe.classList.toggle("cal", !!isCal);
    loupe.hidden = false;

    const ctx = loupe.getContext("2d");
    ctx.clearRect(0, 0, size, size);
    const vw = video.videoWidth, vh = video.videoHeight;
    if (!vw || !vh) return;
    const ow = overlay.clientWidth, oh = overlay.clientHeight;
    const arO = ow / oh, arV = vw / vh;
    let sx0 = 0, sy0 = 0, swh = vw, shv = vh;
    if (arV > arO) { swh = vh * arO; sx0 = (vw - swh) / 2; }
    else            { shv = vw / arO; sy0 = (vh - shv) / 2; }
    const u = p.x / ow, v = p.y / oh;
    const cx = sx0 + u * swh, cy = sy0 + v * shv;
    const half = (size / zoom) / 2 * (swh / ow);
    ctx.save();
    ctx.beginPath();
    ctx.arc(size / 2, size / 2, size / 2 - 1, 0, Math.PI * 2);
    ctx.clip();
    ctx.drawImage(video, cx - half, cy - half, half * 2, half * 2, 0, 0, size, size);
    ctx.strokeStyle = isCal ? "#f59e0b" : "#22d3ee";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(size / 2 - 12, size / 2); ctx.lineTo(size / 2 + 12, size / 2);
    ctx.moveTo(size / 2, size / 2 - 12); ctx.lineTo(size / 2, size / 2 + 12);
    ctx.stroke();
    ctx.restore();
  }
  function hideLoupe() { loupe.hidden = true; }

  // ─── grid (drawn when calibrated) ───────────────────────────────────
  function redrawGrid() {
    const ctx = gridcv.getContext("2d");
    const w = gridcv.clientWidth, h = gridcv.clientHeight;
    ctx.clearRect(0, 0, gridcv.width, gridcv.height);
    if (!ui.showGrid || !PM.state.calibration) return;
    const cal = PM.state.calibration;
    ctx.strokeStyle = "rgba(34, 211, 238, 0.45)";
    ctx.lineWidth = 0.5;

    if (cal.kind === "scale") {
      const pitch = ui.gridMM / cal.mmPerPx;
      for (let x = 0; x < w; x += pitch) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke(); }
      for (let y = 0; y < h; y += pitch) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); }
      return;
    }
    const H = cal.H;
    const Hinv = invert3x3(H);
    if (!Hinv) return;
    const padX = 80, padY = 80;
    const minX = 0, maxX = PM_CAL.SHEET.W_MM;
    const minY = 0, maxY = PM_CAL.SHEET.H_MM;
    const project = (X, Y) => {
      const w0 = Hinv[6]*X + Hinv[7]*Y + Hinv[8];
      return {
        x: (Hinv[0]*X + Hinv[1]*Y + Hinv[2]) / w0,
        y: (Hinv[3]*X + Hinv[4]*Y + Hinv[5]) / w0,
      };
    };
    for (let X = minX - padX; X <= maxX + padX; X += ui.gridMM) {
      const a = project(X, minY - padY), b = project(X, maxY + padY);
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    }
    for (let Y = minY - padY; Y <= maxY + padY; Y += ui.gridMM) {
      const a = project(minX - padX, Y), b = project(maxX + padX, Y);
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    }
  }

  function invert3x3(m) {
    const a=m[0],b=m[1],c=m[2],d=m[3],e=m[4],f=m[5],g=m[6],h=m[7],i=m[8];
    const A=(e*i-f*h), B=-(d*i-f*g), Cm=(d*h-e*g);
    const D=-(b*i-c*h), E=(a*i-c*g), F=-(a*h-b*g);
    const G=(b*f-c*e), HH=-(a*f-c*d), I=(a*e-b*d);
    const det = a*A + b*B + c*Cm;
    if (Math.abs(det) < 1e-9) return null;
    const k = 1/det;
    return [A*k, D*k, G*k, B*k, E*k, HH*k, Cm*k, F*k, I*k];
  }

  // ─── tap-cal helpers ────────────────────────────────────────────────
  function drawCalTaps() {
    const ctx = overlay.getContext("2d");
    redraw();
    ctx.save();
    ctx.fillStyle = "#f59e0b";
    ctx.strokeStyle = "#0b0f14";
    ctx.lineWidth = 2;
    for (const p of calTapBuf) {
      ctx.beginPath();
      ctx.arc(p.x, p.y, 9, 0, Math.PI * 2);
      ctx.fill(); ctx.stroke();
    }
    ctx.restore();
  }

  function redraw() { PM.drawAll(overlay.getContext("2d")); refreshReadout(); }

  function refreshReadout() {
    const cal = PM.state.calibration;
    if (!cal) {
      readoutMode.textContent = "NO CALIBRATION";
      readoutVal.textContent = "—";
      readout.classList.remove("calibrating");
      return;
    }
    readout.classList.remove("calibrating");
    if (cal.kind === "homography") {
      readoutMode.textContent = cal.undistorted ? "HOMOGRAPHY · UNDIST" : "HOMOGRAPHY";
    } else {
      readoutMode.textContent = `SCALE · ${cal.refName || "ref"}`;
    }
    const s = PM.state.current || PM.state.shapes[PM.state.shapes.length - 1];
    if (!s) { readoutVal.textContent = "—"; return; }
    const r = PM.computeShape(s);
    readoutVal.textContent = r.label || "—";
  }

  // ─── bottom action bar ──────────────────────────────────────────────
  document.querySelectorAll(".mode-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".mode-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      PM.state.mode = btn.dataset.mode;
      if (PM.state.current) {
        const fin = PM.finishShape(PM.state.current);
        if (fin) PM.state.shapes.push(fin);
        PM.state.current = null;
      }
      redraw();
    });
  });

  $("btn-undo").addEventListener("click", () => { PM.undo(); redraw(); });
  $("btn-clear").addEventListener("click", () => { PM.clear(); redraw(); });

  let pendingSave = null;
  $("btn-save").addEventListener("click", () => {
    if (PM.state.current) {
      const fin = PM.finishShape(PM.state.current);
      if (fin) PM.state.shapes.push(fin);
      PM.state.current = null;
    }
    const s = PM.state.shapes[PM.state.shapes.length - 1];
    if (!s) return;
    pendingSave = { shape: s, computed: PM.computeShape(s) };
    $("save-name").value = "";
    $("save-note").value = "";
    $("save-modal").hidden = false;
  });
  $("save-cancel").addEventListener("click", () => { $("save-modal").hidden = true; pendingSave = null; });
  $("save-ok").addEventListener("click", () => {
    if (!pendingSave) return;
    const r = pendingSave.computed;
    PM_HIST.add({
      name: $("save-name").value.trim() || pendingSave.shape.mode,
      note: $("save-note").value.trim(),
      mode: pendingSave.shape.mode,
      label: r.label,
      fields: r,
      shape: pendingSave.shape,
    });
    $("save-modal").hidden = true;
    pendingSave = null;
  });

  // ─── panels ─────────────────────────────────────────────────────────
  function openPanel(id) {
    closeAllPanels();
    $(id).hidden = false;
    if (id === "panel-hist") PM_HIST.renderInto(histList);
    if (id === "panel-cal") refreshIntrinsicsStatus();
  }
  function closeAllPanels() {
    for (const id of ["panel-menu", "panel-cal", "panel-hist"]) $(id).hidden = true;
  }
  document.querySelectorAll("[data-close]").forEach(b => b.addEventListener("click", closeAllPanels));

  $("btn-menu").addEventListener("click", () => openPanel("panel-menu"));
  $("btn-cal").addEventListener("click", () => openPanel("panel-cal"));
  $("btn-history").addEventListener("click", () => openPanel("panel-hist"));

  // settings
  $("opt-grid").addEventListener("change", (e) => { ui.showGrid = e.target.checked; redrawGrid(); });
  $("opt-grid-mm").addEventListener("change", (e) => { ui.gridMM = Math.max(1, +e.target.value || 10); redrawGrid(); });
  $("opt-loupe").addEventListener("change", (e) => { ui.loupeOn = e.target.checked; });
  $("opt-decimals").addEventListener("change", (e) => { PM.state.decimals = Math.max(0, +e.target.value | 0); redraw(); });

  // calibration tabs
  document.querySelectorAll(".tab").forEach(t => t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
    t.classList.add("active");
    const which = t.dataset.tab;
    for (const id of ["tab-ref", "tab-sheet", "tab-auto", "tab-intrinsics"]) $(id).hidden = true;
    $("tab-" + which).hidden = false;
  }));

  // tap-the-edge presets
  let pendingMM = null;
  document.querySelectorAll(".preset").forEach(b => b.addEventListener("click", () => {
    document.querySelectorAll(".preset").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    pendingMM = +b.dataset.mm;
    calStatus.textContent = `Reference: ${pendingMM} mm — tap "Begin"`;
    calStatus.className = "status";
  }));
  $("btn-custom").addEventListener("click", () => {
    const v = +$("custom-mm").value;
    if (!(v > 0)) {
      calStatus.textContent = "Enter a positive number in mm.";
      calStatus.className = "status err";
      return;
    }
    pendingMM = v;
    document.querySelectorAll(".preset").forEach(x => x.classList.remove("active"));
    calStatus.textContent = `Reference: ${v} mm — tap "Begin"`;
    calStatus.className = "status";
  });
  $("btn-cal-start").addEventListener("click", () => {
    if (!(pendingMM > 0)) {
      calStatus.textContent = "Pick a preset or enter a custom length first.";
      calStatus.className = "status err";
      return;
    }
    ui.calPending = { mm: pendingMM };
    calTapBuf = [];
    closeAllPanels();
    readout.classList.add("calibrating");
    readoutMode.textContent = "TAP TWO EDGES";
    readoutVal.textContent = `${pendingMM} mm`;
  });

  // sheet detect via server (cv2.aruco)
  $("btn-detect").addEventListener("click", async () => {
    detectStatus.textContent = "Scanning frame on MacBook…";
    detectStatus.className = "status";
    try {
      const msg = await S.oneShot(video, overlay, "detect_aruco", {}, "aruco");
      if (!msg.ok) {
        detectStatus.textContent = `Failed: ${msg.reason}`;
        detectStatus.className = "status err";
        return;
      }
      const cal = PM_CAL.calibrationFromAruco(msg, overlay.clientWidth);
      if (!cal) {
        detectStatus.textContent = "Detected but couldn't build calibration.";
        detectStatus.className = "status err";
        return;
      }
      PM.state.calibration = cal;
      persistCalibration();
      const u = msg.undistorted ? " (undistorted frame)" : "";
      detectStatus.textContent = `Homography solved from 4 markers${u}.`;
      detectStatus.className = "status ok";
      redrawGrid();
      redraw();
    } catch (e) {
      detectStatus.textContent = `Timeout or error: ${e.message || e}`;
      detectStatus.className = "status err";
    }
  });

  // YOLO auto-reference
  $("btn-yolo").addEventListener("click", async () => {
    yoloStatus.textContent = "Running YOLO-World on MacBook…";
    yoloStatus.className = "status";
    yoloList.innerHTML = "";
    const prompts = ($("yolo-prompts").value || "")
      .split(",").map(s => s.trim()).filter(Boolean);
    try {
      const msg = await S.oneShot(video, overlay, "detect_yolo", { prompts }, "yolo", 30000);
      if (!msg.ok) {
        yoloStatus.textContent = `Failed: ${msg.reason}`;
        yoloStatus.className = "status err";
        return;
      }
      const dets = msg.detections || [];
      if (!dets.length) {
        yoloStatus.textContent = "No matching object in frame.";
        yoloStatus.className = "status err";
        return;
      }
      yoloStatus.textContent = `${dets.length} detection${dets.length>1?"s":""} — pick one to set scale.`;
      yoloStatus.className = "status ok";
      for (const d of dets) {
        const row = document.createElement("div");
        row.className = "hist-item";
        const left = document.createElement("div");
        const sc = d.mm_per_px ? `${(1/d.mm_per_px).toFixed(2)} px/mm` : "size unknown";
        left.innerHTML = `
          <div class="h-name">${escapeHTML(d.label)} <span class="h-note">${(d.score*100).toFixed(0)}%</span></div>
          <div class="h-val">${escapeHTML(sc)}</div>`;
        const apply = document.createElement("button");
        apply.className = "btn small primary";
        apply.textContent = d.mm_per_px ? "Apply" : "—";
        apply.disabled = !d.mm_per_px;
        apply.onclick = () => {
          const cal = PM_CAL.calibrationFromYolo(d, msg, overlay.clientWidth);
          if (!cal) return;
          PM.state.calibration = cal;
          persistCalibration();
          yoloStatus.textContent = `Applied: ${d.label} → ${(1/cal.mmPerPx).toFixed(2)} px/mm`;
          redrawGrid(); redraw();
        };
        row.append(left, apply);
        yoloList.appendChild(row);
      }
    } catch (e) {
      yoloStatus.textContent = `Timeout or error: ${e.message || e}`;
      yoloStatus.className = "status err";
    }
  });

  // intrinsics flow
  $("btn-intr-start").addEventListener("click", async () => {
    intrStatus.textContent = "Starting…";
    const msg = await S.oneShot(video, overlay, "calib_start", {}, "calib");
    if (!msg.ok) { intrStatus.textContent = `Failed: ${msg.reason}`; intrStatus.className = "status err"; return; }
    ui.intrCaptures = 0;
    intrStatus.textContent = "Started. Aim board → Capture frame (×12-20).";
    intrStatus.className = "status";
  });
  $("btn-intr-capture").addEventListener("click", async () => {
    const msg = await S.oneShot(video, overlay, "calib_capture", {}, "calib");
    if (!msg.ok) {
      intrStatus.textContent = `Skipped: ${msg.reason || "not enough corners"}`;
      intrStatus.className = "status err";
      return;
    }
    ui.intrCaptures = msg.captures || (ui.intrCaptures + 1);
    intrStatus.textContent = `Captures: ${ui.intrCaptures} (board corners: ${msg.n})`;
    intrStatus.className = "status ok";
  });
  $("btn-intr-clear").addEventListener("click", async () => {
    await S.oneShot(video, overlay, "calib_clear", {}, "calib");
    ui.intrCaptures = 0;
    intrStatus.textContent = "Cleared.";
    intrStatus.className = "status";
  });
  $("btn-intr-solve").addEventListener("click", async () => {
    intrStatus.textContent = "Solving…";
    intrStatus.className = "status";
    const msg = await S.oneShot(video, overlay, "calib_solve", {}, "calib", 30000);
    if (!msg.ok) { intrStatus.textContent = `Failed: ${msg.reason}`; intrStatus.className = "status err"; return; }
    intrStatus.textContent = `Saved. RMS=${msg.rms.toFixed(2)} px @ ${msg.image_size[0]}×${msg.image_size[1]}`;
    intrStatus.className = "status ok";
    refreshIntrinsicsStatus();
  });

  async function refreshIntrinsicsStatus() {
    try {
      const msg = await S.oneShot(video, overlay, "intrinsics_status", {}, "intrinsics", 4000);
      if (msg.have_intrinsics) {
        intrMono.textContent = `${msg.image_size[0]}×${msg.image_size[1]} · rms=${(msg.rms||0).toFixed(2)}`;
      } else {
        intrMono.textContent = "not calibrated";
      }
    } catch {
      intrMono.textContent = "—";
    }
  }

  // history exports
  $("btn-export-csv").addEventListener("click", () => {
    PM_HIST.download(`phonemessure-${stamp()}.csv`, "text/csv", PM_HIST.toCSV());
  });
  $("btn-export-json").addEventListener("click", () => {
    PM_HIST.download(`phonemessure-${stamp()}.json`, "application/json", PM_HIST.toJSON());
  });
  $("btn-export-png").addEventListener("click", () => {
    const url = PM_HIST.snapshot(video, overlay, gridcv);
    const a = document.createElement("a");
    a.href = url;
    a.download = `phonemessure-${stamp()}.png`;
    document.body.appendChild(a); a.click(); a.remove();
  });
  $("btn-hist-clear").addEventListener("click", () => {
    if (confirm("Clear all history?")) {
      PM_HIST.clear();
      PM_HIST.renderInto(histList);
    }
  });

  function escapeHTML(s) {
    return String(s).replace(/[&<>"']/g, m => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[m]));
  }
  function stamp() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}${p(d.getMonth()+1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
  }

  refreshReadout();
})();
