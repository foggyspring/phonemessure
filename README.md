# phonemessure

> 手机当镜头，MacBook 跑模型。

```
┌──────────────┐    JPEG ~6 fps (binary WS)    ┌────────────────────────────┐
│              │ ───────────────────────────▶  │                            │
│   phone      │                               │   MacBook (FastAPI)        │
│   Safari /   │ ◀───────────────────────────  │   • cv2.aruco (DICT_4X4_50)│
│   Chrome     │    JSON results (WS)          │   • Charuco intrinsics     │
│              │                               │   • YOLO-World (MPS)       │
│ getUserMedia │                               │                            │
└──────────────┘                               └────────────────────────────┘
```

This is a phone-camera measurement tool over LAN. The phone is the camera; a
laptop on the same Wi-Fi runs all the computer vision and sends results back
over a WebSocket. Default target hardware is **Apple-silicon MacBook (16 GB
RAM, no discrete GPU, MPS available)** — every model choice respects that
budget.

## Quick start

```bash
git clone … && cd phonemessure
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# (one-off) cache the YOLO-World ckpt locally
python -m scripts.fetch_models --yolo

python server.py --warmup
```

`--warmup` loads YOLO-World before the server starts accepting requests
(takes ~3-5 s on M-series). Without it the first auto-reference call pays
the load cost.

The terminal prints a QR code; scan it with the phone on the same Wi-Fi,
accept the self-signed TLS warning, allow camera access, then calibrate
before measuring.

### CLI

| flag         | default | notes                                                  |
| ------------ | ------- | ------------------------------------------------------ |
| `--port`     | 8443    |                                                        |
| `--host`     | 0.0.0.0 |                                                        |
| `--warmup`   | off     | pre-load YOLO-World at boot                            |
| `--no-cert`  | off     | plain HTTP (camera works only on `localhost`)          |
| `--cert/--key` | —     | bring your own (e.g. mkcert) and skip the auto cert    |

## What the ML layer does

| Step | Model / tool | Where it runs | Why |
|------|--------------|---------------|-----|
| Lens distortion removal | `cv2.aruco` Charuco + `cv2.calibrateCameraCharuco` + `cv2.undistort` | MacBook | Single biggest accuracy improvement. Run once per camera. |
| Calibration sheet → plane homography | `cv2.aruco.ArucoDetector` (DICT_4X4_50) + `cv2.getPerspectiveTransform` | MacBook | Sub-pixel-refined corners; an oblique shot of the sheet still gives you mm-accurate readings on the whole plane. |
| Auto reference object (credit card / A4 / coin) | **YOLO-World** (`yolov8s-worldv2.pt`, ~50 MB) via `ultralytics`, on MPS | MacBook | Open-vocabulary: prompt with text like `"credit card"` or `"one yuan coin"`. Bounding-box width / diameter is divided into a known physical size to recover mm/px. |
| Device selection | `torch.backends.mps` | n/a | Auto-picks MPS on Apple-silicon, CPU otherwise. Never auto-picks CUDA. |

### Why these specific models

- **`opencv-contrib-python` for ArUco / Charuco** is the right call here.
  We had a pure-JS blob detector in v1; replacing it with real ArUco brings
  sub-pixel corners and an actual marker dictionary, which makes the
  homography much more stable to lighting and partial occlusion.
- **YOLO-World** is open-vocabulary, so we don't have to fine-tune anything
  to add "credit card" / "coin" / "A4 paper". The `yolov8s-worldv2.pt`
  variant (~50 MB) is the sweet spot for M-series CPUs at ~120–200 ms on
  640 px input. It also runs on MPS via the standard ultralytics path.
- **Charuco** instead of a plain chessboard for camera intrinsics: the
  marker IDs are robust to partial occlusion, so a 6×9-square pattern that
  exits the frame still contributes good points.

Models we deliberately did **not** include this round:

- **MobileSAM / EfficientSAM / SAM2** — "tap once, get object mask"
  segmentation. High ROI, but the streaming + UI work is non-trivial.
  Slated for next round.
- **Depth Anything V2 / Metric3D V2** — would let you measure things
  off-plane, but a) needs metric-scale rescue from ArUco, and b) the small
  variant is ~99 MB and adds ~250 ms/frame on CPU. Experimental.

## End-to-end calibration path (recommended order)

1. **Intrinsics, once per camera.** Calibration panel → *Intrinsics* tab.
   Print [`/api/charuco-sheet.pdf`](http://localhost:8443/api/charuco-sheet.pdf),
   glue to cardstock, hit *Start*, then *Capture frame* 12-20 times moving
   the board through tilts and distances. Hit *Solve + save*. RMS under
   1 px is good; over 3 px means the board moved while capturing.
2. **Plane homography, per scene.** Calibration panel → *Sheet (ArUco)*.
   Print [`/api/aruco-sheet.pdf`](http://localhost:8443/api/aruco-sheet.pdf),
   lay flat in the frame, tap *Detect*. The whole sheet plane is now
   pixel-perfect mm, even at oblique angles. **Use this whenever you can.**
3. **Auto reference (fallback).** Calibration panel → *Auto (YOLO)*.
   Edit the comma-separated prompt list if you want, hit *Detect references*,
   tap *Apply* on the detection you trust. Convenient when you don't have
   the sheet printed, but accuracy is bbox-based — keep the reference
   object nearly perpendicular to the camera.
4. **Tap-the-edge (manual fallback).** Calibration panel → *Reference*.
   Pick a preset (credit card / A4 / coin) or enter a custom mm value,
   tap the two endpoints of that edge.

## Project layout

```
phonemessure/
├── server.py                       # CLI, HTTPS, QR code, --warmup
├── app/
│   ├── certs.py                    # self-signed cert covering every LAN IP
│   ├── network.py                  # LAN IP discovery
│   ├── routes.py                   # /, /api/*, includes ws router
│   ├── ws.py                       # /ws/{sid} — JPEG up, JSON down
│   ├── aruco_pdf.py                # ArUco sheet + Charuco board PDFs
│   └── inference/
│       ├── runtime.py              # MPS/CPU pick, ckpt dir, state dir
│       ├── intrinsics.py           # Charuco capture + solve, undistort
│       ├── aruco.py                # 4-marker sheet → homography
│       ├── yolo.py                 # YOLO-World + physical-size table
│       └── pipeline.py             # per-session inference state
├── scripts/
│   └── fetch_models.py             # `python -m scripts.fetch_models`
├── checkpoints/                    # gitignored, models cached here
├── static/
│   ├── index.html, style.css       # dark engineering UI
│   ├── stream.js                   # WS, JPEG frame uploader
│   ├── measure.js                  # shapes + mm projection
│   ├── calibrate.js                # tap-the-edge + apply server poses
│   ├── history.js                  # storage, CSV/JSON/PNG exports
│   ├── app.js                      # glue
│   ├── manifest.webmanifest, sw.js
│   └── icons/icon.svg
└── requirements.txt
```

## WebSocket protocol

Endpoint: `/ws/{sessionId}`.

Client → server:

- Binary message: a raw JPEG of the current camera frame (the server keeps
  only the most recent one per session, so dropping is fine).
- Text JSON commands:
  ```json
  {"cmd": "detect_aruco"}
  {"cmd": "detect_yolo", "prompts": ["credit card", "a4 paper"]}
  {"cmd": "calib_start"}      // start charuco capture
  {"cmd": "calib_capture"}    // append latest frame to capture set
  {"cmd": "calib_solve"}      // run calibrateCameraCharuco + save
  {"cmd": "calib_clear"}
  {"cmd": "intrinsics_status"}
  {"cmd": "ping"}
  ```

Server → client (text JSON only):

```jsonc
{"event": "frame_ack",  "n": 123}
{"event": "aruco", "ok": true, "pose": {"image_size":[640,360], "H":[...], "marker_centres":{"TL":[..],"TR":[..],"BR":[..],"BL":[..]}, "found_ids":[0,1,2,3]}, "undistorted": true}
{"event": "yolo",  "ok": true, "image_size":[640,360], "detections":[{"label":"credit card","score":0.82,"box":[x1,y1,x2,y2],"mm_per_px":1.42}], "undistorted": true}
{"event": "calib", "ok": true, "captures": 7, "n": 41}
{"event": "intrinsics", "have_intrinsics": true, "image_size":[640,360], "rms": 0.41}
{"event": "error", "reason": "…"}
```

All server pixel coordinates are in **work-canvas** space (640 px wide by
default, set in `static/stream.js`). The client multiplies them by
`overlay_clientWidth / 640` to get overlay-coord pixels.

## Precision — what this thing is and isn't

Still monocular planar measurement. The ML layer helps in three concrete
ways:

- **Undistorting** the frame before any measurement removes phone-lens
  distortion, especially around the edges. This is the single biggest
  free win.
- **Sub-pixel ArUco corners** give a more stable homography than the
  blob-centroid approach used before. Tilts up to ~45° are fine.
- **Auto-reference** removes finger-tap error from the calibration step,
  although the bbox-vs-edge mismatch caps the improvement.

What it still **can't** do:

- Measure 3-D objects whose dimension is *not* on the calibration plane
  (this would need depth — slated for next round).
- Beat a real caliper. Phone optics, exposure noise, and the fundamental
  ambiguity of bounding boxes cap absolute accuracy somewhere in the
  ±1 mm range under good conditions.

### Tips that still matter

1. Run intrinsics calibration. Seriously. RMS under 1 px is achievable in
   ~5 minutes and shaves multi-mm errors at the image edges.
2. Use the sheet for the homography step. Tap-the-edge has finger-error;
   YOLO has bbox-error; only the sheet has neither.
3. Keep the target object on the same plane as the calibration sheet.
4. Bright, diffuse light. Strong shadows make ArUco corners drift.
5. Lock the phone's focus before opening this app on iOS (long-press the
   live view in the native Camera app; Safari inherits the lock for a
   short window).

## Troubleshooting

**`opencv-contrib-python not installed`** in the inference status —
`pip install -r requirements.txt` again; the regular `opencv-python` ships
without `cv2.aruco`. The contrib build is what you want.

**`ultralytics not installed`** — same. YOLO-World needs ultralytics and
torch, both pinned in `requirements.txt`.

**MPS errors mid-inference** — set `PYTORCH_ENABLE_MPS_FALLBACK=1` (the
runtime sets it for you already). Some ops still don't have an MPS
implementation; the fallback to CPU is silent and a few ms slower.

**WebSocket disconnects every few seconds** — Safari aggressively suspends
WS when the tab goes background. Bring the tab back to foreground; the
client auto-reconnects.

**Camera intrinsics never solve** — you need at least 5 captures with
≥6 charuco corners each. Print on real paper (not just a screen photo of
the PDF), keep the board flat, and cover all four quadrants of the frame.

**YOLO finds nothing** — the default prompt list is "credit card, a4 paper,
us quarter, one yuan coin". Open the *Auto* tab and edit the prompts.
YOLO-World is sensitive to phrasing; try `"playing card"` instead of
`"credit card"` if your card is plain.

## License

MIT — see [LICENSE](./LICENSE).
