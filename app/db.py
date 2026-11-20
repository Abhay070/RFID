"""SQLite storage for GHOST ID."""
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS owner (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT NOT NULL,
    pw_hash TEXT NOT NULL,
    band_id TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS taps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    band_id TEXT NOT NULL,
    ip TEXT, ua TEXT,
    is_owner INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    url TEXT,
    note TEXT,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS requests (
    id TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    name TEXT NOT NULL,
    reason TEXT,
    ip TEXT,
    visitor_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending/approved/denied/claimed
    minutes INTEGER,
    resource_ids TEXT,                        -- comma separated
    decided_ts REAL
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,                       -- owner / guest
    token_hash TEXT NOT NULL UNIQUE,
    label TEXT,
    created REAL NOT NULL,
    expires REAL NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    resource_ids TEXT,
    request_id TEXT,
    ip TEXT
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT,
    ip TEXT
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    with tx() as c:
        c.executescript(SCHEMA)


def audit(c: sqlite3.Connection, actor: str, action: str, detail: str = "", ip: str = "") -> None:
    c.execute(
        "INSERT INTO audit (ts, actor, action, detail, ip) VALUES (?,?,?,?,?)",
        (time.time(), actor, action, detail, ip),
    )


def get_owner(c: sqlite3.Connection):
    return c.execute("SELECT * FROM owner WHERE id = 1").fetchone()
