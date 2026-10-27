"""每日运势库：唯一约束、忽略式写入、并发只落一份、坏数据降级。"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from archive.daily import repository

KEY = {
    "date_key": "2026-10-27",
    "timezone": "Asia/Shanghai",
    "platform_id": "aiocqhttp",
    "user_id": "10001",
}


def payload(marker: str) -> dict:
    return {"character": {"key": marker, "name": marker}, "ganzhi": "甲戌"}


@pytest.fixture
def store(tmp_path) -> repository.DailyFortuneStore:
    instance = repository.DailyFortuneStore(tmp_path / "p5x_daily.sqlite3")
    yield instance
    instance.close()


def insert(store, marker: str, **overrides) -> bool:
    kwargs = dict(KEY)
    kwargs.update(overrides)
    return store.insert(
        **kwargs,
        display_name="山吹桐生",
        character_key=marker,
        payload=payload(marker),
    )


def test_schema_is_created(tmp_path):
    path = tmp_path / "p5x_daily.sqlite3"
    store = repository.DailyFortuneStore(path)
    try:
        assert path.is_file()
        assert store.count() == 0
    finally:
        store.close()

    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(daily_fortunes)")}
    assert columns == {
        "date_key",
        "timezone",
        "platform_id",
        "user_id",
        "display_name",
        "character_key",
        "fortune_json",
        "created_at",
    }


def test_insert_then_get(store):
    assert insert(store, "k001") is True
    record = store.get(**KEY)
    assert record is not None
    assert record.character_key == "k001"
    assert record.payload["character"]["key"] == "k001"
    assert record.display_name == "山吹桐生"
    assert record.created_at.endswith("Z")


def test_second_insert_is_ignored(store):
    assert insert(store, "first") is True
    assert insert(store, "second") is False
    record = store.get(**KEY)
    assert record.character_key == "first"
    assert store.count() == 1


def test_different_user_is_a_different_row(store):
    insert(store, "a", user_id="10001")
    insert(store, "b", user_id="10002")
    assert store.count() == 2
    assert store.get(**{**KEY, "user_id": "10002"}).character_key == "b"


def test_different_timezone_is_a_different_row(store):
    insert(store, "a", timezone="Asia/Shanghai")
    insert(store, "b", timezone="UTC")
    assert store.count() == 2


def test_missing_record_returns_none(store):
    assert store.get(**KEY) is None


def test_corrupt_json_degrades_to_empty_payload(tmp_path):
    path = tmp_path / "p5x_daily.sqlite3"
    store = repository.DailyFortuneStore(path)
    insert(store, "k001")
    store.close()

    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE daily_fortunes SET fortune_json = '{oops'")
        conn.commit()

    reopened = repository.DailyFortuneStore(path)
    try:
        record = reopened.get(**KEY)
        assert record is not None
        assert record.payload == {}
    finally:
        reopened.close()


def test_concurrent_writers_leave_exactly_one_row(tmp_path):
    """8 个线程同时抢着写同一天：只允许一行，且读到的都是同一份。"""
    path = tmp_path / "p5x_daily.sqlite3"
    store = repository.DailyFortuneStore(path)
    barrier = threading.Barrier(8)
    errors: list[BaseException] = []

    def worker(index: int) -> None:
        try:
            barrier.wait(timeout=5)
            insert(store, f"k{index:03d}")
        except BaseException as exc:  # noqa: BLE001 - 收集后在主线程断言
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    assert store.count() == 1
    records = {store.get(**KEY).character_key for _ in range(8)}
    assert len(records) == 1
    store.close()


def test_store_refuses_archive_database_path(tmp_path):
    with pytest.raises(repository.DailyStoreError):
        repository.DailyFortuneStore(tmp_path / "p5x_zh.sqlite3")
    with pytest.raises(repository.DailyStoreError):
        repository.DailyFortuneStore(tmp_path / "p5x.sqlite3")


def test_store_creates_parent_directory(tmp_path):
    target = tmp_path / "nested" / "deeper" / "p5x_daily.sqlite3"
    store = repository.DailyFortuneStore(target)
    try:
        assert target.is_file()
    finally:
        store.close()


def test_payload_is_stored_as_compact_json(store):
    insert(store, "k001")
    with sqlite3.connect(store.path) as conn:
        raw = conn.execute("SELECT fortune_json FROM daily_fortunes").fetchone()[0]
    assert "\n" not in raw
    assert json.loads(raw)["ganzhi"] == "甲戌"


def test_context_manager_closes(tmp_path):
    path = tmp_path / "p5x_daily.sqlite3"
    with repository.DailyFortuneStore(path) as store:
        insert(store, "k001")
        assert store.count() == 1
    assert isinstance(path, Path)
