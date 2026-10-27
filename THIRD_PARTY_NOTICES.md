# 第三方声明

本插件包含的第三方代码与素材，以及它们各自的使用范围。

---

## 1. astrbot_plugin_cyber_divination

| 项目 | 内容 |
| --- | --- |
| 名称 | `astrbot_plugin_cyber_divination`（赛博算命） |
| 作者 | ruoyuxian20-sys（若雨希安） |
| 许可证 | MIT |
| 用途 | 每日运势的塔罗牌义、观音灵签、周易六十四卦数据与起卦算法 |

### 实际使用的模块

| 上游文件 | 本仓库文件 | 使用方式 |
| --- | --- | --- |
| `core/tarot.py` | `archive/daily/tarot.py` | 78 张牌的牌名与正 / 逆位释义；单张抽取 |
| `core/lingqian.py` | `archive/daily/lingqian.py` | 一百签的吉凶、签诗、解曰；抽签 |
| `core/zhouyi.py` | `archive/daily/zhouyi.py` | 六十四卦卦辞与简解、上下卦对照表、三枚铜钱起卦、动爻与变卦 |
| `core/ganzhi.py` | `archive/daily/ganzhi.py` | 天干地支与日干支推算 |
| `core/daily.py` | `archive/daily/engine.py` | **仅参考**「种子 → 塔罗 → 灵签 → 周易」的抽取顺序与吉凶建议措辞 |

### 明确未使用的部分

- 奇门遁甲、占卜历史记录、LLM 润色；
- 上游自带的 T2I 出图、命令树、配置管理与消息排版；
- 求签问事、牌阵解析（三张 / 过去现在未来）、按关键词查牌。

### 本仓库做的改动

1. 随机种子由 **MD5** 改为 **SHA-256**，并且种子里加入日期、平台、用户 ID
   三段，保证「同一人同一天结果固定、跨进程一致」；
2. 所有 `draw` / `cast` 都改成**必须传入** `random.Random` 实例，模块内部不再
   自己取随机源（上游用 `random.SystemRandom()` 兜底，会导致结果不可复现）；
3. 去掉了全部 `format_*` / `build_result` 文本排版函数与命令解析；
4. 周易部分没有引入「六神」：每日运势卡片只有一屏预算，
   优先保证塔罗、灵签、卦象三块完整显示；
5. 数据以 `tuple` 常量保存，避免运行期被意外修改。

### 许可证原文

```
MIT License

Copyright (c) 2026 ruoyuxian20-sys (若雨希安)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## 2. 角色立绘与武器图

| 项目 | 内容 |
| --- | --- |
| 来源 | 《女神异闻录：夜幕魅影》国服官网 `https://p5x.wanmei.com/js/roleData260924.js` |
| 获取方式 | `scripts/fetch_official_art.py` 按角色名下载，转码为 WebP 后存到 `data/assets/characters/` |
| 版权 | 归游戏发行方所有 |
| 使用范围 | **仅限个人自用，不商用、不再分发** |

- 图片**不进仓库**：`.gitignore` 排除了 `data/assets/characters/` 与
  `data/assets/weapons/`，仓库里只有索引文件 `manifest.json` 和自绘占位图。
- 使用者需要自己运行抓取脚本，图片才会落到本地。
- 如果要把本插件用于公开服务或商业用途，请先自行取得授权，
  或把 `show_character_art` / `show_weapon_art` 关掉，退回占位图。

---

## 3. 资料数据

角色、技能、武器、攻略的中文数据来自 Lufel 社区资料，经本项目抓取与中文化后
存入 `p5x_zh.sqlite3`。来源标记见数据库 `source_status` / `translation_status`
字段，对外展示的措辞统一由 `archive/notices.py` 提供。

这部分**不是**官方数据库，也不存在「国服官方核准」的译文；
详情见 README 的「数据来源与口径」一节。
