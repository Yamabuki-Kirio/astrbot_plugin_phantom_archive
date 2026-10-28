"""用户头像：来源解析、下载、校验、缓存、回退。

三条硬规则：

1. 头像必须由插件自己下载并转成 Data URI —— 交给 T2I 去请求远程地址，
   在容器里就是一张破图，而且会把用户的头像 URL（可能带签名）泄露给渲染服务；
2. 任何失败都不能影响查询：拿不到就退回昵称首字符头像；
3. 日志里不出现头像 URL 与完整用户 ID —— 地址通常带 token 或签名。

来源按优先级解析，先命中者生效：

    1. ``config_template`` 用户配置的 ``qq_avatar_url_template``（明确指定，优先级最高）
    2. ``qq_auto``       OneBot 系平台 + 纯数字用户 ID → 腾讯 qlogo 地址
    3. ``event``         平台事件自带的头像字段（Telegram / Discord 等适配器可能有）
    4. ``initial``       以上都没有 → 昵称首字符头像

第二步只认 OneBot：Telegram、Discord 的 ``user_id`` 不是 QQ 号，
拿数字 ID 去猜 QQ 头像只会请求到别人的头像。
"""

from __future__ import annotations

import hashlib
import io
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from . import image_codec
from .identity import RequesterIdentity, platform_avatar_url, platform_id, platform_kind

logger = logging.getLogger(__name__)

AVATAR_DIRNAME = "avatars"
DEFAULT_CACHE_HOURS = 24
DEFAULT_TIMEOUT_S = 5
MIN_TIMEOUT_S = 1
MAX_TIMEOUT_S = 30
MAX_CACHE_HOURS = 24 * 30

PLACEHOLDER_TOKEN = "{user_id}"

# OneBot 系平台类型名（AstrBot 的 aiocqhttp 适配器即 OneBot v11）
ONEBOT_PLATFORMS: frozenset[str] = frozenset(
    {"aiocqhttp", "onebot", "onebot11", "onebot_v11", "llonebot"}
)

# 腾讯 QQ 头像服务，s=640 是 qlogo 支持的最大档；返回 302 由 httpx 跟随
QQ_AVATAR_URL_TEMPLATE = "https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"
QQ_ID_MIN_DIGITS = 5
QQ_ID_MAX_DIGITS = 12

# 头像来源标签，写进日志，便于用户自查「头像为什么没出来」
SOURCE_TEMPLATE = "config_template"
SOURCE_QQ_AUTO = "qq_auto"
SOURCE_EVENT = "event"
SOURCE_INITIAL = "initial"


@dataclass(frozen=True)
class AvatarSettings:
    enabled: bool = True
    cache_hours: int = DEFAULT_CACHE_HOURS
    timeout_s: int = DEFAULT_TIMEOUT_S
    url_template: str = ""
    # 默认不读系统代理：容器里常配着指向宿主的代理，插件不该默默继承
    use_system_proxy: bool = False

    @property
    def ttl_seconds(self) -> int:
        return max(0, self.cache_hours) * 3600

    @property
    def has_template(self) -> bool:
        return PLACEHOLDER_TOKEN in self.url_template

    @classmethod
    def from_config(cls, get) -> "AvatarSettings":
        def raw(key, default):
            value = get(key, default)
            return default if value is None else value

        def as_int(key, default, low, high):
            try:
                value = int(raw(key, default))
            except (TypeError, ValueError):
                logger.warning("配置 %s 非法（%r），回退默认 %s", key, raw(key, default), default)
                return default
            return min(high, max(low, value))

        def as_bool(key, default):
            value = raw(key, default)
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in ("true", "1", "yes", "on"):
                    return True
                if lowered in ("false", "0", "no", "off", ""):
                    return False
            return default

        template = str(raw("qq_avatar_url_template", "")).strip()
        if template and PLACEHOLDER_TOKEN not in template:
            logger.warning("qq_avatar_url_template 缺少 {user_id} 占位符，已忽略")
            template = ""

        return cls(
            enabled=bool(raw("show_requester_header", True)),
            cache_hours=as_int("avatar_cache_hours", DEFAULT_CACHE_HOURS, 0, MAX_CACHE_HOURS),
            timeout_s=as_int("avatar_request_timeout", DEFAULT_TIMEOUT_S, MIN_TIMEOUT_S, MAX_TIMEOUT_S),
            url_template=template,
            use_system_proxy=as_bool("avatar_use_system_proxy", False),
        )


def _mask(user_id: str) -> str:
    """日志里只留一个不可逆短标识，够定位问题、不足以还原账号。"""
    if not user_id:
        return "-"
    return hashlib.sha1(user_id.encode("utf-8")).hexdigest()[:8]


def is_onebot_platform(platform: str) -> bool:
    """平台类型是不是 OneBot 系（只有这类平台的 user_id 才可能是 QQ 号）。"""
    normalized = (platform or "").strip().lower()
    if not normalized:
        return False
    return normalized in ONEBOT_PLATFORMS or "onebot" in normalized


def is_qq_number(user_id: str) -> bool:
    """纯数字且长度像 QQ 号。ASCII 判断挡掉全角数字与其它平台的 openid。"""
    value = (user_id or "").strip()
    if not value.isascii() or not value.isdigit():
        return False
    return QQ_ID_MIN_DIGITS <= len(value) <= QQ_ID_MAX_DIGITS


def auto_qq_avatar_url(platform: str, user_id: str) -> str:
    """OneBot + 纯数字 ID 时直接给出腾讯头像地址；否则返回空串。"""
    if not (is_onebot_platform(platform) and is_qq_number(user_id)):
        return ""
    return QQ_AVATAR_URL_TEMPLATE.replace(PLACEHOLDER_TOKEN, user_id.strip())


def _cache_key(platform: str, user_id: str) -> str:
    """缓存键带平台：不同平台的同名数字 ID 是两个不同的人。"""
    raw = f"{(platform or '-').strip().lower()}:{(user_id or 'anonymous').strip()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _normalize_avatar(data: bytes) -> bytes | None:
    """校验 + 归一化成 128px WebP 字节。失败返回 None。"""
    if not data or len(data) > image_codec.MAX_AVATAR_BYTES:
        return None
    if not image_codec.is_supported(data):
        return None
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        return None

    previous = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = image_codec.MAX_IMAGE_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if image.format not in image_codec.SUPPORTED_FORMATS:
                return None
            width, height = image.size
            if width <= 0 or height <= 0:
                return None
            side = min(width, height)
            left = (width - side) // 2
            top = (height - side) // 2
            cropped = image.crop((left, top, left + side, top + side))
            resized = cropped.resize(
                (image_codec.AVATAR_EDGE, image_codec.AVATAR_EDGE),
                Image.LANCZOS,
            )
            buffer = io.BytesIO()
            resized.convert("RGB").save(
                buffer, format="WEBP", quality=image_codec.AVATAR_QUALITY, method=4
            )
            return buffer.getvalue()
    except Exception as exc:  # noqa: BLE001 - 坏图一律当拿不到
        logger.warning("头像解码失败：%s", type(exc).__name__)
        return None
    finally:
        Image.MAX_IMAGE_PIXELS = previous


class AvatarProvider:
    """一个有缓存的头像获取器。一个实例复用一个 HTTP 连接。"""

    def __init__(
        self,
        cache_dir: str | Path,
        settings: AvatarSettings | None = None,
        *,
        transport=None,
    ):
        self.cache_dir = Path(cache_dir) / AVATAR_DIRNAME
        self.settings = settings or AvatarSettings()
        self._transport = transport
        self._client = None

    # ---- 生命周期 ----

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        try:
            import httpx
        except ImportError:
            return None
        self._client = httpx.AsyncClient(
            timeout=float(self.settings.timeout_s),
            # 是否吃 HTTP(S)_PROXY 由配置决定：容器里既可能必须走代理，
            # 也可能因为继承了宿主代理而彻底连不出去
            trust_env=bool(self.settings.use_system_proxy),
            follow_redirects=True,
            transport=self._transport,
        )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:  # noqa: BLE001
                logger.debug("关闭头像客户端时出错", exc_info=True)
            self._client = None

    # ---- 缓存 ----

    def _cache_path(self, platform: str, user_id: str) -> Path:
        return self.cache_dir / f"{_cache_key(platform, user_id)}.webp"

    def _read_cache(self, platform: str, user_id: str) -> str | None:
        ttl = self.settings.ttl_seconds
        if ttl <= 0:
            # 0 小时 = 不缓存，每次重新获取
            return None
        path = self._cache_path(platform, user_id)
        try:
            stat = path.stat()
        except OSError:
            return None
        if time.time() - stat.st_mtime > ttl:
            return None
        return image_codec.encode_path(path, edge=image_codec.AVATAR_EDGE, use_cache=True)

    def _write_cache(self, platform: str, user_id: str, data: bytes) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path = self._cache_path(platform, user_id)
            temp = path.with_suffix(".tmp")
            temp.write_bytes(data)
            temp.replace(path)
        except OSError as exc:
            logger.warning("写入头像缓存失败：%s", type(exc).__name__)

    # ---- 地址解析 ----

    def _build_url(self, identity: RequesterIdentity, event) -> tuple[str, str]:
        """按优先级挑地址，返回 ``(url, 来源标签)``；都没命中时 url 为空串。

        配置模板排最前：用户既然手动填了地址，就说明他不想要自动推断的结果。
        """
        if self.settings.has_template and identity.user_id:
            return (
                self.settings.url_template.replace(PLACEHOLDER_TOKEN, identity.user_id),
                SOURCE_TEMPLATE,
            )

        auto = auto_qq_avatar_url(platform_kind(event), identity.user_id)
        if auto:
            return auto, SOURCE_QQ_AUTO

        direct = platform_avatar_url(event) if event is not None else ""
        if direct:
            return direct, SOURCE_EVENT

        return "", ""

    async def _download(self, url: str) -> bytes | None:
        if url.startswith("data:image/"):
            try:
                header, _, payload = url.partition(",")
                if "base64" in header:
                    import base64

                    return base64.b64decode(payload, validate=False)
            except Exception:  # noqa: BLE001
                return None
            return None

        client = self._ensure_client()
        if client is None:
            return None

        import httpx

        try:
            buffer = bytearray()
            async with client.stream("GET", url) as response:
                if response.status_code // 100 != 2:
                    return None
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > image_codec.MAX_AVATAR_BYTES:
                    return None
                async for chunk in response.aiter_bytes():
                    buffer.extend(chunk)
                    if len(buffer) > image_codec.MAX_AVATAR_BYTES:
                        return None
            return bytes(buffer)
        except httpx.HTTPError as exc:
            logger.info("头像下载失败：%s", type(exc).__name__)
            return None
        except Exception as exc:  # noqa: BLE001
            logger.info("头像下载异常：%s", type(exc).__name__)
            return None

    # ---- 对外 ----

    async def resolve(self, identity: RequesterIdentity, event=None) -> str:
        """返回头像 Data URI。永远有返回值（最差是首字符头像）。"""
        if identity.avatar_data_uri:
            return identity.avatar_data_uri

        if not identity.user_id:
            logger.info("头像来源=initial：缺少用户 ID")
            return image_codec.make_initial_avatar(identity.display_name)

        platform = platform_id(event)
        cached = self._read_cache(platform, identity.user_id)
        if cached:
            logger.debug("头像来源=cache（platform=%s, user=%s）", platform, _mask(identity.user_id))
            return cached

        url, source = self._build_url(identity, event)
        reason = "没有可用地址"
        if url:
            data = await self._download(url)
            if data:
                normalized = _normalize_avatar(data)
                if normalized:
                    uri = self._encode_and_cache(platform, identity.user_id, normalized)
                    if uri:
                        logger.info(
                            "头像来源=%s（platform=%s, user=%s）",
                            source,
                            platform,
                            _mask(identity.user_id),
                        )
                        return uri
                    reason = "转码失败"
                else:
                    reason = "图片不合格"
            else:
                reason = "下载失败"

        logger.info(
            "头像回退首字符头像：%s（来源=%s, platform=%s, user=%s）",
            reason,
            source or "none",
            platform,
            _mask(identity.user_id),
        )
        return image_codec.make_initial_avatar(identity.display_name)

    def _encode_and_cache(self, platform: str, user_id: str, data: bytes) -> str:
        """归一化后的字节 → Data URI，顺手落缓存。转码失败返回空串。"""
        uri = image_codec.encode_bytes(
            data,
            max_bytes=image_codec.MAX_AVATAR_BYTES,
            edge=image_codec.AVATAR_EDGE,
        )
        if uri:
            self._write_cache(platform, user_id, data)
        return uri


__all__ = [
    "AvatarSettings",
    "AvatarProvider",
    "AVATAR_DIRNAME",
    "DEFAULT_CACHE_HOURS",
    "DEFAULT_TIMEOUT_S",
    "ONEBOT_PLATFORMS",
    "QQ_AVATAR_URL_TEMPLATE",
    "SOURCE_TEMPLATE",
    "SOURCE_QQ_AUTO",
    "SOURCE_EVENT",
    "SOURCE_INITIAL",
    "is_onebot_platform",
    "is_qq_number",
    "auto_qq_avatar_url",
]
