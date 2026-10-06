# XBNEXT · AstrBot 对话质量修正插件

> 版本：**v0.1.0**（开发中）
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
| `enable_reply_attribution` | 回复指向索引（R1） | ❌ 关 |
| `enable_user_profile` | 用户档案注入（R4） | ❌ 关 |

> 纯修正类（R2/R3）默认开，因为它们修的是明确 bug，不开启反而留着 bug；
> 新增行为类（R1/R4）默认关，由用户自行决定是否开启。

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
│  ├─ switches.py          开关对账 + AstrNa 共存检测
│  ├─ storage.py           插件 KV 封装
│  ├─ config.py            配置读取（schema 默认值回退）
│  ├─ features/            ★ 功能模块 —— 一功能一目录
│  │  ├─ quote/            R2 引用占位清洗
│  │  ├─ face/             R3 QQ 表情翻译
│  │  ├─ attribution/      R1 回复指向索引
│  │  └─ profile/          R4 用户档案
│  └─ web/                 WebUI 后端 API
├─ pages/manager/          WebUI 前端
├─ tests/                  单测（不依赖 astrbot）
└─ aidoc/                  AI 协作文档（不进发布包）
```

**新增功能**：见 `xbnext/features/base.py` 的注释，5 步完成。

## 开发

```powershell
# 单测（不依赖 AstrBot 本体）
python -X utf8 -m pytest tests -q
# 语法检查
python -X utf8 -m compileall -q main.py xbnext
# 前端
Get-ChildItem pages -Recurse -Filter *.js | %{ node --check $_ }
```

详细规范见 `aidoc/`（**新会话请先读 `aidoc/README.md`**）。

## 致谢与借鉴

本插件的设计与实现借鉴了以下项目，在此致谢：

- [AstrBot](https://github.com/AstrBotDevs/AstrBot) —— 宿主框架与官方插件规范
- [astrbot_plugin_AstrNa](https://github.com/Sisyphbaous-DT-Project/astrbot_plugin_AstrNa)
  —— 回复指向索引（reply target history）的路线参考与共存策略
- 本机参考插件 [xbdoc](https://github.com/imsuperone/xbdoc) /
  [xbimg](https://github.com/imsuperone/xbimg) /
  [xbbot](https://github.com/imsuperone/xb) —— WebUI 设计语言与工程骨架

## 许可

见仓库 LICENSE。
