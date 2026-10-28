"""第三方声明与已下线模块。

这两个约束不需要 AstrBot 运行时，所以单独放一层：
放在 test_daily_plugin.py（整模块依赖 astrbot）里时，本地会被跳过，
声明和断言的矛盾要等到装了 AstrBot 的环境才暴露。
"""

from __future__ import annotations

from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
NOTICES_PATH = PLUGIN_ROOT / "THIRD_PARTY_NOTICES.md"
DAILY_DIR = PLUGIN_ROOT / "archive" / "daily"


def test_third_party_notices_exist():
    assert NOTICES_PATH.is_file()
    content = NOTICES_PATH.read_text(encoding="utf-8")
    assert "MIT License" in content
    assert "astrbot_plugin_cyber_divination" in content
    assert "tarot.py" in content


def test_removed_vendored_modules_are_gone():
    """灵签与周易已下线：文件不在，声明只把它们列进「未包含」。

    不能断言「全文不含 lingqian.py」—— 声明为了讲清「没有包含这些上游文件」，
    必然要写出文件名。这里查的是语义：文件确实删了，
    且每次提到都落在「未包含 / 未使用」的段落里。
    """
    assert not (DAILY_DIR / "lingqian.py").exists()
    assert not (DAILY_DIR / "zhouyi.py").exists()

    content = NOTICES_PATH.read_text(encoding="utf-8")
    for name in ("lingqian.py", "zhouyi.py"):
        start = 0
        while True:
            index = content.find(name, start)
            if index < 0:
                break
            window = content[max(0, index - 200): index + 200]
            assert "未包含" in window or "未使用" in window, (
                f"{name} 只应在「未包含 / 未使用」的说明里出现"
            )
            start = index + 1


def test_daily_package_no_longer_exports_removed_modules():
    """包入口也不该再 import 已删除的模块。"""
    source = (DAILY_DIR / "__init__.py").read_text(encoding="utf-8")
    assert "lingqian" not in source
    assert "zhouyi" not in source
