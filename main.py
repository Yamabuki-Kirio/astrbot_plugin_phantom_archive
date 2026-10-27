from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register


@register("astrbot_plugin_p5x", "Yamabuki-Kirio", "P5X 助手（开发占位版）", "0.0.1")
class P5XPlugin(Star):
    """P5X 中文资料查询插件的最小占位实现。"""

    def __init__(self, context: Context):
        super().__init__(context)

    @filter.command("p5x")
    async def p5x_status(self, event: AstrMessageEvent):
        """显示插件当前开发状态。"""
        yield event.plain_result(
            "P5X 助手正在开发中。当前仓库仅为占位版本，尚未接入角色、武器、配队和公告查询。"
        )

