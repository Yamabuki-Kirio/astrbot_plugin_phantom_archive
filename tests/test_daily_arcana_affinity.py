from archive.daily import arcana_affinity, engine
from archive.daily.models import DailyCharacter


def character(key: str, name: str = "角色") -> DailyCharacter:
    return DailyCharacter(key=key, slug=key, name=name)


def test_major_arcana_prefers_matching_mainline_character(monkeypatch):
    monkeypatch.setattr(
        "archive.daily.tarot.draw",
        lambda rng: __import__("archive.daily.models", fromlist=["TarotDraw"]).TarotDraw(
            name="VII 战车 The Chariot", is_reversed=False, meaning="前进"
        ),
    )
    pool = [character("YUI"), character("류지", "坂本龙司"), character("아이기스", "埃癸斯")]
    chosen, card, *_ = engine.draw_fortune(
        date_key="2026-10-27", platform_id="qq", user_id="42", characters=pool
    )
    assert card.name.startswith("VII 战车")
    assert chosen.key in {"류지", "아이기스"}


def test_minor_arcana_keeps_full_pool(monkeypatch):
    monkeypatch.setattr(
        "archive.daily.tarot.draw",
        lambda rng: __import__("archive.daily.models", fromlist=["TarotDraw"]).TarotDraw(
            name="权杖王牌", is_reversed=False, meaning="开始"
        ),
    )
    pool = [character("only-new", "P5X原创角色")]
    chosen, *_ = engine.draw_fortune(
        date_key="2026-10-27", platform_id="qq", user_id="42", characters=pool
    )
    assert chosen.key == "only-new"


def test_same_user_same_day_stays_identical():
    pool = [character(key) for keys in arcana_affinity.ARCANA_CHARACTER_KEYS.values() for key in keys]
    one = engine.generate(
        date_key="2026-10-27", timezone="Asia/Shanghai",
        platform_id="qq", user_id="same", characters=pool,
    )
    two = engine.generate(
        date_key="2026-10-27", timezone="Asia/Shanghai",
        platform_id="qq", user_id="same", characters=reversed(pool),
    )
    assert one.to_payload() == two.to_payload()


def test_all_mapped_keys_exist_in_current_database(service):
    existing = {item.source_key for item in service.list_characters()}
    mapped = set().union(*arcana_affinity.ARCANA_CHARACTER_KEYS.values())
    assert mapped <= existing
