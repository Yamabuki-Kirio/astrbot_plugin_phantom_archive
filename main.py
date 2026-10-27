"""夜幕档案 —— AstrBot P5X 综合资料查询插件。

开放指令：
    /夜幕帮助
    /p5x角色 <名称>
    /p5x技能 <名称>
    /p5x武器 <名称>
    /p5x角色列表
    /p5x意识 /p5x属性意识 /p5x养成 /p5x操作 /p5x评价 /p5x配队 /p5x攻略

只读，不写库；只读 p5x_zh.sqlite3，绝不碰原始库 p5x.sqlite3。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .archive import notices, renderer
from .archive.database import DatabaseMissingError, SchemaMismatchError
from .archive.service import ArchiveService, DEFAULT_MAX_CHARS

logger = logging.getLogger(__name__)

PLUGIN_DIR = Path(__file__).resolve().parent
DATA_DIR = PLUGIN_DIR / "data"

PLUGIN_NAME = "astrbot_plugin_phantom_archive"
PLUGIN_AUTHOR = "Yamabuki-Kirio"
PLUGIN_DESC = "夜幕档案：P5X 角色、武器、意识、养成、评价与配队只读查询"
PLUGIN_VERSION = "0.2.0"

DATABASE_FILENAME = "p5x_zh.sqlite3"


def _conf_get(config, key: str, default):
    """兼容 dict 与 AstrBotConfig 两种取值方式。"""
    if config is None:
        return default
    if hasattr(config, "get"):
        value = config.get(key, default)
        return default if value is None else value
    return getattr(config, key, default)


def _extract_arg(event: AstrMessageEvent, command: str) -> str:
    """从消息里取出指令后面的参数。

    同时支持 ``/p5x角色 汐见琴音`` 与 ``/p5x角色汐见琴音`` 两种写法，
    不依赖框架的参数注入细节。
    """
    text = (getattr(event, "message_str", "") or "").strip()
    for prefix in (f"/{command}", command):
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


@register(PLUGIN_NAME, PLUGIN_AUTHOR, PLUGIN_DESC, PLUGIN_VERSION)
class PhantomArchivePlugin(Star):
    """夜幕档案中文综合资料查询插件。"""

    def __init__(self, context: Context, config=None):
        super().__init__(context, config)
        # Star 基类只在 config 非空时才挂 self.config，这里补齐以免属性缺失
        if not hasattr(self, "config"):
            self.config = config if config is not None else {}

        self.max_chars = self._read_max_chars()
        self.database_path: Path | None = None
        self._service: ArchiveService | None = None
        self._init_error: Exception | None = None

        self._open_service()

    # ------------------------------------------------------------ 初始化

    def _read_max_chars(self) -> int:
        raw = _conf_get(self.config, "max_message_length", DEFAULT_MAX_CHARS)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            logger.warning("max_message_length 配置非法（%r），回退默认 %d", raw, DEFAULT_MAX_CHARS)
            return DEFAULT_MAX_CHARS
        return max(200, value)

    def _open_service(self) -> None:
        """初始化只校验数据库，不把正文读进内存（§13）。"""
        configured = _conf_get(self.config, "database_path", "") or ""
        try:
            service = ArchiveService.open(configured, DATA_DIR, max_chars=self.max_chars)
        except DatabaseMissingError as exc:
            self._init_error = exc
            logger.error("夜幕档案数据库不可用：%s", exc)
            for candidate in self._candidate_paths(configured):
                logger.error("  尝试路径：%s", candidate)
            return
        except SchemaMismatchError as exc:
            self._init_error = exc
            logger.error("夜幕档案数据库结构不兼容：%s", exc)
            return

        self._service = service
        self.database_path = service.database_path
        notices.load_from_file(DATA_DIR / "display_notices.json")
        logger.info(
            "夜幕档案已就绪：%s（角色 %d 名，单条上限 %d 字）",
            service.database_path,
            service.count_characters(),
            service.max_chars,
        )

    @staticmethod
    def _candidate_paths(configured: str) -> list[Path]:
        paths: list[Path] = []
        if configured and str(configured).strip():
            paths.append(Path(str(configured).strip()).expanduser())
        paths.append(DATA_DIR / DATABASE_FILENAME)
        return paths

    # ------------------------------------------------------------ 查询入口

    async def _run(self, method_name: str, *args) -> str:
        """把阻塞查询丢到线程里，避免卡住事件循环。"""
        if self._service is None:
            if isinstance(self._init_error, SchemaMismatchError):
                return renderer.render_schema_mismatch()
            return renderer.render_database_missing()

        func = getattr(self._service, method_name)
        try:
            return await asyncio.to_thread(func, *args)
        except Exception:  # noqa: BLE001 - 兜底，不让异常冒到群聊
            logger.exception("夜幕档案查询失败：%s", method_name)
            return renderer.render_query_failed()

    # ------------------------------------------------------------ 指令

    @filter.command("夜幕帮助")
    async def help_command(self, event: AstrMessageEvent):
        """显示当前开放的查询指令。"""
        yield event.plain_result(renderer.render_help())

    @filter.command("p5x角色")
    async def character_command(self, event: AstrMessageEvent):
        """查询角色基本资料。"""
        name = _extract_arg(event, "p5x角色")
        yield event.plain_result(await self._run("character_message", name))

    @filter.command("p5x技能")
    async def skills_command(self, event: AstrMessageEvent):
        """查询角色技能。"""
        name = _extract_arg(event, "p5x技能")
        yield event.plain_result(await self._run("skills_message", name))

    @filter.command("p5x武器")
    async def weapons_command(self, event: AstrMessageEvent):
        """查询角色武器。"""
        name = _extract_arg(event, "p5x武器")
        yield event.plain_result(await self._run("weapons_message", name))

    @filter.command("p5x角色列表")
    async def character_list_command(self, event: AstrMessageEvent):
        """列出当前收录的角色。"""
        yield event.plain_result(await self._run("character_list_message"))

    async def _guide_command(self, event: AstrMessageEvent, command: str, guide_type: str):
        name = _extract_arg(event, command)
        return await self._run("guide_message", name, guide_type)

    @filter.command("p5x意识")
    async def ritual_command(self, event: AstrMessageEvent):
        yield event.plain_result(await self._guide_command(event, "p5x意识", "ritual"))

    @filter.command("p5x属性意识")
    async def innate_command(self, event: AstrMessageEvent):
        yield event.plain_result(await self._guide_command(event, "p5x属性意识", "innate"))

    @filter.command("p5x养成")
    async def setting_command(self, event: AstrMessageEvent):
        yield event.plain_result(await self._guide_command(event, "p5x养成", "setting"))

    @filter.command("p5x操作")
    async def operation_command(self, event: AstrMessageEvent):
        yield event.plain_result(await self._guide_command(event, "p5x操作", "operation"))

    @filter.command("p5x评价")
    async def review_command(self, event: AstrMessageEvent):
        yield event.plain_result(await self._guide_command(event, "p5x评价", "review"))

    @filter.command("p5x配队")
    async def party_command(self, event: AstrMessageEvent):
        yield event.plain_result(await self._guide_command(event, "p5x配队", "party"))

    @filter.command("p5x攻略")
    async def guides_command(self, event: AstrMessageEvent):
        name = _extract_arg(event, "p5x攻略")
        yield event.plain_result(await self._run("all_guides_message", name))

    # ------------------------------------------------------------ 生命周期

    async def terminate(self):
        """插件卸载时释放只读连接。"""
        if self._service is not None:
            self._service.close()
            self._service = None
            logger.info("夜幕档案已关闭数据库连接")
