"""本地 SQLite 消息库：增量落盘，重复拉取不会重复计数。"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    group_id  TEXT NOT NULL,
    msg_id    TEXT NOT NULL,
    ts        INTEGER NOT NULL,
    sender_id TEXT,
    sender    TEXT,
    text      TEXT,
    PRIMARY KEY (group_id, msg_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_group_ts ON messages(group_id, ts);
CREATE TABLE IF NOT EXISTS groups (
    group_id   TEXT PRIMARY KEY,
    name       TEXT,
    updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.commit()

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------ 写
    def add_messages(self, group_id: str, rows) -> tuple:
        """rows: [{id, ts, sender_id, sender, text}]，返回 (新增, 已存在)。"""
        if not rows:
            return 0, 0
        unique = {}
        for row in rows:
            unique[str(row["id"])] = row
        before = self.conn.total_changes
        self.conn.executemany(
            "INSERT OR IGNORE INTO messages(group_id, msg_id, ts, sender_id, sender, text) VALUES (?,?,?,?,?,?)",
            [
                (str(group_id), str(r["id"]), int(r["ts"]), str(r.get("sender_id") or ""), str(r.get("sender") or ""), str(r.get("text") or ""))
                for r in unique.values()
            ],
        )
        self.conn.commit()
        inserted = self.conn.total_changes - before
        return inserted, len(unique) - inserted

    def upsert_group(self, group_id: str, name: str) -> None:
        if not name:
            self.conn.execute(
                "INSERT OR IGNORE INTO groups(group_id, name, updated_at) VALUES (?,?,?)",
                (str(group_id), "", int(time.time())),
            )
        else:
            self.conn.execute(
                "INSERT INTO groups(group_id, name, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(group_id) DO UPDATE SET name=excluded.name, updated_at=excluded.updated_at",
                (str(group_id), name, int(time.time())),
            )
        self.conn.commit()

    def group_names(self) -> dict:
        return {str(r["group_id"]): r["name"] for r in self.conn.execute("SELECT group_id, name FROM groups")}

    def prune(self, keep_days: int = 90) -> int:
        cutoff = int(time.time()) - int(max(1, keep_days)) * 86400
        cur = self.conn.execute("DELETE FROM messages WHERE ts < ?", (cutoff,))
        self.conn.commit()
        return cur.rowcount or 0

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    # ------------------------------------------------------------------ 读
    def messages(self, group_id: str, start: int, end: int) -> list:
        rows = self.conn.execute(
            "SELECT msg_id, ts, sender_id, sender, text FROM messages "
            "WHERE group_id=? AND ts>=? AND ts<=? ORDER BY ts ASC, rowid ASC",
            (str(group_id), int(start), int(end)),
        )
        return [dict(r) for r in rows]

    def total_in_window(self, start: int, end: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE ts>=? AND ts<=?", (int(start), int(end))
        ).fetchone()
        return int(row["n"]) if row else 0

    def group_total(self, group_id: str) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM messages WHERE group_id=?", (str(group_id),)).fetchone()
        return int(row["n"]) if row else 0

    def earliest(self, group_id: str) -> int | None:
        row = self.conn.execute("SELECT MIN(ts) AS t FROM messages WHERE group_id=?", (str(group_id),)).fetchone()
        return int(row["t"]) if row and row["t"] is not None else None
