"""只读保证：连接、语句、文件指纹三层验证（§4 / §15）。"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from archive.database import (
    ArchiveDatabase,
    DatabaseMissingError,
    SchemaMismatchError,
    build_uri,
    open_readonly,
    resolve_database_path,
    validate_schema,
)

WRITE_STATEMENTS = [
    "INSERT INTO zh_characters (source_key) VALUES ('x')",
    "UPDATE zh_characters SET name_zh = 'x' WHERE id = 1",
    "DELETE FROM zh_characters WHERE id = 1",
    "CREATE TABLE t_should_not_exist (x INTEGER)",
    "DROP TABLE zh_skills",
    "ALTER TABLE zh_weapons ADD COLUMN should_not_exist TEXT",
    "REPLACE INTO zh_characters (id, source_key) VALUES (1, 'x')",
    "VACUUM",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_uri_is_read_only(db_path):
    uri = build_uri(db_path)
    assert uri.startswith("file:")
    assert "mode=ro" in uri
    # 非 ASCII 路径（中文目录）必须被编码，否则 sqlite3 打不开；
    # 纯 ASCII 路径（CI 的 runner 就是）本来就没有可编码字符
    if not str(db_path).isascii():
        assert "%" in uri


def test_query_only_pragma_is_on(db_path):
    conn = open_readonly(db_path)
    try:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.parametrize("statement", WRITE_STATEMENTS)
def test_write_statements_fail(db_path, statement):
    conn = open_readonly(db_path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(statement)
    finally:
        conn.close()


def test_database_file_unchanged_after_write_attempts(db_path):
    before = sha256(db_path)
    conn = open_readonly(db_path)
    try:
        for statement in WRITE_STATEMENTS:
            with pytest.raises(sqlite3.OperationalError):
                conn.execute(statement)
        conn.execute("SELECT COUNT(*) FROM zh_characters").fetchone()
    finally:
        conn.close()
    assert sha256(db_path) == before


def test_repository_layer_cannot_write(db_path):
    """即使绕过 open_readonly，ArchiveDatabase 也没有写入口。"""
    with ArchiveDatabase(db_path) as archive:
        assert archive.scalar("SELECT COUNT(*) FROM zh_characters") > 0
        with pytest.raises(Exception):
            archive.executemany_guard(["DELETE FROM zh_characters"])


def test_resolve_prefers_configured_path(db_path):
    assert resolve_database_path(str(db_path), db_path.parent) == db_path


def test_resolve_falls_back_to_data_dir(db_path):
    assert resolve_database_path("", db_path.parent) == db_path


def test_resolve_rejects_raw_database(tmp_path):
    raw = tmp_path / "p5x.sqlite3"
    raw.write_bytes(b"SQLite format 3\x00")
    with pytest.raises(DatabaseMissingError):
        resolve_database_path(str(raw), tmp_path)


def test_resolve_missing_database(tmp_path):
    with pytest.raises(DatabaseMissingError):
        resolve_database_path("", tmp_path)


def test_schema_mismatch_detected(tmp_path):
    broken = tmp_path / "broken.sqlite3"
    conn = sqlite3.connect(broken)
    conn.execute("CREATE TABLE zh_characters (id INTEGER, source_key TEXT)")
    conn.commit()
    conn.close()

    conn = open_readonly(broken)
    try:
        with pytest.raises(SchemaMismatchError):
            validate_schema(conn)
    finally:
        conn.close()
