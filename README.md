# XBNEXT v0.2.3

> AstrBot 对话质量修正插件：修正引用、表情与身份指向等对话细节，附 Web 控制台。

## 简介

修正 AstrBot 日常使用中的实际问题：引用回复误判空白附件、QQ 表情不被模型识别、回复身份串台、用户画像缺失；另提供撤回取消、历史图片瘦身、Token 用量展示等可选能力，均可在控制台独立开关。

本插件基于 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 开发。AstrBot 是一个松耦合、异步、支持多消息平台部署，具有易用的插件系统和完善的大语言模型（LLM）接入功能的聊天机器人及开发框架，使用文档见 [docs.astrbot.app](https://docs.astrbot.app)。

- 插件 ID：`astrbot_plugin_xbnext`
- 当前版本：`v0.2.3`
- 运行要求：AstrBot `>=3.4.0`，平台 `aiocqhttp`
- 仓库：https://github.com/imsuperone/xbnext

## 功能

- 引用清洗（默认开启）：清理引用链失效占位，杜绝空白图片。
- 表情翻译（默认开启）：QQ 表情转文字描述，模型可理解。
- 表情表自动更新（默认开启）：每日 04:30 自动补缺。
- 回复指向索引（默认关闭）：记录回复对象，防止身份串台。
- 用户档案（默认关闭）：用户自助维护个人设定，按群隔离，私聊单独一份。
- 撤回取消（默认开启）：撤回消息时取消在飞回复，可选先询问再取消。
- 图片瘦身（默认关闭）：历史图片换占位文本，节省输入 token。
- Token 用量展示（默认关闭）：回复末尾展示本条消息的 token 用量。
- 控制台：功能开关、行为微调、用户档案、运行状态、使用指南五个页签。

## 安装

1. AstrBot 后台 → 插件 → 从链接安装 `https://github.com/imsuperone/xbnext`；
2. 重启 AstrBot，在后台「XBNEXT 控制台」页面使用。

## 指令

| 指令 | 说明 |
| :--- | :--- |
| `/xbnext` | 指令菜单 |
| `/xbnext status` | 各功能开关与 KV 状态 |
| `/xbnext profile` | 查看本人档案 |
| `/xbnext profile 称呼 小明 自述 学生` | 修改档案（分隔符空格 / `:` / `：` / `=` 混写均可） |
| `/xbnext profile 字段:` / `清空` | 删除单个字段 / 全部档案 |
| `/xbnext token` | 本会话 token 用量展示开关 |

## 说明

- 纯表情消息经翻译补写后可正常触发回复；未被 @、未命中唤醒前缀的纯表情仍不回复，属正常行为。
- 与 xbdoc / xbimg 同开互不干扰：三者注入通道独立，本插件排在最前先清洗，不改写 `system_prompt` 与 `contexts`。
- 每轮仅在有动作时打一条 INFO 汇总；逐条明细可在「行为微调」打开调试日志。
- 致谢：设计参考 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 与 [astrbot_plugin_AstrNa](https://github.com/Sisyphbaous-DT-Project/astrbot_plugin_AstrNa)（回复指向索引），WebUI 骨架取自 [xbdoc](https://github.com/imsuperone/xbdoc) / [xbimg](https://github.com/imsuperone/xbimg)。
