"""Run the CNC quote engine.

    python -m cnc.server                 # http://127.0.0.1:8500
    python -m cnc.server --port 9000 --host 0.0.0.0
    python -m cnc.server --reload        # dev autoreload

The app is plain HTTP (no camera/secure-context requirement like the sibling
phonemessure project), so localhost works out of the box; put a reverse proxy
in front for TLS in production.
"""
from __future__ import annotations

import argparse
import logging
import os

import uvicorn


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="CNC automatic quoting engine")
    p.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=8500, help="port (default 8500)")
    p.add_argument("--reload", action="store_true", help="dev autoreload")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("CNC_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("cnc").info("CNC quote engine → http://%s:%s/", args.host, args.port)
    uvicorn.run(
        "cnc.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
