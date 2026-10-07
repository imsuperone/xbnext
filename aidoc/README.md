# XBNEXT · AI 协作文档（aidoc）

> 项目：`astrbot_plugin_xbnext`（简称 **XBNEXT**）
> 仓库：https://github.com/imsuperone/xbnext
> 定位：解决 AstrBot 当前"认人差 / 引用误判 / 表情不识别"等实际问题的 AstrBot 插件。
> 本文档目录是**后续所有 AI 的第一入口**，新会话先读本文件，再按顺序读下游文档。

---

## 0. 新 AI 必读顺序（严格按序）

| 顺序 | 文档 | 作用 |
| :--: | :--- | :--- |
| 1 | `README.md`（本文件） | 项目定位 + 文档地图 + 当前进度 |
| 2 | `01-需求与根因.md` | 4 个核心需求的**根因分析与源码证据**，实现前必须理解"为什么" |
| 3 | `02-架构设计.md` | 插件分层、模块划分、钩子时序、数据流 |
| 4 | `03-开发规范.md` | 版本号规则、必跑自检、红线、编码约定 |
| 5 | `04-WebUI规范.md` | 前端仿制基准（xbdoc / xbimg / xbbot_beta） |
| 6 | `research/` 目录 | 调研证据库（AstrBot 核心源码、AstrNa、参考插件），**写代码前查这里** |

**优先级**：`03-开发规范.md` 红线 > 用户当次指令 > AI 自行判断。
红线明写"勿改动 / BY DESIGN"的地方，只确认、不修改。

---

## 1. 项目定位

AstrBot 目前存在若干长期问题（详见 `01-需求与根因.md`），XBNEXT 逐一给出插件级解决方案：

| # | 需求 | 一句话 | 状态 |
| :-: | :--- | :--- | :--- |
| R1 | 身份串台 | 用户 A 先问 bot，之后 bot 把 A 的话套到 B 头上 | ✅ 已实现 |
| R2 | 引用空白附件 | 引用回复时 bot 误以为有一张空白图片/附件，导致回复出错 | ✅ 已实现 |
| R3 | QQ 表情不识别 | QQ 自带 emoji（face/mface）发给 bot，bot 看不懂 | ✅ 已实现（**AstrNa 未做，属空白区**） |
| R4 | 认人差 → 用户档案 | 用户自定义对自己的设定，bot 读取档案库了解用户，而非自行臆测 | ✅ 已实现 |
| R5 | WebUI | 前端 UI 完全仿照 xbdoc / xbimg / xbbot_beta 的风格与工程结构 | ✅ 已实现 |

**不做**：xbdoc / xbimg / xbbot_beta 的原有功能（文档记忆、消息转图、游戏系统等）一概不要，
只保留它们的**UI 设计语言与工程骨架**，功能位填 XBNEXT 自己的。

---

## 2. 关键结论速查（根因一句话版）

实现时最容易跑偏的点，先背下来，详细论证见 `01-需求与根因.md`：

- **R1**：AstrBot 会话历史里 user 消息**不带发言人标记**，群聊上下文块也只按
  `[昵称/时间]` 平铺；bot 回复"回给谁"没有落库索引。解决思路 = ①每轮临时注入
  "当前发言人/被引用人"三方区分说明 ②给 bot 回复建立"回给谁"的 KV 索引。
  说明必须**分同人 / 异人两套文案**（同人引用自己时不能说"以上是不同的人"）。
  （AstrNa `reply_target_history.py` 已验证此路线，可借鉴但**不照抄**，见 R1 章节的差异点。）
- **R2**：不是"真的有空白图片"，而是 core 在引用链处理中**注入了占位符文本**
  （`[Image unavailable]` / `[Empty Text]` / `[Image]`），加上失败的图片路径仍留在
  `req.image_urls`，模型据此臆想出一张空白图。解决思路 = 在 `on_llm_request` 清洗
  无效引用占位 + 过滤死路径 + 无法恢复时**明确标注"引用的图片已失效"**而不是留空。
- **R3**：两层根因。① aiocqhttp 适配器把 `face` 段**排除在 `message_str` 之外**、
  `mface`（商城表情）**直接 `continue` 丢弃**，而 `req.prompt = event.message_str`；
  ② 于是纯表情消息 `message_str == ""`，core 的 `internal.py` 判
  `has_valid_message=False` 且表情不算媒体内容 ⇒ **`skip llm request:
  empty message`，LLM 压根不被调用**（`on_llm_request` 根本不执行）。
  解决思路 = 两条腿：正文非空时把表情翻译成 `[表情:得意]` 注入；
  正文为空时在 `event_message_type(ALL)` **早期钩子里补写 `message_str`**。
  （AstrNa 全仓 0 处表情处理，此处为 XBNEXT 独有能力。）
- **R4**：AstrBot 自带 `identifier` 只注入 `User ID/Nickname` 一行；没有"用户自我设定"
  的存储与读取链路。解决思路 = 插件内建**用户档案库**（KV/文件持久化），
  每轮按当前发言人注入其档案（临时内容，不写历史），并提供指令 + WebUI 双入口维护。
- **R5**：三个参考插件同一套 M3 Expressive 设计语言（双主题 + 强调色调色盘 +
  胶囊按钮 + 卡片），bridge 优先 + fetch 多前缀回退。骨架照 `xbimg`（3 文件轻量形态）起步。

---

## 3. 目录地图

```
astrbot_plugin_xbnext/
├─ aidoc/                     ← 本文档（不进发布包）
│  ├─ README.md               ← 你在这里
│  ├─ 01-需求与根因.md
│  ├─ 02-架构设计.md
│  ├─ 03-开发规范.md
│  ├─ 04-WebUI规范.md
│  ├─ tools/
│  │  └─ gen_face_table.py     ← 从上游仓库抓表情表（可重跑，保证 data.py 不是手写）
│  └─ research/
│     ├─ astrbot-core.md      ← AstrBot 核心源码证据（行号可追）
│     ├─ astrna.md            ← AstrNa 方案拆解
│     └─ reference-ui.md      ← 三参考插件 WebUI 结构
├─ main.py                    Star 入口薄壳（只转发钩子，不写业务）
├─ metadata.yaml              插件元数据 + **版本号唯一来源**
├─ _conf_schema.json          配置 schema
├─ README.md / CHANGELOG.md   版本号三处同步中的另外两处
├─ xbnext/                    业务包（分层详见 02-架构设计.md §1）
│  ├─ __init__.py             版本号 / PLUGIN_NAME / HOOK_PRIORITY
│  ├─ runtime.py              调度中心
│  ├─ context.py              RequestContext
│  ├─ injector.py             唯一注入出口
│  ├─ switches.py             开关对账 + WebUI 配置写入
│  ├─ storage.py              插件 KV 封装
│  ├─ config.py               配置读取（schema 默认值回退）
│  ├─ features/               ★ 功能模块 —— 一功能一目录，新增功能只改这里
│  │  ├─ base.py              Feature 基类 + 新增功能的 5 步说明
│  │  ├─ __init__.py          注册表 FEATURES
│  │  ├─ quote/               R2 引用占位清洗
│  │  ├─ face/                R3 QQ 表情翻译
│  │  ├─ attribution/         R1 回复指向索引
│  │  └─ profile/             R4 用户档案
│  └─ web/                    WebUI 后端 API
├─ pages/manager/             R5 WebUI（index.html / style.css / api.js / app.js）
└─ tests/                     单测（unittest，不依赖 astrbot）
```

> 新增功能的扩展步骤写在 `xbnext/features/base.py` 顶部注释里，
> 并有 `tests/test_structure.py` 机械校验兜底。

---

## 4. 进度看板

| 阶段 | 内容 | 状态 |
| :--- | :--- | :--- |
| P0 | 创建 aidoc、根因调研（AstrBot core / AstrNa / 参考 UI） | ✅ 完成 |
| P1 | 项目骨架：metadata.yaml / main.py / _conf_schema.json / `xbnext/` 分层 + 功能注册表 + 单测 | ✅ 完成（**未做真机回归**） |
| P2 | R3 表情翻译：补权威表情表 + 消息链接入 | ✅ 完成（**未做真机回归**） |
| P3 | R2 引用死路径过滤策略（`os.path.exists` 判定 + 正文联动降级） | ✅ 完成（**未做真机回归**） |
| P4 | R1 回复指向注入文案（落库 + 三方说明） | ✅ 完成（**未做真机回归**） |
| P5 | R4 档案指令入口 + 注入落地 | ✅ 完成（**未做真机回归**） |
| P6 | R5 WebUI 页面功能填充（骨架已就位） | ✅ 完成（**未做真机回归**） |
| P7 | 集成自检 + 文档同步 + 首次提交 | ✅ 完成（**未做真机回归**） |

> **P1 交付内容**：结构、注册表、配置 schema、WebUI 骨架、114 条单测全绿。
> 四个功能目前都只完成"存储 + 纯逻辑 + 接线"，**真正改写请求的注入文案
> 要到 P2~P5 才落地** —— 这是刻意的：先把地基和护栏（机械校验）铺好，
> 再往里填行为，避免后面边写边返工。

> **P2 交付内容**：`face/data.py` 换成**脚本生成的权威全量表**（594 条，
> 0~255 主表 + 256~9786 扩展 + unicode 码点段，QFace `_index.json` 补缺；
> 生成器 `aidoc/tools/gen_face_table.py` 可重跑，两源冲突已记录进 `CONFLICTS`）；
> 抽取链路接通"消息链 + OneBot 原始载荷"两条来源（`mface` 不进消息链，
> 只能从 `raw_message` 的 `summary` 拿中文名）；新增 `KIND_SUMMARY` 片段种类；
> 正文里已有的片段不重复注入；单测 **141 → 168**。
> **刻意不做**：aidoc/01 §3.3 第 4 步"翻译群上下文里的 `[Sticker: {id}]`" ——
> 那段文本在会话历史里，不经过 `req.prompt`，实现它就是死代码。

> **P3 交付内容**：死路径判定从"只标非 URL"升级为 **`os.path.exists` 实证**
> （`is_dead_image_url` 判定表写死在 docstring：http/https/data/未知 scheme
> 与非字符串一律保留，只删能证实不存在的本地路径与 `file://`）；新增
> `split_image_urls` 拆分保留/失效两组；正文侧新增 `degrade_bare` 开关 ——
> **只有确实发现死路径**才把裸 `[Image]` 降级成失效说明（否则保留，
> 因为它至少说明"这里曾有一张图"）；新增配置 `quote_drop_dead_images`（默认开）。
> 单测 **168 → 181**。

> **P4 交付内容**：`attribution/service.py`（纯逻辑）产出三方中文说明 ——
> 标题明说"这是给模型的提示，不是任何人的发言"，列出 ①当前发言人
> ②本轮被引用消息的发送者 ③本轮被 @ 的对象 ④bot 最近 N 次回复的对象
> （带"N 分钟前"相对时间），结尾给区分规则。落库目标定为**当前发言人**
> （bot 的回复就是在回应触发它的那条消息），并额外存触发消息的
> `message_id` + 文本 sha256，读取时若本轮引用命中该 id 就补一句
> "你当时回应过它（回应对象：X）"。**引用拿不到被引用者时返回空对、绝不猜**
> （QQ 官方 Bot 缺字段）。身份字段全部过 `sanitize`。单测 **181 → 209**。

> **P5 交付内容**：新增 `commands.py`（`split()` 纯函数，兜住 AstrBot 剥前缀的
> 三种形态 —— 完整形式 / 只剥根命令 / 根命令与子命令都剥掉；解析思路照抄本机
> xbdoc 真机跑过的实现）+ `profile/service.py`（指令语法白名单：字段只认
> 称呼/自述/口吻，其余一律报错；`merge()` 保证改一个字段不抹掉其它字段）。
> `Feature` 加可选 `command` 属性，`runtime.handle_command` 统一分发，
> `main.py` 只多一个静态 `@xbnext.command("profile")` 转发（AstrBot 类加载期
> 扫描，子命令必须静态写在那里）。**档案与开关解耦**：开关只决定喂不喂给模型，
> 查看/设置/清空始终可用（与 `_conf_schema.json` 的 hint 一致）。注入文案带抬头
> "这是用户自己填写的资料，不是他本轮说的话"。单测 **209 → 252**。
> **决策差异（P5 时的结论，已被真机反馈推翻）**：当时"档案页签"定为**不做**，
> 理由见 `04-WebUI规范.md §5`；真机首轮后按用户要求补做，见下方交付记录。

> **P6 交付内容**（早于 P2~P5 完成）：`pages/manager/` 四文件按母版重写
> （M3 token / `.app-layout` / `.top-bar` / `.category-tabs-bar` / `.m3-card` /
> `.m3-switch` / 取色盘 / `data-boot` 防闪 / `?v=` 缓存号）；后端补
> `POST /setting` 写配置（键白名单 + schema 转型 + `save_config*` 落盘 + 失败回滚），
> `GET /state` 一次性回填 `config` + `schema`；新增 `tests/test_web.py`，
> 单测 **114 → 141**。验证方法与实测数值见 `04-WebUI规范.md` §8。

> 排序理由（**实际执行时调整过**）：原计划 WebUI 放最后，但用户反馈
> "前端一点也不像之前的项目"，于是提前重做 —— 先把壳对齐母版，
> 后续功能页签只往里加内容，不再动结构。功能侧仍按 R3 → R2 → R1 → R4 推进。

> **P7 交付内容**：① 全量自检通过（`compileall` 0 / `unittest` **252 OK** /
> `node --check` 全过 / `_conf_schema.json` 合法）；② 补根 `__init__.py`；
> ③ 文档同步 —— `01` 新增「实现落点速查」、`02` 目录树与 §6、`03` §3 自检命令、
> `04` §5 决策记录、根 `README`（指令表 / 自检命令 / 致谢链接）与 `CHANGELOG`。
> **④ 「AstrNa 共存」整套移除（用户指令）**：删掉 `switches.detect_astrna()` /
> `switches.conflict_for()` / `runtime.conflict_for()` / `status().astrna` /
> `RequestContext.enabled()` 里的让路分支 / 状态面板的「AstrNa 共存」卡片 /
> `app.js` 对应渲染 / `state.astrna` 载荷字段，`_conf_schema.json` 的 hint 与
> `status_lines()` 的 AstrNa 行一并去掉；`test_main_smoke` 改为
> `assertNotIn("astrna", status)` 反向看护。**保留**：README 与 `04` §5 的
> 致谢行（AstrNa 仍是路线参考）、`aidoc/research/astrna.md`（调研证据）。
> 移除理由写在 `02-架构设计.md §6`。

> **真机首轮反馈修复（P8，未打 tag）**：用户在云端跑通后回报 4 个问题，全部修复 ——
> ① **「未加载」+ `/xbnext profile` 报"档案存储还没就绪"**：根因是
> `@filter.on_astrbot_loaded` 只在核心启动收尾广播一次，**启动之后才装上/热更新的
> 插件永远收不到**，于是 `runtime._loaded` 一直是 `False`、`ProfileFeature._store`
> 一直是 `None`。修法是把 `on_loaded()` 改成幂等 + 新增 `ensure_loaded()`，
> 并在 `handle_llm_request` / `handle_message_sent` / `handle_command` 与
> `state` / 档案三端点全部兜底调用（`xbnext/runtime.py`、`xbnext/web/__init__.py`）。
> ② **主题色要手动点取色器才生效**：`applyTheme()` 里 `if (CONFIG.ui_accent_color)`
> 的判断让默认态下换主题丢掉派生色；改成无条件 `applyAccent()`，并在
> `writeConfig()` 的**写入成功与回滚两条路径**都补 `applyUiPref(key)`，
> 保证"页面上看到的 = 实际存下来的"。同时 `ui_theme_mode` 默认值从 `light`
> 改为 `""`（= 跟随系统），与 xbdoc / xbimg 一致。
> ③ **右下角通知样式与母版不同**：补上 xbdoc 的整套 toast —— 类型图标、
> 1.2s 去重、按文本长度自适应时长、点击复制（沙箱降级 `execCommand`，再失败
> 选中文本）、上限 4 条、`aria-live`、窄屏居中；`.m3-toast` padding 与
> `.m3-toast-ic/-text/-copy` 一并对齐。
> ④ **用户档案要单独一个地方**：新增第 5 个页签 `tab-profile`（列表/编辑/新建/
> 两步确认删除）+ 后端 `GET profiles` / `POST profile_save` / `POST profile_delete`；
> 因为 AstrBot 插件 KV **没有按键遍历**，新增索引键 `xbnext:profile:index` 并在
> `ProfileStore` 里做增删与孤儿自愈。原"不做档案页签"的决策已在 `04` §5 改写。
> 单测 **252 → 273**。
>
> **真机第二轮反馈修复（P9，未打 tag）**：用户回报 3 个问题 + 顺带一个日志发现，
> 全部修复 ——
> ① **「表情是无效的」（纯表情完全不回复）**：根因比第一轮判断的更深一层 ——
> 不是"模型看不懂"，而是 **LLM 压根没被调用**。
> `core/.../internal.py` L183-199 用 `event.message_str` 判
> `has_valid_message`，为空且无 `Image/File/Record/Video`/`Reply` 就
> `skip llm request: empty message`；而 aiocqhttp 把 `face` 排除在
> `message_str` 外、`mface` 段 `continue` 丢弃、@首个到自己的 `At` 不进正文
> ⇒ 纯表情的 `message_str == ""` ⇒ **`on_llm_request` 钩子根本不跑**。
> 修法：在 core 判定**之前**加早期钩子
> `@filter.event_message_type(ALL, priority=HOOK_PRIORITY)` →
> `runtime.handle_adapter_message` → `FaceFeature.on_adapter_message`
> 把翻译补写回 `event.message_str`。守卫三重：正文为空 &&
> `is_at_or_wake_command` && 抽得到 token（没被 @ 的纯表情仍不开口）。
> ② **档案注入日志看不到、调试一开又乱**：`handle_llm_request` 改成
> **有动作才打一条 INFO 汇总**（`本轮 引用占位清洗·改写正文、用户档案·注入1段`），
> 没动作一行不打；纯表情补写另打 `纯表情补写正文：[表情:得意]`；
> 逐条 notes 仍只在 `debug_log` 落 DEBUG。
> ③ **「注入记得不要和 xbdoc 搞出冲突」**：从 core 源码查实优先级方向
> （`sort(key=lambda h: -priority)` ⇒ **数值越大越靠前**），
> 本插件 1000 > xbdoc 100/0 ⇒ 先清洗后注入；两者都 `append`、互不清除；
> `system_prompt` / `contexts` 本插件从不碰。新增
> `tests/test_coexist.py` 把这 4 条断言锁死。
> ④ **（日志发现）同人引用自己仍写「以上是不同的人」**：
> `service.build_hint` 原来对任何"有引用/有@/有历史"都套同一段 `TAIL`。
> 改为 `key_of()`（`id:` / `name:` 双命名空间）判同人 + 三档文案
> `TAIL_SAME` / `TAIL_SELF_QUOTED`（行尾挂 `（即当前发言人本人）`）/ `TAIL`；
> 身份未知一律回落 `TAIL`（保守）。
> 单测 **273 → 319**。
>
> **真机第三轮反馈修复（P10，未打 tag）**：① **新表情识别不到**（日志
> `[表情:496]`，496 = 阴晴圆缺）—— 根因是内置表靠发布时抓取，QQ 出新表情
> 就滞后。修法分两层：**发布层**给 `gen_face_table.py` 加源 C
> （`koishijs/QFace` 的 `_index.json`，537 条、数字 emojiId 到 507），
> 重生成 `data.py` **314 → 594 条**（496/507 均已收）；**运行层**新增
> `face_auto_update`（默认开）+ `face_update_time`（默认 `04:30`）——
> `face/updater.py` 纯函数层（`parse_index` / `merge_missing` /
> `next_run_delay` / overlay 原子换入）+ `FaceFeature.on_load` 后台循环
> （首拉 1 分钟后先试一次，之后每天定点拉"权威源 − 内置表"的缺口进
> 插件 KV `xbnext:face_overlay`，查表内置优先只补缺）。
> 红线：任务首动作必是 ≥60s sleep（测试永不触网）、网络只走
> `asyncio.to_thread(urllib)`、异常一律不上抛；WebUI「行为微调」页签
> 补自动更新开关与更新时间卡。② `stopped event propagation` 归责查证：
> xbnext 没有 `stop_event`，是指令回复 CommandResult STOP 的正常归责，
> 不改代码仅文档化。单测 **319 → 351**。
>
> **真机第四轮反馈修复（P11，未打 tag）**：用户两条指令 ——
> 「/xbnext 系列回复都乱，弄好之后推送」+「用户档案也分群，那个页面也弄好
> 分类，不然也会很乱」。两件事都做完：——
> ① **回复乱的 5 处病灶**：`_status_line` 的 `**关闭**` markdown 星号在 QQ
> 纯文本里原样显示 → 去掉；`service.describe` 帮助块用空格做列对齐（非等宽
> 字体必乱）→ 改成单行式「改一个字段：/xbnext profile 称呼 小明」+
> 「删除档案：/xbnext profile 清空」；`extra` 与帮助块之间补空行；
> `runtime.handle_command` 的 `{exc!r}` repr 兜底泄露 → 改友好文案
> （repr 只进日志）；设置回执不再重复"维护方式"帮助块（`describe(usage=False)`）。
> ② **裸 `/xbnext`**：AstrBot 新版 core 对指令组精确匹配会自己抛
> "参数不足+指令树"，旧版则会静默漏给 LLM —— 根节点 `xbnext()` 补菜单回复，
> 但只在子命令是空/help 时开口（`ROOT_HELP_WORDS`），子指令命中一律静默，
> 两条通路互不冲突。③ **档案按群分**：键改
> `profile:<platform>:<gid>:<uid>`（群聊）/ 保持老键形（私聊 + 升级前旧数据，
> 零迁移）；`_speaker` 返回 `(platform, gid, uid)`，取群号照抄本机 xbimg
> 已验证的多重兼容（`get_group_id` → `message_obj.group_id` → umo 群段，
> `None`/`0` 视为私聊）；读写**只精确读本 scope、不跨群回退**（回退会让
> 清空失效）；群里查看若本群无档案但存在旧数据，回执补一行"未分群旧档案"
> 迁移提示（`_legacy_hint`，只提示不回退）；回执抬头全部带范围标签
> `（群 123456）` / `（私聊）`。④ **WebUI 档案页分类**：索引记号扩成
> `platform|gid|uid`（旧 `platform|uid` 照常解析 → group=""），
> `list_all` 行带 `group`；前端 `pfRender` 按「平台 · 群」分桶渲染分组标题
> （私聊/未分群排最前、群号按数值升序），编辑器新增可改的"群号"输入
> （改群号 = 挪群，payload 带 `prev_group`，服务端写新键后删旧键不留重复行）。
> 单测 **351 → 379**。
>
> **真机第五轮反馈修复（P12，未打 tag）**：「/xbnext 还是很丑啊，你去看看
> 别的插件怎么弄的」。查证本机参考插件后发现关键差异 —— **xbdoc 与 xbimg
> 都是单指令** `@filter.command(...)` + handler 内自己分发并回**精美菜单**
> （标题 + `━━` 分隔线 + 分节 + 圆点 + emoji），而 xbnext 用的是
> `command_group`：裸 `/xbnext` 会被新版 core 抢答成「参数不足 + 指令树」
> 技术树、旧版会把打错的子指令漏给 LLM 乱答 —— 根节点补的菜单在新版上
> 根本轮不到执行，所以用户看着"还是丑"。改法照抄参考插件：
> ① `main.py` 换成单指令 `@filter.command("xbnext")`（从 core 源码
> `CommandFilter.filter` 核实：裸指令、带参、未知子指令全部命中 handler，
> 且 handler 只收 `event` 时多余 token 不会抛参数错误）；② 新增
> `runtime.dispatch(event)` 统一分发：help 词 → `commands.MENU` 菜单、
> `status/状态` → 状态行、`profile` → `handle_command`、其余 → 「❓ 未知
> 子指令」一句提示（xbdoc 文案同款，不再整份甩菜单也不再漏给 LLM）；
> ③ 菜单 `commands.MENU` 按 xbdoc 分节 + xbimg 分隔线排版
> （🧩 标题 / 📊 状态 / 👤 用户档案 / 💡 开关解耦提示）；
> ④ 原 `command_group` 根节点、`ROOT_HELP_WORDS`、`xbnext_status` /
> `xbnext_profile` 子命令 handler 全部删除（逻辑收敛到 `dispatch`）。
> 文档同步：aidoc/01/02/03 的指令形态说明改为单指令 + dispatch。
> 单测 **379 → 383**（dispatch 矩阵：裸/help/状态/档案往返/未知提示 +
> 菜单排版红线，smoke 的 command_group 断言改为单指令断言）。

---

## 5. 每次交付必须附带的自检结果

见 `03-开发规范.md` §自检。最低要求：

```powershell
python -X utf8 -m compileall -q main.py xbnext tests   # 无输出，退出码 0
python -X utf8 -m unittest discover -s tests           # 打印 OK
Get-ChildItem pages -Recurse -Filter *.js | %{ node --check $_ }   # 无输出
```

**AstrBot 本体在云端，本机跑不了完整真机回归** —— 交付说明里必须写明
**哪些已在真机验证、哪些还没有**（首轮真机已确认：页面打开 / 配置回填 /
改开关刷新保留 / bridge 通道；未确认：R1~R4 触发用例、档案三端点往返、
主题色默认值对比）。
