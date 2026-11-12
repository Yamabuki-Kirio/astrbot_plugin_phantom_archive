import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from archive.plain_commands import COMMANDS, match_plain_command

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("command,entry", COMMANDS.items())
def test_whitelist(command, entry):
    handler, accepts_argument = entry
    assert match_plain_command(f"  {command}  ") == handler
    assert match_plain_command(f"/{command}") is None
    assert match_plain_command(f"{command} JOKER") == (handler if accepts_argument else None)


@pytest.mark.parametrize("text", ["", "十连", "今天运势怎么样", "帮我查 p5x角色 JOKER", "p5x角色JOKER", "p5x每日运势怎么样", "/p5x运势"])
def test_does_not_intercept_chat(text):
    assert match_plain_command(text) is None


def load_entry():
    # 直接执行生产入口函数体；不因本机未安装 AstrBot 而跳过路由测试。
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in {"_conf_get", "_conf_bool"}]
    handler = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "handle_plain_commands")
    handler.decorator_list = []
    namespace = {"AstrMessageEvent": object, "match_plain_command": match_plain_command}
    exec(compile(ast.Module(body=funcs + [handler], type_ignores=[]), "main.py", "exec"), namespace)
    return namespace["handle_plain_commands"]


@pytest.mark.parametrize("command", ["p5x每日运势", "p5x今日运势", "p5x运势", "今日运势", "每日运势", "运势"])
@pytest.mark.parametrize("enabled", [False, True])
def test_fortune_without_slash_respects_plain_commands_switch(command, enabled):
    class Event:
        stopped = False

        def get_message_str(self):
            return command

        def stop_event(self):
            self.stopped = True

    async def original(event):
        assert event.stopped
        yield "fortune-result"

    event = Event()
    plugin = SimpleNamespace(config={"enable_plain_commands": enabled})
    setattr(plugin, COMMANDS[command][0], original)

    async def collect():
        return [result async for result in load_entry()(plugin, event)]

    assert asyncio.run(collect()) == (["fortune-result"] if enabled else [])
    assert event.stopped is enabled


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,expected", [(False, False), (True, True), ("false", False), ("true", True)])
async def test_switch_stop_and_dispatch(enabled, expected):
    calls = []
    class Event:
        stopped = False
        def get_message_str(self):
            return "p5x角色 JOKER"
        def stop_event(self):
            self.stopped = True
    async def original(event):
        assert event.stopped
        calls.append(event.get_message_str())
        yield "original-result"
    event = Event()
    plugin = SimpleNamespace(config={"enable_plain_commands": enabled}, character_command=original)
    results = [r async for r in load_entry()(plugin, event)]
    assert event.stopped is expected
    assert results == (["original-result"] if expected else [])
    assert len(calls) == int(expected)


def test_schema_default_and_all_registered_handlers():
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    assert schema["enable_plain_commands"]["default"] is False
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    registered = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and decorator.func.attr == "command":
                    registered[decorator.args[0].value] = node.name
    assert {key: entry[0] for key, entry in COMMANDS.items()} == registered
