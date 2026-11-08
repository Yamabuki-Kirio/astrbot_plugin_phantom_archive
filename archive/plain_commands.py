"""无斜杠指令白名单：只匹配完整命令词，不扫描聊天内容。"""

# 值为原有处理器，所有查询、运势和管理鉴权继续走原逻辑。
COMMANDS = {
    "夜幕帮助": ("help_command", False),
    "p5x角色": ("character_command", True),
    "p5x技能": ("skills_command", True),
    "p5x武器": ("weapons_command", True),
    "p5x角色列表": ("character_list_command", False),
    "p5x意识": ("ritual_command", True),
    "p5x属性意识": ("innate_command", True),
    "p5x养成": ("setting_command", True),
    "p5x操作": ("operation_command", True),
    "p5x评价": ("review_command", True),
    "p5x配队": ("party_command", True),
    "p5x攻略": ("guides_command", True),
    "p5x抽卡记录": ("gacha_history_command", False),
    "p5x每日运势": ("daily_fortune_command", False),
    "p5x运势": ("daily_fortune_alias_command", False),
    "p5x今日角色": ("daily_character_command", False),
    "p5x渲染状态": ("render_status_command", False),
    "p5x测试渲染": ("test_render_command", False),
}


def match_plain_command(text: str) -> str | None:
    text = text.strip()
    if not text or text.startswith("/"):
        return None
    parts = text.split(maxsplit=1)
    entry = COMMANDS.get(parts[0])
    if entry is None:
        return None
    handler, accepts_argument = entry
    if len(parts) > 1 and not accepts_argument:
        return None
    return handler
