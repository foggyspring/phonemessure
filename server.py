"""phonemessure — entry point.

Usage:
    python server.py                 # auto LAN IP, self-signed HTTPS, port 8443
    python server.py --port 8000
    python server.py --no-cert       # plain HTTP (only useful on localhost
                                       or when you place your own real cert
                                       behind a reverse proxy)
    python server.py --cert ./fullchain.pem --key ./privkey.pem

Browsers refuse to expose the camera over plain HTTP except on `localhost`,
so the default path is HTTPS with a self-signed cert covering every LAN IP
on this host. The phone will warn once, you accept, and the camera works.
"""
from __future__ import annotations

import argparse
import io
import socket
import sys
from pathlib import Path

import qrcode
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.certs import ensure_cert
from app.network import all_lan_ips, primary_lan_ip
from app.routes import build_router


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
CERT_DIR = ROOT / "certs"


def _print_qr(url: str) -> None:
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    buf = io.StringIO()
    qr.print_ascii(out=buf, invert=True)
    sys.stdout.write(buf.getvalue())
    sys.stdout.flush()


def _banner(url: str, extra_urls: list[str]) -> None:
    bar = "─" * 56
    print()
    print(f"  ╭{bar}╮")
    print(f"  │  phonemessure ready                                    │")
    print(f"  ╰{bar}╯")
    print(f"  Scan with your phone (same Wi-Fi):")
    print(f"    {url}")
    if extra_urls:
        print(f"  Other reachable URLs:")
        for u in extra_urls:
            print(f"    {u}")
    print()
    _print_qr(url)
    print("  Tip: accept the TLS warning on the phone — the cert is")
    print("       self-signed but only valid on your LAN.")
    print()


def _check_port(host: str, port: int) -> None:
    """Fail early with a clear message if the port is occupied."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, port))
    except OSError as exc:
        raise SystemExit(
            f"error: cannot bind {host}:{port} ({exc}). "
            f"Is another phonemessure already running? "
            f"Try `--port {port + 1}`."
        ) from exc
    finally:
        s.close()


def build_app() -> FastAPI:
    if not STATIC.exists():
        raise SystemExit(f"error: static/ directory not found at {STATIC}")
    app = FastAPI(title="phonemessure", version="0.1.0")
    app.include_router(build_router(STATIC))
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
    return app


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="phonemessure — phone-camera ruler over LAN")
    p.add_argument("--port", type=int, default=8443, help="port to listen on (default 8443)")
    p.add_argument("--host", default="0.0.0.0", help="bind address (default 0.0.0.0)")
    p.add_argument("--no-cert", action="store_true", help="serve plain HTTP (camera will only work on localhost)")
    p.add_argument("--cert", type=Path, help="path to a real TLS cert (PEM); pairs with --key")
    p.add_argument("--key", type=Path, help="path to a real TLS private key (PEM)")
    p.add_argument("--warmup", action="store_true",
                   help="pre-load YOLO-World before serving (first request gets ~3s back)")
    args = p.parse_args(argv)

    if (args.cert is None) != (args.key is None):
        raise SystemExit("error: --cert and --key must be used together")

    _check_port(args.host, args.port)

    try:
        app = build_app()
    except ModuleNotFoundError as exc:
        raise SystemExit(
            f"error: missing dependency ({exc.name}). "
            f"Run: pip install -r requirements.txt"
        ) from exc

    ips = all_lan_ips()
    primary = primary_lan_ip()

    ssl_certfile: str | None = None
    ssl_keyfile: str | None = None
    scheme = "http"

    if args.cert and args.key:
        if not args.cert.exists() or not args.key.exists():
            raise SystemExit("error: --cert or --key path does not exist")
        ssl_certfile, ssl_keyfile = str(args.cert), str(args.key)
        scheme = "https"
    elif not args.no_cert:
        cert_path, key_path = ensure_cert(CERT_DIR, ips)
        ssl_certfile, ssl_keyfile = str(cert_path), str(key_path)
        scheme = "https"

    primary_url = f"{scheme}://{primary}:{args.port}/"
    extras = [f"{scheme}://{ip}:{args.port}/" for ip in ips if ip != primary]
    _banner(primary_url, extras)

    if args.warmup:
        try:
            from app.inference import yolo as yolo_mod
            from app.inference.runtime import select_device
            print(f"  warmup: device={select_device()}, loading YOLO-World…", flush=True)
            err = yolo_mod.warmup()
            if err:
                print(f"  warmup: skipped — {err}")
            else:
                print("  warmup: ok")
        except Exception as e:
            print(f"  warmup: skipped — {e}")

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
        log_level="info",
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
