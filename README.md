# 夜幕档案

`astrbot_plugin_phantom_archive` 是面向 AstrBot 的《女神异闻录：夜幕魅影》中文资料查询插件。

> **当前为第一阶段**：只开放角色、技能、武器三类只读查询。
> 配队推荐、意识详情、攻略正文均未开放。

## 当前开放的指令

```text
/夜幕帮助            查看可用指令
/p5x角色 <名称>      角色基本资料 + 技能概要 + 武器概要
/p5x技能 <名称>      全部技能（名称 / 类型 / SP / 冷却 / 正文）
/p5x武器 <名称>      全部武器（星级 / 生命·攻击·防御 / 技能名称 / 技能描述）
/p5x角色列表         列出当前收录的角色
```

名称支持中文名、韩文原名、英文代号和 slug：

```text
/p5x角色 汐见琴音
/p5x角色 코토네
/p5x角色 KOTONE
/p5x角色 kotone
```

四条会命中同一个角色。完全匹配优先于模糊匹配，所以「雨宫莲」不会被「雨宫莲·星舞」污染成多候选。

## 数据来源与可信度

**数据来源是 Lufel 社区资料，不是国服官方数据库。**

| 指标 | 数值 |
| --- | --- |
| 收录角色 | 72 名 |
| 技能 | 418 条 |
| 武器 | 142 把 |
| 攻略行（本阶段未开放） | 360 行 |
| 嵌套意识条目（本阶段未开放） | 476 项 |
| 来源数值冲突 | 131 条 |
| **国服官方核准率** | **0%** |

- `source_status` 全部是 `community`，`official` 计数为 0。社区整理的中文未经国服官网逐条核验。
- 中文字段覆盖率 100% 只表示「存在可显示的中文字段」，**不表示内容已核准**。
- 翻译方式分布：来源原文 700 / 992、机器翻译 268 / 992、待人工审核 24 / 992。
- 上游中韩文本存在 131 处数字差异，可能来自服务器版本不同。命中差异的条目会在回复尾部追加
  「⚠ 该条目的不同语言来源存在数值差异，当前结果仅供参考。」。

每条数据回复都固定带免责声明，文案唯一出处是 `archive/notices.py`：

```text
资料来源：Lufel 社区资料
部分名称和攻略经过机器翻译，尚未完成国服逐项核对。
检测到部分区服数值存在差异，请以当前国服游戏内数据为准。
```

## 数据表结构（只读）

| 表 | 用途 |
| --- | --- |
| `zh_characters` | 角色：中文名、代号、slug、属性、职业、稀有度、简介 |
| `zh_skills` | 技能：名称、属性、类型、正文、SP、冷却 |
| `zh_weapons` | 武器：名称、稀有度、生命/攻击/防御、被动名、正文 |
| `zh_guides` | 攻略（本阶段不读） |
| `numeric_conflicts` | 来源数值冲突明细 |
| `source_registry` | 来源登记 |

插件只读 `p5x_zh.sqlite3`，**绝不读取原始库 `p5x.sqlite3`**；配置指向原始库会被直接拒绝。

## 安装

### 1. 放置数据库

插件从两个位置之一读取数据库，按顺序尝试：

1. 配置项 `database_path` 指定的绝对路径；
2. 插件目录下的 `data/p5x_zh.sqlite3`。

```text
astrbot/data/plugins/astrbot_plugin_phantom_archive/
└─ data/
   ├─ p5x_zh.sqlite3        ← 必须
   └─ display_notices.json  ← 可选，仅用于文案一致性自检
```

数据库由 `p5x-data-crawler` 生成，**不随仓库提交**（`data/` 在 `.gitignore` 里）：

```bash
cd p5x-data-crawler
node zh/localize.mjs
cp output/p5x_zh.sqlite3   /path/to/astrbot/data/plugins/astrbot_plugin_phantom_archive/data/
cp output/display_notices.json /path/to/astrbot/data/plugins/astrbot_plugin_phantom_archive/data/
```

### 2. Docker 用户挂载

```yaml
services:
  astrbot:
    image: soulter/astrbot:latest
    volumes:
      - ./data:/AstrBot/data
      # 把数据库单独挂进插件目录，升级插件不会丢
      - ./p5x_zh.sqlite3:/AstrBot/data/plugins/astrbot_plugin_phantom_archive/data/p5x_zh.sqlite3:ro
```

`:ro` 是额外一层保险，即使插件出 bug 也写不动宿主文件。

### 3. 配置项

`_conf_schema.json`：

```json
{
  "database_path": {
    "description": "p5x_zh.sqlite3 的路径，留空时使用插件 data 目录",
    "type": "string",
    "default": ""
  },
  "max_message_length": {
    "description": "单条查询回复的最大字符数",
    "type": "int",
    "default": 1200
  }
}
```

## 数据库缺失时会怎样

不会静默降级，也不会自动下载：

```text
夜幕档案数据库尚未安装，请联系机器人管理员。
```

同时日志里会打出实际尝试的路径和错误类型，但不会把 SQL、服务器路径或异常栈发到群聊。
数据库存在但缺表/缺字段时返回：

```text
夜幕档案数据库版本不兼容，请联系机器人管理员更新。
```

## 开发与测试

```bash
pip install pytest
python -m pytest tests -q
```

测试覆盖：

| 文件 | 内容 |
| --- | --- |
| `test_database_readonly.py` | 只读连接、8 条写语句全部失败、查询前后 SHA-256 不变 |
| `test_search.py` | 四种别名、大小写、空格、无结果、多候选、完全匹配优先 |
| `test_renderer.py` | 长度上限、免责声明、降级文案、截断计数、断句 |
| `test_numeric_conflict.py` | 有冲突必提示、无冲突禁止提示、技能与武器各自范围 |
| `test_full_smoke.py` | 72 名角色 × 3 条指令全量渲染 |
| `test_plugin_smoke.py` | 真实 AstrBot 环境下导入插件并驱动指令 |

`tests/conftest.py` 需要 `data/p5x_zh.sqlite3` 存在，否则整个会话跳过（避免把「没数据」误报成通过）。

## 实现说明

```text
main.py                 AstrBot 指令注册、配置读取、错误兜底
archive/
├─ database.py          只读连接（mode=ro + PRAGMA query_only）、路径解析、结构校验
├─ repository.py        参数化 SQL
├─ search.py            分级检索
├─ renderer.py          带预算的排版
├─ notices.py           提示文案唯一出处
├─ models.py            行模型
└─ service.py           AstrBot 与查询层之间的门面
```

几个刻意的取舍：

- **三层只读**：URI `mode=ro` + `PRAGMA query_only=ON` + 只暴露查询方法。测试逐条验证 8 种写语句都会失败。
- **整块取舍**：消息放不下时整条省略并注明「另有 N 条未显示」，不会切半句话；正文截断只退到句末标点。
- **冲突从库算**：不按角色名硬编码，作用域是「角色 = 技能 + 武器」「技能 = 技能」「武器 = 武器」。
- **查询走线程**：`asyncio.to_thread` + 连接锁，避免阻塞 AstrBot 事件循环。

## 路线图

- [x] 只读数据库接入
- [x] 角色 / 技能 / 武器查询
- [x] 来源与翻译状态标注
- [x] 数值冲突提示
- [ ] 意识详情
- [ ] 配队推荐
- [ ] 攻略正文
- [ ] 图片卡片

## 许可与免责

本插件只做查询与排版，不修改数据。攻略内容版权归社区原作者，建议个人使用，不要重新公开发布整个数据集。
