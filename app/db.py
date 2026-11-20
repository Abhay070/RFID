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
    band_id TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS taps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    band_id TEXT NOT NULL,
    ip TEXT, ua TEXT,
    is_owner INTEGER NOT NULL DEFAULT 0,
    lang TEXT,                 -- Accept-Language header
    referer TEXT,              -- where the visitor came from
    geo TEXT,                  -- JSON: coarse city/region/country from the IP
    token TEXT                 -- random id tying the browser beacon to this tap
);
CREATE TABLE IF NOT EXISTS recon (             -- what a visitor's browser revealed
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tap_id INTEGER NOT NULL UNIQUE,
    ts REAL NOT NULL,
    ip TEXT,
    fp TEXT,                   -- JSON browser fingerprint (no permission prompt)
    photo TEXT,                -- camera still, only if the visitor tapped Allow
    lat REAL, lon REAL, acc REAL   -- precise location, only if the visitor allowed it
);
CREATE TABLE IF NOT EXISTS resources (          -- the owner's quick links
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    url TEXT,
    note TEXT,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS pairings (           -- one-time links that pair a phone
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code_hash TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    created REAL NOT NULL,
    expires REAL NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS devices (            -- the owner's paired phones
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created REAL NOT NULL,
    expires REAL NOT NULL,
    last_seen REAL,
    revoked INTEGER NOT NULL DEFAULT 0,
    ip TEXT, ua TEXT
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
        # Migrate older databases that predate the recon columns.
        for col, decl in (("lang", "TEXT"), ("referer", "TEXT"),
                          ("geo", "TEXT"), ("token", "TEXT")):
            have = [r[1] for r in c.execute("PRAGMA table_info(taps)")]
            if col not in have:
                c.execute(f"ALTER TABLE taps ADD COLUMN {col} {decl}")


def audit(c: sqlite3.Connection, actor: str, action: str, detail: str = "", ip: str = "") -> None:
    c.execute(
        "INSERT INTO audit (ts, actor, action, detail, ip) VALUES (?,?,?,?,?)",
        (time.time(), actor, action, detail, ip),
    )


def get_owner(c: sqlite3.Connection):
    return c.execute("SELECT * FROM owner WHERE id = 1").fetchone()
