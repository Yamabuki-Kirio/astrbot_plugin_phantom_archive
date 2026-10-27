"""用户头像：下载、校验、缓存、回退。

三条硬规则：

1. 头像必须由插件自己下载并转成 Data URI —— 交给 T2I 去请求远程地址，
   在容器里就是一张破图，而且会把用户的头像 URL（可能带签名）泄露给渲染服务；
2. 任何失败都不能影响查询：拿不到就退回昵称首字符头像；
3. 日志里不出现头像 URL —— 那通常带 token 或签名。
"""

from __future__ import annotations

import hashlib
import io
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from . import image_codec
from .identity import RequesterIdentity, platform_avatar_url

logger = logging.getLogger(__name__)

AVATAR_DIRNAME = "avatars"
DEFAULT_CACHE_HOURS = 24
DEFAULT_TIMEOUT_S = 5
MIN_TIMEOUT_S = 1
MAX_TIMEOUT_S = 30
MAX_CACHE_HOURS = 24 * 30

PLACEHOLDER_TOKEN = "{user_id}"


@dataclass(frozen=True)
class AvatarSettings:
    enabled: bool = True
    cache_hours: int = DEFAULT_CACHE_HOURS
    timeout_s: int = DEFAULT_TIMEOUT_S
    url_template: str = ""

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

        template = str(raw("qq_avatar_url_template", "")).strip()
        if template and PLACEHOLDER_TOKEN not in template:
            logger.warning("qq_avatar_url_template 缺少 {user_id} 占位符，已忽略")
            template = ""

        return cls(
            enabled=bool(raw("show_requester_header", True)),
            cache_hours=as_int("avatar_cache_hours", DEFAULT_CACHE_HOURS, 0, MAX_CACHE_HOURS),
            timeout_s=as_int("avatar_request_timeout", DEFAULT_TIMEOUT_S, MIN_TIMEOUT_S, MAX_TIMEOUT_S),
            url_template=template,
        )


def _mask(user_id: str) -> str:
    """日志里只留一个不可逆短标识，够定位问题、不足以还原账号。"""
    if not user_id:
        return "-"
    return hashlib.sha1(user_id.encode("utf-8")).hexdigest()[:8]


def _cache_key(user_id: str) -> str:
    return hashlib.sha1((user_id or "anonymous").encode("utf-8")).hexdigest()


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
            trust_env=False,
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

    def _cache_path(self, user_id: str) -> Path:
        return self.cache_dir / f"{_cache_key(user_id)}.webp"

    def _read_cache(self, user_id: str) -> str | None:
        ttl = self.settings.ttl_seconds
        if ttl <= 0:
            # 0 小时 = 不缓存，每次重新获取
            return None
        path = self._cache_path(user_id)
        try:
            stat = path.stat()
        except OSError:
            return None
        if time.time() - stat.st_mtime > ttl:
            return None
        return image_codec.encode_path(path, edge=image_codec.AVATAR_EDGE, use_cache=True)

    def _write_cache(self, user_id: str, data: bytes) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path = self._cache_path(user_id)
            temp = path.with_suffix(".tmp")
            temp.write_bytes(data)
            temp.replace(path)
        except OSError as exc:
            logger.warning("写入头像缓存失败：%s", type(exc).__name__)

    # ---- 下载 ----

    def _build_url(self, identity: RequesterIdentity, event) -> str:
        direct = platform_avatar_url(event) if event is not None else ""
        if direct:
            return direct
        if self.settings.has_template and identity.user_id:
            return self.settings.url_template.replace(PLACEHOLDER_TOKEN, identity.user_id)
        return ""

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
            return image_codec.make_initial_avatar(identity.display_name)

        cached = self._read_cache(identity.user_id)
        if cached:
            return cached

        url = self._build_url(identity, event)
        if url:
            data = await self._download(url)
            if data:
                normalized = _normalize_avatar(data)
                if normalized:
                    self._write_cache(identity.user_id, normalized)
                    uri = image_codec.encode_bytes(
                        normalized,
                        max_bytes=image_codec.MAX_AVATAR_BYTES,
                        edge=image_codec.AVATAR_EDGE,
                    )
                    if uri:
                        return uri
            logger.info("头像不可用（user=%s），改用首字符头像", _mask(identity.user_id))

        return image_codec.make_initial_avatar(identity.display_name)


__all__ = [
    "AvatarSettings",
    "AvatarProvider",
    "AVATAR_DIRNAME",
    "DEFAULT_CACHE_HOURS",
    "DEFAULT_TIMEOUT_S",
]
