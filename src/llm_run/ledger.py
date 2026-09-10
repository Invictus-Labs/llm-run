"""SQLite WAL ledger — one row per attempt; prompt as hash + bytes only."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
import sqlite3
from pathlib import Path
from typing import Any

from llm_run.paths import ledger_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS dispatch (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    caller TEXT,
    lane TEXT,
    prompt_hash TEXT,
    prompt_bytes INTEGER,
    cwd TEXT,
    engine TEXT,
    model TEXT,
    fallback_depth INTEGER,
    status TEXT,
    exit_code INTEGER,
    duration_s REAL,
    tokens_in INTEGER,
    tokens_out INTEGER,
    quota_event TEXT  -- classification only; never raw provider output
);
"""


def prompt_fingerprint(text: str) -> tuple[str, int]:
    raw = text.encode("utf-8")
    return hashlib.sha256(raw).hexdigest(), len(raw)


class Ledger:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or ledger_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        self._init()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.path), timeout=30)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            with conn:
                yield conn
        finally:
            conn.close()

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            conn.execute("CREATE INDEX IF NOT EXISTS dispatch_ts ON dispatch(ts)")

    def record(self, **row: Any) -> int:
        cols = (
            "ts",
            "caller",
            "lane",
            "prompt_hash",
            "prompt_bytes",
            "cwd",
            "engine",
            "model",
            "fallback_depth",
            "status",
            "exit_code",
            "duration_s",
            "tokens_in",
            "tokens_out",
            "quota_event",
        )
        values = [row.get(c) for c in cols]
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO dispatch (ts, caller, lane, prompt_hash, prompt_bytes, cwd, "
                "engine, model, fallback_depth, status, exit_code, duration_s, tokens_in, "
                "tokens_out, quota_event) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )
            conn.commit()
            return int(cur.lastrowid)

    def rows(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM dispatch ORDER BY id")
            return [dict(r) for r in cur.fetchall()]

    @staticmethod
    def read_since(path: Path, ts_iso: str) -> list[dict[str, Any]]:
        conn = sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5
        )
        conn.row_factory = sqlite3.Row
        try:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM dispatch WHERE ts >= ? ORDER BY id", (ts_iso,)
                )
            ]
        finally:
            conn.close()
