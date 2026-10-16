# 更新日志 (Changelog)

## v0.2.13 (2026-10-16)

- **修复消息接管发送超时异常处理**：解决底层 `aiocqhttp.exceptions.ActionFailed`（继承自 `RuntimeError`）被事件循环获取逻辑误捕获，引发 `cannot reuse already awaited coroutine` 进而错误退回原发送并导致 NapCat 二次超时崩溃的问题。全面加强超时判定，有效拦截高延迟长消息引起的重复发送与管道报错。

## v0.2.12 (2026-10-15)

- **新增插件一键检查与更新（`enable_updater`）**：
  - 支持通过 AstrBot OpenAPI 接口检查并一键更新选定插件（默认关闭）。
  - 支持指令 `/xbnext 检查更新`、`/xbnext 一键更新`、`/xbnext 强制更新`。
  - WebUI 控制台新增插件更新卡片，支持配置 OpenAPI 密钥、服务地址、一键获取已安装插件、多选勾选纳入更新的插件。
  - 支持直接对比 GitHub 远程最新 Commit SHA 与本地 Git Commit，并提供强制从 GitHub 重新拉取并热重载的快捷功能。
  - 默认预设 `xbimg`、`xb2img`、`xbdoc`、`xbbot`、`xbbotbeta`、`xbnext` 系列插件，未安装插件自动静默跳过。

## v0.2.11 (2026-10-13)

- **修复插件加载报错 `KeyError: 'items'`**：将 `_conf_schema.json` 中 `group_features` 的配置类型由无 `items` 的 `object` 修正为 JSON 字符串（`type: "string"`，默认 `"{}"`），並在 `xbnext/switches.py` 与 `xbnext/config.py` 中双向兼容字典与 JSON 字符串，彻底解决 AstrBot 校验器报错导致插件加载失败的问题。
- **新增 TTS 语音字数上限控制（`enable_tts_limit` / `tts_max_chars`）**：当单次回复纯文本超过设定字数（默认 `100` 字）时，自动跳过 AstrBot 语音合成（TTS）直接发送文本，避免长篇小说或大段分析触发 TTS 超时报错。
- **新增群聊总开关与分群独立功能开关（`group_mode` / `group_list` / `group_features`）**：支持全局开启、白名单、黑名单三种模式，并在 Web 控制台提供群聊表格，可按群独立控制插件总开关及每一项子功能的开启/关闭。
- **彻底修复撤回确认超时/取消后仍漏发消息的问题**：消除超时收尾撤回询问消息（`_delete_ask`）期间的竞态窗口，新增已取消消息强拦截表与装饰阶段（`on_decorating_result`）清空机制，并对在途底层发送请求采用 `asyncio.shield` 捕获回执 ID 后立即撤回；补充「行 / 好 / 好的 / 可以 / 嗯」等肯定回答词。
- **修复 Google Gemini 499 / 上下文错误**：清理 `req.contexts` 中混入的 `system` 角色项与残缺空消息；当 `/new` 开启新对话（上下文尚无历史回复）时不再注入旧对话的「最近回复对象」列表；将看门狗超时上调至 `600s` 并跳过 `webchat` 平台。
