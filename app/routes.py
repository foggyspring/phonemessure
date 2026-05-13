"""HTTP routes.

Most state lives in the browser (localStorage). The server only handles:
  - serving the static SPA
  - per-session calibration storage (so a tablet and a phone can connect
    to the same laptop without trampling each other)
  - generating a printable ArUco PDF on demand
"""
from __future__ import annotations

import io
import secrets
import time
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse

from .aruco_pdf import build_aruco_pdf


def build_router(static_dir: Path) -> APIRouter:
    router = APIRouter()

    # sessionId -> {calibration, history, last_seen}
    sessions: Dict[str, Dict[str, Any]] = {}

    def _touch(sid: str) -> Dict[str, Any]:
        s = sessions.setdefault(sid, {"calibration": None, "history": [], "last_seen": 0.0})
        s["last_seen"] = time.time()
        return s

    @router.get("/")
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @router.get("/api/session/new")
    def new_session() -> JSONResponse:
        sid = secrets.token_urlsafe(9)
        _touch(sid)
        return JSONResponse({"sessionId": sid})

    @router.get("/api/session/{sid}")
    def get_session(sid: str) -> JSONResponse:
        s = sessions.get(sid)
        if s is None:
            raise HTTPException(404, "unknown session")
        s["last_seen"] = time.time()
        return JSONResponse({"calibration": s["calibration"], "history": s["history"]})

    @router.post("/api/session/{sid}/calibration")
    async def set_calibration(sid: str, request: Request) -> JSONResponse:
        body = await request.json()
        s = _touch(sid)
        s["calibration"] = body
        return JSONResponse({"ok": True})

    @router.post("/api/session/{sid}/history")
    async def add_history(sid: str, request: Request) -> JSONResponse:
        body = await request.json()
        s = _touch(sid)
        s["history"].append(body)
        return JSONResponse({"ok": True, "count": len(s["history"])})

    @router.delete("/api/session/{sid}/history")
    def clear_history(sid: str) -> JSONResponse:
        s = _touch(sid)
        s["history"] = []
        return JSONResponse({"ok": True})

    @router.get("/api/aruco-sheet.pdf")
    def aruco_sheet(size_mm: float = 40.0) -> Response:
        size_mm = max(15.0, min(80.0, size_mm))
        buf = io.BytesIO()
        build_aruco_pdf(buf, marker_mm=size_mm)
        return Response(
            content=buf.getvalue(),
            media_type="application/pdf",
            headers={"Content-Disposition": 'inline; filename="aruco-sheet.pdf"'},
        )

    @router.get("/api/health")
    def health() -> JSONResponse:
        return JSONResponse({"ok": True, "sessions": len(sessions)})

    return router
