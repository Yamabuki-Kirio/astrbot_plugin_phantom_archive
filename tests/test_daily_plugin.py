"""插件层的每日运势：开关、降级、跨群一致、重启不变、只读资料库。

这些用例跑在真实 AstrBot 环境里（未安装则跳过），
渲染服务用 httpx.MockTransport 在进程内模拟，不需要外部服务。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import threading
from datetime import date
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = PLUGIN_ROOT.name
DB_PATH = PLUGIN_ROOT / "data" / "p5x_zh.sqlite3"

httpx = pytest.importorskip("httpx")
pytest.importorskip("PIL")
pytest.importorskip("jinja2")


@pytest.fixture(scope="module", autouse=True)
def astrbot_env(tmp_path_factory):
    cwd = os.getcwd()
    probe = tmp_path_factory.mktemp("astrbot_cwd_daily")
    os.chdir(probe)
    try:
        pytest.importorskip("astrbot", reason="未安装 AstrBot，跳过每日运势插件测试")
    finally:
        os.chdir(cwd)


class FakePlatformMeta:
    def __init__(self, name: str = "aiocqhttp"):
        self.name = name
        self.id = name


class FakeEvent:
    """插件真正用到的接口：plain_result / chain_result / 发起者 / 平台。"""

    def __init__(
        self,
        message_str: str,
        *,
        user_id: str = "10001",
        name: str = "山吹桐生",
        platform: str = "aiocqhttp",
        group_id: str = "",
    ):
        self.message_str = message_str
        self._user_id = user_id
        self._name = name
        self.group_id = group_id
        self.platform_meta = FakePlatformMeta(platform)

    def plain_result(self, text: str):
        return ("text", text)

    def chain_result(self, chain):
        return ("chain", chain)

    def is_admin(self) -> bool:
        return False

    def get_sender_id(self) -> str:
        return self._user_id

    def get_sender_name(self) -> str:
        return self._name


def run(handler, event) -> list:
    async def _collect():
        return [result async for result in handler(event)]

    return asyncio.run(_collect())


@pytest.fixture(scope="module")
def plugin_module(astrbot_env):
    if str(PLUGIN_ROOT.parent) not in sys.path:
        sys.path.insert(0, str(PLUGIN_ROOT.parent))
    return pytest.importorskip(f"{PACKAGE_NAME}.main", reason="插件模块导入失败")


@pytest.fixture
def make_plugin(plugin_module, tmp_path, monkeypatch):
    from importlib import import_module

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(plugin_module, "DATA_DIR", data_dir)

    renderer_module = import_module(f"{PACKAGE_NAME}.archive.image_renderer")
    created = []

    def factory(*, handler=None, **config):
        base = {
            "database_path": str(DB_PATH),
            "max_message_length": 1200,
            "t2i_endpoint": "http://t2i.invalid/render",
            "daily_fortune_enabled": True,
        }
        base.update(config)
        plugin = plugin_module.PhantomArchivePlugin(context=None, config=base)
        if handler is not None:
            plugin._image_renderer = renderer_module.ImageRenderer(
                plugin.render_settings, transport=httpx.MockTransport(handler)
            )
        created.append(plugin)
        return plugin

    yield factory

    for plugin in created:
        if plugin._daily_store is not None:
            plugin._daily_store.close()
            plugin._daily_store = None
        if plugin._service is not None:
            plugin._service.close()
            plugin._service = None
        plugin._image_renderer = None


def ok_handler(request: httpx.Request) -> httpx.Response:
    import io

    from PIL import Image as PILImage

    buffer = io.BytesIO()
    PILImage.new("RGB", (200, 120), (30, 34, 44)).save(buffer, format="PNG")
    return httpx.Response(200, content=buffer.getvalue(), headers={"content-type": "image/png"})


def fail_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, text="boom")


# ------------------------------------------------------------------ 开关与不可用


def test_disabled_returns_fixed_text(make_plugin):
    plugin = make_plugin(daily_fortune_enabled=False)
    results = run(plugin.daily_fortune_command, FakeEvent("/p5x每日运势"))
    assert results == [("text", "P5X每日运势当前未开启。")]


def test_missing_database_returns_fixed_text(make_plugin, tmp_path):
    plugin = make_plugin(database_path=str(tmp_path / "nope.sqlite3"))
    results = run(plugin.daily_fortune_command, FakeEvent("/p5x每日运势"))
    assert results == [("text", "夜幕档案数据库暂时不可用，无法抽取今日角色。")]


def test_disabled_does_not_create_daily_database(make_plugin, plugin_module, tmp_path):
    make_plugin(daily_fortune_enabled=False)
    assert not (tmp_path / "data" / "p5x_daily.sqlite3").exists()


# ------------------------------------------------------------------ 一致性


def test_same_user_same_day_returns_identical_text(make_plugin):
    plugin = make_plugin(daily_fortune_image_enabled=False)
    event = FakeEvent("/p5x每日运势")
    first = run(plugin.daily_fortune_command, event)
    for _ in range(9):
        assert run(plugin.daily_fortune_command, event) == first


def test_alias_commands_return_same_result(make_plugin):
    plugin = make_plugin(daily_fortune_image_enabled=False)
    main = run(plugin.daily_fortune_command, FakeEvent("/p5x每日运势"))
    alias = run(plugin.daily_fortune_alias_command, FakeEvent("/p5x运势"))
    today = run(plugin.daily_character_command, FakeEvent("/p5x今日角色"))
    assert main == alias == today


def test_cross_group_returns_same_result(make_plugin):
    """群号不参与用户键：同一个人在 A 群和 B 群看到的是同一份。"""
    plugin = make_plugin(daily_fortune_image_enabled=False)
    left = run(plugin.daily_fortune_command, FakeEvent("/p5x每日运势", group_id="111"))
    right = run(plugin.daily_fortune_command, FakeEvent("/p5x每日运势", group_id="222"))
    assert left == right


def test_rename_returns_same_result(make_plugin):
    plugin = make_plugin(daily_fortune_image_enabled=False)
    before = run(plugin.daily_fortune_command, FakeEvent("/p5x每日运势", name="旧名字"))
    after = run(plugin.daily_fortune_command, FakeEvent("/p5x每日运势", name="改过的名字"))
    assert before == after


def test_different_users_get_different_results(make_plugin):
    plugin = make_plugin(daily_fortune_image_enabled=False)
    texts = {
        run(plugin.daily_fortune_command, FakeEvent("/p5x每日运势", user_id=f"u{index}"))[0][1]
        for index in range(12)
    }
    assert len(texts) > 1


def test_restart_returns_same_result(make_plugin):
    """关掉再开（等价于重启）：结果必须还是同一份。"""
    first_plugin = make_plugin(daily_fortune_image_enabled=False)
    event = FakeEvent("/p5x每日运势")
    first = run(first_plugin.daily_fortune_command, event)
    first_plugin._daily_store.close()
    first_plugin._daily_store = None

    second_plugin = make_plugin(daily_fortune_image_enabled=False)
    assert run(second_plugin.daily_fortune_command, event) == first


def test_concurrent_first_visits_leave_one_record(make_plugin, plugin_module):
    plugin = make_plugin(daily_fortune_image_enabled=False)
    store = plugin._daily_store_instance()
    assert store is not None

    from archive.daily import engine

    zone, zone_name = engine.resolve_timezone("Asia/Shanghai")
    date_key = engine.date_key_of(engine.today_in(zone))

    barrier = threading.Barrier(8)
    errors: list[BaseException] = []
    results: list[str] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=5)
            fortune = plugin._load_or_create_fortune(
                date_key=date_key,
                timezone_name=zone_name,
                platform="aiocqhttp",
                user_id="10001",
                display_name="山吹桐生",
            )
            results.append(fortune.character.key + fortune.tarot.name)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert not errors
    assert len(set(results)) == 1
    assert store.count() == 1


# ------------------------------------------------------------------ 出图与降级


def test_image_success_sends_one_image(make_plugin):
    plugin = make_plugin(handler=ok_handler)
    results = run(plugin.daily_fortune_command, FakeEvent("/p5x每日运势"))
    assert len(results) == 1
    kind, chain = results[0]
    assert kind == "chain"
    assert len(chain) == 1
    assert chain[0].file.startswith("base64://")


def test_image_failure_falls_back_to_same_text(make_plugin):
    """图挂了改发文字，内容必须和图里是同一份。"""
    text_plugin = make_plugin(daily_fortune_image_enabled=False)
    expected = run(text_plugin.daily_fortune_command, FakeEvent("/p5x每日运势"))
    text_plugin._daily_store.close()
    text_plugin._daily_store = None

    broken = make_plugin(handler=fail_handler)
    actual = run(broken.daily_fortune_command, FakeEvent("/p5x每日运势"))
    assert actual == expected


def test_text_output_mentions_entertainment(make_plugin):
    plugin = make_plugin(daily_fortune_image_enabled=False)
    text = run(plugin.daily_fortune_command, FakeEvent("/p5x每日运势"))[0][1]
    assert "仅供娱乐参考" in text
    assert "今日角色：" in text


def test_image_disabled_skips_renderer(make_plugin):
    plugin = make_plugin(daily_fortune_image_enabled=False)
    results = run(plugin.daily_fortune_command, FakeEvent("/p5x每日运势"))
    assert results[0][0] == "text"


# ------------------------------------------------------------------ 只读与合规


def test_archive_database_is_untouched(make_plugin):
    before = hashlib.sha256(DB_PATH.read_bytes()).hexdigest()
    plugin = make_plugin(handler=ok_handler)
    run(plugin.daily_fortune_command, FakeEvent("/p5x每日运势"))
    after = hashlib.sha256(DB_PATH.read_bytes()).hexdigest()
    assert before == after


def test_daily_database_is_separate(make_plugin, tmp_path):
    plugin = make_plugin(daily_fortune_image_enabled=False)
    run(plugin.daily_fortune_command, FakeEvent("/p5x每日运势"))
    daily_path = tmp_path / "data" / "p5x_daily.sqlite3"
    assert daily_path.is_file()
    assert daily_path != DB_PATH


def test_record_is_persisted_with_expected_columns(make_plugin, tmp_path):
    import sqlite3

    plugin = make_plugin(daily_fortune_image_enabled=False)
    run(plugin.daily_fortune_command, FakeEvent("/p5x每日运势"))

    with sqlite3.connect(tmp_path / "data" / "p5x_daily.sqlite3") as conn:
        row = conn.execute(
            "SELECT date_key, timezone, platform_id, user_id, character_key, fortune_json"
            " FROM daily_fortunes"
        ).fetchone()

    assert row[1] == "Asia/Shanghai"
    assert row[2] == "aiocqhttp"
    assert row[3] == "10001"
    payload = json.loads(row[5])
    assert payload["character"]["key"] == row[4]
    assert payload["ganzhi"]


def test_write_failure_still_returns_result(make_plugin, plugin_module, caplog):
    """每日库写不进去也要出结果，只在日志里提示。"""
    # main.py 用的是包内相对导入，类对象与裸 ``archive.daily.repository`` 不同，必须取同一份
    store_error = plugin_module.daily_repository.DailyStoreError

    plugin = make_plugin(daily_fortune_image_enabled=False)
    store = plugin._daily_store_instance()
    assert store is not None

    def boom(**_kwargs):
        raise store_error("磁盘只读")

    plugin._daily_store.insert = boom

    with caplog.at_level("WARNING"):
        results = run(plugin.daily_fortune_command, FakeEvent("/p5x每日运势"))

    assert len(results) == 1
    kind, text = results[0]
    assert kind == "text"
    assert "今日角色：" in text
    assert "每日运势写入失败" in caplog.text


def test_no_llm_or_network_in_daily_modules():
    """每日运势不调用任何模型、不发任何外部请求。"""
    from archive.daily import engine, lingqian, presenter, repository, tarot, zhouyi

    for module in (engine, lingqian, presenter, repository, tarot, zhouyi):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for forbidden in ("httpx", "requests", "openai", "anthropic", "aiohttp", "urllib"):
            assert forbidden not in source, f"{module.__name__} 不应出现 {forbidden}"


def test_help_menu_lists_daily_command():
    from archive import renderer

    help_text = renderer.render_help()
    assert "/p5x每日运势" in help_text
    assert "抽取今日P5X角色与每日运势" in help_text
    assert "同一用户当天结果固定，次日刷新，仅供娱乐。" in help_text


def test_third_party_notices_exist():
    notices_path = PLUGIN_ROOT / "THIRD_PARTY_NOTICES.md"
    assert notices_path.is_file()
    content = notices_path.read_text(encoding="utf-8")
    assert "MIT License" in content
    assert "astrbot_plugin_cyber_divination" in content
    assert "tarot.py" in content and "lingqian.py" in content and "zhouyi.py" in content


def test_status_panel_shows_daily_info(make_plugin):
    from archive import renderer

    plugin = make_plugin(daily_fortune_image_enabled=False)
    text = renderer.render_image_status(plugin._status_info())
    assert "每日运势" in text
    assert "Asia/Shanghai" in text
