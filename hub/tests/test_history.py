import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pa_hub.history import HistoryEntry, HistoryStore

T0 = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)


def _entry(
    minutes: int,
    *,
    target: str = "jw-pc:docker:abc",
    device: str = "jw-pc",
    ok: bool = True,
) -> HistoryEntry:
    return HistoryEntry(
        ts=T0 + timedelta(minutes=minutes),
        client="mcp",
        device=device,
        target=target,
        action="restart",
        ok=ok,
        message=f"msg {minutes}",
    )


def test_add_and_recent_order(tmp_path: Path) -> None:
    store = HistoryStore(str(tmp_path / "history.db"))
    store.add(_entry(1))
    store.add(_entry(3))
    store.add(_entry(2))

    recent = store.recent()

    assert [e.message for e in recent] == ["msg 3", "msg 2", "msg 1"]
    assert recent[0].ts == T0 + timedelta(minutes=3)
    assert recent[0].ts.tzinfo is not None
    assert recent[0].ts.utcoffset() == timedelta(0)
    assert recent[0].client == "mcp"
    assert recent[0].device == "jw-pc"
    assert recent[0].target == "jw-pc:docker:abc"
    assert recent[0].action == "restart"
    assert recent[0].ok is True


def test_same_timestamp_newest_insert_first(tmp_path: Path) -> None:
    store = HistoryStore(str(tmp_path / "history.db"))
    store.add(_entry(5))
    store.add(_entry(5))
    store.add(_entry(5))
    store.add(_entry(5, target="usb"))

    recent = store.recent()

    assert recent[0].target == "usb"
    assert len(recent) == 4


def test_filter_by_target(tmp_path: Path) -> None:
    store = HistoryStore(str(tmp_path / "history.db"))
    store.add(_entry(1, target="jw-pc:docker:abc"))
    store.add(_entry(2, target="usb"))
    store.add(_entry(3, target="jw-pc:docker:abc", ok=False))

    matched = store.recent(target="jw-pc:docker:abc")

    assert [e.message for e in matched] == ["msg 3", "msg 1"]
    assert matched[0].ok is False
    assert store.recent(target="reboot") == []


def test_filter_by_device(tmp_path: Path) -> None:
    store = HistoryStore(str(tmp_path / "history.db"))
    store.add(_entry(1, device="pi1"))
    store.add(_entry(2, device="jw-pc"))

    matched = store.recent(device="pi1")

    assert [e.device for e in matched] == ["pi1"]


def test_filter_by_device_and_target(tmp_path: Path) -> None:
    store = HistoryStore(str(tmp_path / "history.db"))
    store.add(_entry(1, device="pi1", target="usb"))
    store.add(_entry(2, device="pi1", target="reboot"))
    store.add(_entry(3, device="jw-pc", target="usb"))

    matched = store.recent(device="pi1", target="usb")

    assert [e.message for e in matched] == ["msg 1"]


def test_limit_default_and_clamped(tmp_path: Path) -> None:
    store = HistoryStore(str(tmp_path / "history.db"))
    for minute in range(3):
        store.add(_entry(minute))

    assert len(store.recent(limit=0)) == 1
    assert len(store.recent(limit=-5)) == 1
    assert len(store.recent(limit=2)) == 2
    assert len(store.recent(limit=10_000)) == 3
    assert len(store.recent()) == 3


def test_limit_clamped_to_500(tmp_path: Path) -> None:
    store = HistoryStore(str(tmp_path / "history.db"))
    for minute in range(510):
        store.add(_entry(minute))

    assert len(store.recent(limit=100_000)) == 500


def test_survives_reopen(tmp_path: Path) -> None:
    db = str(tmp_path / "nested" / "history.db")
    HistoryStore(db).add(_entry(7, ok=False, target="usb"))

    reopened = HistoryStore(db)
    recent = reopened.recent()

    assert len(recent) == 1
    assert recent[0].ok is False
    assert recent[0].target == "usb"
    assert recent[0].ts == T0 + timedelta(minutes=7)


def test_creates_parent_dir_and_indexes(tmp_path: Path) -> None:
    db = tmp_path / "a" / "b" / "history.db"
    HistoryStore(str(db))

    assert db.exists()
    with sqlite3.connect(db) as conn:
        names = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
    assert "idx_history_target_ts" in names
    assert "idx_history_device_ts" in names


def test_concurrent_writes_from_threads(tmp_path: Path) -> None:
    store = HistoryStore(str(tmp_path / "history.db"))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda m: store.add(_entry(m)), range(200)))

    assert len(store.recent(limit=500)) == 200


def test_naive_timestamp_treated_as_utc(tmp_path: Path) -> None:
    store = HistoryStore(str(tmp_path / "history.db"))
    naive = HistoryEntry(
        ts=datetime(2026, 11, 1, 12, 0, 0),
        client="mcp",
        device="jw-pc",
        target="usb",
        action="on",
        ok=True,
        message="ok",
    )
    store.add(naive)

    back = store.recent()[0]
    assert back.ts == datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)
