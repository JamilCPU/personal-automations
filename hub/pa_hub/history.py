"""SQLite action history: who did what to which device/program, when, and whether it worked.

Each call opens its own connection, so the store is safe to use from multiple threads
(the hub calls it from async code via worker threads).
"""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

DEFAULT_LIMIT = 50
MIN_LIMIT = 1
MAX_LIMIT = 500

_SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    ts TEXT NOT NULL,
    client TEXT NOT NULL,
    device TEXT NOT NULL,
    target TEXT NOT NULL,
    action TEXT NOT NULL,
    ok INTEGER NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_target_ts ON history (target, ts);
CREATE INDEX IF NOT EXISTS idx_history_device_ts ON history (device, ts);
"""


class HistoryEntry(BaseModel):
    ts: datetime
    client: str
    device: str
    target: str
    action: str
    ok: bool
    message: str


def _to_utc_text(ts: datetime) -> str:
    aware = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
    return aware.isoformat(timespec="microseconds")


def _clamp(limit: int) -> int:
    return max(MIN_LIMIT, min(limit, MAX_LIMIT))


class HistoryStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def add(self, entry: HistoryEntry) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO history (ts, client, device, target, action, ok, message) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    _to_utc_text(entry.ts),
                    entry.client,
                    entry.device,
                    entry.target,
                    entry.action,
                    int(entry.ok),
                    entry.message,
                ),
            )

    def recent(
        self,
        limit: int = DEFAULT_LIMIT,
        device: str | None = None,
        target: str | None = None,
    ) -> list[HistoryEntry]:
        clauses: list[str] = []
        params: list[str | int] = []
        if device is not None:
            clauses.append("device = ?")
            params.append(device)
        if target is not None:
            clauses.append("target = ?")
            params.append(target)
        where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        sql = (
            "SELECT ts, client, device, target, action, ok, message FROM history "
            f"{where}ORDER BY ts DESC, rowid DESC LIMIT ?"
        )
        params.append(_clamp(limit))
        with closing(self._connect()) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            HistoryEntry(
                ts=datetime.fromisoformat(ts),
                client=client,
                device=device_id,
                target=row_target,
                action=action,
                ok=bool(ok),
                message=message,
            )
            for ts, client, device_id, row_target, action, ok, message in rows
        ]
