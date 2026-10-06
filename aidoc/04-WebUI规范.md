# XBNEXT · WebUI 规范（仿制基准）

> 目标：**完全仿照** `xbdoc` / `xbimg` / `xbbot_beta` 的视觉与工程风格；
> 功能位换成 XBNEXT 自己的。本文件是前端的唯一规格来源。

---

## 1. 形态选型：取 xbimg 轻量形态

三者对比（详见 `research/reference-ui.md`）：

| 插件 | 形态 | 规模 |
| :--- | :--- | :--- |
| xbimg | 单 HTML + 单 CSS + 单 JS（桥接层内联） | ~4,563 行 |
| xbdoc | 单 HTML + 单 CSS + app.js + **独立 api.js** | ~4,239 行 |
| xbbot_beta | 单 HTML + 单 CSS + 11 个按域拆分 JS | ~11,958 行 |

**决策：xbimg/xbdoc 形态起步**（XBNEXT 首版只有 4-5 个功能页签，用不上 beta 的 14 tab 工程）：

```
pages/manager/
├─ index.html    骨架（data-boot 防闪 + top-bar + 页签 + section 面板 + toast）
├─ style.css     M3 Expressive 令牌 + 组件（移植 xbimg/xbdoc style.css）
├─ api.js        网络层（bridge 优先 + fetch 多前缀回退 + 超时）—— 照 xbdoc api.js
└─ app.js        业务（页签切换、开关、档案管理、主题）
```

> 若后续 JS > 1500 行或页签 > 8，再升级 beta 的 `js/NN_域.js` 拆分模式。

## 2. metadata 声明（必须）

照 xbimg 正例（xbdoc/beta 省略 `pages:` 是反面教材）：

```yaml
pages:
  - name: manager
    title: XBNEXT 控制台
    path: pages/manager/index.html
```

## 3. 设计语言（M3 Expressive，与三插件同源）

### 3.1 令牌（从 xbimg/xbdoc style.css 移植）

```css
:root / [data-theme="light"] {
  --bg: #F4F7FB;  --panel: #FFFFFF;  --acc: #4A90D9;
  --text: #191C20;  --ok: …;  --warn: …;  --bad: …;
  --radius-*: 12/20/28/999px;  --shadow-*: …;
  --m3-sys-color-*;  --m3-radius-*;  --m3-shadow-card;
}
[data-theme="dark"] {
  --bg: #111418;  --panel: #1A1F26;  --acc: #8AB8F0;  --text: #E7EBF0;
}
```

- 双主题：`<html data-theme="light|dark">`，**由 AstrBot bridge SDK 自动维护**
  （见官方 plugin-pages 文档"亮暗主题"节），页面用 CSS 变量响应。
- 强调色调色盘：`#accentPickerBtn / #accentPopover / #accentSv / #accentHue /
  #accentPresets` —— 三插件同款 DOM，直接抄 xbimg `index.html` L32 行内结构。

### 3.2 组件类命名（沿用参考插件混用体系，不发明新名）

| 组件 | 类名 |
| :--- | :--- |
| 顶栏 | `.top-bar` + `.top-bar-brand` + `.top-bar-actions` |
| 页签 | `.category-tabs-bar` + `.cat-tab`（激活 `.active`），`data-tab` ↔ section `data-section` |
| 卡片 | `.panel` / `.m3-card` + `.card-title` + `.card-desc` |
| 按钮 | `.m3-btn`（主）/ `.ghost`（描边）/ `.tonal`（次）/ `.del`（危险）/ `.sm` |
| 图标按钮 | `.m3-icon-btn`（40px 圆形） |
| 开关/表单 | M3 switch / input / select（左对齐 switch） |
| 反馈 | `#toastContainer` toast、页内确认框（**禁原生 confirm/alert**，iframe 被拦） |
| 空态 | `.empty-state` |

类名规则：**旧名保留、新名追加**（参考插件的既定约定），暗色靠 `[data-theme="dark"]` 前缀覆盖。

### 3.3 结构骨架（index.html 必备节）

```html
<html lang="zh-CN" data-theme="light">
  <head>
    <meta charset="utf-8"/>
    <meta name="color-scheme" content="light dark"/>
    <link rel="stylesheet" href="./style.css?v=<版本>"/>
    <style>html[data-boot]{visibility:hidden}</style>  <!-- 首帧防闪 + 3s 兜底 -->
  </head>
  <body>
    <header class="top-bar">品牌 + version-tag[data-ver] + 调色盘/主题/刷新按钮</header>
    <nav class="category-tabs-bar">页签…</nav>
    <main class="main-content-wrap">
      <section class="settings-section active" data-section="…">…</section>
      …
    </main>
    <div id="toastContainer"></div>
    <script src="./api.js" defer></script>
    <script src="./app.js?v=<版本>" defer></script>
  </body>
</html>
```

## 4. 网络层规范（api.js，照 xbdoc）

```js
const PLUGIN_ID = "astrbot_plugin_xbnext";
const FRONTEND_VER = "<版本>";          // 与 metadata 同步

getBridge(): window.AstrBotPluginPage → parent.AstrBotPluginPage → bridge/parent 兜底
  每步校验 typeof …apiGet === "function"

fetch 前缀回退表（并发合并探测，结果缓存）:
  ['/api/plugins/<id>/', '/<id>/', 'api/', './api/', './']

api.get/post/upload/download(endpoint, …)
  - endpoint 是插件内相对路径，不带插件名前缀、不带 '/' 开头、不拼 query
  - 桥失败才 fetch；统一 apiTimeout(30s)
  - 上传 ≤8MB 走 base64 JSON，>8MB 走 multipart（xbdoc api.js:153-201 原样）
```

后端对齐：`context.register_web_api(f"/{PLUGIN_ID}/{suffix}", handler, methods, desc)`，
handler 用 `from astrbot.api.web import request, json_response, error_response`。

## 5. 页签规划（XBNEXT 功能位）

| 页签 | 内容 | 对应后端 |
| :--- | :--- | :--- |
| 总览 | 版本、AstrNa 共存检测状态、各功能开关摘要、最近注入统计 | `state` |
| 表情翻译（R3） | 主开关、自定义映射表编辑、预览翻译 | `config/get` `config/save` |
| 引用清洗（R2） | 主开关、清洗规则子项 | 同上 |
| 回复指向（R1） | 主开关、索引查看/清空、AstrNa 冲突提示 | 同上 + `reply/index` |
| 用户档案（R4） | 档案列表（搜索/查看/编辑/删除/导出）、注入开关 | `profiles` `profiles/save` `profiles/delete` |
| 关于 | 版本、仓库链接、aidoc 摘要 | 静态 |

## 6. 硬性工程要求（红线）

1. **零 CDN、零框架**：纯原生 DOM + 内联 SVG（三插件全部如此）。
2. 改完 HTML/JS 必跑 `node --check`（每个 js 文件）。
3. 版本锚点两处：`index.html` 的 `data-ver` + 所有 `?v=` 缓存号，随版本同步。
4. iframe 沙箱禁 localStorage/原生弹窗 → UI 偏好存服务端（`config/save`
   存 `ui_theme_mode` / `ui_accent_color`，照 xbbot_beta `01_theme.js` L130-182）。
5. 桥通信用 `bridge.ready()` 先等上下文；主题读 `bridge.getContext()?.isDark`
   并 `onContext()` 监听。
6. **功能不做参考插件的原有功能**（文档记忆/转图/游戏），只借壳。

## 7. 验收清单

- [ ] 亮/暗主题跟随 AstrBot 切换，无首帧闪烁
- [ ] 调色盘可改强调色且持久化（服务端）
- [ ] 所有开关点立即保存，失败回滚并 toast 提示
- [ ] 档案编辑有确认框（页内 modal，非原生 confirm）
- [ ] 窄屏（<640px）页签横向滚动、卡片单列
- [ ] `node --check` 全绿 + `metadata.yaml` `pages:` 已声明
