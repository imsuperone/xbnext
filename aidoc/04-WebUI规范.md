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
├─ api.js        网络层 —— **文件分工沿用 xbdoc（独立 api.js），
│                 内部契约照 xbimg/xbbot_beta**（四级 getBridge + 前缀探测）
└─ app.js        业务（页签切换、开关写入与回滚、条件显隐、主题）
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

### 3.1 令牌（**实测结论：母版只用 `--m3-sys-color-*` 一套**）

初稿里那套 `--bg/--panel/--acc` 是凭印象写的，实际读三插件 `style.css` 后确认
**它们不存在**，一律 `--m3-sys-color-*`：

```css
:root {                                  /* = 浅色主题（不是深色！） */
  --m3-sys-color-primary: #4A90D9;        --m3-sys-color-on-primary: #FFFFFF;
  --m3-sys-color-primary-container: #D9E8F7;
  --m3-sys-color-on-primary-container: #0F2B46;
  --m3-sys-color-surface: #F4F7FB;        --m3-sys-color-on-surface: #191C20;
  --m3-sys-color-surface-container: #E8EDF4;
  --m3-sys-color-surface-container-high: #FFFFFF;
  --m3-sys-color-surface-container-highest: #DFE6EF;
  --m3-sys-color-outline: #73777F;        --m3-sys-color-outline-variant: #C3C6CF;
  --m3-sys-color-divider: rgba(90, 100, 115, 0.14);
  --m3-sys-color-error: #B3261E;          --m3-sys-color-error-container: #F9DEDC;
  --m3-status-cyan/amber/purple/green(+ -bg);
  --font-system: "Google Sans", "MiSans", …;
  --font-mono:  ui-monospace, …;
  --m3-radius-small/medium/large/pill: 12/20/28/999px;
  --m3-shadow-card: 0 4px 16px rgba(30,45,70,.06);
}
[data-theme="dark"] {                     /* 覆盖同一组键 */
  --m3-sys-color-primary: #8AB8F0;  --m3-sys-color-surface: #111418;
  --m3-sys-color-surface-container-high: #232A33;  …
  --m3-shadow-card: 0 8px 24px rgba(0,0,0,.4);
}
```

- **双主题**：`<html data-theme="light">`（浅色是默认值，不是深色）。
  本插件不用 bridge 的 `onContext` 维护，而是**存服务端配置键 `ui_theme_mode`**，
  与 xbdoc 的 `settings/save → ui_theme_mode` 同款（见 §6 红线 4）。
- **首帧防闪**：`html[data-boot]{visibility:hidden; background:#f4f6f8}` +
  内联脚本打 `data-boot`、**3s 超时兜底移除**；由 `revealUiPrefs()` 在服务端
  偏好回填后主动揭开（本插件对应 `reveal()`）。
- **强调色派生只染强调系**：`--m3-sys-color-primary` + `primary-container`，
  surface 系保持主题原色，否则整页被染脏；另算一个 `--m3-seg-ink` 保证
  选中分段的文字与新背景对比度 ≥ 3:1。
- 强调色调色盘：`#accentPickerBtn / #accentPopover / #accentSv / #accentHue /
  #accentPresets` —— 三插件同款 DOM，结构直接抄 xbdoc `index.html` L32 行内结构。

### 3.2 组件类命名（沿用参考插件混用体系，不发明新名）

| 组件 | 类名（实际使用） |
| :--- | :--- |
| 顶栏 | `.top-bar` + `.top-bar-brand` > `.brand-text` > `h1` + `.version-tag[data-ver]` + `.top-bar-actions` |
| 页签 | `.category-tabs-bar` + `.cat-tab`（激活 `.active`）> `.cat-icon` + `.cat-title`，`data-tab` ↔ section `data-section` |
| 内容区 | `.main-content-wrap`（max-width 960）> `.settings-section[data-section]`，激活 `.active` + `sectionIn` 动画 |
| 卡片 | `.m3-card` + `.card-title` + `.card-desc`，开关行用 `.card-row-split` |
| 图标按钮 | `.m3-icon-btn`（40px 圆形，hover `rotate(15deg)`）；调色盘 `button.m3-accent-picker` |
| 开关 | `label.m3-switch > input + span.switch-slider` |
| 分段控件 | `.m3-segmented-control > .seg-item[data-val]`（激活 `.active`） |
| 表单 | `.form-group` + `.form-label` + `.m3-input` + `.field-hint` |
| 状态列表 | `.kv-list > .kv-row > .kv-k` / `.kv-v` |
| 反馈 | `.toast-container#toastBox` 内堆叠 `.m3-toast`（成功 `.okk` / 失败 `.badk`）；**禁原生 confirm/alert**（iframe 被拦） |
| 空态 | `.card-note`（虚线边框说明块） |

类名规则：**旧名保留、新名追加**（参考插件的既定约定），暗色靠 `[data-theme="dark"]` 前缀覆盖。

### 3.3 结构骨架（index.html 实际形态）

```html
<html lang="zh-CN" data-theme="light">
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no"/>
    <title>XBNEXT 控制台</title>
    <link rel="stylesheet" href="./style.css?v=0.1.0"/>
    <style>
      html[data-boot] { visibility: hidden; background: #f4f6f8; }
      @media (prefers-color-scheme: dark) { html[data-boot] { background: #0f1115; } }
    </style>
    <script>/* 打 data-boot + 3s 兜底移除 */</script>
  </head>
  <body>
    <div class="app-layout">                     <!-- max-width 1320 -->
      <header class="top-bar">
        <div class="top-bar-brand"><div class="brand-text">
          <h1>XBNEXT <span class="version-tag" data-ver="0.1.0">0.1.0</span></h1>
        </div></div>
        <div class="top-bar-actions">取色器 + 恢复默认色 + 主题切换 + 刷新</div>
      </header>
      <nav class="category-tabs-bar">4 个 .cat-tab[data-tab]</nav>
      <main class="main-content-wrap" id="mainWorkspace">
        <section class="settings-section active" data-section="tab-switches">…</section>
        <section class="settings-section" data-section="tab-tuning">…</section>
        <section class="settings-section" data-section="tab-runtime">…</section>
        <section class="settings-section" data-section="tab-guide">…</section>
      </main>
    </div>
    <div class="toast-container" id="toastBox"></div>
    <script src="./api.js?v=0.1.0"></script>
    <script src="./app.js?v=0.1.0"></script>
  </body>
</html>
```

> 脚本**不用 `defer`**：两者都在 body 末尾，`app.js` 直接依赖 `window.XbnextApi`。
> 所有 `?v=` 与 `data-ver` 必须等于 `metadata.yaml` 的版本号（红线 3）。

## 4. 网络层规范（api.js，实测落地形态）

```js
var PLUGIN_ID = "astrbot_plugin_xbnext";
var BRIDGE_TIMEOUT_MS = 30000, HTTP_TIMEOUT_MS = 25000;

getBridge():  window.AstrBotPluginPage → window.bridge
           → window.parent.AstrBotPluginPage → window.parent.bridge
              每步都要求 typeof …apiGet === "function"（照 xbimg app.js L11-24）

API_PREFIXES（按序探测，命中的缓存到 _WORKING_PREFIX）:
  ['/api/plugins/<id>/', '/<id>/', 'api/', './api/', '']

404 = 这个前缀没接上 → 换下一个；
**其余非 2xx（401/403/500…）说明请求已被路由处理 → 直接抛错，绝不换前缀、绝不重放**
（xbbot_beta 00_core.js 的既定约定，POST 重放会写坏数据）

GET  : bridge 失败/超时 → 回退 fetch（幂等，安全）
POST : bridge 失败直接抛；没有 bridge 时**先用 GET ping 探前缀**再发，
       绝不逐前缀试错 POST

响应归一（bridge 与 HTTP 同一份 JSON）:
  unwrap(res): res.ok === false → throw res.error
               有 "data" 键     → 返回 res.data
               否则             → 原样返回
对外暴露 window.XbnextApi = {
  ping, state, setting,
  profiles, profileSave, profileDelete,
  channel, prefix, getBridge
}
```

### 4.1 后端路由（**与母版不同：端点不含 `/xbnext/` 中缀**）

```python
base = f"/{PLUGIN_NAME}"          # /astrbot_plugin_xbnext
register(f"{base}/ping",           ping,           ["GET"],  "存活探测")
register(f"{base}/state",          state,          ["GET"],  "运行状态")
register(f"{base}/setting",        setting,        ["POST"], "写入配置项")
register(f"{base}/profiles",       profiles,       ["GET"],  "用户档案列表")
register(f"{base}/profile_save",   profile_save,   ["POST"], "写入用户档案")
register(f"{base}/profile_delete", profile_delete, ["POST"], "删除用户档案")
```

- 路由前缀必须用 **metadata.yaml 里保留大小写的插件名**：Plugin Page Bridge 按
  metadata 名请求，而 AstrBot 会把插件类上的 `name` 规范成小写，两者不一致会打偏。
- handler 签名 `async def handler()`；请求体 `await astrbot_web.request.json(default={})`；
  响应**只用 `astrbot_web.json_response(body)` 单参数形式**——第二参的签名不确定，
  宁可 HTTP 恒 200、靠 body 的 `ok:false` 报错，两条通路形状才一致。
- `POST setting` 载荷 `{key, value}`，写入路径固定为：
  **键白名单（= SCHEMA 全键）→ 按 schema type 转型 → 写内存 → `save_config*()` 落盘
  → 失败回滚内存**（`xbnext/switches.py::apply_conf`）。前端失败同样回滚，
  保证"页面上看到的 = 实际存下来的"。
- `GET state` 一次性回填全部：`version/loaded/features/kv_usable`
  + `config`（全 schema 键当前值）+ `schema`（type/condition/default）
  + `hook_priority` + `writable_keys`。**唯一真相源在服务端，页面不留本地副本。**
  （早期载荷里的 `astrna` 字段已随「AstrNa 共存」整套移除。）
- **`state` / `profiles` / `profile_save` / `profile_delete` 进门都先
  `await runtime.ensure_loaded()`** —— `on_astrbot_loaded` 只在核心启动收尾广播
  一次，热装的插件永远收不到；不兜底就会一直显示"未加载"、档案报"还没就绪"
  （真机首轮发现的根因，见 `aidoc/README` 交付记录）。
- 档案三端点的载荷：
  - `GET profiles` → `[{platform, uid, profile:{name,facts,style}, updated}]`
  - `POST profile_save` → `{platform, uid, name, facts, style}`，**三字段整体替换**，
    全空即删整份；`platform` / `uid` 缺失直接 `ok:false`
  - `POST profile_delete` → `{platform, uid}`

## 5. 页签规划（首版实际落地 5 个）

| 页签 | `data-tab` | 内容 | 后端 |
| :--- | :--- | :--- | :--- |
| 功能开关 | `tab-switches` | 4 张卡：引用占位清洗 / QQ 表情翻译 / 回复指向索引 / 用户档案注入，每张 `.card-row-split` + `.m3-switch` | `state` 回填 + `setting` 写 `enable_*` |
| 行为微调 | `tab-tuning` | 占位处理方式（分段）、失效图片路径（开关）、表情格式（输入）、表情表自动更新（开关 + 更新时间输入）、R1 两个整数、R4 整数、调试日志开关 | `setting` |
| 用户档案 | `tab-profile` | 列出全部档案（`platform / uid` + 称呼/自述/口吻摘要）、行内编辑/删除、新建、两步确认删除 | `GET profiles` + `POST profile_save` / `profile_delete` |
| 运行状态 | `tab-runtime` | 版本/加载/priority、KV 可用性与接口通道 | `state` |
| 使用指南 | `tab-guide` | 指令表、新增功能 5 步、致谢与借鉴、**未做真机回归提示** | 静态 |

> **§5 决策记录（真机首轮反馈后修订）**：
>
> - **档案页签：从"不做"改为"做"**。原决策理由是 WebUI 母版没有"当前用户"
>   上下文，做不到"我改我自己的"。真机试用后按用户要求补上了独立页签，
>   定位改为**管理视角**（列出全部档案、群聊里一眼分清谁是谁），与
>   `/xbnext profile` 指令（自助视角：谁发指令改谁的）**并存**，两端写同一份
>   数据。身份校验仍由后端把关：`platform` / `uid` 必填，字段白名单只认
>   称呼 / 自述 / 口吻，全部经 `store.normalize()` 的 `sanitize` 清洗。
> - **AstrBot 的插件 KV 没有"按键遍历"**（只有 `get/put/delete_kv_data`），
>   所以列表能力靠自建索引键 `xbnext:profile:index`（成员记号 `platform|uid`）
>   维护；每次 `set` 追加、`delete` 移除，`list_all()` 读到孤儿条目会顺手剔除
>   （自愈），不会永远报幽灵档案。
> - **删除用两步确认**（按钮变成"确认删除"，3 秒超时还原）：iframe 沙箱里
>   原生 `confirm()` 恒返回 false，与 xbdoc 的页内确认框是同一类问题，这里
>   取更轻的实现。
> - **表情映射表编辑器：不做**。`face/data.py` 的表由
>   `aidoc/tools/gen_face_table.py` 从上游抓取生成，**禁止手改**（红线：
>   凭记忆改表比不改更糟），提供编辑器等于提供一个编错的入口。
> - **深浅色默认跟随系统**：`ui_theme_mode` 默认 `""`（= `prefers-color-scheme`），
>   与 xbdoc / xbimg 一致；手动切换后若结果与系统一致就写回 `""`，
>   避免留下一个和系统一样的硬锁。
> - 后续要加页签，先补本表再写代码。

**条件显隐**：`schema[key].condition` 未满足时，控件 `disabled + opacity .45 +
pointer-events:none`（如关掉 `enable_face_translate` 后 `face_format` 变灰）。

## 6. 硬性工程要求（红线）

1. **零 CDN、零框架**：纯原生 DOM + 内联 SVG（三插件全部如此）。
2. 改完 HTML/JS 必跑 `node --check`（每个 js 文件）。
3. 版本锚点两处：`index.html` 的 `data-ver` + 所有 `?v=` 缓存号，随版本同步。
4. iframe 沙箱禁 localStorage/原生弹窗 → **UI 偏好存服务端配置键** `ui_theme_mode` /
   `ui_accent_color`（已加进 `_conf_schema.json`，由本插件自己的 `POST setting`
   维护、经 `save_config*()` 落盘），页面不留本地副本。
5. **主题不走 bridge `getContext().isDark`**：首版用服务端键，理由是 bridge 的
   `ready()/onContext()` 在母版里也没有统一用法，而服务端键同时能在 AstrBot
   配置页看到、可备份，且与 xbdoc 的 `ui_theme_mode` 完全同构。若后续要做
   "跟随 AstrBot 全局主题"，再加 `bridge.onContext` 监听，两个来源按
   "用户手动切换过就以本地为准" 合并。
6. **功能不做参考插件的原有功能**（文档记忆/转图/游戏），只借壳。

## 7. 验收清单（已验证项打 ✅）

- [x] 亮/暗主题可切换，无首帧闪烁（`data-boot` + 3s 兜底；实测 `visibility` 揭开）
- [x] 调色盘可改强调色且持久化（走服务端 `ui_accent_color`）
- [x] 所有开关/分段/输入点立即保存，**失败回滚并 toast 提示**（E2E 实测见 §8）
- [x] 条件显隐生效（关 `enable_face_translate` → `face_format` 变灰）
- [x] 窄屏（<640px）卡片单列、页签折行（toast 与档案行都居中/竖排）
- [x] `node --check` 全绿 + `metadata.yaml` `pages:` 已声明
- [x] 档案删除有页内两步确认（按钮变「确认删除」，3s 超时还原）—— 沙箱里原生
      `confirm()` 恒返回 false，与 xbdoc 的页内确认框同类问题，取更轻的实现
- [x] 右下角通知与母版同款（图标 / 去重 / 自适应时长 / 点击复制 / aria-live）
- [x] 计算样式与母版逐项一致（见 §8 实测值）
- [ ] 主题色默认值与 xbdoc / xbimg 再对比一次（真机首轮用户反馈「颜色深了一些」，
      四者的 `--m3-sys-color-primary` 已逐行比对为完全一致，待复核）

## 8. 本机验证记录（**未做真机回归**，AstrBot 本体在云端）

方法：`python -m http.server` 起静态站 + 临时 Flask 式 mock 后端复用**真实的**
`xbnext.web.build_state / handle_setting`，浏览器端跑完整链路。

**计算样式比对母版（全部一致）**：

| 项 | 实测 | 母版 |
| :--- | :--- | :--- |
| `.app-layout` max-width | 1320px | 1320px |
| `.top-bar` / `.m3-card` radius | 28px | 28px |
| `.cat-tab.active` bg / color | `#D9E8F7` / `#0F2B46` | 同 |
| `.m3-switch` / `.switch-slider` | 54px / 999px | 同 |
| `.m3-segmented-control` radius | 999px | 同 |
| `.m3-icon-btn` | 40px | 40px |
| `body` font / padding | `"Google Sans"` / 24px | 同 |
| `.main-content-wrap` | 960px | 960px |
| 暗色 `body` / 卡片 | `#111418` / `#232A33` | 同 |

**链路验证**：
- 加载：前缀探测命中 `/api/plugins/astrbot_plugin_xbnext/`，`channel=http`，
  版本/KV/开关/分段/输入全部正确回填；
- 前缀探空：静态站上 5 个前缀逐个 404 → 最终报"所有接口前缀均未命中"（日志可证）；
- 写入：开关 → `config.debug_log=true`；分段 → `quote_placeholder_action="strip"`；
- **失败回滚**：把 `setting` 改成 reject 后再点开关 → 前端回滚到原值 +
  `.m3-toast.badk`「保存失败：…」，分段同样回到旧选项。

**仍待真机确认**：`register_web_api` 的真实路由前缀、`AstrBotConfig.save_config*`
是否可用（mock 里是假的）、bridge 的 `apiGet/apiPost` 实际签名。
