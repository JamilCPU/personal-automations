from pathlib import Path

import pytest
from pa_hub.history import HistoryStore
from pa_hub.pins import Pin, PinStore


def _pin(key: str = "docker:web", device: str = "pi1", name: str = "web") -> Pin:
    return Pin(device=device, pin_key=key, name=name, kind="docker")


def test_add_get_all_remove(tmp_path: Path) -> None:
    store = PinStore(str(tmp_path / "hub.db"))
    assert store.all() == []
    store.add(_pin())
    store.add(_pin("process:Discord", device="jw-pc", name="Discord"))
    assert store.get("pi1", "docker:web") == _pin()
    assert store.get("jw-pc", "docker:web") is None
    assert {(p.device, p.pin_key) for p in store.all()} == {
        ("pi1", "docker:web"),
        ("jw-pc", "process:Discord"),
    }
    assert store.remove("pi1", "docker:web") is True
    assert store.remove("pi1", "docker:web") is False
    assert [p.pin_key for p in store.all()] == ["process:Discord"]


def test_add_is_idempotent_and_refreshes_name(tmp_path: Path) -> None:
    store = PinStore(str(tmp_path / "hub.db"))
    store.add(_pin(name="old"))
    store.add(_pin(name="new"))
    assert store.all() == [_pin(name="new")]


def test_persists_and_shares_history_db(tmp_path: Path) -> None:
    path = str(tmp_path / "hub.db")
    HistoryStore(path)
    PinStore(path).add(_pin())
    assert PinStore(path).all() == [_pin()]
    assert HistoryStore(path).recent() == []


def test_memory_db_rejected() -> None:
    with pytest.raises(ValueError, match="file path"):
        PinStore(":memory:")
