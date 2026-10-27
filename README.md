# 夜幕档案

`astrbot_plugin_phantom_archive` 是面向 AstrBot 的《女神异闻录：夜幕魅影》中文资料查询插件。

> **当前为图片卡片阶段**：角色、技能、武器、意识、养成、操作、评价、配队与综合攻略均已开放，
> 查询成功时把结果渲染成带发起者头像与角色立绘的图片卡片发送，渲染不可用时自动降级为文字。
> `v0.1.0` 是第一阶段基线，`v0.2.0` 是完整查询阶段，`v0.3.0` 是图片卡片阶段，当前开发版本为 `v0.4.0`。

## 验收状态

```text
AstrBot 4.29.3 本地真实进程：已验证
本机 T2I 渲染服务（真实 HTTP + Chrome 截图）：已验证
发起者头像下载 → 转 Data URI → 写入卡片：已验证（本机头像服务替身）
角色立绘 / 武器图：链路已通，素材覆盖率为 0%（见「素材覆盖率」）
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
/p5x角色 <名称>      角色基本资料 + 技能加点推荐 + 技能概要 + 武器概要
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
→ image_presenter 转展示模型并分页 → 读发起用户信息 → 解析角色/武器素材
→ visual_context 合成 VisualContext → image_templates 生成 HTML
→ image_renderer 调 T2I 并校验 → main.py 决定发图 / 发文字 / 报错
```

职责边界固定：查询层不认识 HTML，模板层不认识 HTTP，渲染层不认识数据库。
文字与图片共用同一份排版块，所以降级后看到的内容与图片一致。

### 分页

- 一页约 28 行（820px 宽、正文 13.5–15px）。
- **整块切分**：放不下的条目整条挪到下一页，绝不从句子中间切开。
- 最多 4 张；超出时在最后一张显示「另有 N 项未展示」。
- 页码 `2/4` 只在多页时显示；来源、数值冲突警告、免责声明**只在最后一张**。
- 多页时的内容优先级：头像与主视觉 → 基本资料 → 技能加点推荐 → 技能概要 → 武器概要 → 来源与免责。
  免责声明永远不会为了塞下第一页而被截断。

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

「卡片视觉」配置组（`v0.4.0`）：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `show_requester_header` | `true` | 表头显示发起者头像与昵称 |
| `show_character_art` | `true` | 角色类查询使用角色立绘 |
| `show_weapon_art` | `true` | 武器类查询使用武器图与条目缩略图 |
| `asset_manifest_path` | 空 | 留空用插件 `data/assets/manifest.json` |
| `card_background_opacity` | `0.24` | 背景图不透明度（夹到 0–0.6） |
| `card_hero_opacity` | `0.92` | 主视觉不透明度（夹到 0.2–1.0） |
| `card_background_blur` | `8` | 背景模糊半径（夹到 0–40） |
| `avatar_cache_hours` | `24` | 头像缓存有效期，`0` = 每次都重新获取 |
| `avatar_request_timeout` | `5` | 头像下载超时（秒，夹到 1–30） |
| `qq_avatar_url_template` | 空 | 必须含 `{user_id}`；留空只用首字符头像 |

非法值不会让插件启动失败：一律回落默认值并在日志里记一条（不带令牌、不带头像地址）。

### 卡片视觉

三层结构，全部由 `VisualContext` 一份数据驱动：

```text
.hero-bg        同一张素材放大铺满 + 模糊 + 低不透明度（背景）
.hero-scrim     上下渐变遮罩；浅色主题的遮罩更实，保证文字可读
.hero-figure    右侧主视觉，宽 42%（上限 344px），底部渐隐、左侧渐隐
.content        半透明面板，正文压在最上层
```

- **表头**（`.requester`）：52px 圆形头像 + 昵称（最多 20 字符，超出省略）+ 「正在查阅 · 夜幕档案」。
- **主视觉**：角色类用角色立绘；武器类用**当前页第一把**武器的图，并在每个武器条目左侧加 62×62 缩略图；
  角色列表用通用档案背景图。多页时每页都保留表头与主视觉。
- **图片一律是 Data URI**（`data:image/webp;base64,...`）。HTML 里不出现任何远程图片地址 ——
  T2I 容器常常没有外网，而且远程地址会把用户头像 URL（可能带签名）泄露给渲染服务。
- 昵称走 Jinja2 `autoescape`，`<script>` 之类只会显示成文本；`/p5x测试渲染` 会报告转义是否正常。

### 发起者头像

```text
事件 → identity.from_event() → RequesterIdentity(user_id, display_name, "")
→ avatar_provider.resolve() → 下载 → 校验 → 128px WebP → Data URI
```

| 环节 | 规则 |
| --- | --- |
| 地址来源 | 平台事件自带的头像字段优先，其次 `qq_avatar_url_template`（必须含 `{user_id}`） |
| 下载 | `httpx` + `trust_env=False`，`Content-Length` 与实际读取双重 1 MiB 限制 |
| 校验 | 文件头必须是 PNG / JPEG / WebP，再用 Pillow 完整解码；拒绝 HTML、JSON、SVG |
| 归一化 | 居中裁成正方形 → 128×128 → WebP（约 1.5 KB） |
| 缓存 | `data/image_cache/avatars/<sha1>.webp`，默认 24 小时 |
| 回退 | 任何一步失败都退回**昵称首字符头像**（渐变底 + 首字），查询照常返回 |
| 日志 | 只打 `sha1(user_id)[:8]`，**不打印头像 URL** |

AstrBot 4.29.3 的 `MessageMember` 只有 `user_id` / `nickname`，没有头像字段，
所以默认表现是首字符头像；想用真实头像就在 `qq_avatar_url_template` 里填一个带 `{user_id}` 的地址。

### 素材与覆盖率

素材与数据库**完全分离**：不改 `p5x_zh.sqlite3` 结构，不热链，缺图不影响启动。

```text
data/assets/
├─ manifest.json                     清单（由脚本生成，可手工补 source_url）
├─ characters/<slug>/portrait.webp   角色立绘
├─ characters/<slug>/background.webp 角色背景（可缺）
├─ weapons/<owner>/<weapon_key>.webp 武器图
└─ fallback/{character,weapon,archive}.webp   三张自产占位图
```

匹配只按标识符，不做模糊猜测：角色 `slug → source_key`，武器 `角色:武器 → 武器`。
清单里每项都可记 `source_url` 与 `source_status`；**没登记状态的素材一律记为 `unavailable`，
绝不默认成 `official`**。

回退顺序：

```text
角色主视觉：角色立绘 → 角色占位图
武器主视觉：武器图 → 角色立绘 → 武器占位图
条目缩略图：武器图 → 武器占位图
角色列表：  通用档案占位图
清单不存在：纯色卡片（不报错、不降级文字）
```

**素材覆盖率（真实数字，`scripts/verify_assets.py` 实测）：**

```text
角色立绘覆盖率：0/72  = 0.0%
武器图片覆盖率：0/142 = 0.0%
```

仓库里没有任何第三方图片素材 —— 立绘与武器图的版权不属于本插件。
当前卡片右侧显示的是**自产占位图**（角色剪影 / 武器剑影 / 档案叠卡），
用来验证排版与回退链路，不代表已有角色立绘。
补齐方式：把图片放进上表路径，在清单里登记 `source_url` 与 `source_status`，
再跑 `python scripts/verify_assets.py` 复核。

### 技能加点推荐

`/p5x角色` 的图片里新增「■ 技能加点推荐」，位置在**角色简介之后、技能概要之前**，
第一页必须出现完整推荐（必要时把部分武器概要挪到后页）。

```text
■ 技能加点推荐
优先级：技能1·琴奏之调 ＞ 技能3·焰华·绯月昂扬 ＞ 战斗技巧 ＞ 技能2·月鸣之炎
推荐顺序：琴奏之调 → 焰华·绯月昂扬 → 月鸣之炎
说明：加点目标：技能1 琴奏之调 MAX!｜技能2 月鸣之炎 MAX｜技能3 焰华·绯月昂扬 MAX｜战斗技巧 MAX
```

数据来源**只有一条**：`zh_guides` 里该角色 `guide_type='setting'` 的 `skill_priority` 字段
（形如 `3>1>4>2`，数字是技能槽位）。槽位按数据本身解释，不靠猜：

| 槽位 | 标签 | 依据 |
| --- | --- | --- |
| 1 / 2 / 3 | `技能N·中文技能名` | `zh_skills` 里存在 `skillN` |
| 4 | `战斗技巧` | 69 条养成数据的 `skill_levels` 全是 `skill1~skill4` 四槽，第 4 槽客观存在但不在 `zh_skills` |
| 4（有 `skill4` 行时） | `技能4·中文技能名` | 例如 J&C |
| 其它编号 | `技能N` + 「待核对」 | 不编造名字 |

明确**不做**的事：不按伤害/倍率反推、不拿 `zh_skills` 的排列顺序当推荐、没有数据就留空。
拿不到可靠数据时**必须显示**「暂未收录可靠的技能加点顺序」，而不是隐藏整个板块 ——
隐藏会让人以为漏了。

全量统计（`python scripts/verify_recommendation.py`）：

```text
角色总数    72 名
有推荐      68 名
无推荐      4 名（显示「暂未收录可靠的技能加点顺序」）
待人工核对  0 名

无推荐名单：坂本龙司·星舞、荒垣真次郎、天田乾、主角
```

前 3 名没有 `setting` 攻略行；「主角」有 `setting` 行但 `skill_priority` 与 `skill_levels` 都是 `null`。
这 4 名都如实显示「暂未收录」，没有用别的角色数据顶替。

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
├─ base.html            卡片骨架（配色变量、表头、主视觉层、块样式、页脚）
├─ character.html       角色档案（红）
├─ skill.html           技能档案（蓝）
├─ weapon.html          武器档案（金）
├─ guide.html           攻略类共用（紫）
└─ character_list.html  角色名册（绿）
```

- 纯 HTML + CSS，**没有外链字体、CDN、JavaScript** —— 渲染服务可能没有外网。
- 宽 820px，落在 760–900px 区间内。
- 图片只接受 `data:image/...#base64`，由受控编码器产生；模板不拼接任何路径或 URL。
- Jinja2 `autoescape` 全开 + `StrictUndefined`：所有用户数据（含昵称）自动转义，模板少字段直接报错而不是渲染空白。
- 明暗两套主题都支持背景图；浅色主题的遮罩更实、立绘整体再压一层，避免深色立绘吃掉正文。
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
   ├─ display_notices.json  ← 可选，仅用于文案一致性自检
   ├─ assets/               ← 可选，缺省时卡片使用占位图
   │  ├─ manifest.json
   │  ├─ characters/…  weapons/…  fallback/…
   └─ image_cache/avatars/  ← 插件自动创建，头像缓存
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

### 3. 素材脚本

```bash
# 生成三张占位图（仓库已带，改版式时才需要重跑）
python scripts/make_placeholders.py

# 扫描素材目录，生成 manifest.json 与 coverage.json
python scripts/build_asset_manifest.py

# 校验清单与图片，并打印真实覆盖率
python scripts/verify_assets.py

# 统计技能加点推荐覆盖率
python scripts/verify_recommendation.py
```

四个脚本都只读数据库（`mode=ro`），不会修改 `p5x_zh.sqlite3`。

### 4. 配置项

`_conf_schema.json` 共 23 项：2 项数据与文字、11 项图片输出、10 项卡片视觉
（见「图片卡片 · 配置项」）。

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
  "image_theme": { "type": "string", "default": "dark" },
  "show_requester_header": { "type": "bool", "default": true },
  "show_character_art": { "type": "bool", "default": true },
  "show_weapon_art": { "type": "bool", "default": true },
  "asset_manifest_path": { "type": "string", "default": "" },
  "card_background_opacity": { "type": "float", "default": 0.24 },
  "card_hero_opacity": { "type": "float", "default": 0.92 },
  "card_background_blur": { "type": "int", "default": 8 },
  "avatar_cache_hours": { "type": "int", "default": 24 },
  "avatar_request_timeout": { "type": "int", "default": 5 },
  "qq_avatar_url_template": { "type": "string", "default": "" }
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
| `test_plugin_image.py` | 11 条指令出图、表头/立绘/缩略图、三类错误降级、两种失败模式、管理员指令 |
| `test_identity.py` | 昵称截断（20 字符）、缺方法/抛异常回退、平台自带头像 |
| `test_image_codec.py` | 格式嗅探、体积上限、等比缩放、缓存失效、首字符头像 |
| `test_avatar_provider.py` | 下载→归一化、缓存命中与过期、404/超大/HTML 回退、日志不含 URL |
| `test_asset_manifest.py` | 路径越界拦截、来源状态白名单、缺文件不报错、覆盖率统计 |
| `test_asset_resolver.py` | 立绘/武器逐级回退、缩略图、开关生效、坏清单不抛异常 |
| `test_visual_context.py` | 表头开关、主视觉来源、缩略图收集、配置夹取、坏解析器不连累卡片 |
| `test_recommendation.py` | 槽位映射、分隔符、缺失必显示「暂未收录」、待核对标记、真实数据 |

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

`v0.4.0` 视觉升级 + 技能加点推荐实测：

```text
pytest                  409 passed
真实进程 + 本机渲染     13 条指令 13/13 通过（11 条出图，2 条文字）
                       角色 1 张 / 技能 1 张 / 武器 1 张 / 角色列表 3 张
                       操作 2 张 / 攻略 4 张，合计 17 张
单张卡片                820px 宽，127～238 KB，渲染约 1.0 s
视觉合成                首次 53 ms（占位图转码），命中缓存后 <1 ms
头像链路                下载 → 128px WebP → Data URI → 缓存命中（156 B）
日志                    0 ERROR，日志里不出现头像地址
素材覆盖率              角色立绘 0/72、武器图片 0/142（全部走占位图）
技能加点推荐            68 有 / 4 无 / 0 待核对（72 名角色全量）
数据库 SHA-256          查询前后一致（c739a23e943c8ad7…）
```

「不同用户头像与昵称分别正确」这一条由 `test_plugin_image.py` 的
`test_different_users_get_different_header` 覆盖 —— 真实进程里 WebUI 只有一个登录用户，
无法在同一进程内换人，因此没有把它写成「真实链路已验证」。

## 实现说明

```text
main.py                 AstrBot 指令注册、配置读取、头像/素材装配、发图/降级决策
archive/
├─ database.py          只读连接（mode=ro + PRAGMA query_only）、路径解析、结构校验
├─ repository.py        参数化 SQL
├─ search.py            分级检索
├─ renderer.py          带预算的文字排版
├─ notices.py           提示文案唯一出处
├─ models.py            行模型（含 SkillUpgradeRecommendation）
├─ service.py           查询门面，返回 QueryOutcome（text + pages）
├─ recommendation.py    技能加点解析（只读 setting 的结构化字段）
├─ cards.py             排版块分类与整块分页（含主视觉标识）
├─ image_presenter.py   查询结果 → 卡片展示模型
├─ identity.py          从事件读发起者（ID + 昵称）
├─ avatar_provider.py   头像下载、校验、缓存、回退
├─ image_codec.py       Data URI 编解码 + 首字符头像
├─ asset_manifest.py    素材清单解析（路径安全、来源状态白名单）
├─ asset_resolver.py    素材匹配与逐级回退
├─ visual_context.py    页面 + 素材 + 用户 → 模板视觉上下文
├─ image_templates.py   模板读取与渲染上下文
└─ image_renderer.py    T2I HTTP 客户端与响应校验
templates/              6 个纯 HTML+CSS 模板
scripts/                占位图生成、清单构建、素材校验、推荐覆盖率统计
```

几个刻意的取舍：

- **三层只读**：URI `mode=ro` + `PRAGMA query_only=ON` + 只暴露查询方法。测试逐条验证 8 种写语句都会失败。
- **整块取舍**：消息放不下时整条省略并注明「另有 N 条未显示」，不会切半句话；正文截断只退到句末标点。
- **冲突从库算**：不按角色名硬编码，作用域是「角色 = 技能 + 武器」「技能 = 技能」「武器 = 武器」。
- **查询走线程**：`asyncio.to_thread` + 连接锁，避免阻塞 AstrBot 事件循环。
- **图片只是多一条路径**：`QueryOutcome.text` 与旧的 `*_message()` 逐字相同，出图失败时用户看到的内容不变。
- **协议字段写死为 `tmpl`**：这是渲染服务的硬约定，测试里逐字断言，避免有人「顺手」改回 `html`。
- **不认识的服务就当坏图**：宁可降级成文字，也不把一张错误页当卡片发到群里。
- **图片必须自包含**：HTML 里只有 `data:image/...#base64`，T2I 有没有外网都能出图。
- **一张图缺失只影响一张图**：素材逐级回退到占位图，绝不会因为缺立绘把整页降级成文字。
- **加点推荐不猜**：只读 `setting.skill_priority`；没有就写「暂未收录」，不用技能表顺序或伤害数值凑。
- **素材来源照实标**：清单里没登记 `source_status` 的一律算 `unavailable`，社区素材永远不会被标成官方。

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

视觉升级阶段（`v0.4.0`）：

- [x] 表头显示发起者头像与昵称（首字符头像兜底，缓存 24 小时）
- [x] 角色立绘 / 武器图作为主视觉，武器条目带缩略图
- [x] 背景 = 同一张素材模糊放大 + 深色渐变 + 遮罩，明暗主题都支持
- [x] 独立素材清单与三张自产占位图，缺图逐级回退
- [x] 角色卡新增「技能加点推荐」（68 有 / 4 无 / 0 待核对）
- [x] 10 项卡片视觉配置
- [ ] 真实角色立绘与武器图（需先解决素材来源与授权，当前覆盖率 0%）

后续：

- [ ] Docker / 腾讯云部署
- [ ] SnowLuma → OneBot → AstrBot 链路
- [ ] 素材来源登记与授权确认

## 许可与免责

本插件只做查询与排版，不修改数据。攻略内容版权归社区原作者，建议个人使用，不要重新公开发布整个数据集。
