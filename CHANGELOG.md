# Changelog

本项目遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## v0.1.0

首个版本：R1~R5 全部落地，控制台 WebUI 就绪。

### Added

- **R3 · QQ 表情翻译**（`enable_face_translate`，默认开）：消息链与 `raw_message`
  双来源抽 token，支持 JSON list / 单 dict / JSON 字符串 / CQ 码 / 嵌套 `message` 字段，
  `mface` 走段自带 `summary`，正文已有片段不重复注入；
  `face/data.py` 由 `aidoc/tools/gen_face_table.py` 从上游抓取生成的 **314 条权威表**
  （**禁止手改**）
- **R2 · 引用占位清洗**（`enable_quote_clean`，默认开）：只删**可证实不存在**的
  本地路径与 `file://`（`quote_drop_dead_images`，默认开），未知 scheme / data URI
  一律保留；判定函数抛异常时保守保留，不做猜测式删除
- **R1 · 回复指向**（`enable_reply_attribution`，默认关）：`on_message_sent` 落库
  （目标 = 当前发言人，另存触发 `message_id` + 文本 sha256），`on_llm_request` 注入
  三方中文说明（当前发言人 / 被引用者 / 被 @ 对象 / bot 最近 N 次回复对象），
  身份字段全部过 `sanitize`
- **R4 · 用户档案**（`enable_user_profile`，默认关）：`/xbnext profile` 指令
  （查看 / 设置 / 清空，字段白名单 + `merge` 保不抹其它字段），
  `on_llm_request` 按当前发言人注入；**档案与开关解耦**，开关只决定喂不喂给模型。
  另有 WebUI「用户档案」页签（管理视角）：`GET /profiles` 列出全部、
  `POST /profile_save` 写（三字段整体替换，全空即删）、`POST /profile_delete` 删，
  列表能力靠自建索引键 `xbnext:profile:index` 维护（AstrBot 插件 KV 没有按键遍历）
- **R5 · WebUI**（`pages/manager/`）：M3 Expressive 令牌、`.app-layout` /
  `.top-bar` / `.category-tabs-bar` / `.m3-card` / `.m3-switch`、取色盘、
  `data-boot` 防闪、`?v=` 缓存号；**5 个页签**（功能开关 / 行为微调 / 用户档案 /
  运行状态 / 使用指南）；后端 `GET /state` 一次性回填、
  `POST /setting` 键白名单 + schema 转型 + 落盘 + 失败回滚。
  右下角通知与母版同款（类型图标 / 1.2s 去重 / 长文本自适应时长 / 点击复制 /
  上限 4 条 / `aria-live`）；深浅色**默认跟随系统**（`ui_theme_mode` 留空），
  主题色在写入成功与回滚两条路径都会重新套用
- 插件入口 `main.py`（薄壳，仅转发钩子）+ `metadata.yaml` + `_conf_schema.json`
- `xbnext/` 运行时包：`runtime` 调度中心、`context` 请求上下文、
  `injector` 统一注入出口、`switches` 开关对账与 WebUI 配置写入、
  `storage` 插件 KV 封装、`config` 配置读取、`commands` 指令文本解析
- 功能注册表 `xbnext/features/`：一功能一目录，新增功能只需改一个文件
- 单测 `tests/`（**273 条**，标准库 unittest，不依赖 astrbot）

### Fixed

- **热装插件拿不到 `on_astrbot_loaded`**：该事件只在 AstrBot 核心启动收尾广播
  一次，启动之后才装上/热更新的插件永远收不到，表现为 WebUI 一直显示「未加载」、
  `/xbnext profile` 回「档案存储还没就绪」、日志里一条 `[XBNEXT]` 都没有。
  现改为 `runtime.on_loaded()` 幂等 + 新增 `ensure_loaded()`，在
  `handle_llm_request` / `handle_message_sent` / `handle_command` 与
  `state` / 档案三端点全部兜底调用
- **主题色要手动点取色器才生效**：`applyTheme()` 里对
  `CONFIG.ui_accent_color` 的非空判断会让默认态下换主题丢掉派生色；改为
  无条件 `applyAccent()`，并在 `writeConfig()` 的写入成功与回滚两条路径都补
  `applyUiPref(key)`，保证「页面上看到的 = 实际存下来的」

### Notes

- **不与 AstrNa 做共存让路**：早期设计里的 `switches.detect_astrna()` /
  `runtime.conflict_for()` / WebUI 的「AstrNa 共存」卡片与 `state.astrna`
  字段已**整套移除**（AstrNa 未装时那张卡永远是 `—`，属假功能位）。
  两个插件同装时各自独立工作；AstrNa 仅作为路线参考保留在致谢里。见 `aidoc/02 §6`。
- **纯表情消息不触发回复**：aiocqhttp 把 `face` 排除在 `message_str` 外，
  纯表情消息 `message_str` 为空，AstrBot 对空消息不进 provider —— 插件钩子
  `on_llm_request` 根本不会被执行。测「QQ 表情翻译」请文字与表情同发一条。
- **真机回归（首轮）已跑通**：WebUI 打开 / 配置回填 / 改开关刷新保留、
  bridge 通道均正常；深浅色与主题色默认值仍待与 xbdoc / xbimg 再对比一次。
- **仍未做**：完整功能回归（R1~R4 触发用例 + 三个 WebUI 端点的真机往返）。
- 发布包排除 `aidoc/ tests/ .git/ __pycache__/ *.pyc`，但 `aidoc/` 与 `tests/`
  **必须进 git**。
- 已 push 到 `origin/main`；`v0.1.0` tag **待真机测试通过后再打**。
