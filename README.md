# XBNEXT v0.2.41

> AstrBot 对话质量修正插件：修正引用、表情与身份指向等对话细节，附 Web 控制台。

## 简介

修正 AstrBot 日常使用中的实际问题：引用回复误判空白附件、QQ 表情不被模型识别、回复身份串台、用户画像缺失；另提供机器人拦截、撤回取消、历史图片瘦身、Token 用量展示、TTS 限长与插件更新等可选能力，均可在控制台独立开关。

本插件基于 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 开发。AstrBot 是一个松耦合、异步、支持多消息平台部署，具有易用的插件系统和完善的大语言模型（LLM）接入功能的聊天机器人及开发框架，使用文档见 [docs.astrbot.app](https://docs.astrbot.app)。

- 插件 ID：`astrbot_plugin_xbnext`
- 当前版本：`v0.2.41`
- 运行要求：AstrBot `>=3.4.0`，平台 `aiocqhttp`
- 仓库：https://github.com/imsuperone/xbnext

## 功能

- 机器人拦截（默认开启）：屏蔽指定机器人与黑名单账号消息，杜绝死循环对轰与 token 浪费；可拦截未被处理的斜杠指令；可开启「群聊指令需 @ 才触发」，让裸 `/` 指令不再唤醒所有机器人。
- 引用清洗（默认开启）：清理引用链失效占位，杜绝空白图片。
- 表情翻译（默认开启）：QQ 表情转文字描述，模型可理解；表情表每日 04:30 自动补缺。
- 回复指向索引（默认关闭）：记录回复对象，防止身份串台。
- 用户档案（默认关闭）：支持对话自动建档（对机器人说「以后叫我XX，我是...」即可由 AI 自动保存）、极简指令与按群隔离存储。
- 文字玩法（默认关闭）：按配置对特定文字做谐音梗 / 双关处理。
- 撤回取消（默认开启）：撤回消息时取消在飞回复，可选先询问再取消。
- 图片瘦身（默认关闭）：历史图片换占位文本，节省输入 token。
- Token 用量展示（默认关闭）：回复末尾展示本条消息的 token 用量。
- TTS 限长（默认开启）：超长文本截断并淡出，避免语音接口报错。
- 插件更新（默认关闭）：检查 / 一键更新 / 强制更新 XBNEXT 及同系列插件，支持 Git 快进与 GitHub 归档包回退。聊天指令仅限管理员、不需要 OpenAPI 密钥；WebUI 更新必须填写 Key。
- 控制台：功能开关、群组、插件更新、行为微调、用户档案、运行状态、使用指南七个页签。

## 安装

1. AstrBot 后台 → 插件 → 从链接安装 `https://github.com/imsuperone/xbnext`；
2. 重启 AstrBot，在后台「XBNEXT 控制台」页面使用。

## 指令

| 指令 | 说明 |
| :--- | :--- |
| `/xbnext` | 指令菜单 |
| `/xbnext status` | 各功能开关与 KV 状态 |
| `/xbnext profile [@群友]` | 查看本人或被 `@` 群友的档案 |
| `/xbnext profile 小明` / `小明\|学生` | 极简一键设置称呼或「称呼\|自述」（支持 `叫我...`、`我是...`） |
| `/xbnext profile 称呼 小明 自述 学生` | 修改档案（分隔符空格 / `:` / `：` / `=` 混写均可） |
| `/xbnext profile 字段:` / `清空` | 删除单个字段 / 全部档案 |
| `/xbnext token` | 本会话 token 用量展示开关 |
| `/xbnext botguard [add/del QQ]` | 查看 / 维护机器人拦截名单 |
| `/xbnext 检查更新` | 检查插件新版本 |
| `/xbnext 一键更新` | 更新有新版本的插件并热重载 |
| `/xbnext 强制更新` | 强制重新拉取本插件并热重载 |

> 指令必须使用**带空格**的写法：`/xbnext 强制更新`。不支持 `/xbnext强制更新`。
>
> 若开启「群聊全部指令需 @ 触发」，`/xbnext` 系列也必须 @ 本机器人：`@机器人 /xbnext 强制更新`。裸 `/xbnext 强制更新` 会被静默拦截，避免同群多个 Bot 同时响应。

## 说明

- 纯表情消息经翻译补写后可正常触发回复；未被 @、未命中唤醒前缀的纯表情仍不回复，属正常行为。
- 与 xbdoc / xbimg 同开互不干扰：三者注入通道独立，本插件排在最前先清洗；不改写 `system_prompt`，只清理 `contexts` 中混入的 `system` 项与空消息。
- 每轮仅在有动作时打一条 INFO 汇总；逐条明细可在「行为微调」打开调试日志。
- 致谢：设计参考 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 与 [astrbot_plugin_AstrNa](https://github.com/Sisyphbaous-DT-Project/astrbot_plugin_AstrNa)（回复指向索引），WebUI 骨架取自 [xbdoc](https://github.com/imsuperone/xbdoc) / [xbimg](https://github.com/imsuperone/xbimg)。
