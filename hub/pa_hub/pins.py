"""SQLite pins: programs a person wants always listed, keyed by (device, pin key).

Lives in the same database file as the action history. Each call opens its own
connection, so the store is safe to use from worker threads.
"""

import sqlite3
from contextlib import closing
from pathlib import Path

from pa_core.models import Kind
from pydantic import BaseModel

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pins (
    device TEXT NOT NULL,
    pin_key TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    PRIMARY KEY (device, pin_key)
);
"""


class Pin(BaseModel):
    device: str
    pin_key: str
    name: str
    kind: Kind


class PinStore:
    def __init__(self, db_path: str) -> None:
        if db_path == ":memory:":
            raise ValueError("PinStore needs a file path; ':memory:' is not supported")
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def add(self, pin: Pin) -> None:
        """Pin (or re-pin, refreshing the stored name and kind)."""
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO pins (device, pin_key, name, kind) VALUES (?, ?, ?, ?)",
                (pin.device, pin.pin_key, pin.name, pin.kind),
            )

    def remove(self, device: str, pin_key: str) -> bool:
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                "DELETE FROM pins WHERE device = ? AND pin_key = ?", (device, pin_key)
            )
            return cursor.rowcount > 0

    def get(self, device: str, pin_key: str) -> Pin | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT device, pin_key, name, kind FROM pins WHERE device = ? AND pin_key = ?",
                (device, pin_key),
            ).fetchone()
        return None if row is None else _pin(row)

    def all(self) -> list[Pin]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT device, pin_key, name, kind FROM pins ORDER BY device, pin_key"
            ).fetchall()
        return [_pin(row) for row in rows]


def _pin(row: tuple[str, str, str, str]) -> Pin:
    device, pin_key, name, kind = row
    return Pin.model_validate({"device": device, "pin_key": pin_key, "name": name, "kind": kind})
