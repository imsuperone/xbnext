# XBNEXT · AstrBot 对话质量修正插件

> 版本：**v0.2.0**
> 仓库：https://github.com/imsuperone/xbnext

XBNEXT 解决 AstrBot 使用过程中的四个实际问题，并附带一个控制台 WebUI。

| # | 需求 | 一句话 |
|:-:|:---|:---|
| R1 | 身份串台 | A 先问 bot，之后 bot 把 A 的话套到 B 头上 |
| R2 | 引用空白附件 | 引用回复时 bot 误以为有一张空白图片，导致回复出错 |
| R3 | QQ 表情不识别 | QQ 自带 emoji 发给 bot，机器人看不懂 |
| R4 | 用户档案 | 用户自定义对自己的设定，bot 读取档案库理解用户而非乱猜 |
| R5 | WebUI | 仿 xbdoc / xbimg / xbbot_beta 风格的控制台 |

## 功能开关

| 配置键 | 功能 | 默认 |
|:---|:---|:---:|
| `enable_quote_clean` | 引用占位清洗（R2） | ✅ 开 |
| `enable_face_translate` | QQ 表情翻译（R3） | ✅ 开 |
| `face_auto_update` | R3 表情表自动更新（每天从权威源补缺，需 `enable_face_translate` 开） | ✅ 开 |
| `face_update_time` | 表情表每天更新时间（`HH:MM`，默认 `04:30`；首次启动约 1 分钟后先拉一次） | `04:30` |
| `enable_reply_attribution` | 回复指向索引（R1） | ❌ 关 |
| `enable_user_profile` | 用户档案注入（R4） | ❌ 关 |
| `enable_recall_cancel` | 撤回取消请求（撤回触发消息 → 掐掉在飞 LLM 请求 + 连带撤回已发回复） | ✅ 开 |
| `enable_image_slim` | 历史图片瘦身（R8，历史旧图换占位，省输入 token） | ❌ 关 |
| `enable_token_usage` | Token 用量展示（R9，回复末尾显示 输入/输出/缓存） | ❌ 关 |
| `token_usage_umos` | 显示 token 用量的会话名单（逗号分隔 UMO，需 `enable_token_usage` 开；留空 = 不显示） | 空 |

> 纯修正类（R2/R3）默认开，因为它们修的是明确 bug，不开启反而留着 bug；
> 新增行为类（R1/R4/R8/R9）默认关，由用户自行决定是否开启。

## 目录结构

```
astrbot_plugin_xbnext/
├─ main.py                 Star 入口薄壳（只转发钩子，不写业务）
├─ metadata.yaml           插件元数据（含 pages 声明）
├─ _conf_schema.json       配置 schema
├─ xbnext/
│  ├─ runtime.py           调度中心：按固定顺序调用功能
│  ├─ context.py           RequestContext（单次请求上下文）
│  ├─ injector.py          唯一注入出口（temp part + sanitize）
│  ├─ inject_log.py        注入记录（KV 环形 10 轮，WebUI 运行状态入口读）
│  ├─ switches.py          开关对账 + WebUI 配置写入
│  ├─ storage.py           插件 KV 封装
│  ├─ config.py            配置读取（schema 默认值回退）
│  ├─ commands.py          指令文本解析（纯函数，兜住 AstrBot 剥前缀的多种形态）
│  ├─ features/            ★ 功能模块 —— 一功能一目录
│  │  ├─ quote/            R2 引用占位清洗（service.py 纯逻辑）
│  │  ├─ face/             R3 QQ 表情翻译（service + data.py 权威表情表 + updater.py 自动更新）
│  │  ├─ attribution/      R1 回复指向索引（service.py 注入文案 + store.py 索引）
│  │  ├─ profile/          R4 用户档案（service.py 指令语法 + store.py 存储）
│  │  └─ recall/           R6 撤回取消请求（早期钩子登记在飞任务，撤回即掐）
│  └─ web/                 WebUI 后端 API
├─ pages/manager/          WebUI 前端
├─ tests/                  单测（不依赖 astrbot）
└─ aidoc/                  AI 协作文档（含 tools/，**不进发布包，必须进 git**）
```

**新增功能**：见 `xbnext/features/base.py` 的注释，5 步完成；要暴露子指令再加 2 行。

## 指令

| 指令 | 作用 |
|:---|:---|
| `/xbnext` | 查看指令菜单（`help` / `菜单` 同效） |
| `/xbnext status` | 查看各功能开关与 KV 可用性 |
| `/xbnext profile` | 查看自己的档案 |
| `/xbnext profile 称呼 小明` | 改称呼（别名：名字 / 昵称；`称呼:小明`、`称呼=小明` 也认） |
| `/xbnext profile 自述 <内容>` | 改自述（别名：信息 / 描述） |
| `/xbnext profile 称呼 小明 自述 学生` | 一次改多条（分隔符可混写） |
| `/xbnext profile 称呼:` | 删掉单个字段（字段名后跟分隔符且留空 = 删除该字段） |
| `/xbnext profile 清空` | 删除自己的全部档案 |

> 档案与「用户档案」开关**解耦**：开关只决定喂不喂给模型，
> 查看 / 设置 / 清空始终可用。字段只认上面三个，其余一律报错。
> 分隔符（空格 / `:` / `：` / `=`）随便混；裸字段名没写值会报错并给示例。
> 档案**按群隔离**：同一个人在不同群各存一份、私聊单独一份，
> 回执抬头会标出当前范围（`群 123456` / `私聊`）。

## 控制台（WebUI）

5 个页签：**功能开关** / **行为微调** / **用户档案** / **运行状态** / **使用指南**。

- 「用户档案」页签按「平台 · 群」**分组分类**列出全部档案，支持新建、编辑、
  删除（两步确认），与 `/xbnext profile` 指令写的是**同一份数据** —— 群里自助
  维护、控制台统一管理；编辑时可改「群号」把档案挪到另一个群（旧范围自动清掉，
  不留重复行），群号留空 = 私聊 / 未分群。
- 深浅色**默认跟随系统**（`ui_theme_mode` 留空 = `prefers-color-scheme`），
  右上角可手动切换；主题色由取色盘维护，两者都存服务端配置键
  （iframe 沙箱里 `localStorage` 不可用）。
- 右下角通知与 xbdoc / xbimg / xbbot_beta 同款：类型图标、按长度自适应时长、
  点击复制。

## 使用提示

- **纯表情消息现在也能触发回复**：aiocqhttp 把 `face` 排除在 `message_str` 外、
  `mface` 段直接丢弃，只发表情时 `message_str` 是空的，AstrBot core 会
  `skip llm request: empty message`（**LLM 根本不被调用**）。本插件在
  `event_message_type(ALL)` 早期钩子里把表情翻译补写回 `message_str`，
  所以 `@机器人 + 一个表情` 能正常回复。
  **没被 @ / 没命中唤醒前缀的纯表情仍然不会让 bot 开口** —— 这是正常行为。
- **日志怎么看**：每轮只在"真有动作"时打一条 INFO 汇总，例如
  `[XBNEXT] 本轮 引用占位清洗·改写正文、用户档案·注入1段`；
  纯表情补写会另打一行 `[XBNEXT] 纯表情补写正文：[表情:得意]`。
  需要逐条明细（跳过原因、注入字数）时到「行为微调」打开**调试日志**，
  DEBUG 里会出现 `[XBNEXT] …` 开头的行。
- **与 xbdoc / xbimg 同开**：三者注入通道互相独立（都是 `append`），
  本插件 `priority=1000` 排在最前 —— 先清洗、后注入，
  xbdoc 的 `【参考资料】` 不会被改写，`req.system_prompt` / `req.contexts`
  本插件**从不碰**。

## 开发

```powershell
# 单测（不依赖 AstrBot 本体；本机未装 pytest，统一用标准库 unittest）
python -X utf8 -m unittest discover -s tests
# 语法检查
python -X utf8 -m compileall -q main.py xbnext tests
# 前端
Get-ChildItem pages -Recurse -Filter *.js | %{ node --check $_ }
```

详细规范见 `aidoc/`（**新会话请先读 `aidoc/README.md`**）。

> 发布包排除 `aidoc/ tests/ .git/ __pycache__/ *.pyc`，但 `aidoc/` 与 `tests/`
> **必须进 git**。

## 致谢与借鉴

本插件的设计与实现借鉴了以下项目，在此致谢：

- [AstrBot](https://github.com/AstrBotDevs/AstrBot) —— 宿主框架与官方插件规范
- [astrbot_plugin_AstrNa](https://github.com/Sisyphbaous-DT-Project/astrbot_plugin_AstrNa)
  —— 回复指向索引（reply target history）的路线参考（v1.6.8）
- 本机参考插件 [xbdoc](https://github.com/imsuperone/xbdoc) /
  [xbimg](https://github.com/imsuperone/xbimg) /
  [xbbot_beta](https://github.com/imsuperone/xbtest) —— WebUI 设计语言与工程骨架
  （`pages/manager/` 的页面骨架取自 xbdoc / xbimg，bridge 四级探测样例取自
  xbbot_beta；**只借 UI 壳，三者的原有功能一个都不带**）
- 本机 [xbbot 正式版](https://github.com/imsuperone/xb) —— 插件元数据与发布流程

## 许可

见仓库 LICENSE。
