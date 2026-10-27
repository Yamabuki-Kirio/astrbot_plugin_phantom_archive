"""夜幕档案 —— AstrBot P5X 综合资料查询插件。

开放指令：
    /夜幕帮助
    /p5x角色 <名称>
    /p5x技能 <名称>
    /p5x武器 <名称>
    /p5x角色列表
    /p5x意识 /p5x属性意识 /p5x养成 /p5x操作 /p5x评价 /p5x配队 /p5x攻略
    /p5x抽卡记录（预留功能）
    /p5x渲染状态（管理员）
    /p5x测试渲染（管理员）

只读，不写库；只读 p5x_zh.sqlite3，绝不碰原始库 p5x.sqlite3。

图片输出：查询成功时把结构化结果渲染成卡片图片发送；
渲染服务不可用、返回非图片、或校验不通过时，按配置降级为文字。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, register

from .archive import (
    asset_manifest,
    asset_resolver,
    avatar_provider,
    cards,
    identity,
    image_templates,
    notices,
    renderer,
    visual_context,
)
from .archive.database import DatabaseMissingError, SchemaMismatchError
from .archive.image_renderer import (
    DEFAULT_MAX_CONCURRENT,
    ImageRenderer,
    RenderError,
    RenderSettings,
)
from .archive.service import (
    DEFAULT_MAX_CHARS,
    DEFAULT_MAX_PAGES,
    DEFAULT_ROWS_PER_PAGE,
    ArchiveService,
    QueryOutcome,
)

logger = logging.getLogger(__name__)

PLUGIN_DIR = Path(__file__).resolve().parent
DATA_DIR = PLUGIN_DIR / "data"

PLUGIN_NAME = "astrbot_plugin_phantom_archive"
PLUGIN_AUTHOR = "Yamabuki-Kirio"
PLUGIN_DESC = "夜幕档案：P5X 角色、武器、意识、养成、评价与配队只读查询"
PLUGIN_VERSION = "0.4.0"

DATABASE_FILENAME = "p5x_zh.sqlite3"
ASSETS_DIRNAME = "assets"
MANIFEST_FILENAME = "manifest.json"
IMAGE_CACHE_DIRNAME = "image_cache"

FALLBACK_TEXT = "text"
FALLBACK_ERROR = "error"
FALLBACK_MODES: tuple[str, ...] = (FALLBACK_TEXT, FALLBACK_ERROR)

# 测试渲染用的固定内容：包含需要转义的尖括号，用来验证模板的自动转义是否生效
PROBE_SECTION = cards.Section(
    heading="渲染自检",
    blocks=(
        cards.Block(kind="para", lines=("这是一张测试卡片，用于确认渲染服务与模板链路可用。",)),
        cards.Block(
            kind="item",
            lines=(
                "· 转义检查",
                "  <script>alert(1)</script> 应显示为纯文本",
                "  引号 \" 与 & 与尖括号 < > 不得被解析成标签",
            ),
        ),
        cards.Block(
            kind="bullets",
            lines=("· 中文字形与数字 12345", "· 符号 ★ ☆ ／ ％ ＜ ＞"),
        ),
    ),
)


def _conf_get(config, key: str, default):
    """兼容 dict 与 AstrBotConfig 两种取值方式。"""
    if config is None:
        return default
    if hasattr(config, "get"):
        value = config.get(key, default)
        return default if value is None else value
    return getattr(config, key, default)


def _conf_bool(config, key: str, default: bool) -> bool:
    value = _conf_get(config, key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off", ""):
            return False
    return default


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


def build_probe_page() -> cards.CardPage:
    """测试渲染用的固定卡片，不含任何真实资料。"""
    return cards.CardPage(
        category="角色",
        title="渲染测试",
        subtitle="5★ 测试属性｜测试职业",
        lead="该卡片由 /p5x测试渲染 生成，不包含真实游戏资料。",
        sections=(PROBE_SECTION,),
        page_index=1,
        page_total=1,
        source_label=notices.source_label("community"),
        translation_label=notices.translation_label("machine_translated"),
        has_conflict=False,
        show_footer=True,
    )


@register(PLUGIN_NAME, PLUGIN_AUTHOR, PLUGIN_DESC, PLUGIN_VERSION)
class PhantomArchivePlugin(Star):
    """夜幕档案中文综合资料查询插件。"""

    def __init__(self, context: Context, config=None):
        super().__init__(context, config)
        # Star 基类只在 config 非空时才挂 self.config，这里补齐以免属性缺失
        if not hasattr(self, "config"):
            self.config = config if config is not None else {}

        self.max_chars = self._read_max_chars()
        self.image_enabled = _conf_bool(self.config, "image_output_enabled", True)
        self.fallback_mode = self._read_fallback_mode()
        self.theme = self._read_theme()
        self.render_settings = RenderSettings.from_config(
            lambda key, default: _conf_get(self.config, key, default),
            theme=self.theme,
        )
        self.visual_settings = visual_context.VisualSettings.from_config(
            lambda key, default: _conf_get(self.config, key, default)
        )
        self.avatar_settings = avatar_provider.AvatarSettings.from_config(
            lambda key, default: _conf_get(self.config, key, default)
        )
        self.asset_manifest_path = self._read_manifest_path()

        self.database_path: Path | None = None
        self._service: ArchiveService | None = None
        self._init_error: Exception | None = None
        self._image_renderer: ImageRenderer | None = None
        self._manifest = asset_manifest.EMPTY_MANIFEST
        self._asset_resolver = asset_resolver.AssetResolver(
            None,
            show_character_art=self.visual_settings.show_character_art,
            show_weapon_art=self.visual_settings.show_weapon_art,
        )
        self._avatar_provider: avatar_provider.AvatarProvider | None = None

        self._open_service()
        self._load_assets()

    # ------------------------------------------------------------ 初始化

    def _read_max_chars(self) -> int:
        raw = _conf_get(self.config, "max_message_length", DEFAULT_MAX_CHARS)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            logger.warning("max_message_length 配置非法（%r），回退默认 %d", raw, DEFAULT_MAX_CHARS)
            return DEFAULT_MAX_CHARS
        return max(200, value)

    def _read_fallback_mode(self) -> str:
        raw = str(_conf_get(self.config, "t2i_fallback_mode", FALLBACK_TEXT)).strip().lower()
        if raw not in FALLBACK_MODES:
            logger.warning("t2i_fallback_mode 配置非法（%r），回退默认 %s", raw, FALLBACK_TEXT)
            return FALLBACK_TEXT
        return raw

    def _read_theme(self) -> str:
        raw = str(_conf_get(self.config, "image_theme", image_templates.THEME_DARK)).strip().lower()
        if raw not in image_templates.THEMES:
            logger.warning("image_theme 配置非法（%r），回退默认 %s", raw, image_templates.THEME_DARK)
            return image_templates.THEME_DARK
        return raw

    def _open_service(self) -> None:
        """初始化只校验数据库，不把正文读进内存（§13）。"""
        configured = _conf_get(self.config, "database_path", "") or ""
        try:
            service = ArchiveService.open(
                configured,
                DATA_DIR,
                max_chars=self.max_chars,
                rows_per_page=DEFAULT_ROWS_PER_PAGE,
                max_pages=DEFAULT_MAX_PAGES,
            )
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
            "夜幕档案已就绪：%s（角色 %d 名，单条上限 %d 字，图片输出 %s）",
            service.database_path,
            service.count_characters(),
            service.max_chars,
            "已启用" if self.image_ready else "未启用",
        )
        if self.image_enabled and not self.render_settings.configured:
            logger.warning("已开启图片输出但未配置 t2i_endpoint，查询将只发送文字")

    @staticmethod
    def _candidate_paths(configured: str) -> list[Path]:
        paths: list[Path] = []
        if configured and str(configured).strip():
            paths.append(Path(str(configured).strip()).expanduser())
        paths.append(DATA_DIR / DATABASE_FILENAME)
        return paths

    # ------------------------------------------------------------ 素材

    def _read_manifest_path(self) -> Path:
        """清单路径：留空时用插件 data/assets/manifest.json。"""
        raw = str(_conf_get(self.config, "asset_manifest_path", "") or "").strip()
        if raw:
            return Path(raw).expanduser()
        return DATA_DIR / ASSETS_DIRNAME / MANIFEST_FILENAME

    def _load_assets(self) -> None:
        """素材缺失只是没有图，不影响启动（§四）。"""
        self._manifest = asset_manifest.EMPTY_MANIFEST
        try:
            manifest = asset_manifest.load_manifest(self.asset_manifest_path)
        except Exception as exc:  # noqa: BLE001 - 清单坏了也不能拦住启动
            logger.warning("素材清单加载失败（%s），本次使用纯色卡片", type(exc).__name__)
            manifest = asset_manifest.EMPTY_MANIFEST

        self._manifest = manifest
        self._asset_resolver = asset_resolver.AssetResolver(
            manifest if manifest.loaded else None,
            show_character_art=self.visual_settings.show_character_art,
            show_weapon_art=self.visual_settings.show_weapon_art,
        )
        if not manifest.loaded:
            logger.info("未找到可用素材清单（%s），卡片使用占位图", self.asset_manifest_path)
            return
        counts = manifest.counts()
        logger.info(
            "素材清单已加载：角色 %d 名（有立绘 %d），武器 %d 把（有图 %d）",
            counts["characters"],
            counts["characters_with_portrait"],
            counts["weapons"],
            counts["weapons_with_image"],
        )

    def _avatar(self) -> avatar_provider.AvatarProvider:
        if self._avatar_provider is None:
            self._avatar_provider = avatar_provider.AvatarProvider(
                DATA_DIR / IMAGE_CACHE_DIRNAME,
                self.avatar_settings,
            )
        return self._avatar_provider

    # ------------------------------------------------------------ 图片输出

    @property
    def image_ready(self) -> bool:
        """真正会尝试出图的充要条件：开关打开 + 渲染服务地址已配置。"""
        return self.image_enabled and self.render_settings.configured

    def _renderer_instance(self) -> ImageRenderer:
        if self._image_renderer is None:
            self._image_renderer = ImageRenderer(
                self.render_settings,
                max_concurrent=DEFAULT_MAX_CONCURRENT,
            )
        return self._image_renderer

    def _sanitize(self, text: str) -> str:
        """把 token 从任何要外泄的文本里抹掉。"""
        token = self.render_settings.token
        if token and token in text:
            return text.replace(token, "***")
        return text

    async def _visuals(self, event: AstrMessageEvent, pages) -> list:
        """一次查询只取一次头像，多页复用；每页各自解析主视觉。"""
        try:
            requester = identity.from_event(event)
            if self.visual_settings.show_requester_header:
                # resolve 永远有返回值（最差是首字符头像），不会抛
                requester = requester.with_avatar(
                    await self._avatar().resolve(requester, event)
                )
        except Exception as exc:  # noqa: BLE001 - 头像失败只影响表头
            logger.warning("读取发起者信息失败：%s", type(exc).__name__)
            requester = identity.RequesterIdentity(
                user_id="", display_name=identity.FALLBACK_NAME
            )

        return await asyncio.to_thread(
            lambda: [
                visual_context.build(
                    page,
                    identity=requester,
                    resolver=self._asset_resolver,
                    settings=self.visual_settings,
                )
                for page in pages
            ]
        )

    async def _render_pages(self, event: AstrMessageEvent, pages) -> list:
        """模板渲染走线程（Jinja 是同步的），HTTP 请求走事件循环。"""
        visuals = await self._visuals(event, pages)
        htmls = [
            await asyncio.to_thread(
                image_templates.render_page, page, theme=self.theme, visual=visual
            )
            for page, visual in zip(pages, visuals)
        ]
        image_renderer_instance = self._renderer_instance()
        results = await asyncio.gather(
            *(image_renderer_instance.render(html) for html in htmls),
            return_exceptions=True,
        )
        for item in results:
            if isinstance(item, BaseException):
                raise item
        return list(results)

    async def _deliver(self, event: AstrMessageEvent, outcome: QueryOutcome) -> list:
        """决定发图、发文字还是报错。所有分支都只返回结果，不直接 yield。"""
        if not (outcome.ok and outcome.has_pages and self.image_ready):
            return [event.plain_result(outcome.text)]

        try:
            images = await self._render_pages(event, outcome.pages)
        except RenderError as exc:
            reason = self._sanitize(str(exc))
            logger.warning("图片渲染失败：%s", reason)
            return [event.plain_result(self._failure_result(reason, outcome))]
        except Exception as exc:  # noqa: BLE001 - 兜底，不让异常冒到群聊
            logger.exception("图片渲染出现未预期错误")
            reason = self._sanitize(f"{type(exc).__name__}")
            return [event.plain_result(self._failure_result(reason, outcome))]

        return [event.chain_result([Image.fromBytes(image.data)]) for image in images]

    def _failure_result(self, reason: str, outcome: QueryOutcome) -> str:
        if self.fallback_mode == FALLBACK_ERROR:
            return renderer.render_image_failed(reason)
        return outcome.text

    # ------------------------------------------------------------ 查询入口

    async def _outcome(self, method_name: str, *args) -> QueryOutcome:
        """把阻塞查询丢到线程里，避免卡住事件循环。"""
        if self._service is None:
            if isinstance(self._init_error, SchemaMismatchError):
                return QueryOutcome(False, renderer.render_schema_mismatch(), error_kind="unavailable")
            return QueryOutcome(False, renderer.render_database_missing(), error_kind="unavailable")

        func = getattr(self._service, method_name)
        try:
            return await asyncio.to_thread(func, *args)
        except Exception:  # noqa: BLE001 - 兜底，不让异常冒到群聊
            logger.exception("夜幕档案查询失败：%s", method_name)
            return QueryOutcome(False, renderer.render_query_failed(), error_kind="failed")

    async def _respond(self, event: AstrMessageEvent, method_name: str, *args) -> list:
        outcome = await self._outcome(method_name, *args)
        return await self._deliver(event, outcome)

    # ------------------------------------------------------------ 指令

    @filter.command("夜幕帮助")
    async def help_command(self, event: AstrMessageEvent):
        """显示当前开放的查询指令。"""
        yield event.plain_result(renderer.render_help())

    @filter.command("p5x角色")
    async def character_command(self, event: AstrMessageEvent):
        """查询角色基本资料。"""
        name = _extract_arg(event, "p5x角色")
        for result in await self._respond(event, "character_outcome", name):
            yield result

    @filter.command("p5x技能")
    async def skills_command(self, event: AstrMessageEvent):
        """查询角色技能。"""
        name = _extract_arg(event, "p5x技能")
        for result in await self._respond(event, "skills_outcome", name):
            yield result

    @filter.command("p5x武器")
    async def weapons_command(self, event: AstrMessageEvent):
        """查询角色武器。"""
        name = _extract_arg(event, "p5x武器")
        for result in await self._respond(event, "weapons_outcome", name):
            yield result

    @filter.command("p5x角色列表")
    async def character_list_command(self, event: AstrMessageEvent):
        """列出当前收录的角色。"""
        for result in await self._respond(event, "character_list_outcome"):
            yield result

    async def _guide_command(self, event: AstrMessageEvent, command: str, guide_type: str) -> list:
        name = _extract_arg(event, command)
        return await self._respond(event, "guide_outcome", name, guide_type)

    @filter.command("p5x意识")
    async def ritual_command(self, event: AstrMessageEvent):
        for result in await self._guide_command(event, "p5x意识", "ritual"):
            yield result

    @filter.command("p5x属性意识")
    async def innate_command(self, event: AstrMessageEvent):
        for result in await self._guide_command(event, "p5x属性意识", "innate"):
            yield result

    @filter.command("p5x养成")
    async def setting_command(self, event: AstrMessageEvent):
        for result in await self._guide_command(event, "p5x养成", "setting"):
            yield result

    @filter.command("p5x操作")
    async def operation_command(self, event: AstrMessageEvent):
        for result in await self._guide_command(event, "p5x操作", "operation"):
            yield result

    @filter.command("p5x评价")
    async def review_command(self, event: AstrMessageEvent):
        for result in await self._guide_command(event, "p5x评价", "review"):
            yield result

    @filter.command("p5x配队")
    async def party_command(self, event: AstrMessageEvent):
        for result in await self._guide_command(event, "p5x配队", "party"):
            yield result

    @filter.command("p5x攻略")
    async def guides_command(self, event: AstrMessageEvent):
        name = _extract_arg(event, "p5x攻略")
        for result in await self._respond(event, "all_guides_outcome", name):
            yield result

    @filter.command("p5x抽卡记录")
    async def gacha_history_command(self, event: AstrMessageEvent):
        """预留抽卡记录入口，鉴权方案完成前不读取任何用户数据。"""
        yield event.plain_result(renderer.render_gacha_history_placeholder())

    # ------------------------------------------------------------ 管理员指令

    @filter.command("p5x渲染状态")
    async def render_status_command(self, event: AstrMessageEvent):
        """查看图片渲染配置（不含令牌值）。"""
        if not _is_admin(event):
            yield event.plain_result(renderer.render_admin_only())
            return
        yield event.plain_result(renderer.render_image_status(self._status_info()))

    @filter.command("p5x测试渲染")
    async def test_render_command(self, event: AstrMessageEvent):
        """用固定内容试渲染一张卡片，验证链路。"""
        if not _is_admin(event):
            yield event.plain_result(renderer.render_admin_only())
            return
        if not self.image_ready:
            yield event.plain_result(
                renderer.render_render_test_failed("图片输出未启用或未配置渲染服务")
            )
            return
        try:
            page = build_probe_page()
            visuals = await self._visuals(event, [page])
            html = await asyncio.to_thread(
                image_templates.render_page, page, theme=self.theme, visual=visuals[0]
            )
            image = await self._renderer_instance().render(html)
        except RenderError as exc:
            reason = self._sanitize(str(exc))
            logger.warning("渲染测试失败：%s", reason)
            yield event.plain_result(renderer.render_render_test_failed(reason))
            return
        except Exception as exc:  # noqa: BLE001 - 兜底
            logger.exception("渲染测试出现未预期错误")
            yield event.plain_result(
                renderer.render_render_test_failed(self._sanitize(type(exc).__name__))
            )
            return

        escaped = "&lt;script&gt;" in html
        yield event.chain_result([Image.fromBytes(image.data)])
        yield event.plain_result(
            renderer.render_render_test_ok(
                image.width, image.height, image.size_kb, image.elapsed_ms, escaped
            )
        )

    def _status_info(self) -> dict:
        templates = image_templates.available_templates()
        info = self.render_settings.describe()
        counts = self._manifest.counts() if self._manifest.loaded else {}
        info.update(
            {
                "enabled": self.image_enabled,
                "fallback_mode": self.fallback_mode,
                "theme": self.theme,
                "concurrency": DEFAULT_MAX_CONCURRENT,
                "template_count": len(templates),
                "templates": list(templates),
                "dependencies": _dependency_versions(),
                "requester_header": self.visual_settings.show_requester_header,
                "character_art": self.visual_settings.show_character_art,
                "weapon_art": self.visual_settings.show_weapon_art,
                "avatar_source": (
                    "配置模板" if self.avatar_settings.has_template else "首字符头像"
                ),
                "manifest_loaded": self._manifest.loaded,
                "manifest_path": str(self.asset_manifest_path),
                "manifest_counts": counts,
            }
        )
        return info

    # ------------------------------------------------------------ 生命周期

    async def terminate(self):
        """插件卸载时释放只读连接、渲染客户端与头像客户端。"""
        if self._avatar_provider is not None:
            await self._avatar_provider.aclose()
            self._avatar_provider = None
        if self._image_renderer is not None:
            await self._image_renderer.aclose()
            self._image_renderer = None
        if self._service is not None:
            self._service.close()
            self._service = None
            logger.info("夜幕档案已关闭数据库连接")


def _is_admin(event: AstrMessageEvent) -> bool:
    """AstrBot 的 is_admin 是方法；测试替身可能没有，缺省按非管理员处理。"""
    checker = getattr(event, "is_admin", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:  # noqa: BLE001 - 权限判断失败按无权限处理
            return False
    return False


def _dependency_versions() -> str:
    from importlib import metadata

    parts = []
    for name, label in (("httpx", "httpx"), ("Pillow", "Pillow"), ("Jinja2", "Jinja2")):
        try:
            parts.append(f"{label} {metadata.version(name)}")
        except metadata.PackageNotFoundError:
            parts.append(f"{label} 缺失")
    return "｜".join(parts)
