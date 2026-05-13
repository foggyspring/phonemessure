# phonemessure

> 把手机变成卡尺。电脑跑 Web 服务，手机在同一局域网扫码进入，对准物体即可测量。
>
> Turn your phone into a digital caliper. The laptop runs a tiny web server; the
> phone joins over Wi-Fi (by scanning a QR code printed in the terminal), opens
> its camera, and measures real-world objects on a known plane.

```
┌─────────────────────────┐         ┌──────────────────────────────────┐
│  laptop (this repo)     │         │  phone (Safari / Chrome)         │
│  python server.py       │ ──Wi-Fi → │  scan QR → camera → tap to     │
│  ↳ self-signed HTTPS    │         │  measure (mm, cm², …)             │
│  ↳ QR code in terminal  │         │                                  │
└─────────────────────────┘         └──────────────────────────────────┘
```

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python server.py
```

The terminal prints a QR code. Scan it with the phone (must be on the same
Wi-Fi). Accept the TLS warning once — the cert is self-signed and only valid
on your LAN. Allow camera access, then **Calibrate** before measuring.

### CLI

```
python server.py [--port PORT] [--host HOST] [--no-cert]
                 [--cert path/to/fullchain.pem --key path/to/privkey.pem]
```

| flag        | default | notes                                                   |
| ----------- | ------- | ------------------------------------------------------- |
| `--port`    | 8443    | any free port works                                     |
| `--host`    | 0.0.0.0 | bind address                                            |
| `--no-cert` | off     | plain HTTP. Camera only works on `localhost` this way.  |
| `--cert/--key` | —    | bring your own cert (e.g. mkcert) and skip the auto one |

## Screenshots

> screenshots go in `docs/` — `docs/main.png`, `docs/calibrate.png`,
> `docs/history.png`. Capture them with the in-app **Snapshot PNG** button.

## Features

- **Two calibration modes**
  - *Reference object* — pick a preset (credit card, A4, ¥1/¥0.1/¥0.5 coin) or
    enter a custom mm value, tap the two endpoints of the known edge. Quick
    and works without any printing.
  - *Calibration sheet* — print [`/api/aruco-sheet.pdf`](#calibration-sheet)
    on A4, lay it in the frame, tap **Detect**. Four black corner fiducials
    pin down a full plane-to-plane homography, so oblique camera angles get
    corrected automatically.
- **Five measurement modes:** straight line · polyline (cumulative) ·
  polygon (perimeter + area, shoelace) · rectangle (W × H + area) ·
  3-point circle (Ø + circumference + area).
- **Live readout** in a monospace caliper-style display; numbers update while
  you drag endpoints.
- **Endpoint magnifier** — a 140 px circular loupe with 2.5× zoom and a
  cross-hair, so you can land a tap on the actual edge instead of guessing
  under your finger.
- **Grid overlay** — after calibration, draws a real-world grid (default
  10 mm). Under homography the grid skews correctly with the plane.
- **History** — name + note each measurement; export to CSV or JSON; save a
  PNG snapshot of the camera frame with annotations and units burned in.
- **PWA** — `manifest.webmanifest` + service worker for "Add to Home
  Screen"; the shell works offline once cached.
- **Session isolation** — every browser gets its own `sessionId`; multiple
  phones can connect to the same laptop without sharing calibration.

## Calibration sheet

The auto-generated PDF (`/api/aruco-sheet.pdf`) lays four solid black
fiducial squares on an A4 page at known mm positions:

```
┌──────────────────────────┐
│ ■ TL                 ■ TR│
│                          │  ← 247 mm between centers
│                          │
│ ■ BL                 ■ BR│
└──────────────────────────┘
       ← 160 mm →
```

We deliberately use plain solid squares instead of a full ArUco dictionary.
Detection is then a few hundred lines of pure JS (Otsu threshold +
connected-component labelling) — no 8 MB `opencv.js` bundle needed for what
is effectively a 4-correspondence homography. Each marker is also printed
with its name (`TL 4`, `TR 3`, …) so you can see at a glance whether the
sheet is upright in the frame.

**Print at 100 % scale (no "fit to page").** A4 origin from the printer is
critical to absolute accuracy.

## Precision — what this thing is and isn't

This is **monocular planar measurement.** It is excellent for jobs where:

- the object lies on a single flat surface (paper, table, wall), and
- the calibration reference is on the *same* surface.

It is **not** suitable for:

- arbitrary 3-D shapes — only the projected outline on the calibration plane
  is meaningful;
- precision machining (think micrometre-class) — phone optics have
  distortion, sensor pixels are tiny, and finger taps have ±1–2 mm of
  uncertainty in good light;
- moving subjects — a hand wobble during the tap shows up directly.

### Tips that visibly improve accuracy

1. **Use the calibration sheet, not a reference object,** whenever you can.
   Homography corrects for camera tilt; a single scale factor cannot.
2. **Fill the frame.** The bigger the calibration markers in the image, the
   smaller the relative pixel error.
3. **Lock exposure / focus** on iOS by long-pressing the live view before
   opening this app (Safari inherits the lock). Auto-focus mid-tap shifts
   the apparent edge by a few pixels.
4. **Keep the object on the same plane as the calibration markers** —
   3 mm of out-of-plane offset can be several mm of error at typical
   distances.
5. **Tap with the magnifier on.** Most error budget is in your fingertip,
   not the math.
6. **Sanity-check with a known length** (e.g. lay a ruler in frame; if you
   measure 100 mm you should read 100 mm ± 1).

If you need sub-mm accuracy, use a real caliper.

## Project layout

```
phonemessure/
├── server.py            # CLI, HTTPS, QR code, uvicorn launch
├── app/
│   ├── certs.py         # self-signed cert covering every LAN IP
│   ├── network.py       # LAN IP discovery
│   ├── routes.py        # /api/*, /, static
│   └── aruco_pdf.py     # printable A4 calibration sheet
├── static/
│   ├── index.html
│   ├── style.css        # dark engineering panel theme
│   ├── app.js           # camera, pointer, panels, glue
│   ├── measure.js       # shapes + drawing + mm projection
│   ├── calibrate.js     # marker detection + homography
│   ├── history.js       # storage, exports, snapshot
│   ├── manifest.webmanifest
│   ├── sw.js
│   └── icons/icon.svg
├── certs/               # generated at first run (gitignored)
└── requirements.txt
```

## API

| Method | Path                                | Purpose                              |
| ------ | ----------------------------------- | ------------------------------------ |
| GET    | `/`                                 | SPA shell                            |
| GET    | `/api/health`                       | `{ ok, sessions }`                   |
| GET    | `/api/session/new`                  | mint a session id                    |
| GET    | `/api/session/{sid}`                | fetch calibration + history          |
| POST   | `/api/session/{sid}/calibration`    | persist current calibration JSON     |
| POST   | `/api/session/{sid}/history`        | append a measurement                 |
| DELETE | `/api/session/{sid}/history`        | clear server-side history            |
| GET    | `/api/aruco-sheet.pdf?size_mm=40`   | A4 calibration PDF                   |

Sessions live in-memory only. Lose the laptop = lose the server-side copy;
the phone keeps its own copy in `localStorage`.

## Troubleshooting

**`getUserMedia not available`** — you opened the page over plain HTTP.
Either reach it as `https://…`, or test from `http://localhost:…` on the
same machine. Mobile browsers refuse camera over HTTP.

**TLS "not private" warning every time** — that's expected for a
self-signed cert. The cert covers your current LAN IPs; if you move
networks, just restart `python server.py` and the cert is re-issued.

**Port already in use** — `python server.py --port 8444` (or whatever is
free). The server prints a clear error and exits when the port is taken.

**Detect markers fails** — make sure all four black squares are fully
inside the frame on a bright white background, no shadow cutting one in
half. Try moving the phone slightly closer.

**Camera shows but tapping does nothing on iOS** — pull down from the top
to dismiss the camera permission/share sheet, then tap inside the live
view first to give the page a "user gesture" again.

## License

MIT — see [LICENSE](./LICENSE).
