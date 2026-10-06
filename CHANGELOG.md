# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## v0.1.0

首个开发版：项目骨架落地。

### Added

- 插件入口 `main.py`（薄壳，仅转发钩子）+ `metadata.yaml` + `_conf_schema.json`
- `xbnext/` 运行时包：`runtime` 调度中心、`context` 请求上下文、
  `injector` 统一注入出口、`switches` 开关与 AstrNa 共存检测、
  `storage` 插件 KV 封装、`config` 配置读取
- 功能注册表 `xbnext/features/`：一功能一目录，新增功能只需改一个文件
- R2 `quote/`：引用占位识别与三种处理策略（label / strip / keep）
- R3 `face/`：表情翻译纯逻辑 + 起步样例表情表（全量表待补）
- R1 `attribution/`：回复指向索引存储（message_id 优先、hash 兜底、歧义放弃）
- R4 `profile/`：用户档案存储（字段白名单 + sanitize + 中文平铺渲染）
- WebUI 后端 `xbnext/web/`（ping / state）与前端骨架 `pages/manager/`
- 单测 `tests/`：结构自检、注入、清洗、表情、存储

### Notes

- **未做真机回归**：AstrBot 本体在云端，本机只能静态检查 + 单测。
- R1 / R4 的注入文案、R3 的权威表情表与消息链接入、WebUI 页面待后续版本。
