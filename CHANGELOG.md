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
  `on_llm_request` 按当前发言人注入；**档案与开关解耦**，开关只决定喂不喂给模型
- **R5 · WebUI**（`pages/manager/`）：M3 Expressive 令牌、`.app-layout` /
  `.top-bar` / `.category-tabs-bar` / `.m3-card` / `.m3-switch`、取色盘、
  `data-boot` 防闪、`?v=` 缓存号；后端 `GET /state` 一次性回填、
  `POST /setting` 键白名单 + schema 转型 + 落盘 + 失败回滚
- 插件入口 `main.py`（薄壳，仅转发钩子）+ `metadata.yaml` + `_conf_schema.json`
- `xbnext/` 运行时包：`runtime` 调度中心、`context` 请求上下文、
  `injector` 统一注入出口、`switches` 开关对账与 WebUI 配置写入、
  `storage` 插件 KV 封装、`config` 配置读取、`commands` 指令文本解析
- 功能注册表 `xbnext/features/`：一功能一目录，新增功能只需改一个文件
- 单测 `tests/`（**252 条**，标准库 unittest，不依赖 astrbot）

### Notes

- **不与 AstrNa 做共存让路**：早期设计里的 `switches.detect_astrna()` /
  `runtime.conflict_for()` / WebUI 的「AstrNa 共存」卡片与 `state.astrna`
  字段已**整套移除**（AstrNa 未装时那张卡永远是 `—`，属假功能位）。
  两个插件同装时各自独立工作；AstrNa 仅作为路线参考保留在致谢里。见 `aidoc/02 §6`。
- **未做真机回归**：AstrBot 本体在云端，本机只能静态检查 + 单测。
  待真机确认三项：`register_web_api` 真实路由前缀、`AstrBotConfig.save_config*`
  是否可用、bridge 的 `apiGet/apiPost` 实际签名。
- 发布包排除 `aidoc/ tests/ .git/ __pycache__/ *.pyc`，但 `aidoc/` 与 `tests/`
  **必须进 git**。
- 未 push、未打 tag：待确认后再执行。
