"""插件层冒烟：在真实 AstrBot 环境里导入并驱动四个指令（§16）。

AstrBot 未安装时跳过，而不是伪造一个假的 astrbot 模块 ——
本地单元测试通过不能代替真实环境验证。

注意：AstrBot 在 import 时会在**当前工作目录**创建 ``data/``。
所以导入动作被包进一个临时目录里，避免污染插件仓库。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from archive import notices

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = PLUGIN_ROOT.name


@pytest.fixture(scope="session")
def astrbot_env(tmp_path_factory):
    """在临时目录里导入 astrbot，隔离它的 data/ 副作用。"""
    cwd = os.getcwd()
    probe = tmp_path_factory.mktemp("astrbot_cwd")
    os.chdir(probe)
    try:
        pytest.importorskip("astrbot", reason="未安装 AstrBot，跳过插件加载测试")
    finally:
        os.chdir(cwd)
    return True


class FakeEvent:
    """只实现插件真正用到的两个接口，避免依赖 OneBot 实现。"""

    def __init__(self, message_str: str):
        self.message_str = message_str

    def plain_result(self, text: str) -> str:
        return text


@pytest.fixture(scope="module")
def plugin_module(astrbot_env):
    if str(PLUGIN_ROOT.parent) not in sys.path:
        sys.path.insert(0, str(PLUGIN_ROOT.parent))
    return pytest.importorskip(f"{PACKAGE_NAME}.main", reason="插件模块导入失败")


@pytest.fixture(scope="module")
def plugin(plugin_module):
    return plugin_module.PhantomArchivePlugin(
        context=None,
        config={
            "database_path": str(PLUGIN_ROOT / "data" / "p5x_zh.sqlite3"),
            "max_message_length": 1200,
        },
    )


def run_command(handler, event) -> str:
    async def _collect():
        return [result async for result in handler(event)]

    results = asyncio.run(_collect())
    assert len(results) == 1
    return results[0]


def test_plugin_registers_four_commands(plugin):
    for name in (
        "help_command", "character_command", "skills_command", "weapons_command",
        "ritual_command", "innate_command", "setting_command", "operation_command",
        "review_command", "party_command", "guides_command",
    ):
        assert callable(getattr(plugin, name))


def test_plugin_database_ready(plugin):
    assert plugin._init_error is None
    assert plugin.database_path is not None
    assert plugin.database_path.name == "p5x_zh.sqlite3"


def test_help_command(plugin):
    text = run_command(plugin.help_command, FakeEvent("/夜幕帮助"))
    assert "【夜幕档案】" in text
    assert "/p5x角色 <名称>" in text
    assert notices.HELP_SOURCE_NOTE in text


def test_character_command(plugin):
    text = run_command(plugin.character_command, FakeEvent("/p5x角色 汐见琴音"))
    assert text.startswith("【汐见琴音】")
    assert notices.DISCLAIMER in text


def test_skills_command(plugin):
    text = run_command(plugin.skills_command, FakeEvent("/p5x技能 汐见琴音"))
    assert text.startswith("【汐见琴音】技能")
    assert "琴奏之调" in text
    assert notices.DISCLAIMER in text


def test_weapons_command(plugin):
    text = run_command(plugin.weapons_command, FakeEvent("/p5x武器 汐见琴音"))
    assert text.startswith("【汐见琴音】武器")
    assert "孔雀御前" in text
    assert notices.DISCLAIMER in text


def test_character_list_command(plugin):
    text = run_command(plugin.character_list_command, FakeEvent("/p5x角色列表"))
    assert "当前收录 72 名角色" in text


def test_phase_two_commands(plugin):
    cases = (
        (plugin.ritual_command, "/p5x意识 汐见琴音", "【汐见琴音】意识"),
        (plugin.innate_command, "/p5x属性意识 汐见琴音", "【汐见琴音】属性意识"),
        (plugin.setting_command, "/p5x养成 汐见琴音", "【汐见琴音】养成"),
        (plugin.operation_command, "/p5x操作 汐见琴音", "【汐见琴音】操作"),
        (plugin.review_command, "/p5x评价 汐见琴音", "【汐见琴音】评价"),
        (plugin.party_command, "/p5x配队 汐见琴音", "【汐见琴音】配队"),
        (plugin.guides_command, "/p5x攻略 汐见琴音", "【汐见琴音】综合攻略"),
    )
    for handler, message, heading in cases:
        text = run_command(handler, FakeEvent(message))
        assert text.startswith(heading)
        assert notices.DISCLAIMER in text


def test_argument_extraction_without_space(plugin):
    text = run_command(plugin.character_command, FakeEvent("/p5x角色汐见琴音"))
    assert text.startswith("【汐见琴音】")


def test_empty_argument_shows_usage(plugin):
    text = run_command(plugin.character_command, FakeEvent("/p5x角色"))
    assert text.startswith("用法：/p5x角色")


def test_unknown_character(plugin):
    text = run_command(plugin.character_command, FakeEvent("/p5x角色 不存在角色XYZ"))
    assert "没有找到" in text


def test_missing_database_message(plugin_module, tmp_path, monkeypatch):
    """数据库彻底找不到时必须给出明确提示，而不是抛异常。"""
    monkeypatch.setattr(plugin_module, "DATA_DIR", tmp_path)
    broken = plugin_module.PhantomArchivePlugin(
        context=None,
        config={"database_path": str(tmp_path / "nope.sqlite3")},
    )
    text = run_command(broken.character_command, FakeEvent("/p5x角色 汐见琴音"))
    assert text == "夜幕档案数据库尚未安装，请联系机器人管理员。"


def test_configured_path_missing_falls_back_to_data_dir(plugin_module, tmp_path):
    """配置路径不存在时按 §5 顺序回退到 data 目录，而不是直接报错。"""
    instance = plugin_module.PhantomArchivePlugin(
        context=None,
        config={"database_path": str(tmp_path / "nope.sqlite3")},
    )
    assert instance._init_error is None
    assert instance.database_path.name == "p5x_zh.sqlite3"


def test_plugin_terminate_closes_database(plugin_module):
    instance = plugin_module.PhantomArchivePlugin(
        context=None,
        config={"database_path": str(PLUGIN_ROOT / "data" / "p5x_zh.sqlite3")},
    )
    asyncio.run(instance.terminate())
    assert instance._service is None


def test_command_names_match_in_real_astrbot(plugin_module):
    """用 AstrBot 自己的 CommandFilter 验证指令名能被正确匹配。"""
    from astrbot.core.star.filter.command import CommandFilter
    from astrbot.core.star.star_handler import star_handlers_registry

    registered: dict[str, CommandFilter] = {}
    for handler in star_handlers_registry:
        for event_filter in getattr(handler, "event_filters", []):
            if isinstance(event_filter, CommandFilter):
                registered[event_filter.command_name] = event_filter

    expected = {
        "夜幕帮助", "p5x角色", "p5x技能", "p5x武器", "p5x角色列表",
        "p5x意识", "p5x属性意识", "p5x养成", "p5x操作", "p5x评价",
        "p5x配队", "p5x攻略",
    }
    assert expected.issubset(set(registered)), f"未注册：{expected - set(registered)}"

    class FilterEvent:
        is_at_or_wake_command = True

        def __init__(self, text: str):
            self._text = text
            self.extra: dict = {}

        def get_message_str(self) -> str:
            return self._text

        def set_extra(self, key, value):
            self.extra[key] = value

    # 指令名 + 空格 + 参数 必须命中
    assert registered["p5x角色"].filter(FilterEvent("p5x角色 汐见琴音"), None) is True
    # 「p5x角色列表」不能被「p5x角色」吞掉
    assert registered["p5x角色"].filter(FilterEvent("p5x角色列表"), None) is False
    assert registered["p5x角色列表"].filter(FilterEvent("p5x角色列表"), None) is True
    # 前缀不同不应互相命中
    assert registered["p5x技能"].filter(FilterEvent("p5x角色 汐见琴音"), None) is False
