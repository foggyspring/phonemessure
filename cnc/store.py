"""Persistence layer (SQLite, stdlib only) — phase-3 business plumbing.

Stores two things the brief calls for ("存储材料账期价格、机床费率、客户报价历史"):

  * quote history   — every generated quote, retrievable + re-printable later
  * price overrides  — runtime edits to material unit price / machine rate, so
    an estimator can maintain "当日市场克单价" without redeploying

The DB path defaults to cnc/data/quotes.db and is overridable with the CNC_DB
env var. Nothing here imports the web layer, so it stays unit-testable.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

_DEFAULT_DB = Path(__file__).resolve().parent / "data" / "quotes.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS quotes (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    part_name   TEXT,
    material    TEXT,
    quantity    INTEGER,
    unit_price  REAL,
    line_total  REAL,
    currency    TEXT,
    payload     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS price_overrides (
    kind        TEXT NOT NULL,      -- 'material' | 'machine'
    key         TEXT NOT NULL,      -- e.g. 'AL6061' | 'mill_3axis'
    field       TEXT NOT NULL,      -- e.g. 'price_cny_per_kg' | 'rate_cny_per_hour'
    value       REAL NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (kind, key, field)
);
CREATE TABLE IF NOT EXISTS users (
    username    TEXT PRIMARY KEY,
    pw_hash     TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'admin',
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    actor       TEXT,
    action      TEXT NOT NULL,
    detail      TEXT
);
CREATE TABLE IF NOT EXISTS ai_sessions (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    title       TEXT,
    messages    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS calibration_samples (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,
    material      TEXT NOT NULL,
    backend       TEXT,
    quote_id      TEXT,
    estimated_min REAL NOT NULL,
    actual_min    REAL NOT NULL
);
"""


def _db_path(path: str | os.PathLike | None = None) -> Path:
    return Path(path or os.environ.get("CNC_DB") or _DEFAULT_DB)


def _connect(path: str | os.PathLike | None = None) -> sqlite3.Connection:
    p = _db_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=5.0)
    conn.row_factory = sqlite3.Row
    # WAL + a busy timeout keep concurrent quote-saves from hitting
    # "database is locked" under request bursts (writers wait, not fail).
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)          # CREATE IF NOT EXISTS — safe to re-run
    _migrate(conn)
    return conn


# Schema version + ordered migrations. Bump _SCHEMA_VERSION and append an entry
# when a column/table change can't be expressed as a plain CREATE IF NOT EXISTS
# (e.g. ALTER TABLE / backfill), so old databases upgrade in place.
_SCHEMA_VERSION = 1
_MIGRATIONS: dict[int, list[str]] = {
    # 2: ["ALTER TABLE quotes ADD COLUMN ...", ...],
}


def _migrate(conn: sqlite3.Connection) -> None:
    cur = conn.execute("PRAGMA user_version").fetchone()[0]
    if cur >= _SCHEMA_VERSION:
        return
    for v in range(cur + 1, _SCHEMA_VERSION + 1):
        for stmt in _MIGRATIONS.get(v, []):
            conn.execute(stmt)
    conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------- quotes ----
def save_quote(payload: dict, *, path: str | os.PathLike | None = None) -> str:
    """Persist a /api/quote result and return its new id."""
    qid = "Q" + uuid.uuid4().hex[:12]
    inp = payload.get("input", {})
    req = payload.get("quote", {}).get("requested", {})
    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO quotes (id, created_at, part_name, material, quantity, "
            "unit_price, line_total, currency, payload) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                qid,
                _now(),
                inp.get("part_name"),
                inp.get("material"),
                req.get("quantity"),
                req.get("unit_price_cny"),
                req.get("line_total_cny"),
                payload.get("quote", {}).get("currency"),
                json.dumps(payload, ensure_ascii=False),
            ),
        )
    return qid


def get_quote(qid: str, *, path: str | os.PathLike | None = None) -> dict | None:
    with _connect(path) as conn:
        row = conn.execute("SELECT payload FROM quotes WHERE id=?", (qid,)).fetchone()
    if not row:
        return None
    payload = json.loads(row["payload"])
    payload["id"] = qid
    return payload


def list_quotes(
    limit: int = 50, *, offset: int = 0, search: str = "",
    path: str | os.PathLike | None = None,
) -> tuple[list[dict], int]:
    """Return (rows, total). Optional case-insensitive search on part name /
    material; offset/limit for pagination."""
    cols = ("id, created_at, part_name, material, quantity, unit_price, "
            "line_total, currency")
    where, args = "", []
    if search:
        where = " WHERE part_name LIKE ? OR material LIKE ?"
        args = [f"%{search}%", f"%{search}%"]
    limit = max(1, min(200, int(limit)))
    offset = max(0, int(offset))
    with _connect(path) as conn:
        total = conn.execute(f"SELECT COUNT(*) AS n FROM quotes{where}", args).fetchone()["n"]
        rows = conn.execute(
            f"SELECT {cols} FROM quotes{where} ORDER BY created_at DESC, rowid DESC "
            "LIMIT ? OFFSET ?", (*args, limit, offset),
        ).fetchall()
    return [dict(r) for r in rows], total


# ----------------------------------------------------------- overrides ----
def set_override(
    kind: str, key: str, field: str, value: float,
    *, path: str | os.PathLike | None = None,
) -> None:
    if kind not in ("material", "machine", "finish", "business", "capp", "cutting"):
        raise ValueError("kind must be material/machine/finish/business/capp/cutting")
    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO price_overrides (kind, key, field, value, updated_at) "
            "VALUES (?,?,?,?,?) ON CONFLICT(kind, key, field) "
            "DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (kind, key, field, float(value), _now()),
        )


def get_overrides(*, path: str | os.PathLike | None = None) -> dict:
    """Return {'material': {key: {field: value}}, 'machine': {...}}."""
    out: dict[str, dict] = {"material": {}, "machine": {}}
    with _connect(path) as conn:
        for r in conn.execute("SELECT kind, key, field, value FROM price_overrides"):
            out.setdefault(r["kind"], {}).setdefault(r["key"], {})[r["field"]] = r["value"]
    return out


def clear_overrides(*, path: str | os.PathLike | None = None) -> None:
    with _connect(path) as conn:
        conn.execute("DELETE FROM price_overrides")


def clear_override(kind: str, key: str, field: str,
                   *, path: str | os.PathLike | None = None) -> int:
    """Revert one override back to the static default. Returns rows removed."""
    with _connect(path) as conn:
        cur = conn.execute("DELETE FROM price_overrides WHERE kind=? AND key=? AND field=?",
                           (kind, key, field))
        return cur.rowcount


# ----------------------------------------------------------- auth / users --
import secrets as _secrets


def get_secret(*, path: str | os.PathLike | None = None) -> str:
    """Token-signing secret: $CNC_SECRET, else a generated value persisted once."""
    env = os.environ.get("CNC_SECRET")
    if env:
        return env
    with _connect(path) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key='secret'").fetchone()
        if row:
            return row["value"]
        val = _secrets.token_hex(32)
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('secret', ?)", (val,))
        return val


def create_user(username: str, pw_hash: str, role: str = "admin",
                *, path: str | os.PathLike | None = None) -> None:
    with _connect(path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO users (username, pw_hash, role, created_at) "
            "VALUES (?,?,?,?)",
            (username, pw_hash, role, _now()),
        )


def get_user(username: str, *, path: str | os.PathLike | None = None) -> dict | None:
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT username, pw_hash, role FROM users WHERE username=?", (username,)
        ).fetchone()
    return dict(row) if row else None


def count_users(*, path: str | os.PathLike | None = None) -> int:
    with _connect(path) as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]


# ------------------------------------------------------- calibration ----
def add_calibration_sample(
    material: str, estimated_min: float, actual_min: float,
    *, backend: str | None = None, quote_id: str | None = None,
    path: str | os.PathLike | None = None,
) -> None:
    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO calibration_samples "
            "(created_at, material, backend, quote_id, estimated_min, actual_min) "
            "VALUES (?,?,?,?,?,?)",
            (_now(), material, backend, quote_id, float(estimated_min), float(actual_min)),
        )


def calibration_samples(*, path: str | os.PathLike | None = None) -> list[dict]:
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT material, backend, estimated_min, actual_min FROM calibration_samples"
        ).fetchall()
    return [dict(r) for r in rows]


def add_audit(actor: str, action: str, detail: str = "",
              *, path: str | os.PathLike | None = None) -> None:
    with _connect(path) as conn:
        conn.execute("INSERT INTO audit_log (created_at, actor, action, detail) VALUES (?,?,?,?)",
                     (_now(), actor, action, detail))


def list_audit(*, limit: int = 100, path: str | os.PathLike | None = None) -> list[dict]:
    with _connect(path) as conn:
        rows = conn.execute("SELECT created_at, actor, action, detail FROM audit_log "
                            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def save_ai_session(session_id: str, messages: list, title: str = "",
                    *, path: str | os.PathLike | None = None) -> None:
    import json as _json
    with _connect(path) as conn:
        conn.execute("INSERT OR REPLACE INTO ai_sessions (id, created_at, title, messages) "
                     "VALUES (?,?,?,?)", (session_id, _now(), title, _json.dumps(messages, ensure_ascii=False)))


def list_ai_sessions(*, limit: int = 30, path: str | os.PathLike | None = None) -> list[dict]:
    with _connect(path) as conn:
        rows = conn.execute("SELECT id, created_at, title FROM ai_sessions "
                            "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_ai_session(session_id: str, *, path: str | os.PathLike | None = None) -> dict | None:
    import json as _json
    with _connect(path) as conn:
        row = conn.execute("SELECT id, created_at, title, messages FROM ai_sessions WHERE id=?",
                           (session_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["messages"] = _json.loads(d["messages"])
    return d


def time_factors(*, path: str | os.PathLike | None = None) -> dict:
    from .calibration import compute_time_factors
    return compute_time_factors(calibration_samples(path=path))
