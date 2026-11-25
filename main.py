"""夜幕档案 —— AstrBot P5X 综合资料查询插件。

开放指令：
    /夜幕帮助
    /p5x角色 <名称>
    /p5x技能 <名称>
    /p5x武器 <名称>
    /p5x角色列表
    /p5x意识 /p5x属性意识 /p5x养成 /p5x操作 /p5x评价 /p5x配队 /p5x攻略
    /p5x每日运势（别名 /p5x运势、/p5x今日角色）
    /p5x抽卡记录（预留功能）
    /p5x渲染状态（管理员）
    /p5x测试渲染（管理员）

资料查询只读，不写库；只读 p5x_zh.sqlite3，绝不碰原始库 p5x.sqlite3。
每日运势另开一个可写的 p5x_daily.sqlite3，只存「谁在哪天抽到了什么」，
资料库本身仍然只读。

图片输出：查询成功时把结构化结果渲染成卡片图片发送；
渲染服务不可用、返回非图片、或校验不通过时，按配置降级为文字。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace
from pathlib import Path

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.event.filter import EventMessageType
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
    panel_review,
    renderer,
    visual_context,
)
from .archive.daily import character_draw as daily_character_draw
from .archive.daily import engine as daily_engine
from .archive.daily import models as daily_models
from .archive.daily import presenter as daily_presenter
from .archive.daily import repository as daily_repository
from .archive.database import DatabaseMissingError, SchemaMismatchError
from .archive.official_profiles import find_profile_images
from .archive.backgrounds import BackgroundRotation, resolve_background_mode
from .archive.plain_commands import match_plain_command
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
PLUGIN_VERSION = "0.9.0"

DATABASE_FILENAME = "p5x_zh.sqlite3"
DAILY_DATABASE_FILENAME = daily_repository.DAILY_DB_FILENAME
ASSETS_DIRNAME = "assets"
MANIFEST_FILENAME = "manifest.json"
IMAGE_CACHE_DIRNAME = "image_cache"

FALLBACK_TEXT = "text"
FALLBACK_ERROR = "error"
FALLBACK_MODES: tuple[str, ...] = (FALLBACK_TEXT, FALLBACK_ERROR)

# 每日运势的三条固定文案。写在常量里，测试与文档都引用同一份。
DAILY_DISABLED_TEXT = "P5X每日运势当前未开启。"
DAILY_UNAVAILABLE_TEXT = "夜幕档案数据库暂时不可用，无法抽取今日角色。"
DAILY_FAILED_TEXT = "今日运势生成失败，请稍后再试。"

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
        self._background_rotation = BackgroundRotation()
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
        self.daily_enabled = _conf_bool(self.config, "daily_fortune_enabled", True)
        self.daily_image_enabled = _conf_bool(self.config, "daily_fortune_image_enabled", True)
        self.daily_timezone = str(
            _conf_get(self.config, "daily_fortune_timezone", daily_engine.DEFAULT_TIMEZONE)
            or daily_engine.DEFAULT_TIMEZONE
        ).strip()

        self.database_path: Path | None = None
        self._service: ArchiveService | None = None
        self._init_error: Exception | None = None
        self._image_renderer: ImageRenderer | None = None
        self._manifest = asset_manifest.EMPTY_MANIFEST
        self._asset_resolver = asset_resolver.AssetResolver(
            None,
            show_character_art=self.visual_settings.show_character_art,
            show_persona_art=self.visual_settings.show_persona_art,
            show_weapon_art=self.visual_settings.show_weapon_art,
        )
        self._avatar_provider: avatar_provider.AvatarProvider | None = None
        self._daily_store: daily_repository.DailyFortuneStore | None = None
        self._daily_store_error: Exception | None = None
        self._daily_timezone_label = daily_engine.DEFAULT_TIMEZONE
        self._panel_cooldowns: dict[str, float] = {}

        self._open_service()
        self._load_assets()
        self._init_daily()

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
        default = image_templates.THEME_LIGHT
        raw = str(_conf_get(self.config, "image_theme", default)).strip().lower()
        if raw not in image_templates.THEMES:
            logger.warning("image_theme 配置非法（%r），回退默认 %s", raw, default)
            return default
        return raw

    def _open_service(self) -> None:
        """初始化只校验数据库，不把正文读进内存（§13）。"""
        configured = _conf_get(self.config, "database_path", "") or ""
        try:
            service = ArchiveService.open(
                configured,
                DATA_DIR,
                max_chars=self.max_chars,
                rows_per_page=min(DEFAULT_ROWS_PER_PAGE, 7),
                max_pages=max(DEFAULT_MAX_PAGES, 8),
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
            show_persona_art=self.visual_settings.show_persona_art,
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

    # ------------------------------------------------------------ 每日运势

    def _init_daily(self) -> None:
        """只解析时区，不建库 —— 功能关掉时不该留下任何文件。"""
        _, self._daily_timezone_label = daily_engine.resolve_timezone(self.daily_timezone)
        if not self.daily_enabled:
            logger.info("每日运势未开启（daily_fortune_enabled=false）")
            return
        logger.info(
            "每日运势已就绪：时区 %s，图片输出 %s",
            self._daily_timezone_label,
            "已启用" if (self.daily_image_enabled and self.image_ready) else "未启用",
        )

    def _daily_store_instance(self) -> daily_repository.DailyFortuneStore | None:
        """懒建每日库。建不出来就返回 None —— 运势照样生成，只是不落库。"""
        if self._daily_store is not None:
            return self._daily_store
        if self._daily_store_error is not None:
            return None
        try:
            self._daily_store = daily_repository.DailyFortuneStore(
                DATA_DIR / DAILY_DATABASE_FILENAME
            )
        except daily_repository.DailyStoreError as exc:
            self._daily_store_error = exc
            logger.error("每日运势库不可用，本次仍会返回结果但不做持久化：%s", exc)
            return None
        return self._daily_store

    @staticmethod
    def _daily_candidates(characters) -> list:
        return [daily_character_draw.from_character(item) for item in characters]

    def _load_or_create_fortune(
        self,
        *,
        date_key: str,
        timezone_name: str,
        platform: str,
        user_id: str,
        display_name: str,
    ) -> daily_models.DailyFortune:
        """算日期 → 查当天 → 有则返回 → 无则生成 → 写库 → 冲突读回。"""
        identity_kwargs = {
            "date_key": date_key,
            "timezone": timezone_name,
            "platform_id": platform,
            "user_id": user_id,
            "display_name": display_name,
        }

        store = self._daily_store_instance()
        if store is not None:
            try:
                record = store.get(
                    date_key=date_key,
                    timezone=timezone_name,
                    platform_id=platform,
                    user_id=user_id,
                )
            except Exception as exc:  # noqa: BLE001 - 读失败按「没存过」处理
                logger.warning("每日运势读取失败（%s），本次重新生成", type(exc).__name__)
                record = None
            if record is not None:
                restored = daily_models.DailyFortune.from_payload(record.payload, **identity_kwargs)
                if not restored.topic:
                    topic, topic_advice = daily_engine.draw_topic(
                        daily_engine.make_seed(daily_engine.TOPIC_SALT, date_key, platform, user_id)
                    )
                    restored = replace(restored, topic=topic, topic_advice=topic_advice)
                return restored

        if self._service is None:
            raise daily_engine.FortuneUnavailableError("资料库不可用")

        fortune = daily_engine.generate(
            date_key=date_key,
            timezone=timezone_name,
            platform_id=platform,
            user_id=user_id,
            display_name=display_name,
            characters=self._daily_candidates(self._service.list_characters()),
        )

        if store is None:
            return fortune

        try:
            store.insert(
                date_key=date_key,
                timezone=timezone_name,
                platform_id=platform,
                user_id=user_id,
                display_name=display_name,
                character_key=fortune.character_key,
                payload=fortune.to_payload(),
            )
            # 并发场景下可能是别人先写进去了，读回库里那一份才是唯一结果
            again = store.get(
                date_key=date_key,
                timezone=timezone_name,
                platform_id=platform,
                user_id=user_id,
            )
        except daily_repository.DailyStoreError as exc:
            logger.warning("每日运势写入失败，本次仍返回结果：%s", exc)
            return fortune
        except Exception as exc:  # noqa: BLE001 - 兜底，不让持久化问题影响出图
            logger.warning("每日运势持久化异常（%s），本次仍返回结果", type(exc).__name__)
            return fortune

        if again is None:
            return fortune
        return daily_models.DailyFortune.from_payload(again.payload, **identity_kwargs)

    async def _daily_fortune_result(
        self, event: AstrMessageEvent, character_term: str = ""
    ) -> list:
        """接收指令 → 取身份 → 取/建结果 → 出图或出文字。"""
        if not self.daily_enabled:
            return [event.plain_result(DAILY_DISABLED_TEXT)]
        if self._service is None:
            return [event.plain_result(DAILY_UNAVAILABLE_TEXT)]

        selected_character = None
        term = (character_term or "").strip()
        if term:
            match = await asyncio.to_thread(self._service.exact_character, term)
            if match.kind == "none":
                return [
                    event.plain_result(
                        f"未找到角色「{term}」，请检查名称或形态写法（例如：双叶、sp素羽、周星驰、平野亚兰凛华）。"
                    )
                ]
            if match.kind == "many":
                names = "、".join(item.display_name for item in match.candidates[:6])
                return [event.plain_result(f"「{term}」匹配到多个角色：{names}，请输入更准确的名称。")]
            selected_character = daily_character_draw.from_character(match.character)

        try:
            requester = identity.from_event(event)
            platform = identity.platform_id(event)
            zone, zone_name = daily_engine.resolve_timezone(self.daily_timezone)
            date_key = daily_engine.date_key_of(daily_engine.today_in(zone))
            fortune = await asyncio.to_thread(
                self._load_or_create_fortune,
                date_key=date_key,
                timezone_name=zone_name,
                platform=platform,
                user_id=requester.user_id,
                display_name=requester.display_name,
            )
            if selected_character is not None:
                fortune = replace(fortune, character=selected_character)
        except daily_engine.FortuneUnavailableError as exc:
            logger.warning("每日运势不可用：%s", exc)
            return [event.plain_result(DAILY_UNAVAILABLE_TEXT)]
        except Exception:  # noqa: BLE001 - 兜底，不让异常冒到群聊
            logger.exception("每日运势生成失败")
            return [event.plain_result(DAILY_FAILED_TEXT)]

        if self.daily_image_enabled and self.image_ready:
            try:
                page = daily_presenter.build_page(fortune)
                visuals = await self._visuals(event, [page])
                html = await asyncio.to_thread(
                    image_templates.render_page, page, theme=self.theme, visual=visuals[0]
                )
                image = await self._renderer_instance().render(html)
                return [event.chain_result([Image.fromBytes(image.data)])]
            except RenderError as exc:
                logger.warning("每日运势出图失败：%s", self._sanitize(str(exc)))
            except Exception:  # noqa: BLE001 - 出图失败就降级文字，内容一致
                logger.exception("每日运势出图出现未预期错误")

        # 文字与图片来自同一个 fortune 对象，内容不会漂移
        return [event.plain_result(daily_presenter.render_text(fortune))]

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
        background = self._background_rotation.select(
            fixed=str(_conf_get(self.config, "card_background", "phantom")),
            mode=resolve_background_mode(
                _conf_get(self.config, "card_background_mode", None),
                legacy_rotate=_conf_bool(self.config, "card_background_rotation", True),
                legacy_mode=_conf_get(self.config, "card_background_rotation_mode", "sequential"),
            ),
        )
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
                replace(visual_context.build(
                    page,
                    identity=requester,
                    resolver=self._asset_resolver,
                    settings=self.visual_settings,
                ), background_preset=background)
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

    @filter.event_message_type(EventMessageType.ALL, priority=10000)
    async def handle_plain_commands(self, event: AstrMessageEvent):
        """可选的无斜杠入口，复用原处理器并阻止重复执行。"""
        if not _conf_bool(self.config, "enable_plain_commands", False):
            return
        handler = match_plain_command(event.get_message_str())
        if handler is None:
            return
        event.stop_event()
        async for result in getattr(self, handler)(event):
            yield result

    def _event_image_components(self, event: AstrMessageEvent) -> list:
        messages = None
        get_messages = getattr(event, "get_messages", None)
        if callable(get_messages):
            try:
                messages = get_messages()
            except Exception:  # noqa: BLE001
                messages = None
        if not messages:
            msg_obj = getattr(event, "message_obj", None)
            messages = getattr(msg_obj, "message", None)
        if not messages:
            return []
        return [comp for comp in messages if isinstance(comp, Image)]

    async def _read_image_component_bytes(self, comp: Image) -> bytes | None:
        """从 AstrBot Image 组件读取原始图片字节（兼容本地路径、convert_to_file_path 与 URL）。"""
        convert = getattr(comp, "convert_to_file_path", None)
        if callable(convert):
            try:
                converted = await convert()
                if converted:
                    path = Path(str(converted))
                    if path.is_file():
                        return await asyncio.to_thread(path.read_bytes)
            except Exception:  # noqa: BLE001
                pass

        for attr in ("file", "path"):
            raw = str(getattr(comp, attr, "") or "").strip()
            if raw and not raw.startswith(("http://", "https://", "base64://")):
                local = Path(raw.removeprefix("file:///").removeprefix("file://"))
                if local.is_file():
                    try:
                        return await asyncio.to_thread(local.read_bytes)
                    except OSError:
                        pass

        url = str(getattr(comp, "url", "") or getattr(comp, "file", "") or "").strip()
        if url.startswith(("http://", "https://")):
            try:
                import httpx

                async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200 and len(resp.content) <= 12 * 1024 * 1024:
                        return resp.content
            except Exception as exc:  # noqa: BLE001
                logger.debug("拉取待识图图片失败：%s", type(exc).__name__)
        return None

    def _panel_session_key(self, event: AstrMessageEvent) -> str:
        for attr in ("unified_msg_origin", "session_id"):
            val = getattr(event, attr, None)
            if val:
                return str(val)
        return str(getattr(event, "get_sender_id", lambda: "")() or "default")

    async def _resolve_active_persona_prompt(self, event: AstrMessageEvent) -> str:
        """获取当前会话生效的人格 system_prompt（如 Justine 等当前扮演人设）。"""
        ctx = getattr(self, "context", None)
        if ctx is None:
            return ""
        umo = getattr(event, "unified_msg_origin", None)
        platform_name = ""
        get_platform = getattr(event, "get_platform_name", None)
        if callable(get_platform):
            try:
                platform_name = str(get_platform() or "")
            except Exception:  # noqa: BLE001
                platform_name = ""

        conv_persona_id = None
        conv_mgr = getattr(ctx, "conversation_manager", None)
        if conv_mgr is not None and umo:
            try:
                cid = await conv_mgr.get_curr_conversation_id(umo)
                if cid:
                    conv = await conv_mgr.get_conversation(umo, cid)
                    if conv is not None:
                        conv_persona_id = getattr(conv, "persona_id", None)
            except Exception:  # noqa: BLE001
                conv_persona_id = None

        persona_mgr = getattr(ctx, "persona_manager", None)
        if persona_mgr is None:
            return ""

        persona_obj = None
        resolve_fn = getattr(persona_mgr, "resolve_selected_persona", None)
        if callable(resolve_fn) and umo:
            try:
                _, persona_obj, _, _ = await resolve_fn(
                    umo=umo,
                    conversation_persona_id=conv_persona_id,
                    platform_name=platform_name,
                )
            except Exception:  # noqa: BLE001
                persona_obj = None

        if persona_obj is None:
            default_fn = getattr(persona_mgr, "get_default_persona_v3", None)
            if callable(default_fn):
                try:
                    persona_obj = await default_fn(umo=umo)
                except Exception:  # noqa: BLE001
                    persona_obj = None

        if isinstance(persona_obj, dict):
            return str(persona_obj.get("prompt") or persona_obj.get("system_prompt") or "").strip()
        if persona_obj is not None:
            return str(
                getattr(persona_obj, "system_prompt", "") or getattr(persona_obj, "prompt", "") or ""
            ).strip()
        return ""

    async def _generate_persona_review_text(
        self, event: AstrMessageEvent, review: panel_review.PanelReview
    ) -> str:
        """使用当前启用人格 + 纯文本 LLM（绝不传图）生成 OC 风格面板点评；不可用时回退结构化文本。"""
        ctx = getattr(self, "context", None)
        if ctx is None:
            return panel_review.render_review_text(review)

        umo = getattr(event, "unified_msg_origin", None)
        provider = None
        get_prov_async = getattr(ctx, "get_using_provider_async", None)
        if callable(get_prov_async):
            try:
                provider = await get_prov_async(umo=umo)
            except Exception:  # noqa: BLE001
                provider = None
        if provider is None:
            get_prov = getattr(ctx, "get_using_provider", None)
            if callable(get_prov):
                try:
                    provider = get_prov(umo=umo)
                except Exception:  # noqa: BLE001
                    provider = None

        if provider is None:
            return panel_review.render_review_text(review)

        system_prompt = await self._resolve_active_persona_prompt(event)
        user_prompt = panel_review.build_persona_llm_prompt(review)
        try:
            llm_resp = await provider.text_chat(
                prompt=user_prompt,
                system_prompt=system_prompt,
                image_urls=[],
            )
            reply = str(getattr(llm_resp, "completion_text", "") or "").strip()
            if reply:
                return reply
        except Exception as exc:  # noqa: BLE001
            logger.warning("人格化面板点评 LLM 调用失败（%s），降级为结构化文本", type(exc).__name__)
        return panel_review.render_review_text(review)

    @filter.event_message_type(EventMessageType.ALL, priority=5000)
    async def handle_panel_image(self, event: AstrMessageEvent):
        """主动识图：纯本地 OCR 识别 P5X 角色面板截图，按配置输出卡片图与当前人格文字点评。"""
        if not _conf_bool(self.config, "auto_panel_review_enabled", True):
            return
        send_image = _conf_bool(self.config, "auto_panel_review_send_image", True)
        send_text = _conf_bool(self.config, "auto_panel_review_send_text", True)
        if not send_image and not send_text:
            return
        if self._service is None:
            return
        text = (event.get_message_str() or "").strip()
        if text.startswith("/"):
            return

        images = self._event_image_components(event)
        if not images:
            return

        cooldown = 10
        try:
            cooldown = max(0, int(_conf_get(self.config, "auto_panel_review_cooldown_seconds", 10)))
        except (TypeError, ValueError):
            cooldown = 10
        session_key = self._panel_session_key(event)
        now = time.monotonic()
        if cooldown > 0 and (now - self._panel_cooldowns.get(session_key, 0.0)) < cooldown:
            return

        for comp in images[:2]:
            raw_bytes = await self._read_image_component_bytes(comp)
            if not raw_bytes:
                continue
            try:
                outcome = await asyncio.to_thread(self._service.review_panel_image, raw_bytes)
            except Exception:  # noqa: BLE001
                logger.exception("角色面板主动识图异常")
                continue
            if outcome is None:
                continue

            self._panel_cooldowns[session_key] = time.monotonic()
            event.stop_event()

            if send_image and outcome.has_pages and self.image_ready:
                try:
                    rendered = await self._render_pages(event, outcome.pages)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("面板点评卡片渲染失败：%s", type(exc).__name__)
                else:
                    for img in rendered:
                        yield event.chain_result([Image.fromBytes(img.data)])

            if send_text:
                review_obj = outcome.pages[0].panel_review if outcome.pages else None
                if isinstance(review_obj, panel_review.PanelReview):
                    reply_text = await self._generate_persona_review_text(event, review_obj)
                else:
                    reply_text = outcome.text
                yield event.plain_result(reply_text)
            elif not self.image_ready:
                # 仅开图片但未配 T2I 时，仍降级输出结构化文本避免静默
                yield event.plain_result(outcome.text)
            return

    @filter.command("夜幕帮助")
    async def help_command(self, event: AstrMessageEvent):
        """显示当前开放的查询指令。"""
        yield event.plain_result(renderer.render_help())

    @filter.command("p5x角色")
    async def character_command(self, event: AstrMessageEvent):
        """查询角色资料（命中一图流时只输出一张完整卡片）。"""
        name = _extract_arg(event, "p5x角色")
        outcome = await self._outcome("character_outcome", name)
        if outcome.ok or outcome.error_kind != "not_found":
            for result in await self._deliver(event, outcome):
                yield result
            return
        images = find_profile_images(DATA_DIR, name)
        if images:
            try:
                payloads = await asyncio.to_thread(lambda: [path.read_bytes() for path in images])
            except OSError:
                logger.exception("官方角色信息图读取失败")
            else:
                for payload in payloads:
                    yield event.chain_result([Image.fromBytes(payload)])
                return
        for result in await self._deliver(event, outcome):
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

    @filter.command("p5x榜单")
    async def table_command(self, event: AstrMessageEvent):
        """查询一图流附表（倍率表、辅助榜单、对比表等）。"""
        name = _extract_arg(event, "p5x榜单")
        for result in await self._respond(event, "table_outcome", name):
            yield result

    @filter.command("p5x附表")
    async def table_alias_command(self, event: AstrMessageEvent):
        """p5x榜单的别名。"""
        name = _extract_arg(event, "p5x附表")
        for result in await self._respond(event, "table_outcome", name):
            yield result

    @filter.command("p5x倍率")
    async def dps_table_command(self, event: AstrMessageEvent):
        """直接输出全角色输出定位倍率梯度表。"""
        for result in await self._respond(event, "table_outcome", "dps-multiplier"):
            yield result

    @filter.command("p5x辅助榜")
    async def support_table_command(self, event: AstrMessageEvent):
        """直接输出常见辅助提升榜单。"""
        for result in await self._respond(event, "table_outcome", "support-ranking"):
            yield result

    @filter.command("p5x抽卡记录")
    async def gacha_history_command(self, event: AstrMessageEvent):
        """预留抽卡记录入口，鉴权方案完成前不读取任何用户数据。"""
        yield event.plain_result(renderer.render_gacha_history_placeholder())

    @filter.command("p5x每日运势")
    async def daily_fortune_command(self, event: AstrMessageEvent):
        """抽取今日角色与每日运势（同日同人结果固定）。"""
        name = _extract_arg(event, "p5x每日运势")
        for result in await self._daily_fortune_result(event, name):
            yield result

    @filter.command("p5x运势")
    async def daily_fortune_alias_command(self, event: AstrMessageEvent):
        """``/p5x每日运势`` 的别名。"""
        name = _extract_arg(event, "p5x运势")
        for result in await self._daily_fortune_result(event, name):
            yield result

    @filter.command("p5x今日运势")
    async def daily_today_command(self, event: AstrMessageEvent):
        name = _extract_arg(event, "p5x今日运势")
        for result in await self._daily_fortune_result(event, name):
            yield result

    @filter.command("今日运势")
    async def fortune_today_command(self, event: AstrMessageEvent):
        name = _extract_arg(event, "今日运势")
        for result in await self._daily_fortune_result(event, name):
            yield result

    @filter.command("每日运势")
    async def fortune_daily_command(self, event: AstrMessageEvent):
        name = _extract_arg(event, "每日运势")
        for result in await self._daily_fortune_result(event, name):
            yield result

    @filter.command("运势")
    async def fortune_command(self, event: AstrMessageEvent):
        name = _extract_arg(event, "运势")
        for result in await self._daily_fortune_result(event, name):
            yield result

    @filter.command("p5x今日角色")
    async def daily_character_command(self, event: AstrMessageEvent):
        """``/p5x每日运势`` 的别名。"""
        name = _extract_arg(event, "p5x今日角色")
        for result in await self._daily_fortune_result(event, name):
            yield result

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
                "persona_art": self.visual_settings.show_persona_art,
                "weapon_art": self.visual_settings.show_weapon_art,
                "avatar_source": self._avatar_source_chain(),
                "avatar_proxy": self.avatar_settings.use_system_proxy,
                "manifest_loaded": self._manifest.loaded,
                "manifest_path": str(self.asset_manifest_path),
                "manifest_counts": counts,
                "daily_enabled": self.daily_enabled,
                "daily_image": self.daily_image_enabled,
                "daily_timezone": self._daily_timezone_label,
                "daily_records": self._daily_record_count(),
            }
        )
        return info

    def _avatar_source_chain(self) -> str:
        """状态指令里列出头像会按什么顺序找，方便用户自己判断该配哪一项。"""
        chain: list[str] = []
        if self.avatar_settings.has_template:
            chain.append("配置模板")
        chain.append("QQ 自动（OneBot 纯数字 ID）")
        chain.append("平台字段")
        chain.append("首字符头像")
        return " → ".join(chain)

    def _daily_record_count(self) -> int:
        if self._daily_store is None:
            return 0
        try:
            return self._daily_store.count()
        except Exception:  # noqa: BLE001 - 自检信息不该因库异常而失败
            return 0

    # ------------------------------------------------------------ 生命周期

    async def terminate(self):
        """插件卸载时释放只读连接、渲染客户端、头像客户端与每日库。"""
        if self._daily_store is not None:
            self._daily_store.close()
            self._daily_store = None
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
