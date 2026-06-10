"""AI assistant routes: status, chat/analyze, approvals, tools, sessions."""
from __future__ import annotations

import base64
import json

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from .. import auth, store
from ..api import (
    _AI_HISTORY_MAX,
    _AI_MSG_MAX,
    _ai_cost_guard,
    _ai_throttled,
    _effective_shop,
    _metrics_from_request,
    _read_capped,
    require_admin,
)
from ..geometry import GeometryError
from ..geometry.parser import KernelUnavailable

router = APIRouter()


@router.get("/api/ai/status")
def ai_status() -> dict:
    from ..ai import get_provider
    p = get_provider()
    return {"provider": p.name, "available": p.available,
            "live": p.name != "mock",
            "note": ("使用离线模拟助手；上线接入真实 LLM 后自动切换。"
                     if p.name == "mock" else "已接入真实 LLM。")}


@router.post("/api/ai/chat")
async def ai_chat(
    request: Request,
    message: str = Form(...),
    params: str = Form("{}"),
    history: str = Form("[]"),
    file: UploadFile | None = File(None),
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    from ..ai.agent import run_agent
    from ..ai.tools import AgentContext
    ip = request.client.host if request.client else "unknown"
    if _ai_throttled(ip):
        raise HTTPException(status_code=429, detail="AI 请求过于频繁，请稍后再试。")
    _ai_cost_guard(ip, authorization)
    message = str(message)
    if len(message) > _AI_MSG_MAX:
        raise HTTPException(status_code=400, detail=f"消息过长（>{_AI_MSG_MAX} 字）。")
    try:
        p = json.loads(params) if params else {}
        hist = json.loads(history) if history else []
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"bad params/history: {exc}") from exc
    if not isinstance(p, dict) or not isinstance(hist, list):
        raise HTTPException(status_code=400, detail="params must be object, history a list")
    hist = hist[-_AI_HISTORY_MAX:]            # cap context window

    metrics = mesh_stl = None
    if file is not None:
        data = _read_capped(file)
        try:
            metrics, extra = _metrics_from_request(data, file.filename, None)
            if extra.get("preview_stl_b64"):
                mesh_stl = base64.b64decode(extra["preview_stl_b64"])
            elif file.filename and file.filename.lower().endswith(".stl"):
                mesh_stl = data
        except (GeometryError, KernelUnavailable):
            metrics = None

    tok = auth.bearer_from_header(authorization)
    is_admin = bool(tok and auth.verify_token(store.get_secret(), tok))
    shop, sources = _effective_shop()
    ctx = AgentContext(shop=shop, metrics=metrics, mesh_stl=mesh_stl, params=p,
                       price_sources=sources, calibration_factors=store.time_factors(),
                       is_admin=is_admin)
    return JSONResponse(run_agent(str(message), ctx, history=hist))


@router.post("/api/ai/approve")
def ai_approve(body: dict, _admin: dict = Depends(require_admin)) -> dict:
    """Execute an AI-proposed write action after explicit admin approval."""
    from ..ai.tools import AgentContext, dispatch, get_tool
    tool = str(body.get("tool", ""))
    t = get_tool(tool)
    if t is None or not t.requires_approval:
        raise HTTPException(status_code=400, detail="not an approvable tool")
    shop, _ = _effective_shop()
    ctx = AgentContext(shop=shop, params=body.get("params") or {}, is_admin=True)
    res = dispatch(tool, body.get("arguments") or {}, ctx)
    store.add_audit(_admin.get("u", "?"), f"ai_approve:{tool}",
                    f"{body.get('arguments')} ok={res.get('error') is None}")
    return {"ok": res.get("error") is None, "summary": res.get("summary", ""),
            "error": res.get("error"), "data": res.get("data")}


@router.get("/api/ai/tools")
def ai_tools() -> dict:
    from ..ai.tools import tool_schemas
    return {"tools": tool_schemas(is_admin=True)}


@router.post("/api/ai/session")
def ai_save_session(body: dict, request: Request) -> dict:
    # Open (the panel persists chats without a login) but bounded + rate-limited
    # so it can't be abused for unbounded storage writes.
    if _ai_throttled(request.client.host if request.client else "unknown"):
        raise HTTPException(status_code=429, detail="保存过于频繁，请稍后再试。")
    sid = str(body.get("id") or "")[:64]
    msgs = body.get("messages")
    if not sid or not isinstance(msgs, list):
        raise HTTPException(status_code=400, detail="id and messages[] required")
    # cap each message so a single request can't write an unbounded blob
    trimmed = [{"role": str(m.get("role", ""))[:16],
                "content": str(m.get("content", ""))[:_AI_MSG_MAX]}
               for m in msgs[-_AI_HISTORY_MAX:] if isinstance(m, dict)]
    title = str(body.get("title", ""))[:80]
    store.save_ai_session(sid, trimmed, title)
    return {"ok": True, "id": sid}


@router.get("/api/ai/sessions")
def ai_sessions() -> dict:
    return {"sessions": store.list_ai_sessions()}


@router.get("/api/ai/session/{sid}")
def ai_get_session(sid: str) -> dict:
    s = store.get_ai_session(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="session not found")
    return s


@router.post("/api/ai/analyze")
async def ai_analyze(
    request: Request,
    params: str = Form("{}"),
    file: UploadFile | None = File(None),
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    from ..ai.agent import analyze_part
    from ..ai.tools import AgentContext
    ip = request.client.host if request.client else "unknown"
    if _ai_throttled(ip):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试。")
    _ai_cost_guard(ip, authorization)
    try:
        p = json.loads(params) if params else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"bad params: {exc}") from exc
    if not isinstance(p, dict):
        raise HTTPException(status_code=400, detail="params must be an object")
    metrics = mesh_stl = None
    if file is not None:
        data = _read_capped(file)
        try:
            metrics, extra = _metrics_from_request(data, file.filename, None)
            if extra.get("preview_stl_b64"):
                mesh_stl = base64.b64decode(extra["preview_stl_b64"])
            elif file.filename and file.filename.lower().endswith(".stl"):
                mesh_stl = data
        except (GeometryError, KernelUnavailable):
            metrics = None
    shop, sources = _effective_shop()
    ctx = AgentContext(shop=shop, metrics=metrics, mesh_stl=mesh_stl, params=p,
                       price_sources=sources, calibration_factors=store.time_factors())
    return JSONResponse(analyze_part(ctx))
