"""发起者身份解析：昵称截断、缺方法、抛异常、平台自带头像。"""

from __future__ import annotations

import pytest

from archive import identity


class FullEvent:
    def __init__(self, user_id="10001", name="山吹桐生"):
        self._user_id = user_id
        self._name = name

    def get_sender_id(self):
        return self._user_id

    def get_sender_name(self):
        return self._name


class ExplodingEvent:
    def get_sender_id(self):
        raise RuntimeError("platform error")

    def get_sender_name(self):
        raise RuntimeError("platform error")


class NicknameOnlyEvent:
    class Sender:
        nickname = "只有昵称"

    def __init__(self):
        self.message_obj = type("MessageObj", (), {"sender": self.Sender()})()


class AvatarEvent(FullEvent):
    def __init__(self, url):
        super().__init__()
        self.message_obj = type("MessageObj", (), {"sender": type("S", (), {"avatar": url})()})()


# ------------------------------------------------------------------ 截断


def test_truncate_keeps_short_name():
    assert identity.truncate_name("山吹桐生") == "山吹桐生"


def test_truncate_limits_to_20_chars():
    name = "一" * 30
    result = identity.truncate_name(name)
    assert len(result) == 20
    assert result.endswith(identity.ELLIPSIS)


def test_truncate_exactly_at_limit():
    name = "二" * 20
    assert identity.truncate_name(name) == name


def test_truncate_collapses_whitespace():
    assert identity.truncate_name("  山吹 \n 桐生  ") == "山吹 桐生"


def test_truncate_empty_falls_back():
    assert identity.truncate_name("") == identity.FALLBACK_NAME
    assert identity.truncate_name("   ") == identity.FALLBACK_NAME
    assert identity.truncate_name(None) == identity.FALLBACK_NAME


# ------------------------------------------------------------------ 读事件


def test_from_event_reads_id_and_name():
    who = identity.from_event(FullEvent())
    assert who.user_id == "10001"
    assert who.display_name == "山吹桐生"
    assert who.avatar_data_uri == ""


def test_from_event_truncates_long_name():
    who = identity.from_event(FullEvent(name="很长" * 20))
    assert len(who.display_name) == identity.MAX_DISPLAY_CHARS


def test_from_event_survives_exceptions():
    who = identity.from_event(ExplodingEvent())
    assert who.user_id == ""
    assert who.display_name == identity.FALLBACK_NAME


def test_from_event_falls_back_to_sender_nickname():
    who = identity.from_event(NicknameOnlyEvent())
    assert who.display_name == "只有昵称"


def test_from_event_without_methods():
    who = identity.from_event(object())
    assert who.display_name == identity.FALLBACK_NAME


def test_from_event_none():
    who = identity.from_event(None)
    assert who.user_id == ""
    assert who.display_name == identity.FALLBACK_NAME


# ------------------------------------------------------------------ 其它


def test_display_initial():
    assert identity.RequesterIdentity("1", "山吹桐生").display_initial == "山"
    assert identity.RequesterIdentity("1", "  A").display_initial == "A"
    assert identity.RequesterIdentity("1", "").display_initial == "夜"


def test_with_avatar_keeps_other_fields():
    who = identity.RequesterIdentity("1", "山吹桐生")
    updated = who.with_avatar("data:image/webp;base64,AAA")
    assert updated.user_id == who.user_id
    assert updated.display_name == who.display_name
    assert updated.avatar_data_uri.startswith("data:image/webp")


def test_platform_avatar_url_from_sender():
    event = AvatarEvent("https://example.invalid/avatar.png")
    assert identity.platform_avatar_url(event).startswith("https://example.invalid/")


def test_platform_avatar_url_ignores_non_url():
    event = AvatarEvent("not-a-url")
    assert identity.platform_avatar_url(event) == ""


def test_platform_avatar_url_without_event():
    assert identity.platform_avatar_url(None) == ""
    assert identity.platform_avatar_url(object()) == ""


@pytest.mark.parametrize("limit", [1, 2, 5])
def test_truncate_respects_custom_limit(limit):
    result = identity.truncate_name("abcdefghij", limit=limit)
    assert len(result) == limit
