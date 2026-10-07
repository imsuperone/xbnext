# Changelog

本项目遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## v0.1.1

v0.1.0 发布当日的自检加固：无功能增减，只修隐患与文案。

### Fixed

- **注入记录并发写丢条目**：两轮 LLM 请求同时收尾时 `record()` 的
  读-改-写竞态会互相覆盖（真实 KV 文件写有让出窗口）→ 加模块级
  `asyncio.Lock` 串行化，并用「每次读写都让出事件循环」的假 KV
  写并发回归测试看护
- **注入记录渲染对脏数据不设防**：`actions` / `parts` 不是数组会让
  整块渲染抛错变成「加载失败」→ `Array.isArray` 守卫；`ts` 缺失时
  不再显示 1970 年时间
- **空注入段被记成 `<TextPart>` 占位**：`part_text` 对空文本返回
  空串由 `build_entry` 跳过，记录里不再出现无意义占位
- **`face/updater.py` 硬编码 UA 版本字面量**（违反 aidoc/03「禁止在
  Python 代码里硬编码版本字面量」）→ 改读 `xbnext.__version__`

### Changed

- 注入记录轮次文案：列表是新在前，原「第 1 轮」实际是最新一条、
  易误读 → 改「最新一轮 / N 轮前」，卡片说明补「（新在前）」
- 版本同步看护扩到第四处 `pages/manager/index.html`（`data-ver`
  静态位 + `?v=` 缓存戳），`aidoc/03` 规则同步更新，由
  `test_structure::TestVersionSync` 机械看护

**自检**：单测 475 → 478；`v0.1.1` tag 随本版打上。

## v0.1.0

首个版本：R1~R5 全部落地，控制台 WebUI 就绪。

### Added

- **R3 · QQ 表情翻译**（`enable_face_translate`，默认开）：消息链与 `raw_message`
  双来源抽 token，支持 JSON list / 单 dict / JSON 字符串 / CQ 码 / 嵌套 `message` 字段，
  `mface` 走段自带 `summary`，正文已有片段不重复注入；
  `face/data.py` 由 `aidoc/tools/gen_face_table.py` 从上游抓取生成的 **594 条权威表**
  （**禁止手改**）
- **R3 · 表情表自动更新**（`face_auto_update`，默认开 + `face_update_time`，
  默认 `04:30`）：启动后约 1 分钟先拉一次（KV 里从未更新过时），之后每天定点从
  QFace `_index.json` 拉"权威源 − 内置表"的缺口补进插件 KV `xbnext:face_overlay`；
  查表**内置表优先、overlay 只补缺**，有新增打一条 INFO、无变化 DEBUG、
  失败 WARNING（不影响表情翻译本身）；纯逻辑在 `face/updater.py`，
  网络走 `asyncio.to_thread(urllib)` 不阻塞事件循环
- **R2 · 引用占位清洗**（`enable_quote_clean`，默认开）：只删**可证实不存在**的
  本地路径与 `file://`（`quote_drop_dead_images`，默认开），未知 scheme / data URI
  一律保留；判定函数抛异常时保守保留，不做猜测式删除
- **R1 · 回复指向**（`enable_reply_attribution`，默认关）：`on_message_sent` 落库
  （目标 = 当前发言人，另存触发 `message_id` + 文本 sha256），`on_llm_request` 注入
  三方中文说明（当前发言人 / 被引用者 / 被 @ 对象 / bot 最近 N 次回复对象），
  身份字段全部过 `sanitize`
- **R4 · 用户档案**（`enable_user_profile`，默认关）：`/xbnext profile` 指令
  （查看 / 设置 / 删单字段 / 清空，字段白名单 + `merge` 保不抹其它字段；
  分隔符空格 / 冒号 / 等号可混写），`on_llm_request` 按当前发言人注入；
  **档案与开关解耦**，开关只决定喂不喂给模型。
  **档案按群隔离**：键 `xbnext:profile:<platform>:<gid>:<uid>`（群聊），
  私聊与升级前的旧数据保持老键形 `<platform>:<uid>`（零迁移），
  读写只精确命中本 scope、不跨群回退；回执抬头标范围（`群 123456` / `私聊`），
  群里没有档案但有旧数据时补一行迁移提示。另有 WebUI「用户档案」页签
  （管理视角，**按「平台 · 群」分组分类**展示，编辑可改群号挪群）：
  `GET /profiles` 列出全部、`POST /profile_save` 写（字段整体替换，
  全空即删，带 `group` / `prev_group`）、`POST /profile_delete` 删，
  列表能力靠自建索引键 `xbnext:profile:index` 维护（AstrBot 插件 KV 没有按键遍历，
  记号 `platform|gid|uid`，旧记号 `platform|uid` 照常解析）
- **R5 · WebUI**（`pages/manager/`）：M3 Expressive 令牌、`.app-layout` /
  `.top-bar` / `.category-tabs-bar` / `.m3-card` / `.m3-switch`、取色盘、
  `data-boot` 防闪、`?v=` 缓存号；**5 个页签**（功能开关 / 行为微调 / 用户档案 /
  运行状态 / 使用指南）；后端 `GET /state` 一次性回填、
  `POST /setting` 键白名单 + schema 转型 + 落盘 + 失败回滚。
  右下角通知与母版同款（类型图标 / 1.2s 去重 / 长文本自适应时长 / 点击复制 /
  上限 4 条 / `aria-live`）；深浅色**默认跟随系统**（`ui_theme_mode` 留空），
  主题色在写入成功与回滚两条路径都会重新套用
- **撤回取消请求**（`enable_recall_cancel`，默认开）：撤回触发消息后取消这条
  消息的在飞 LLM 请求 —— 早期钩子登记 `message_id → pipeline 任务`
  （`add_done_callback` 自动摘表，登记表天然有界），撤回 notice（aiocqhttp
  转成 `message_str=""` 的 dict 事件）查表 `task.cancel()`；`CancelledError`
  是 `BaseException`，沿途 `except Exception` 全拦不住，直穿
  `scheduler.execute` 到 EventBus（`task.cancelled()` 静默 return），
  **不弹报错、不发错误回执**；只掐请求本身，已发出的回复不跟着撤
- **提示词注入记录**（用户拍板「运行状态最下面加入口」）：每轮请求收尾把
  「清洗后 prompt + parts 注入段 + 动作汇总 + 图片张数」存插件 KV
  `xbnext:inject_log`（最近 **10 轮**、新在前，截断 正文 8000 / 段 4000 /
  24 段，重启不丢）；WebUI「运行状态」页签底部新增「提示词注入记录」卡片，
  `GET /inject_log` 拉取、`textContent` 渲染（模型输出绝不 innerHTML）
- 插件入口 `main.py`（薄壳，仅转发钩子）+ `metadata.yaml` + `_conf_schema.json`
- `xbnext/` 运行时包：`runtime` 调度中心、`context` 请求上下文、
  `injector` 统一注入出口、`switches` 开关对账与 WebUI 配置写入、
  `storage` 插件 KV 封装、`config` 配置读取、`commands` 指令文本解析
- 功能注册表 `xbnext/features/`：一功能一目录，新增功能只需改一个文件
- 单测 `tests/`（**475 条**，标准库 unittest，不依赖 astrbot）

### Fixed

- **纯表情消息（含 `@bot + 表情`）完全不回复** —— 真机第二轮定性「表情是无效的」。
  第二层根因在 core：`internal.py` L183-199 用 `event.message_str` 判
  `has_valid_message`，而 aiocqhttp 把 `face` 排除在 `message_str` 外、`mface`
  段直接 `continue` 丢弃、@首个到自己的 `At` 也不进正文 ⇒ `message_str == ""`
  且表情不算 `has_media_content` ⇒ **`skip llm request: empty message`，
  `on_llm_request` 钩子根本不会执行**。
  改为在 core 判定**之前**加早期钩子 `@filter.event_message_type(ALL,
  priority=1000)` → `runtime.handle_adapter_message` →
  `FaceFeature.on_adapter_message` 把翻译补写回 `event.message_str`。
  三重守卫：正文为空 && `is_at_or_wake_command` && 抽得到表情 token ——
  没被 @ 的纯表情仍然不开口（不是 bug）
- **同人引用自己仍被写成「以上是不同的人」**：`service.build_hint` 原来
  对任何"有引用/有@/有历史"都套同一段 `TAIL`。改为
  `key_of()`（`id:` / `name:` 双命名空间）判同人，三档文案
  `TAIL_SAME`（全同）/ `TAIL_SELF_QUOTED`（引用自己但还有别人，被引用者行尾
  挂 `（即当前发言人本人）`）/ `TAIL`（异人）；**身份未知一律回落 `TAIL`**
- **注入日志看不到、且一开调试就乱**：`handle_llm_request` 原来只在
  `debug_log` 下逐条打 notes。改为**有动作才打一条 INFO 汇总**，
  形如 `[XBNEXT] 本轮 引用占位清洗·改写正文、用户档案·注入1段`
  （动作标签：`注入N段` / `改写正文` / `清洗N块` / `清图a→b`），没动作一行不打；
  纯表情补写另打 `[XBNEXT] 纯表情补写正文：[表情:得意]`；
  逐条 notes 仍然只在 `debug_log` 打开时落 DEBUG
- **热装插件拿不到 `on_astrbot_loaded`**：该事件只在 AstrBot 核心启动收尾广播
  一次，启动之后才装上/热更新的插件永远收不到，表现为 WebUI 一直显示「未加载」、
  `/xbnext profile` 回「档案存储还没就绪」、日志里一条 `[XBNEXT]` 都没有。
  现改为 `runtime.on_loaded()` 幂等 + 新增 `ensure_loaded()`，在
  `handle_llm_request` / `handle_adapter_message` / `handle_message_sent` /
  `handle_command` 与 `state` / 档案三端点全部兜底调用
- **主题色要手动点取色器才生效**：`applyTheme()` 里对
  `CONFIG.ui_accent_color` 的非空判断会让默认态下换主题丢掉派生色；改为
  无条件 `applyAccent()`，并在 `writeConfig()` 的写入成功与回滚两条路径都补
  `applyUiPref(key)`，保证「页面上看到的 = 实际存下来的」
- **`/xbnext` 系列回复乱**（真机第四轮反馈）：5 处病灶一次修掉 ——
  ① 状态行 `**关闭**` 的 markdown 星号在 QQ 纯文本里原样显示 → 去掉；
  ② 查看回执的帮助块用空格做列对齐（非等宽字体必乱）→ 改单行式用法；
  ③ `extra` 与帮助块之间缺空行 → 补；
  ④ `handle_command` 异常兜底把 `{exc!r}` 原样回给用户 → 改友好文案，
  repr 只进日志；
  ⑤ 设置回执不再重复整段"维护方式"帮助块
- **裸 `/xbnext` 的回执丑**（真机第五轮反馈）：从 `command_group` 改成
  **单指令分发**（xbdoc / xbimg 同款形态）—— `main.py` 只挂
  `@filter.command("xbnext")`，`runtime.dispatch` 统一分发：菜单 / 状态 /
  档案 / 未知提示全部由插件自己回。根因是指令组形态下裸指令会被新版 core
  抢答成「参数不足 + 指令树」技术树、旧版会把打错的子指令漏给 LLM；
  菜单换成 xbdoc 分节 + xbimg 分隔线的精美排版（`commands.MENU`），
  未知子指令回一句「❓ 未知子指令」提示（不再漏给 LLM 乱答）
- **单引用+@无正文时模型仍盯着「图、附件」说事**（真机第六轮反馈）：
  从 core 源码（`astr_main_agent.py`）查实 —— 引用块
  `<Quoted Message>…</Quoted Message>`、`[Image unavailable]` /
  `[File Attachment in quoted message: name X, path …]` 等标记全部
  append 进 `req.extra_user_content_parts`，`req.prompt` 只是
  `event.message_str`，而 R2 只清 prompt；单引用 + @ 且无正文时 prompt
  几乎为空，**这些块是模型唯一看到的内容**。修法三层：
  ① `service.clean_parts()` 与 `clean_prompt` 同规则**就地**清洗内容块
  （未命中的他方 part 对象原样保留、列表原地改保引用；改写后变空的噪声块
  剔除）；② 附件标记中文归因改写 `[File Attachment in quoted message:
  name X, path …]` → `［引用消息中的文件附件：X］`（补 `Voice unavailable`），
  引用块被删空补 `（此消息没有文字内容）` 不留半截空壳；
  ③ `QuoteFeature` 去掉「prompt 为空直接 return」的早退（那正是本场景），
  汇总行新增第四动作标签 `清洗N块`（`ctx.parts_cleaned`）。
  红线措辞同步为「本轮请求面 `req.prompt` / `req.image_urls` /
  `req.extra_user_content_parts`」，`req.system_prompt` /
  `req.contexts` / `conversation.history` 仍然不碰
- **`/xbnext profile` 设置逻辑乱**（真机反馈「设置逻辑有点乱」）：
  原来两种写法（`字段 值` / `字段=值`）不能混、空格写法一次只能改一条
  （`称呼 小明 自述 学生` 会把后面全吞进"称呼"的值）、`称呼:小明` 冒号写法
  直接报错、`口吻 a=b` 值里带等号被误拆。`classify` 重写成单遍宽容扫描：
  ① 空格 / `:` / `：` / `=` 四种分隔可混写（只有「字段名+分隔符」前缀才切，
  白名单外的 `a=b` 当普通词）；② 裸字段名开新字段 → 一次改多条；
  ③ 分隔符留空（`称呼:`）= 删单个字段（`merge` 本就支持 pop，原来被拦着），
  裸字段名没值仍报错防手滑误删并给正确示例；④ 查看词后面带多余内容
  直接当查看。回执同步瘦身：首行 `已更新：称呼=小明（群 111）` /
  `已删除：口吻（私聊）`（值超 20 字截断），范围标签只出现一次
  （原来 `· 群 111` 与 `你的档案（群 111）：` 重复两遍）；
  清空回执改「档案已清空（范围）。其它范围的档案不受影响。」；
  全字段删空走删除分支（`store.set` 对空值不落盘，不处理会误报"写入失败"）
- **引用 bot 自己的旧回复被当成"别人的话"**（真机第八轮反馈）：
  `build_hint` 缺「引用者 = bot 自己」分支 —— bot 自己的旧消息（core 把
  引用原文再塞一遍 + 旧消息本就在会话历史里）被归进 `TAIL`「以上是不同
  的人」，模型把自己发过的内容当成别人的话认真回应（评图、聊旧话题），
  冷落当前发言人的新消息。加第四档 `TAIL_BOT_QUOTED` + 被引用者行尾
  `（即你自己的旧回复）`：`self_id_of` 传入 `build_hint`，**只比 ID 不比
  昵称**（撞昵称不判），取不到 `self_id` 一律回落 `TAIL`（保守）
- **输出面清洗落点**（AstrNa 对照④）：模型偶尔把注入用的 `<xbnext>` 注入体
  原样复述出去 —— `strip_xbnext` 只在请求面有人调，**输出面此前没有清洗点**。
  新增 `injector.clean_output()`（整块删 + 兜底删零散标记，未闭合开标记吞到
  结尾防半截泄漏）+ `runtime.handle_decorating_result()`，由
  `@filter.on_decorating_result(priority=999999)` 发送前执行：**必须抢在
  消息转图插件 xbimg（99999）之前** —— core 按 `-priority` 降序执行，
  xbimg 跑完会把整段文本渲染成图片并丢弃所有 `Plain`，排它后面清洗就是
  空做（标记会被画进卡片图）。只认 `Plain`（类型名 + `text` 属性判定），
  图片卡片不碰；清完变空的组件从链里摘掉，不发空消息
- **回复指向索引的会话数无上限**（AstrNa 对照⑤）：原来只有每会话 200 条
  上限，`reply_targets:<umo>` 键随群聊增多无限涨（AstrBot 插件 KV 没有
  按键遍历，想清都不知道有谁）。新增 `reply_targets:index` 全局会话索引
  （活跃序，对标 AstrNa 的 300 取 **200**）：每次落库把会话提到最前，
  超限从尾部把**最久没活跃的会话连数据带索引一起删**；`clear` 同步摘
  索引，坏索引自愈、limit 写坏回落默认值（`touch_session`）
- **回复指向每会话默认记录数 200 → 50**（用户拍板「默认改50」）：
  schema `reply_history_limit` 默认值、hint、WebUI 行文案与代码
  fallback（`store.DEFAULT_LIMIT`）同步改 50；全局会话上限 200 不变
- **用户档案「口吻」字段删除**（用户拍板「可以删除，记得删除相关代码」）：
  字段白名单（`FIELDS` / `FIELD_LABELS` / 别名表）、渲染
  （`希望的相处方式：…`）、菜单 / README / WebUI 表单与摘要 / `web_save`
  payload 全部移除；旧数据里的 `style` 读取路径一律过滤、下次写入被
  `normalize` 顺手清掉；再发 `口吻 …` 走"未知字段"报错（测试看护断言）

### Notes

- **不与 AstrNa 做共存让路**：早期设计里的 `switches.detect_astrna()` /
  `runtime.conflict_for()` / WebUI 的「AstrNa 共存」卡片与 `state.astrna`
  字段已**整套移除**（AstrNa 未装时那张卡永远是 `—`，属假功能位）。
  两个插件同装时各自独立工作；AstrNa 仅作为路线参考保留在致谢里。见 `aidoc/02 §6`。
- **与 xbdoc / xbimg 共存**（真机第三轮反馈：「注入记得不要和 xbdoc 搞出冲突」）：
  core 按 priority **降序**执行（`sort(key=lambda h: -priority)`），
  本插件 `1000 > xbdoc 100/0 > xbimg 100` ⇒ **先清洗、后注入**；
  两者都 `append` 进 `extra_user_content_parts`，互相不清除；
  本插件**从不碰** `req.system_prompt` / `req.contexts`，`strip_xbnext`
  只剥 `<xbnext>` 标签，改不动 xbdoc 的 `【参考资料】` 块。
  上述约束由 `tests/test_coexist.py` 机械锁死。
- **真机回归（首轮）已跑通**：WebUI 打开 / 配置回填 / 改开关刷新保留、
  bridge 通道均正常；深浅色与主题色默认值仍待与 xbdoc / xbimg 再对比一次。
- **真机回归（第二轮）已跑通**：加载与注入正常、主题色 OK、R4 档案三步指令
  + 问答生效（bot 称呼 "OP"、记得"爱玩原神"）。
- **仍未做**：完整功能回归（R1~R4 触发用例 + 三个 WebUI 端点的真机往返 +
  与 xbdoc 同开的实机对照）。
- 发布包排除 `aidoc/ tests/ .git/ __pycache__/ *.pyc`，但 `aidoc/` 与 `tests/`
  **必须进 git**。
- 已 push 到 `origin/main`；`v0.1.0` tag 已在菜单三条真机回归通过后打上。
