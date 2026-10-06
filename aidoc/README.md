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
| R5 | WebUI | 前端 UI 完全仿照 xbdoc / xbimg / xbbot_beta 的风格与工程结构 | 基准已调研，待实现 |

**不做**：xbdoc / xbimg / xbbot_beta 的原有功能（文档记忆、消息转图、游戏系统等）一概不要，
只保留它们的**UI 设计语言与工程骨架**，功能位填 XBNEXT 自己的。

---

## 2. 关键结论速查（根因一句话版）

实现时最容易跑偏的点，先背下来，详细论证见 `01-需求与根因.md`：

- **R1**：AstrBot 会话历史里 user 消息**不带发言人标记**，群聊上下文块也只按
  `[昵称/时间]` 平铺；bot 回复"回给谁"没有落库索引。解决思路 = ①每轮临时注入
  "当前发言人/被引用人"三方区分说明 ②给 bot 回复建立"回给谁"的 KV 索引。
  （AstrNa `reply_target_history.py` 已验证此路线，可借鉴但**不照抄**，见 R1 章节的差异点。）
- **R2**：不是"真的有空白图片"，而是 core 在引用链处理中**注入了占位符文本**
  （`[Image unavailable]` / `[Empty Text]` / `[Image]`），加上失败的图片路径仍留在
  `req.image_urls`，模型据此臆想出一张空白图。解决思路 = 在 `on_llm_request` 清洗
  无效引用占位 + 过滤死路径 + 无法恢复时**明确标注"引用的图片已失效"**而不是留空。
- **R3**：aiocqhttp 适配器把 `face` 段**排除在 `message_str` 之外**、`mface`（商城表情）
  **直接 `continue` 丢弃**，而 `req.prompt = event.message_str` —— 所以模型根本收不到
  表情。解决思路 = 拦截消息链，把 Face/mface 翻译成 `[表情:得意]` 类文本描述再进 prompt。
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
│  ├─ switches.py             开关对账 + AstrNa 让路
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
| P7 | 集成自检 + 首次提交 | ⬜ 待开始 |

> **P1 交付内容**：结构、注册表、配置 schema、WebUI 骨架、114 条单测全绿。
> 四个功能目前都只完成"存储 + 纯逻辑 + 接线"，**真正改写请求的注入文案
> 要到 P2~P5 才落地** —— 这是刻意的：先把地基和护栏（机械校验）铺好，
> 再往里填行为，避免后面边写边返工。

> **P2 交付内容**：`face/data.py` 换成**脚本生成的权威全量表**（314 条，
> 0~255 主表 + 256~9786 扩展 + unicode 码点段；生成器
> `aidoc/tools/gen_face_table.py` 可重跑，两源冲突已记录进 `CONFLICTS`）；
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
> **决策差异**：原计划的"档案页签"**不做**，理由见 `04-WebUI规范.md §5`。

> **P6 交付内容**（早于 P2~P5 完成）：`pages/manager/` 四文件按母版重写
> （M3 token / `.app-layout` / `.top-bar` / `.category-tabs-bar` / `.m3-card` /
> `.m3-switch` / 取色盘 / `data-boot` 防闪 / `?v=` 缓存号）；后端补
> `POST /setting` 写配置（键白名单 + schema 转型 + `save_config*` 落盘 + 失败回滚），
> `GET /state` 一次性回填 `config` + `schema`；新增 `tests/test_web.py`，
> 单测 **114 → 141**。验证方法与实测数值见 `04-WebUI规范.md` §8。

> 排序理由（**实际执行时调整过**）：原计划 WebUI 放最后，但用户反馈
> "前端一点也不像之前的项目"，于是提前重做 —— 先把壳对齐母版，
> 后续功能页签只往里加内容，不再动结构。功能侧仍按 R3 → R2 → R1 → R4 推进。

---

## 5. 每次交付必须附带的自检结果

见 `03-开发规范.md` §自检。最低要求：

```powershell
python -X utf8 -m compileall -q main.py xbnext tests   # 无输出，退出码 0
python -X utf8 -m unittest discover -s tests           # 打印 OK
Get-ChildItem pages -Recurse -Filter *.js | %{ node --check $_ }   # 无输出
```

**AstrBot 本体在云端，本机无法真机回归** —— 交付说明里必须写明"未做真机回归"。
