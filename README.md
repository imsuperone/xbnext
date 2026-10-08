# XBNEXT

> AstrBot 对话质量修正插件：修正引用、表情与身份指向等对话细节，附 Web 控制台。

## 简介

修正 AstrBot 日常使用中的实际问题：引用回复误判空白附件、QQ 表情不被模型识别、回复身份串台、用户画像缺失；另提供撤回取消、历史图片瘦身、Token 用量展示等可选能力，功能均可在控制台独立开关。

本插件基于 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 开发。AstrBot 是一款开源的多平台聊天机器人框架，可接入 QQ、Telegram 等消息平台与多家大模型服务，自带 Web 管理界面，使用文档见 [docs.astrbot.app](https://docs.astrbot.app)。

- 插件 ID：`astrbot_plugin_xbnext`
- 当前版本：`v0.2.1`
- 运行要求：AstrBot `>=3.4.0`，平台 `aiocqhttp`
- 仓库：https://github.com/imsuperone/xbnext

## 功能开关

| 配置键 | 功能 | 默认 |
| :--- | :--- | :---: |
| `enable_quote_clean` | 引用占位清洗 | 开 |
| `enable_face_translate` | QQ 表情翻译 | 开 |
| `face_auto_update` / `face_update_time` | 表情表每日自动补缺（默认 `04:30`） | 开 |
| `enable_reply_attribution` | 回复指向索引（防身份串台） | 关 |
| `enable_user_profile` | 用户档案注入 | 关 |
| `enable_recall_cancel` | 撤回即取消在飞回复 | 开 |
| `enable_recall_confirm` | 撤回先询问再取消（30 秒无应答自动取消） | 关 |
| `enable_image_slim` | 历史图片换占位，省输入 token | 关 |
| `enable_token_usage` / `token_usage_umos` | 回复末尾展示 token 用量（按会话白名单） | 关 |

> 纯修正类默认开（修的是明确问题）；新增行为类默认关，由用户自行开启。

## 指令

| 指令 | 说明 |
| :--- | :--- |
| `/xbnext` | 指令菜单 |
| `/xbnext status` | 各功能开关与 KV 状态 |
| `/xbnext profile` | 查看本人档案 |
| `/xbnext profile 称呼 小明 自述 学生` | 修改档案（分隔符空格 / `:` / `：` / `=` 混写均可） |
| `/xbnext profile 字段:` / `清空` | 删除单个字段 / 全部档案 |
| `/xbnext token` | 本会话 token 用量展示开关 |

> 档案与注入开关解耦：查看、设置、清空始终可用；档案按群隔离，私聊单独一份。

## 控制台

五个页签：功能开关、行为微调、用户档案、运行状态、使用指南。深浅色默认跟随系统，右上角可手动切换；用户档案按「平台 · 群」分组管理，与指令写的是同一份数据。

## 说明

- 纯表情消息经翻译补写后可正常触发回复；未被 @、未命中唤醒前缀的纯表情仍不回复，属正常行为。
- 与 xbdoc / xbimg 同开互不干扰：三者注入通道独立，本插件排在最前先清洗，不改写 `system_prompt` 与 `contexts`。
- 每轮仅在有动作时打一条 INFO 汇总；逐条明细可在「行为微调」打开调试日志。

## 致谢

设计参考 [AstrBot](https://github.com/AstrBotDevs/AstrBot)、[astrbot_plugin_AstrNa](https://github.com/Sisyphbaous-DT-Project/astrbot_plugin_AstrNa)（回复指向索引），WebUI 骨架取自 [xbdoc](https://github.com/imsuperone/xbdoc) / [xbimg](https://github.com/imsuperone/xbimg)，在此致谢。
