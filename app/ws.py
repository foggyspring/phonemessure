"""WebSocket frame pipe.

Protocol (single endpoint at /ws/{sid}):

  Client → server
    • binary frames  ........ raw JPEG bytes; the most recent one is kept
                              as `latest_frame` for that session, plus a
                              ring buffer of size FRAME_BUFFER_LEN for
                              multi-frame ArUco averaging.
    • text JSON commands:
        {"cmd": "detect_aruco"}
        {"cmd": "detect_yolo", "prompts": ["credit card", "a4 paper"]}
        {"cmd": "refine_point", "x": 123.4, "y": 56.7, "radius": 20}
        {"cmd": "calib_start"}     – begin charuco intrinsics collection
        {"cmd": "calib_capture"}   – add current frame as a sample
        {"cmd": "calib_clear"}     – throw away current samples
        {"cmd": "calib_solve"}     – calibrate + persist
        {"cmd": "intrinsics_status"}
        {"cmd": "ping"}

  Server → client (text JSON)
        {"event": "frame_ack", "n": <count>}
        {"event": "aruco",     ...payload}
        {"event": "yolo",      ...payload}
        {"event": "calib",     ...payload}
        {"event": "intrinsics", have_intrinsics: bool, ...}
        {"event": "pong"}
        {"event": "error", "reason": str}
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .inference import pipeline as pipe_mod


router = APIRouter()


@router.websocket("/ws/{sid}")
async def ws(sock: WebSocket, sid: str) -> None:
    await sock.accept()
    pipe = pipe_mod.get_pipeline(sid)
    # Frame-ack throttle so we don't spam the phone for every JPEG.
    last_ack = 0.0

    async def send_json(obj: Dict[str, Any]) -> None:
        try:
            await sock.send_text(json.dumps(obj))
        except Exception:
            pass

    async def run_op(cmd: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        loop = asyncio.get_running_loop()
        # cv2.aruco is fast (<10ms); YOLO can be 100-300ms. Run anything
        # potentially blocking in a thread so the websocket stays responsive.
        if cmd == "detect_aruco":
            return await loop.run_in_executor(None, pipe.detect_aruco)
        if cmd == "detect_yolo":
            prompts = payload.get("prompts")
            return await loop.run_in_executor(None, lambda: pipe.detect_yolo(prompts))
        if cmd == "refine_point":
            x = float(payload.get("x", 0))
            y = float(payload.get("y", 0))
            r = int(payload.get("radius", 20))
            return await loop.run_in_executor(None, lambda: pipe.refine_point(x, y, r))
        if cmd == "calib_start":
            return await loop.run_in_executor(None, pipe.calib_start)
        if cmd == "calib_capture":
            return await loop.run_in_executor(None, pipe.calib_capture)
        if cmd == "calib_clear":
            return await loop.run_in_executor(None, pipe.calib_clear)
        if cmd == "calib_solve":
            return await loop.run_in_executor(None, pipe.calib_solve)
        if cmd == "intrinsics_status":
            return pipe.intrinsics_status()
        return {"ok": False, "reason": f"unknown cmd {cmd!r}"}

    try:
        while True:
            msg = await sock.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if (b := msg.get("bytes")) is not None:
                ok = pipe.push_jpeg(b)
                now = time.time()
                if ok and now - last_ack > 0.5:
                    last_ack = now
                    await send_json({"event": "frame_ack", "n": pipe.frame_count})
                elif not ok:
                    await send_json({"event": "error", "reason": "frame decode failed"})
                continue
            if (t := msg.get("text")) is not None:
                try:
                    payload = json.loads(t)
                except json.JSONDecodeError:
                    await send_json({"event": "error", "reason": "bad json"})
                    continue
                cmd = payload.get("cmd")
                if cmd == "ping":
                    await send_json({"event": "pong", "t": time.time()})
                    continue
                if not cmd:
                    await send_json({"event": "error", "reason": "missing cmd"})
                    continue
                try:
                    result = await run_op(cmd, payload)
                except Exception as e:
                    await send_json({"event": "error", "reason": f"{cmd} crashed: {e}"})
                    continue
                # Tag the event name so the client can route the response.
                evt = {
                    "detect_aruco":      "aruco",
                    "detect_yolo":       "yolo",
                    "refine_point":      "refine",
                    "calib_start":       "calib",
                    "calib_capture":     "calib",
                    "calib_clear":       "calib",
                    "calib_solve":       "calib",
                    "intrinsics_status": "intrinsics",
                }.get(cmd, "result")
                await send_json({"event": evt, **result})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        await send_json({"event": "error", "reason": str(e)})
