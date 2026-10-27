"""插件层图片输出测试：发图、降级、管理员指令。

在真实 AstrBot 环境里跑（未安装则跳过），渲染服务用 httpx.MockTransport
在进程内模拟，所以不需要任何外部服务。
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
import sys
from pathlib import Path

import pytest

from archive import notices, renderer

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = PLUGIN_ROOT.name
DB_PATH = PLUGIN_ROOT / "data" / "p5x_zh.sqlite3"

httpx = pytest.importorskip("httpx")
pytest.importorskip("PIL")
pytest.importorskip("jinja2")


@pytest.fixture(scope="module", autouse=True)
def astrbot_env(tmp_path_factory):
    cwd = os.getcwd()
    probe = tmp_path_factory.mktemp("astrbot_cwd_image")
    os.chdir(probe)
    try:
        pytest.importorskip("astrbot", reason="未安装 AstrBot，跳过插件图片测试")
    finally:
        os.chdir(cwd)


def _png(size: tuple[int, int] = (200, 120)) -> bytes:
    from PIL import Image as PILImage

    buffer = io.BytesIO()
    PILImage.new("RGB", size, (30, 34, 44)).save(buffer, format="PNG")
    return buffer.getvalue()


class FakeEvent:
    """实现插件真正用到的接口：plain_result / chain_result / is_admin。"""

    def __init__(self, message_str: str, *, admin: bool = False):
        self.message_str = message_str
        self._admin = admin

    def plain_result(self, text: str):
        return ("text", text)

    def chain_result(self, chain):
        return ("chain", chain)

    def is_admin(self) -> bool:
        return self._admin


@pytest.fixture(scope="module")
def plugin_module(astrbot_env):
    if str(PLUGIN_ROOT.parent) not in sys.path:
        sys.path.insert(0, str(PLUGIN_ROOT.parent))
    return pytest.importorskip(f"{PACKAGE_NAME}.main", reason="插件模块导入失败")


@pytest.fixture
def make_plugin(plugin_module):
    """按需构造插件，并把渲染客户端换成 MockTransport。

    注意必须从**插件包内**导入 ImageRenderer：测试同时把插件目录加进了
    sys.path，``archive.image_renderer`` 和 ``<pkg>.archive.image_renderer``
    会是两个不同的模块对象，异常类型对不上，except 就捕不到了。
    """
    from importlib import import_module

    renderer_module = import_module(f"{PACKAGE_NAME}.archive.image_renderer")
    created = []

    def factory(*, handler=None, **config):
        base = {
            "database_path": str(DB_PATH),
            "max_message_length": 1200,
            "t2i_endpoint": "http://t2i.invalid/render",
            "t2i_token": "tok-123",
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
        if plugin._service is not None:
            plugin._service.close()
            plugin._service = None
        plugin._image_renderer = None


def run(handler, event) -> list:
    async def _collect():
        return [result async for result in handler(event)]

    return asyncio.run(_collect())


def ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=_png(), headers={"content-type": "image/png"})


# ------------------------------------------------------------------ 成功出图


def test_character_sends_image(make_plugin):
    plugin = make_plugin(handler=ok_handler)
    results = run(plugin.character_command, FakeEvent("/p5x角色 汐见琴音"))

    assert len(results) == 1
    kind, chain = results[0]
    assert kind == "chain"
    assert len(chain) == 1
    assert chain[0].file.startswith("base64://")
    payload = base64.b64decode(chain[0].file.split("base64://", 1)[1])
    assert payload.startswith(b"\x89PNG")


def test_multi_page_query_sends_one_image_per_page(make_plugin):
    plugin = make_plugin(handler=ok_handler)
    outcome = plugin._service.all_guides_outcome("汐见琴音")
    results = run(plugin.guides_command, FakeEvent("/p5x攻略 汐见琴音"))

    assert len(results) == len(outcome.pages) > 1
    assert all(kind == "chain" for kind, _ in results)


def test_character_list_sends_image(make_plugin):
    plugin = make_plugin(handler=ok_handler)
    results = run(plugin.character_list_command, FakeEvent("/p5x角色列表"))
    assert results[0][0] == "chain"


def test_all_query_commands_send_image(make_plugin):
    plugin = make_plugin(handler=ok_handler)
    cases = (
        (plugin.character_command, "/p5x角色 汐见琴音"),
        (plugin.skills_command, "/p5x技能 汐见琴音"),
        (plugin.weapons_command, "/p5x武器 汐见琴音"),
        (plugin.character_list_command, "/p5x角色列表"),
        (plugin.ritual_command, "/p5x意识 汐见琴音"),
        (plugin.innate_command, "/p5x属性意识 汐见琴音"),
        (plugin.setting_command, "/p5x养成 汐见琴音"),
        (plugin.operation_command, "/p5x操作 汐见琴音"),
        (plugin.review_command, "/p5x评价 汐见琴音"),
        (plugin.party_command, "/p5x配队 汐见琴音"),
        (plugin.guides_command, "/p5x攻略 汐见琴音"),
    )
    for handler, message in cases:
        results = run(handler, FakeEvent(message))
        assert all(kind == "chain" for kind, _ in results), message


# ------------------------------------------------------------------ 降级与错误


def test_error_still_returns_text(make_plugin):
    plugin = make_plugin(handler=ok_handler)
    results = run(plugin.character_command, FakeEvent("/p5x角色 不存在的角色XYZ"))
    assert results[0][0] == "text"
    assert "没有找到" in results[0][1]


def test_usage_still_returns_text(make_plugin):
    plugin = make_plugin(handler=ok_handler)
    results = run(plugin.character_command, FakeEvent("/p5x角色"))
    assert results[0][0] == "text"
    assert results[0][1].startswith("用法：/p5x角色")


def test_render_failure_falls_back_to_text(make_plugin):
    plugin = make_plugin(handler=lambda request: httpx.Response(502, text="bad gateway"))
    results = run(plugin.character_command, FakeEvent("/p5x角色 汐见琴音"))

    assert results[0][0] == "text"
    assert results[0][1].startswith("【汐见琴音】")
    assert notices.DISCLAIMER in results[0][1]


def test_render_failure_error_mode_reports_only(make_plugin):
    plugin = make_plugin(
        handler=lambda request: httpx.Response(502, text="bad gateway"),
        t2i_fallback_mode="error",
    )
    results = run(plugin.character_command, FakeEvent("/p5x角色 汐见琴音"))

    assert results[0][0] == "text"
    assert results[0][1].startswith("图片渲染失败")
    assert "502" in results[0][1]


def test_non_image_response_falls_back(make_plugin):
    plugin = make_plugin(
        handler=lambda request: httpx.Response(200, content=b"<!DOCTYPE html><html>x</html>")
    )
    results = run(plugin.character_command, FakeEvent("/p5x角色 汐见琴音"))
    assert results[0][0] == "text"
    assert results[0][1].startswith("【汐见琴音】")


def test_image_disabled_sends_text(make_plugin):
    plugin = make_plugin(handler=ok_handler, image_output_enabled=False)
    results = run(plugin.character_command, FakeEvent("/p5x角色 汐见琴音"))
    assert results[0][0] == "text"


def test_missing_endpoint_sends_text(make_plugin):
    plugin = make_plugin(handler=ok_handler, t2i_endpoint="")
    results = run(plugin.character_command, FakeEvent("/p5x角色 汐见琴音"))
    assert results[0][0] == "text"


def test_database_missing_still_reports_text(plugin_module, tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_module, "DATA_DIR", tmp_path)
    plugin = plugin_module.PhantomArchivePlugin(
        context=None,
        config={"database_path": str(tmp_path / "nope.sqlite3")},
    )
    results = run(plugin.character_command, FakeEvent("/p5x角色 汐见琴音"))
    assert results[0][0] == "text"
    assert results[0][1] == renderer.render_database_missing()


# ------------------------------------------------------------------ 管理员指令


def test_render_status_requires_admin(make_plugin):
    plugin = make_plugin(handler=ok_handler)
    results = run(plugin.render_status_command, FakeEvent("/p5x渲染状态"))
    assert results[0][1] == renderer.render_admin_only()


def test_render_status_shows_config_without_token(make_plugin):
    plugin = make_plugin(handler=ok_handler, t2i_token="super-secret-token")
    results = run(plugin.render_status_command, FakeEvent("/p5x渲染状态", admin=True))

    text = results[0][1]
    assert "【夜幕档案 · 渲染状态】" in text
    assert "http://t2i.invalid/render" in text
    assert "访问令牌：已设置" in text
    assert "super-secret-token" not in text


def test_render_status_when_not_configured(make_plugin):
    plugin = make_plugin(handler=ok_handler, t2i_endpoint="")
    results = run(plugin.render_status_command, FakeEvent("/p5x渲染状态", admin=True))
    assert "未配置" in results[0][1]


def test_test_render_requires_admin(make_plugin):
    plugin = make_plugin(handler=ok_handler)
    results = run(plugin.test_render_command, FakeEvent("/p5x测试渲染"))
    assert results[0][1] == renderer.render_admin_only()


def test_test_render_success(make_plugin):
    plugin = make_plugin(handler=ok_handler)
    results = run(plugin.test_render_command, FakeEvent("/p5x测试渲染", admin=True))

    assert results[0][0] == "chain"
    assert results[1][0] == "text"
    assert "结果：成功" in results[1][1]
    assert "HTML 转义：正常" in results[1][1]


def test_test_render_failure(make_plugin):
    plugin = make_plugin(handler=lambda request: httpx.Response(500, text="boom"))
    results = run(plugin.test_render_command, FakeEvent("/p5x测试渲染", admin=True))

    assert len(results) == 1
    assert "结果：失败" in results[0][1]


def test_test_render_when_not_configured(make_plugin):
    plugin = make_plugin(handler=ok_handler, t2i_endpoint="")
    results = run(plugin.test_render_command, FakeEvent("/p5x测试渲染", admin=True))
    assert "结果：失败" in results[0][1]


# ------------------------------------------------------------------ 帮助与卸载


def test_help_mentions_render_commands(make_plugin):
    plugin = make_plugin()
    results = run(plugin.help_command, FakeEvent("/夜幕帮助"))
    assert "/p5x渲染状态" in results[0][1]
    assert "/p5x抽卡记录（待实现）" in results[0][1]


def test_terminate_closes_renderer(make_plugin):
    plugin = make_plugin(handler=ok_handler)
    plugin._renderer_instance()
    asyncio.run(plugin.terminate())
    assert plugin._image_renderer is None
    assert plugin._service is None
