"""T2I（HTML → 图片）客户端与响应校验。

设计约束（都是踩过的坑，不要图省事绕过）：

1. 请求体里放 HTML 的字段是 ``tmpl``，不是 ``html``；
2. 渲染服务地址完全来自配置，代码里不出现任何域名、IP、端口，
   也不替用户补 ``/generate`` —— 路径由部署者自己决定；
3. ``t2i_token`` 只进请求头，绝不进日志、异常文本和状态输出；
4. 响应必须先验状态码、再验体积、再验文件头、最后用 Pillow 完整解码，
   任何一步不过都当失败处理，避免把一段 HTML 错误页当图片发到群里；
5. ``trust_env=False``，不读取系统代理设置，否则在容器里会莫名其妙走代理超时。
"""

from __future__ import annotations

import io
import logging
import time
import warnings
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# 同时渲染的张数上限，避免一次查询把渲染服务打满
DEFAULT_MAX_CONCURRENT = 2

# 允许的图片格式
ALLOWED_FORMATS: tuple[str, ...] = ("png", "jpeg")
FORMAT_ALIASES: dict[str, str] = {
    "png": "png",
    "jpg": "jpeg",
    "jpeg": "jpeg",
}

# device_scale_factor_level 的可选值
ALLOWED_SCALES: tuple[str, ...] = ("sd", "hd", "fhd")

# 像素上限，防止一张图把内存吃光
MAX_IMAGE_PIXELS = 40_000_000

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"

DEFAULT_RENDER_TIMEOUT_MS = 30_000
DEFAULT_REQUEST_TIMEOUT_MS = 45_000
DEFAULT_MAX_RESPONSE_MB = 16
DEFAULT_QUALITY = 90
DEFAULT_SCALE = "hd"
DEFAULT_IMAGE_FORMAT = "png"

MIN_QUALITY = 10
MAX_QUALITY = 100
MAX_RESPONSE_MB_CEILING = 64


class RenderError(Exception):
    """渲染失败的基类。message 会直接给用户看，不要塞内部细节。"""


class RenderNotConfigured(RenderError):
    """没有配置渲染服务地址，或依赖缺失。"""


class RenderTransportError(RenderError):
    """连不上、超时、连接被重置。"""


class RenderStatusError(RenderError):
    """服务返回了非 2xx。"""


class RenderPayloadError(RenderError):
    """返回内容不是一张合格的图片。"""


@dataclass(frozen=True)
class RenderSettings:
    """一份可配置的渲染参数。默认值就是「没配地址」，即不可用。"""

    endpoint: str = ""
    token: str = ""
    image_format: str = DEFAULT_IMAGE_FORMAT
    quality: int = DEFAULT_QUALITY
    scale: str = DEFAULT_SCALE
    render_timeout_ms: int = DEFAULT_RENDER_TIMEOUT_MS
    request_timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MS
    max_response_mb: int = DEFAULT_MAX_RESPONSE_MB
    theme: str = "dark"

    @property
    def configured(self) -> bool:
        return bool(self.endpoint)

    @property
    def request_timeout_s(self) -> float:
        return max(1.0, self.request_timeout_ms / 1000.0)

    @property
    def max_bytes(self) -> int:
        return self.max_response_mb * 1024 * 1024

    @property
    def masked_endpoint(self) -> str:
        """给状态输出用的地址：去掉查询串和 userinfo，其余照实显示。"""
        raw = self.endpoint
        if not raw:
            return ""
        head, _, _ = raw.partition("?")
        scheme, sep, rest = head.partition("://")
        if not sep:
            return head
        authority, slash, path = rest.partition("/")
        host = authority.rpartition("@")[2]
        return f"{scheme}://{host}{slash}{path}"

    def describe(self) -> dict[str, Any]:
        """状态输出。这里绝不能出现 token 的值。"""
        return {
            "configured": self.configured,
            "endpoint": self.masked_endpoint,
            "token_set": bool(self.token),
            "image_format": self.image_format,
            "quality": self.quality,
            "scale": self.scale,
            "render_timeout_ms": self.render_timeout_ms,
            "request_timeout_ms": self.request_timeout_ms,
            "max_response_mb": self.max_response_mb,
            "theme": self.theme,
        }

    @classmethod
    def from_config(cls, get, theme: str = "dark") -> "RenderSettings":
        """从插件配置构造。非法值一律回落默认，并记一条不带敏感信息的日志。"""

        def raw(key: str, default: Any) -> Any:
            value = get(key, default)
            return default if value is None else value

        def as_int(key: str, default: int, low: int, high: int) -> int:
            try:
                value = int(raw(key, default))
            except (TypeError, ValueError):
                logger.warning("配置 %s 非法（%r），回退默认 %s", key, raw(key, default), default)
                return default
            if value < low or value > high:
                logger.warning("配置 %s 超出范围（%s），回退默认 %s", key, value, default)
                return default
            return value

        fmt = str(raw("t2i_image_format", DEFAULT_IMAGE_FORMAT)).strip().lower()
        fmt = FORMAT_ALIASES.get(fmt, DEFAULT_IMAGE_FORMAT)

        scale = str(raw("t2i_scale", DEFAULT_SCALE)).strip().lower()
        if scale not in ALLOWED_SCALES:
            logger.warning("配置 t2i_scale 非法（%r），回退默认 %s", scale, DEFAULT_SCALE)
            scale = DEFAULT_SCALE

        return cls(
            endpoint=str(raw("t2i_endpoint", "")).strip(),
            token=str(raw("t2i_token", "")).strip(),
            image_format=fmt,
            quality=as_int("t2i_quality", DEFAULT_QUALITY, MIN_QUALITY, MAX_QUALITY),
            scale=scale,
            render_timeout_ms=as_int(
                "t2i_render_timeout_ms", DEFAULT_RENDER_TIMEOUT_MS, 1_000, 300_000
            ),
            request_timeout_ms=as_int(
                "t2i_request_timeout_ms", DEFAULT_REQUEST_TIMEOUT_MS, 1_000, 300_000
            ),
            max_response_mb=as_int(
                "t2i_max_response_mb", DEFAULT_MAX_RESPONSE_MB, 1, MAX_RESPONSE_MB_CEILING
            ),
            theme=theme,
        )


@dataclass(frozen=True)
class RenderedImage:
    """一张已经通过校验的图片。"""

    data: bytes
    image_format: str
    width: int
    height: int
    elapsed_ms: int

    @property
    def size_kb(self) -> int:
        return max(1, round(len(self.data) / 1024))


def build_payload(html: str, settings: RenderSettings) -> dict[str, Any]:
    """按渲染服务约定拼请求体。字段名是 ``tmpl``，写错服务端会直接报模板缺失。"""
    return {
        "tmpl": html,
        "json": {},
        "tmpldata": {},
        "options": {
            "full_page": True,
            "type": settings.image_format,
            "quality": settings.quality,
            "device_scale_factor_level": settings.scale,
            "timeout": settings.render_timeout_ms,
        },
    }


def _sniff_format(data: bytes) -> str | None:
    if data.startswith(PNG_MAGIC):
        return "png"
    if data.startswith(JPEG_MAGIC):
        return "jpeg"
    return None


def _looks_like_text(data: bytes) -> str | None:
    """识别常见的「服务出错但返回 200」的情况，给出更准确的原因。"""
    head = data[:512].lstrip()
    lowered = head.lower()
    if lowered.startswith(b"<!doctype") or lowered.startswith(b"<html") or b"<body" in lowered:
        return "服务返回了 HTML 页面而不是图片，请检查渲染服务地址是否正确"
    if lowered.startswith(b"<svg") or b"<svg" in lowered[:200]:
        return "服务返回了 SVG 而不是位图"
    if head[:1] in (b"{", b"["):
        return "服务返回了 JSON 而不是图片，请检查渲染服务地址与参数"
    return None


def validate_image(data: bytes, *, max_bytes: int) -> tuple[str, int, int]:
    """校验响应体确实是可完整解码的位图。返回 (格式, 宽, 高)。"""
    if not data:
        raise RenderPayloadError("渲染服务返回了空内容")

    if len(data) > max_bytes:
        raise RenderPayloadError(
            f"图片体积超过上限（{len(data) // 1024} KB > {max_bytes // 1024} KB）"
        )

    sniffed = _sniff_format(data)
    if sniffed is None:
        reason = _looks_like_text(data)
        raise RenderPayloadError(reason or "返回内容不是 PNG 或 JPEG 图片")

    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - 依赖缺失由配置检查兜住
        raise RenderNotConfigured("缺少 Pillow 依赖，无法校验图片") from exc

    previous_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                image_format = (image.format or "").lower()
                width, height = image.size
                if image_format not in ("png", "jpeg"):
                    raise RenderPayloadError(f"不支持的图片格式：{image_format or '未知'}")
                if width <= 0 or height <= 0:
                    raise RenderPayloadError("图片尺寸非法")
                if width * height > MAX_IMAGE_PIXELS:
                    raise RenderPayloadError("图片像素数超出上限")
                # verify() 之后对象不可再用，必须重新打开做完整解码
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
    except RenderPayloadError:
        raise
    except Image.DecompressionBombError as exc:
        raise RenderPayloadError("图片像素数超出安全上限") from exc
    except Image.DecompressionBombWarning as exc:
        raise RenderPayloadError("图片像素数超出安全上限") from exc
    except Exception as exc:  # noqa: BLE001 - 任何解码失败都算坏图
        raise RenderPayloadError(f"图片解码失败：{type(exc).__name__}") from exc
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit

    return image_format, width, height


def _short(text: str, limit: int = 120) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


class ImageRenderer:
    """并发受控的 T2I 客户端。一个实例复用一个连接池。"""

    def __init__(
        self,
        settings: RenderSettings,
        *,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        transport=None,
    ):
        self.settings = settings
        self.max_concurrent = max(1, int(max_concurrent))
        # 测试用的注入点：传 httpx.MockTransport 就能在不起服务的情况下验证协议
        self._transport = transport
        self._semaphore = None
        self._client = None

    # ---- 生命周期 ----

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        try:
            import httpx
        except ImportError as exc:
            raise RenderNotConfigured("缺少 httpx 依赖，无法调用渲染服务") from exc
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.settings.request_timeout_s),
            trust_env=False,  # 不吃系统代理，容器里更可预期
            follow_redirects=False,
            transport=self._transport,
        )
        return self._client

    def _ensure_semaphore(self):
        if self._semaphore is None:
            import asyncio

            self._semaphore = asyncio.Semaphore(self.max_concurrent)
        return self._semaphore

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:  # noqa: BLE001 - 关闭失败不影响卸载
                logger.debug("关闭渲染客户端时出错", exc_info=True)
            self._client = None

    # ---- 请求 ----

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "image/png,image/jpeg"}
        if self.settings.token:
            headers["Authorization"] = f"Bearer {self.settings.token}"
        return headers

    async def render(self, html: str) -> RenderedImage:
        """把一段 HTML 交给渲染服务，拿回一张校验过的图片。"""
        if not self.settings.configured:
            raise RenderNotConfigured("尚未配置 T2I 渲染服务地址")
        if not html or not html.strip():
            raise RenderPayloadError("待渲染的 HTML 为空")

        import httpx

        client = self._ensure_client()
        semaphore = self._ensure_semaphore()
        payload = build_payload(html, self.settings)
        limit = self.settings.max_bytes
        started = time.perf_counter()

        async with semaphore:
            try:
                buffer = bytearray()
                async with client.stream(
                    "POST",
                    self.settings.endpoint,
                    json=payload,
                    headers=self._headers(),
                ) as response:
                    if response.status_code // 100 != 2:
                        snippet = ""
                        try:
                            raw = await response.aread()
                            snippet = _short(raw.decode("utf-8", "replace"))
                        except Exception:  # noqa: BLE001 - 读错误体失败不影响判断
                            snippet = ""
                        raise RenderStatusError(
                            f"渲染服务返回 HTTP {response.status_code}"
                            + (f"：{snippet}" if snippet else "")
                        )

                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > limit:
                        raise RenderPayloadError(
                            f"图片体积超过上限（{int(declared) // 1024} KB > {limit // 1024} KB）"
                        )

                    async for chunk in response.aiter_bytes():
                        buffer.extend(chunk)
                        if len(buffer) > limit:
                            raise RenderPayloadError(
                                f"图片体积超过上限（>{limit // 1024} KB）"
                            )
            except RenderError:
                raise
            except httpx.TimeoutException as exc:
                raise RenderTransportError("渲染服务响应超时，请稍后重试") from exc
            except httpx.HTTPError as exc:
                raise RenderTransportError(
                    f"无法连接渲染服务：{type(exc).__name__}"
                ) from exc

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        data = bytes(buffer)
        image_format, width, height = validate_image(data, max_bytes=limit)
        return RenderedImage(
            data=data,
            image_format=image_format,
            width=width,
            height=height,
            elapsed_ms=elapsed_ms,
        )


__all__ = [
    "RenderError",
    "RenderNotConfigured",
    "RenderTransportError",
    "RenderStatusError",
    "RenderPayloadError",
    "RenderSettings",
    "RenderedImage",
    "ImageRenderer",
    "build_payload",
    "validate_image",
    "ALLOWED_FORMATS",
    "ALLOWED_SCALES",
    "DEFAULT_MAX_CONCURRENT",
    "MAX_IMAGE_PIXELS",
]
