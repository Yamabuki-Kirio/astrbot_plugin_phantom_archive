"""本地图片编解码：把磁盘上的图片变成可直接塞进 HTML 的 Data URI。

为什么必须自己做这件事，而不是把文件路径或 URL 交给模板：

1. 渲染服务（T2I）常常跑在没有外网、也读不到插件目录的容器里，
   远程 URL 和本地路径都会变成一张破图；
2. 只有 ``data:image/...;base64,...`` 是自包含的，一张 HTML 走遍天下；
3. 顺手把体积压下来 —— 立绘原图动辄几 MB，base64 再膨胀 33%，
   不压缩会让每张卡片都变成几十 MB 的请求体。

这一层不判断「该用哪张图」，只负责「把这张图变成能用的字符串」。
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
from collections import OrderedDict
from pathlib import Path

logger = logging.getLogger(__name__)

# 单张源图上限（素材 8 MiB，头像 1 MiB）
MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_AVATAR_BYTES = 1024 * 1024

# 输出边长上限：立绘 900px 足够 820px 宽卡片用，头像 128px 足够 52px 圆形显示
MAX_EDGE = 900
AVATAR_EDGE = 128

DEFAULT_QUALITY = 82
AVATAR_QUALITY = 80

# 像素上限，防止解压炸弹
MAX_IMAGE_PIXELS = 40_000_000

SUPPORTED_FORMATS = ("PNG", "JPEG", "WEBP")

# 文件头 → 格式，用于在 Pillow 之前先挡一次明显不是图片的东西
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "PNG"),
    (b"\xff\xd8\xff", "JPEG"),
)

# 缓存：(路径, mtime, size) → data uri。素材不会天天变，编码一次就够。
_CACHE: OrderedDict[tuple, str] = OrderedDict()
_CACHE_LIMIT = 128

# 只有真正横构图（宽 ≥ 高）的图才算「宽图」：同一角色的动作大字图会落在这一档，
# 这类图塞进窄高的立绘列必须 contain，否则 cover 会把人物裁到画面外。
# 接近方形的立绘（如 855×960）仍按竖构图处理 —— 裁掉两边远比留一圈空白好看。
WIDE_ASPECT_RATIO = 1.0

# 只读文件头拿到的尺寸缓存
_SIZE_CACHE: OrderedDict[tuple, tuple[int, int] | None] = OrderedDict()


def clear_cache() -> None:
    _CACHE.clear()
    _SIZE_CACHE.clear()


def sniff_format(data: bytes) -> str | None:
    """按文件头判断格式；WebP 需要看 RIFF....WEBP。"""
    for magic, name in _MAGIC:
        if data.startswith(magic):
            return name
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    return None


def is_supported(data: bytes) -> bool:
    return sniff_format(data) in SUPPORTED_FORMATS


def _open_image(data: bytes):
    from PIL import Image

    previous = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        return image
    finally:
        Image.MAX_IMAGE_PIXELS = previous


def _to_data_uri(image, *, edge: int, quality: int) -> str:
    """等比缩到 edge 以内，转 WebP，再 base64。"""
    image = image.convert("RGBA") if image.mode in ("RGBA", "LA", "P") else image.convert("RGB")
    width, height = image.size
    longest = max(width, height)
    if longest > edge:
        scale = edge / longest
        image = image.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            image.LANCZOS if hasattr(image, "LANCZOS") else 1,
        )

    buffer = io.BytesIO()
    if image.mode == "RGBA":
        image.save(buffer, format="WEBP", quality=quality, method=4)
    else:
        image.save(buffer, format="WEBP", quality=quality, method=4)
    payload = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/webp;base64,{payload}"


def encode_bytes(
    data: bytes,
    *,
    max_bytes: int = MAX_SOURCE_BYTES,
    edge: int = MAX_EDGE,
    quality: int = DEFAULT_QUALITY,
) -> str | None:
    """原始字节 → data URI。任何一步失败都返回 None，调用方负责回退。"""
    if not data:
        return None
    if len(data) > max_bytes:
        logger.warning("图片超过体积上限（%d > %d 字节），跳过", len(data), max_bytes)
        return None
    if not is_supported(data):
        logger.warning("不支持的图片格式（文件头 %r），跳过", data[:8])
        return None
    try:
        image = _open_image(data)
    except Exception as exc:  # noqa: BLE001 - 解码失败一律当坏图
        logger.warning("图片解码失败：%s", type(exc).__name__)
        return None
    if image.size[0] * image.size[1] > MAX_IMAGE_PIXELS:
        logger.warning("图片像素数超限：%sx%s", *image.size)
        return None
    try:
        return _to_data_uri(image, edge=edge, quality=quality)
    except Exception as exc:  # noqa: BLE001
        logger.warning("图片转码失败：%s", type(exc).__name__)
        return None


def encode_path(
    path: str | Path,
    *,
    edge: int = MAX_EDGE,
    quality: int = DEFAULT_QUALITY,
    max_bytes: int = MAX_SOURCE_BYTES,
    use_cache: bool = True,
) -> str | None:
    """磁盘文件 → data URI，带 (路径, mtime, size) 缓存。"""
    try:
        file = Path(path)
        stat = file.stat()
    except OSError:
        return None
    if not file.is_file() or stat.st_size == 0:
        return None

    key = (str(file.resolve()), stat.st_mtime_ns, stat.st_size, edge, quality)
    if use_cache:
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            return cached

    try:
        data = file.read_bytes()
    except OSError as exc:
        logger.warning("读取图片失败：%s（%s）", file.name, type(exc).__name__)
        return None

    uri = encode_bytes(data, max_bytes=max_bytes, edge=edge, quality=quality)
    if uri and use_cache:
        _CACHE[key] = uri
        while len(_CACHE) > _CACHE_LIMIT:
            _CACHE.popitem(last=False)
    return uri


def probe_size(path: str | Path) -> tuple[int, int] | None:
    """只读文件头拿宽高（不解码像素），用于判断立绘是竖构图还是横构图。

    失败返回 ``None``：调用方一律当作「竖构图」处理，最坏只是裁得紧一点。
    """
    try:
        file = Path(path)
        stat = file.stat()
    except OSError:
        return None
    key = (str(file.resolve()), stat.st_mtime_ns, stat.st_size)
    cached = _SIZE_CACHE.get(key, ...)
    if cached is not ...:
        _SIZE_CACHE.move_to_end(key)
        return cached  # type: ignore[return-value]

    size: tuple[int, int] | None
    try:
        from PIL import Image

        with Image.open(file) as image:
            size = image.size
    except Exception:  # noqa: BLE001 - 坏图当拿不到
        size = None

    _SIZE_CACHE[key] = size
    while len(_SIZE_CACHE) > _CACHE_LIMIT:
        _SIZE_CACHE.popitem(last=False)
    return size


def is_wide_image(path: str | Path) -> bool:
    """宽 ≥ 高的图在窄列里应改用 contain。读不出来一律当作竖构图。"""
    size = probe_size(path)
    if not size:
        return False
    width, height = size
    if width <= 0 or height <= 0:
        return False
    return height < width * WIDE_ASPECT_RATIO


# ------------------------------------------------------------------ 首字符头像

_FONT_CANDIDATES: tuple[str, ...] = (
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/PingFang.ttc",
)

AVATAR_SIZE = 128
_AVATAR_PALETTE: tuple[tuple[int, int, int], ...] = (
    (226, 58, 69),
    (63, 134, 214),
    (216, 161, 58),
    (155, 107, 216),
    (67, 181, 129),
    (201, 92, 168),
)


def _load_font(size: int):
    from PIL import ImageFont

    for candidate in _FONT_CANDIDATES:
        try:
            if Path(candidate).is_file():
                return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except Exception:  # noqa: BLE001 - 老版本 Pillow 不支持 size 参数
        return ImageFont.load_default()


def _initial_char(name: str) -> str:
    for char in (name or "").strip():
        if not char.isspace():
            return char
    return "夜"


def make_initial_avatar(name: str, *, size: int = AVATAR_SIZE) -> str:
    """用昵称首字符生成默认头像。

    头像永远有得用 —— 下载失败、平台没有头像、用户没填昵称，
    都不该让整张卡片失去表头。
    """
    from PIL import Image, ImageDraw

    char = _initial_char(name)
    index = int(hashlib.sha1(char.encode("utf-8")).hexdigest()[:8], 16)
    color = _AVATAR_PALETTE[index % len(_AVATAR_PALETTE)]

    image = Image.new("RGB", (size, size), color)
    draw = ImageDraw.Draw(image)
    # 对角渐变，避免纯色块看起来像加载失败
    for step in range(size):
        ratio = step / max(1, size - 1)
        shade = tuple(int(value * (0.72 + 0.28 * ratio)) for value in color)
        draw.line([(step, 0), (step, size)], fill=shade)

    font = _load_font(int(size * 0.46))
    try:
        left, top, right, bottom = draw.textbbox((0, 0), char, font=font)
        draw.text(
            ((size - (right - left)) / 2 - left, (size - (bottom - top)) / 2 - top),
            char,
            font=font,
            fill=(255, 255, 255),
        )
    except Exception:  # noqa: BLE001 - 字体画不出来就退化成剪影
        draw.ellipse(
            [size * 0.30, size * 0.20, size * 0.70, size * 0.60], fill=(255, 255, 255)
        )
        draw.ellipse(
            [size * 0.18, size * 0.62, size * 0.82, size * 1.10], fill=(255, 255, 255)
        )

    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", quality=AVATAR_QUALITY, method=4)
    payload = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/webp;base64,{payload}"


__all__ = [
    "MAX_SOURCE_BYTES",
    "MAX_AVATAR_BYTES",
    "MAX_EDGE",
    "AVATAR_EDGE",
    "SUPPORTED_FORMATS",
    "sniff_format",
    "is_supported",
    "encode_bytes",
    "encode_path",
    "probe_size",
    "is_wide_image",
    "WIDE_ASPECT_RATIO",
    "make_initial_avatar",
    "clear_cache",
]
