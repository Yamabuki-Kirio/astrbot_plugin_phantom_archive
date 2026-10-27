"""测试夹具：真实数据库 + 真实查询层，不做 mock。

数据库放在插件 ``data/`` 目录，由部署步骤复制；
缺失时整个测试会话直接跳过，避免把「没数据」误报成「测试通过」。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))
sys.path.insert(0, str(PLUGIN_ROOT.parent))

DB_PATH = PLUGIN_ROOT / "data" / "p5x_zh.sqlite3"

pytestmark = pytest.mark.skipif(
    not DB_PATH.is_file(),
    reason=f"测试数据库不存在：{DB_PATH}",
)


@pytest.fixture(scope="session", autouse=True)
def isolate_cwd(tmp_path_factory):
    """AstrBot 在导入/实例化时会在**当前工作目录**创建 data/。

    整个测试会话切到临时目录，避免往插件仓库里塞 cmd_config.json、temp/ 之类的杂物。
    测试里所有路径都是绝对路径，切目录不影响取数。
    """
    original = os.getcwd()
    probe = tmp_path_factory.mktemp("cwd")
    os.chdir(probe)
    try:
        yield probe
    finally:
        os.chdir(original)


@pytest.fixture(scope="session")
def db_path() -> Path:
    if not DB_PATH.is_file():
        pytest.skip(f"测试数据库不存在：{DB_PATH}")
    return DB_PATH


@pytest.fixture(scope="session")
def repository(db_path):
    from archive.database import ArchiveDatabase
    from archive.repository import ArchiveRepository

    db = ArchiveDatabase(db_path)
    yield ArchiveRepository(db)
    db.close()


@pytest.fixture(scope="session")
def service(db_path):
    from archive.service import ArchiveService

    svc = ArchiveService(db_path, max_chars=1200)
    yield svc
    svc.close()
