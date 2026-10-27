# 夜幕档案

`astrbot_plugin_phantom_archive` 是面向 AstrBot 的《女神异闻录：夜幕魅影》中文资料查询插件。

> **当前为图片卡片阶段**：角色、技能、武器、意识、养成、操作、评价、配队与综合攻略均已开放，
> 查询成功时把结果渲染成图片卡片发送，渲染不可用时自动降级为文字。
> `v0.1.0` 是第一阶段基线，`v0.2.0` 是完整查询阶段，当前开发版本为 `v0.3.0`。

## 验收状态

```text
AstrBot 4.29.3 本地真实进程：已验证
本机 T2I 渲染服务（真实 HTTP + Chrome 截图）：已验证
Docker/AstrBot 容器部署：尚未验证
容器内 T2I 渲染服务：尚未验证
SnowLuma → OneBot → AstrBot → 夜幕档案：尚未验证
```

「本地真实进程」指用 pip 安装的 AstrBot 4.29.3 起完整进程，插件由官方插件管理器加载，
指令经 `/api/v1/chat` 进入真实消息管道。**这不等于容器部署已验证**，
也不等于 QQ 侧链路已验证。

图片链路的验证方式是：插件 → 真实 HTTP → 本机渲染服务（无头 Chrome 截图）→ PNG 校验 →
AstrBot 附件 → WebUI 聊天流。11 条查询指令均收到 1～4 张图片，3 类错误输入均只收到文字。
**这不代表任意第三方 T2I 服务都已验证** —— 换成别的服务时请先跑 `/p5x测试渲染`。

日志里若看到 `Plugin astrbot (4.4.0)`，那是 AstrBot 的内置插件版本，不是框架版本。

## 当前开放的指令

```text
/夜幕帮助            查看可用指令
/p5x角色 <名称>      角色基本资料 + 技能概要 + 武器概要
/p5x技能 <名称>      全部技能（名称 / 类型 / SP / 冷却 / 正文）
/p5x武器 <名称>      全部武器（星级 / 生命·攻击·防御 / 技能名称 / 技能描述）
/p5x角色列表         列出当前收录的角色
/p5x意识 <名称>      意识阶段与效果
/p5x属性意识 <名称>  属性意识与觉醒技能
/p5x养成 <名称>      启示、属性、技能优先级等养成配置
/p5x操作 <名称>      推荐操作说明与技能循环
/p5x评价 <名称>      角色评价、优点与注意事项
/p5x配队 <名称>      推荐队友、武器、人格面具与队伍
/p5x攻略 <名称>      汇总展示该角色现有攻略
/p5x抽卡记录         预留入口；鉴权方案完成前只返回开发状态（始终文字）
/p5x渲染状态         查看图片渲染配置（管理员，不显示令牌）
/p5x测试渲染         用固定内容试渲染一张卡片（管理员）
```

查询成功发图片；参数缺失、角色不存在、候选过多这三类情况仍然只发文字，
`/p5x抽卡记录` 也固定返回文字。

名称支持中文名、韩文原名、英文代号和 slug：

```text
/p5x角色 汐见琴音
/p5x角色 코토네
/p5x角色 KOTONE
/p5x角色 kotone
```

四条会命中同一个角色。完全匹配优先于模糊匹配，所以「雨宫莲」不会被「雨宫莲·星舞」污染成多候选。

## 图片卡片

### 流程

```text
repository 只查库 → service 返回 QueryOutcome（text + pages）
→ image_presenter 转展示模型并分页 → image_templates 生成 HTML
→ image_renderer 调 T2I 并校验 → main.py 决定发图 / 发文字 / 报错
```

职责边界固定：查询层不认识 HTML，模板层不认识 HTTP，渲染层不认识数据库。
文字与图片共用同一份排版块，所以降级后看到的内容与图片一致。

### 分页

- 一页约 28 行（820px 宽、正文 13.5–15px）。
- **整块切分**：放不下的条目整条挪到下一页，绝不从句子中间切开。
- 最多 4 张；超出时在最后一张显示「另有 N 项未展示」。
- 页码 `2/4` 只在多页时显示；来源、数值冲突警告、免责声明**只在最后一张**。

实测页数（72 名角色全量扫描）：

```text
角色 1 页   技能 ≤2 页   武器 1 页   角色列表 3 页
意识 ≤2 页  属性意识 1 页  养成 1 页  操作 ≤2 页
评价 1 页   配队 1 页     综合攻略 ≤4 页
```

### 配置项

图片输出全部走配置，**代码里没有任何域名、IP、端口，也不会自动补 `/generate`**。

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `image_output_enabled` | `true` | 关掉后所有查询只发文字 |
| `t2i_endpoint` | 空 | 完整 URL；留空即不启用图片 |
| `t2i_token` | 空 | 只进 `Authorization` 头，不进日志与状态输出 |
| `t2i_image_format` | `png` | `png` / `jpeg`（`jpg` 自动归一） |
| `t2i_quality` | `90` | 10–100 |
| `t2i_scale` | `hd` | `sd` / `hd` / `fhd` |
| `t2i_render_timeout_ms` | `30000` | 交给渲染服务的页面渲染超时 |
| `t2i_request_timeout_ms` | `45000` | 插件等待响应的 HTTP 超时 |
| `t2i_fallback_mode` | `text` | `text` 发文字结果，`error` 只发错误提示 |
| `t2i_max_response_mb` | `16` | 接收体积上限（1–64） |
| `image_theme` | `dark` | `dark` 暗色档案卡 / `light` 浅色 |

非法值不会让插件启动失败：一律回落默认值并在日志里记一条（不带令牌）。

### 请求协议

```json
POST <t2i_endpoint>
Authorization: Bearer <t2i_token>     // 仅在配置了令牌时发送
Content-Type: application/json

{
  "tmpl": "<完整 HTML>",
  "json": {},
  "tmpldata": {},
  "options": {
    "full_page": true,
    "type": "png",
    "quality": 90,
    "device_scale_factor_level": "hd",
    "timeout": 30000
  }
}
```

HTML 放在 **`tmpl`** 字段，不是 `html`。测试会逐字校验这一点。

### 响应校验

按顺序，任何一步不过都算失败：

1. HTTP 状态码必须是 2xx（错误体只取前 120 字用于日志）；
2. `Content-Length` 与实际读取都不得超过 `t2i_max_response_mb`（边读边计数，超限立刻断开）；
3. 文件头必须是 PNG（`89 50 4E 47`）或 JPEG（`FF D8 FF`）；
4. 明确拒绝 HTML 页面、JSON、SVG —— 服务出错时经常返回 200 + 错误页；
5. Pillow `verify()` + 重新打开 `load()` 完整解码，像素数上限 4000 万。

另外 `trust_env=False`，不读系统代理；并发上限 2 张，一个查询的多页会并行渲染。

### 模板

```text
templates/
├─ base.html            卡片骨架（配色变量、块样式、页脚）
├─ character.html       角色档案（红）
├─ skill.html           技能档案（蓝）
├─ weapon.html          武器档案（金）
├─ guide.html           攻略类共用（紫）
└─ character_list.html  角色名册（绿）
```

- 纯 HTML + CSS，**没有外链字体、CDN、JavaScript** —— 渲染服务可能没有外网。
- 宽 820px，落在 760–900px 区间内。
- Jinja2 `autoescape` 全开 + `StrictUndefined`：所有用户数据自动转义，模板少字段直接报错而不是渲染空白。
- `/p5x测试渲染` 的探针卡片里带 `<script>alert(1)</script>`，返回结果会报告「HTML 转义：正常/异常」。

### 失败时的表现

| 情况 | `text` 模式 | `error` 模式 |
| --- | --- | --- |
| 未配置 `t2i_endpoint` | 文字结果 | 文字结果 |
| `image_output_enabled=false` | 文字结果 | 文字结果 |
| 服务超时 / 连不上 | 文字结果 | 「图片渲染失败：…」 |
| 返回非 2xx | 文字结果 | 「图片渲染失败：HTTP 502…」 |
| 返回 HTML / JSON / SVG | 文字结果 | 「图片渲染失败：…」 |
| 图片超过体积上限 | 文字结果 | 「图片渲染失败：…」 |

错误提示里不会出现令牌；`/p5x渲染状态` 只显示「访问令牌：已设置 / 未设置」。

## 数据来源与可信度

**数据来源是 Lufel 社区资料，不是国服官方数据库。**

| 指标 | 数值 |
| --- | --- |
| 收录角色 | 72 名 |
| 技能 | 418 条 |
| 武器 | 142 把 |
| 攻略行 | 360 行 |
| 嵌套意识条目 | 476 项 |
| 来源数值冲突 | 131 条 |
| **国服官方核准率** | **0%** |

- `source_status` 全部是 `community`，`official` 计数为 0。社区整理的中文未经国服官网逐条核验。
- 中文字段覆盖率 100% 只表示「存在可显示的中文字段」，**不表示内容已核准**。
- 翻译方式分布：来源原文 700 / 992、机器翻译 268 / 992、待人工审核 24 / 992。
- 上游中韩文本存在 131 处数字差异，可能来自服务器版本不同。命中差异的条目会在回复尾部追加
  「⚠ 该条目的不同语言来源存在数值差异，当前结果仅供参考。」。

### 冲突角色的两个数字

这两个数字含义不同，不能混用：

| 说法 | 数值 | 含义 |
| --- | --- | --- |
| 角色总览冲突角色 | **33 名** | 角色总览展示范围（技能 + 武器） |
| 数据库全类型冲突角色 | **55 名** | 含 `review` / `ritual`，在对应攻略查询中提示 |

角色总览仍只汇总 `("skill", "weapon")`，避免显示没有上下文的警告；
评价、意识等分类查询按各自资料类型判断冲突。综合攻略汇总全部已展示的攻略类型。

## 冷却显示规则

```text
cooldown > 0    → 显示「冷却 N 回合」
cooldown = 0    → 不显示
cooldown = NULL → 不显示
```

源数据里 `cooldown = 0` 表示「无冷却」，显示成「冷却 0 回合」会误导。

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
| `zh_guides` | 评价、养成、操作、配队、意识与属性意识 |
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

`_conf_schema.json` 共 13 项：前两项是数据与文字，其余 11 项控制图片输出（见「图片卡片 · 配置项」）。

```json
{
  "database_path": { "type": "string", "default": "" },
  "max_message_length": { "type": "int", "default": 1200 },
  "image_output_enabled": { "type": "bool", "default": true },
  "t2i_endpoint": { "type": "string", "default": "" },
  "t2i_token": { "type": "string", "default": "" },
  "t2i_image_format": { "type": "string", "default": "png" },
  "t2i_quality": { "type": "int", "default": 90 },
  "t2i_scale": { "type": "string", "default": "hd" },
  "t2i_render_timeout_ms": { "type": "int", "default": 30000 },
  "t2i_request_timeout_ms": { "type": "int", "default": 45000 },
  "t2i_fallback_mode": { "type": "string", "default": "text" },
  "t2i_max_response_mb": { "type": "int", "default": 16 },
  "image_theme": { "type": "string", "default": "dark" }
}
```

（上面为压缩写法，仓库里的 `_conf_schema.json` 每项都带 `description`。）

`t2i_endpoint` 必须填**完整 URL 含路径**，插件不会替你补 `/generate`。
留空即「不启用图片」，所有查询按文字返回，不需要额外关闭 `image_output_enabled`。

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
| `test_full_smoke.py` | 72 名角色的全部查询全量渲染 |
| `test_plugin_smoke.py` | 真实 AstrBot 环境下导入插件并驱动指令 |
| `test_guides.py` | 六类攻略与综合攻略、72 名角色全量渲染 |
| `test_cards.py` | 块分类、整块分页、页数上限与「另有 N 项未展示」 |
| `test_image_presenter.py` | 六类卡片展示模型、页脚只在最后一页、冲突标记跟随数据 |
| `test_image_templates.py` | 用户数据转义、无外链资源、宽度、主题、类别映射 |
| `test_image_renderer.py` | `tmpl` 字段、鉴权头、状态码/体积/文件头/坏图拦截、配置回落 |
| `test_service_outcome.py` | 结构化结果与旧文字接口逐字一致、三类错误不带卡片 |
| `test_plugin_image.py` | 11 条指令出图、三类错误降级、两种失败模式、管理员指令 |

`tests/conftest.py` 需要 `data/p5x_zh.sqlite3` 存在，否则整个会话跳过（避免把「没数据」误报成通过）。

`v0.1.0` 第一阶段冻结时的实测结果：

```text
pytest            103 passed
渲染最长消息      1185 字（上限 1200）
真实进程冒烟      72 名角色，0 失败，最长回复 378 字
数据库 SHA-256    查询前后均为 c739a23e943c8ad7…
```

`v0.2.0` 完整查询阶段实测：

```text
pytest            111 passed
攻略查询渲染      72 名角色 × 7 种输出 = 504 次
最长攻略消息      1176 字（上限 1200）
攻略数据          review/setting/operation/party/ritual 各 69，innate 15
```

`v0.3.0` 图片卡片阶段实测：

```text
pytest                  230 passed
全量页数扫描            72 名角色 × 10 种查询，最大 4 页（综合攻略）
真实进程 + 本机渲染     11 条查询指令全部出图（1～4 张），3 类错误只发文字
单张卡片                820×604 起，约 60～215 KB，单张 0.9～1.3 s
数据库 SHA-256          查询前后一致（只读未被破坏）
```

## 实现说明

```text
main.py                 AstrBot 指令注册、配置读取、发图/降级决策
archive/
├─ database.py          只读连接（mode=ro + PRAGMA query_only）、路径解析、结构校验
├─ repository.py        参数化 SQL
├─ search.py            分级检索
├─ renderer.py          带预算的文字排版
├─ notices.py           提示文案唯一出处
├─ models.py            行模型
├─ service.py           查询门面，返回 QueryOutcome（text + pages）
├─ cards.py             排版块分类与整块分页
├─ image_presenter.py   查询结果 → 卡片展示模型
├─ image_templates.py   模板读取与渲染上下文
└─ image_renderer.py    T2I HTTP 客户端与响应校验
templates/              6 个纯 HTML+CSS 模板
```

几个刻意的取舍：

- **三层只读**：URI `mode=ro` + `PRAGMA query_only=ON` + 只暴露查询方法。测试逐条验证 8 种写语句都会失败。
- **整块取舍**：消息放不下时整条省略并注明「另有 N 条未显示」，不会切半句话；正文截断只退到句末标点。
- **冲突从库算**：不按角色名硬编码，作用域是「角色 = 技能 + 武器」「技能 = 技能」「武器 = 武器」。
- **查询走线程**：`asyncio.to_thread` + 连接锁，避免阻塞 AstrBot 事件循环。
- **图片只是多一条路径**：`QueryOutcome.text` 与旧的 `*_message()` 逐字相同，出图失败时用户看到的内容不变。
- **协议字段写死为 `tmpl`**：这是渲染服务的硬约定，测试里逐字断言，避免有人「顺手」改回 `html`。
- **不认识的服务就当坏图**：宁可降级成文字，也不把一张错误页当卡片发到群里。

## 路线图

第一阶段（`v0.1.0`，已冻结）：

- [x] 只读数据库接入
- [x] 角色 / 技能 / 武器查询
- [x] 来源与翻译状态标注
- [x] 数值冲突提示

完整查询阶段（`v0.2.0`）：

- [x] 意识与属性意识
- [x] 养成与操作
- [x] 角色评价
- [x] 配队推荐
- [x] 综合攻略

图片卡片阶段（`v0.3.0`）：

- [x] 11 条查询指令出图
- [x] 可配置 T2I 服务参数（11 项）
- [x] 整块分页，最多 4 张
- [x] 响应校验与文字降级
- [x] `/p5x渲染状态`、`/p5x测试渲染`
- [ ] 腾讯云 Docker 部署验收
- [ ] 容器内 T2I 渲染服务验收

后续：

- [ ] Docker / 腾讯云部署
- [ ] SnowLuma → OneBot → AstrBot 链路

## 许可与免责

本插件只做查询与排版，不修改数据。攻略内容版权归社区原作者，建议个人使用，不要重新公开发布整个数据集。
