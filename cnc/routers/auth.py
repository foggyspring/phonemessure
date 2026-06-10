"""Auth routes: login (with per-IP throttle) and token introspection."""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request

from .. import auth, store
from ..api import _DUMMY_PW_HASH, _login_fails, _login_throttled, _record_login_fail

router = APIRouter()


@router.post("/api/login")
def login(body: dict, request: Request) -> dict:
    ip = request.client.host if request.client else "unknown"
    if _login_throttled(ip):
        raise HTTPException(status_code=429, detail="尝试过于频繁，请稍后再试 too many attempts")
    username = str(body.get("username", ""))
    password = str(body.get("password", ""))
    u = store.get_user(username)
    # Verify against a real (or dummy) hash either way so a missing username
    # costs the same PBKDF2 time as a wrong password (no timing enumeration).
    ok = auth.verify_password(password, u["pw_hash"] if u else _DUMMY_PW_HASH)
    if not u or not ok:
        _record_login_fail(ip)
        raise HTTPException(status_code=401, detail="用户名或密码错误 invalid credentials")
    _login_fails.pop(ip, None)   # reset on success
    ttl = 8 * 3600
    token = auth.make_token(store.get_secret(), username, u["role"], ttl)
    return {"token": token, "username": username, "role": u["role"], "expires_in": ttl}


@router.get("/api/me")
def me(authorization: str | None = Header(default=None)) -> dict:
    token = auth.bearer_from_header(authorization)
    payload = auth.verify_token(store.get_secret(), token) if token else None
    if not payload:
        raise HTTPException(status_code=401, detail="not authenticated")
    return {"username": payload["u"], "role": payload["r"], "exp": payload["exp"]}
