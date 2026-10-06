# 调研：本机参考插件 WebUI（xbdoc / xbimg / xbbot_beta）

> 调研日期：2026-10-06。三个参考插件位于 `C:\Users\Light\Desktop\astrbot_plusgin\`（与本项目同级）。
> 结论先行：**xbimg 的形态最轻，xbbot_beta 的工程形态最完整**。XBNEXT 取两者之长：
> 页面骨架与入口仿 xbimg，样式与 bridge 层仿 xbbot_beta。

---

## 1. 三插件结构对比

| 插件 | 页面 | 文件 | 总行数 | 形态 | metadata `pages:` |
|---|---|---|---|---|---|
| astrbot_plugin_xbdoc | `/manager` | index.html + style.css + api.js | 1,191（210+656+325） | 轻量，JS 内联 | 未显式声明（隐患） |
| astrbot_plugin_xbimg | `/manager` | index.html + style.css + api.js | 1,078（110+716+252） | 最轻，**有防闪骨架** | **已声明 `pages:`（正例）** |
| astrbot_plugin_xbbot_beta | `/admin` | index.html + 5 css + 11 js | 12,120（318+2,343+9,459） | 完整工程 | 未显式声明（隐患） |

共同事实：三者都用 `pages["xxx"]= {…}` 注册 + `get_pages()` 返回；都用 bridge；
xbbot_beta 有 `web/`（Starlight 内嵌式整站，实际未挂载），xbdoc/xbimg 没有。

---

## 2. xbimg —— 页面骨架母版（必抄）

`astrbot_plugin_xbimg/pages/manager/index.html`：

- **L10-21 防闪骨架**：`<style>` 内联 `#app{opacity:0;transition:opacity .22s}` +
  `#app.ready{opacity:1}`；boot CSS 只做三件事——中心 loading 文案、
  `no-bridge` 警示条、窄屏（≤760px）顶栏换行。**其余样式全部外链 style.css**。
- **L25-49 顶栏**：`top-bar > brand-mark(X 图标) + brand-text(标题/副标题) + status-chip#conn`
  + tab-nav（按钮 `data-view`：`settings` / `runtime`，默认激活 settings）。
- **L52-73 分类 tab**：`cat-tabs` + 按钮 `data-cat`，`active` 类切分类。
- **L94-100 页脚**：`footer-note` 服务端时间 `#serverTime`。
- JS 组织：`app.js`（约 300 行，全逻辑）+ `api.js`（网络层）。

xbimg `app.js` 关键行为：
- boot 流程 `init()` → `bindTabs()` → `refreshPageContext()` → `loadSettings()`
  → `loadRuntime()` → `markReady()`。
- `markReady()` 才加 `ready` 类（内容加载完成后淡入，避免白屏/闪屏）。
- `setConn(status, detail)` 统一维护 `#conn` 三态（checking / online / offline）。

## 3. xbimg api.js —— bridge 探测模式（必抄）

`astrbot_plugin_xbimg/pages/manager/api.js`：

- **L57-91 `resolvePrefix()` 并发合并探测**：
  `if (sessionWindow.__xbimgPrefix) return …` → 同一 tab 会话缓存前缀，后续请求零探测开销；
  `window.__xbimgPrefixPromise` 去重并发（多处同时调用只发一次探测）；
  探测顺序 `["", "/api/plugins/astrbot_plugin_xbimg", "/api/plugins/astrbot_plugin_xbimg"]`——
  第 1 个空串（bridge 直连场景），后两个是 HTTP 兜底；
  逐个 `GET {prefix}/xbimg/ping`，`res.ok && data.status === "ok"` 即采用并缓存。
- **L153-201 上传双通道**：无 bridge 时 fetch `/upload_image?…`（FormData）；
  有 bridge 时 `bridge.uploadFile("xbimg.uploadImage", {dataBase64, filename})`
  ——**图片走 base64 走 bridge，不走 URL**。
- `xbimgRequest(path, body)` 统一 POST JSON → `{status, message, data}`，
  非 ok 抛 Error（message 带服务端 error 字段）。
- **无全局轮询**：`bridgeRequest` 收不到响应时 20 秒才 reject，且全文件只出现 1 次。

## 4. xbbot_beta —— 样式与工程形态参考

`astrbot_plugin_xbbot_beta/pages/admin/`：

- **admin.css M3 令牌（L1-98）**：MD3 You 3.0 / Expressive：
  `ease-standard(--ease-emphasized: cubic-bezier(.2,0,0,1))`、
  `--ease-emphasized-decel: cubic-bezier(.05,.7,.1,1)`；
  背景 `#101319`，分层 `#12151c/#171b23/#1c212a/#212732/#272e3a`，描边 `#2d3542/#394252`；
  主色 `#79a5ff`、次色 `#b39bff`、成功 `#78d99a`、警告 `#f3c969`、危险 `#ff8a8a`；
  `--duration-fast: 120ms / --duration-medium: 240ms / --duration-slow: 420ms`；
  圆角 `--radius-xs: 5px … --radius-pill: 999px`；
  阴影 `--shadow-overlay: 0 12px 36px rgba(0,0,0,.36)`。
- **js/00_core.js L48-184 bridge 探测与事件注册**：`XB.bridge` 包装
  `window.astrbotPluginBridge?.request()`；`XB.on(name, handler)` 事件分发；
  `XB.apiPrefixes`（L36）HTTP 前缀表。
- 文件组织：`00_core / 01_state / 02_events / 10_tabs / 11_fragments / 12_settings /
  13_tools / 14_browser / 15_external / 16_prompts / 20_llm / 21_personas`——
  以 `00_` 前缀保证加载顺序，每个文件一个关注点。
- index.html L150-217 就是完整 tab 树（nav 左侧）+ 11 个 `<section class="view" data-view="…">`。

> **XBNEXT 取舍**：不抄 xbbot_beta 的 12k 行功能代码（明确被要求"原有功能不要"），
> 只抄它的 CSS 令牌与 js 分层思想。页面骨架用 xbimg（110 行 vs 318 行）。

## 5. xbdoc api.js（L43-81 `xbdocRequest`）——错误处理样板

- `bridgeRequest(path, body)`：**无 bridge → 普通 fetch `path`**（同源相对路径，不加前缀）；
  **有 bridge → `bridgeRequest`**（支持 bridge 就一律走 bridge，不走 HTTP）。
- 超时实现：`setTimeout` reject `new Error("bridge request timeout")`，**不是 AbortController**
  ——与 AstrBot 文档"HTTP 请求库不支持超时"的告警配套，20 秒兜底。
- `xbdocRequest`：bridge 响应 `{status,message,data}` 与 HTTP 响应
  `{ok:true, data}` 两种形态归一到 `data`。

---

## 6. 三插件共同的坑（XBNEXT 必须避开）

1. **`metadata.yaml` 未显式写 `pages:`**（只有 xbimg 写了）——
   AstrBot 官方开发原则第 9 条明确"若仍使用旧式 get_pages()，metadata.yaml
   可不声明 pages，但不推荐"。**XBNEXT 必须显式声明。**
2. **xbdoc / xbbot_beta 的 index.html 有 `[)]</script>` 被正则截断的痕迹**（外部抓取导致，
   本机文件应无此问题）——提醒我们读参考文件要用 Read 工具而非网络抓取。
3. 三插件都没有独立的 `state.js`/`router.js`，xbimg 把状态和路由都塞在 `app.js` 里
   （约 300 行仍可读）。XBNEXT 页面更小，**单 app.js 足够，不必过度拆分**。
