"""从事件里读出「谁在查」——只有 ID 和昵称，不含任何平台细节。

这一层必须足够宽厚：不同平台的事件对象差别很大，
少一个方法、多一个异常，都不该让查询失败。
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_DISPLAY_CHARS = 20
ELLIPSIS = "…"
FALLBACK_NAME = "匿名用户"


@dataclass(frozen=True)
class RequesterIdentity:
    """发起查询的用户。avatar_data_uri 由 avatar_provider 事后填充。"""

    user_id: str
    display_name: str
    avatar_data_uri: str = ""

    @property
    def display_initial(self) -> str:
        for char in self.display_name:
            if not char.isspace():
                return char
        return "夜"

    def with_avatar(self, data_uri: str) -> "RequesterIdentity":
        return RequesterIdentity(
            user_id=self.user_id,
            display_name=self.display_name,
            avatar_data_uri=data_uri,
        )


def truncate_name(name: str, limit: int = MAX_DISPLAY_CHARS) -> str:
    """昵称最长 limit 个字符，超出用省略号收尾（省略号也算一个字符）。"""
    cleaned = " ".join((name or "").split())
    if not cleaned:
        return FALLBACK_NAME
    if len(cleaned) <= limit:
        return cleaned
    if limit <= 1:
        # 只留得下 1 个字符时，省略号反而更没用
        return cleaned[:1] if limit == 1 else ""
    return cleaned[: limit - 1] + ELLIPSIS


def _safe_call(event, method: str) -> str:
    func = getattr(event, method, None)
    if not callable(func):
        return ""
    try:
        value = func()
    except Exception:  # noqa: BLE001 - 事件实现千奇百怪，取不到就算了
        return ""
    return value if isinstance(value, str) else ("" if value is None else str(value))


def from_event(event) -> RequesterIdentity:
    """读取发起者。任何异常都退化成匿名用户，绝不向上抛。"""
    if event is None:
        return RequesterIdentity(user_id="", display_name=FALLBACK_NAME)

    user_id = _safe_call(event, "get_sender_id").strip()
    raw_name = _safe_call(event, "get_sender_name").strip()

    if not raw_name:
        # 有些平台把昵称挂在 message_obj.sender.nickname 上
        sender = getattr(getattr(event, "message_obj", None), "sender", None)
        nickname = getattr(sender, "nickname", None)
        if isinstance(nickname, str):
            raw_name = nickname.strip()

    return RequesterIdentity(user_id=user_id, display_name=truncate_name(raw_name))


UNKNOWN_PLATFORM = "unknown"


def platform_id(event) -> str:
    """平台标识，用于和 sender_id 拼成用户唯一键。

    不同平台上的数字 ID 会撞车（QQ 的 10001 和别的平台 10001 是两个人），
    所以用户键必须是「平台 + 用户」，不能只用 sender_id。
    优先取平台实例 id（同一平台多开时也不会混），退到平台名。
    """
    if event is None:
        return UNKNOWN_PLATFORM

    meta = getattr(event, "platform_meta", None)
    for holder in (meta, event):
        if holder is None:
            continue
        for attr in ("id", "name"):
            value = getattr(holder, attr, None)
            if isinstance(value, str) and value.strip():
                return value.strip()

    for method in ("get_platform_name", "get_platform_id"):
        value = _safe_call(event, method).strip()
        if value:
            return value
    return UNKNOWN_PLATFORM


def platform_kind(event) -> str:
    """平台**类型**（aiocqhttp / telegram / …），用于判断能不能自动拼头像地址。

    与 :func:`platform_id` 的区别：那个优先取实例 id（同一平台多开时用来区分身份），
    这里只要类型名 —— ``aiocqhttp`` 才知道对方是 OneBot 后端。
    """
    if event is None:
        return ""

    meta = getattr(event, "platform_meta", None)
    for holder in (meta, event):
        if holder is None:
            continue
        for attr in ("name", "type", "adapter_name"):
            value = (
                holder.get(attr)
                if isinstance(holder, dict)
                else getattr(holder, attr, None)
            )
            if isinstance(value, str) and value.strip():
                return value.strip().lower()

    return _safe_call(event, "get_platform_name").strip().lower()


def platform_avatar_url(event) -> str:
    """平台事件里自带的头像地址。

    AstrBot 的 ``MessageMember`` 目前只有 user_id / nickname，
    所以这里只是「如果有就用」的兼容分支，不能指望一定有。
    """
    candidates = []
    message_obj = getattr(event, "message_obj", None)
    sender = getattr(message_obj, "sender", None)
    for holder in (sender, message_obj, event):
        if holder is None:
            continue
        for attr in ("avatar", "avatar_url", "avatarUrl", "head_img"):
            value = getattr(holder, attr, None)
            if isinstance(value, str) and value.startswith(("http://", "https://", "data:image/")):
                candidates.append(value)
    return candidates[0] if candidates else ""


__all__ = [
    "RequesterIdentity",
    "from_event",
    "truncate_name",
    "platform_avatar_url",
    "platform_id",
    "platform_kind",
    "MAX_DISPLAY_CHARS",
    "FALLBACK_NAME",
    "UNKNOWN_PLATFORM",
]
