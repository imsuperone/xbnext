# 调研证据 · AstrBot 核心源码

> 采源：`github.com/AstrBotDevs/AstrBot` master 分支（2026-10-06 抓取 raw 文件）。
> 用途：实现 R1/R2/R3 时核对上游行为；上游变更时先回这里对账。
> 本机未能克隆 core（GitHub 直连不稳），证据来自 raw.githubusercontent 抓取的片段。

---

## 1. 消息链与 prompt 的关系（R3 关键）

### 1.1 aiocqhttp 适配器的 message_str 组装

文件：`astrbot/core/platform/sources/aiocqhttp/aiocqhttp_platform_adapter.py`
函数：`_convert_handle_message_event()`（L231 起 `message_str = ""`，L422 赋值）

按段类型 `itertools.groupby` 分派，**只有三类进 message_str**：

| 段类型 | 进 abm.message（消息链） | 进 message_str | 备注 |
| :---: | :---: | :---: | :--- |
| `text` | ✅ | ✅ | `message_str += current_text`（L250） |
| `at` | ✅ | ⚠️ | @别人进（L397），**第一个 @bot 不进**（L385-388） |
| `markdown` | ✅ | ✅ | L404 |
| `face` | ✅ | ❌ | 走 else 分支（L411-420），只 append 链 |
| `mface` | ❌ | ❌ | **`continue` 直接丢弃**（L398-399） |
| `reply` | ✅（展开成 Reply） | ❌ | 调 `get_msg` 回取原文构造 Reply（L303-345） |
| `image/file/video` | ✅ | ❌ | — |

Reply 组件构造（L327-340）：

```python
reply_seg = Reply(
    id=abm_reply.message_id,
    chain=abm_reply.message,
    sender_id=abm_reply.sender.user_id,
    sender_nickname=abm_reply.sender.nickname,
    time=abm_reply.timestamp,
    message_str=abm_reply.message_str,
    text=..., qq=...   # 兼容字段
)
```

→ **Reply 自带 sender_id/sender_nickname**（R1 回复指向可直接用，不必只靠文本 hash）。
→ 若 `get_msg` 失败，退化为只带 `id` 的裸 Reply（L341-344），此时链为空。

### 1.2 prompt 只取 message_str

文件：`astrbot/core/astr_main_agent.py`

```
L1491: req.prompt = event.message_str
L1492-1495: provider_wake_prefix 时剥前缀
L1482: 某分支 req.prompt = ""
L1790: 附件分支 req.prompt = "<attachment>"
```

**结论**：Face/mface 不在 message_str → 模型看不见（R3 根因）。

### 1.3 Face 组件定义

`astrbot/core/message/components.py` L127-129：

```python
class Face(BaseMessageComponent):
    type: ComponentType = ComponentType.Face
    id: int
```

只有 `id`，**没有文本含义字段**（含义需自己建 ID 表）。
`ComponentTypes` 映射表 L928：`"face": Face`。

---

## 2. 引用消息处理链（R2 关键）

### 2.1 `_process_quote_message`

文件：`astrbot/core/astr_main_agent.py` L866-959

```
L893-899: 找第一个 Reply 组件，没有就 return
L903:     sender_info = f"({quote.sender_nickname}): "  ← 有昵称就带上
L904-912: message_str = await extract_quoted_message_text(...) or quote.message_str or "[Empty Text]"
L913:     content_parts.append(f"{sender_info}{message_str}")
L915-954: 有 image_ref 且主模型不支持图且配了转述模型 → 文字转述
          成功则 append "[Image Caption in quoted message]: ..."
L956-958: quoted_text = f"<Quoted Message>\n{quoted_content}\n</Quoted Message>"
          req.extra_user_content_parts.append(TextPart(text=quoted_text))
```

**R2 占位符来源**：`[Empty Text]`（L911）、`[Image Caption ...]` 失败时无 caption。

### 2.2 引用附件处理（主构建流程）

同文件 L1550-1663：

```
L1551-1553: 收集所有 Reply 组件
L1559-1576: 遍历 comp.chain：
    Image → has_embedded_image = True   ← ⚠️ 在 try 之前置位！
           try: image_path = convert_to_file_path()
           except: append "[Image unavailable]" ; continue
           req.image_urls.append(image_path)
    Record → 失败 append "[Voice unavailable]"
    File  → append "[File Attachment in quoted message: name ..., path ...]"
    Video → _append_video_attachment(quoted=True)
L1618-1663: if not has_embedded_image:   ← 图挂了也进不了这个兜底
            fallback = await extract_quoted_message_images(...)
            （受 config.max_quoted_fallback_images=20 限制）
            req.image_urls.append(image_ref)   ← 不校验存在性
```

**R2 根因证据**：
- `[Image unavailable]` / `[Voice unavailable]` 注入点（L1573-1575, L1598-1600）；
- `has_embedded_image` 提前置位 → 失败后兜底提取失效；
- `req.image_urls` 追加不验证文件存在（L1653）；
- 引用文件注入 `[File Attachment in quoted message: ...]`（L1610-1612），
  path 拿不到时也会有类似空白语义。

### 2.3 链解析占位符

文件：`astrbot/core/utils/quoted_message/chain_parser.py`

```
_extract_text_from_component_chain():
    Image → "[Image]"     Video → "[Video]"     File → "[File:{name}]"
    Forward → "[Forward Message]"    At → "@{name}"    AtAll → "@all"
    Reply → 递归展开

_FORWARD_PLACEHOLDER_PATTERN（L20-24）：
    匹配 "xxx: [forward message]" / "[转发消息]" / "[合并转发]"
_is_forward_placeholder_only_text()：整段文本全是此类占位 → True
```

→ core 自己也知道"占位符污染引用文本"是问题（有专门的识别函数）。

### 2.4 图片准备入口

`astrbot/core/utils/image_input.py`：单函数 `prepare_request_images()`（L18 起），
被 `astr_main_agent.py`（L1754, L1945）与 `internal.py`（L309）调用。
AstrNa 的 quoted_image_input 就是包装这个函数（详见 astrna.md）。

### 2.5 预处理阶段对 Reply 内嵌媒体的规整

`astrbot/core/pipeline/preprocess_stage/stage.py`：

```
_normalize_image_component(): 下载/校验图 → 失败 raise
主链图片：try/except 记 warning 继续（L150-161 附近）
Reply 链内 Image/Record：同样规整（"Also normalize media components inside Reply chains"）
失败仅 logger.warning，不阻断 → 组件保持原样（可能仍是死引用）
STT：Record → Plain，成功后 `event.message_str += plain_comp.text`
```

→ 预处理失败的图会**带着死引用继续往下走**（R2 过滤的必要性）。

---

## 3. 身份注入与群上下文（R1 关键）

### 3.1 内置身份注入

`astrbot/core/astr_main_agent.py` `_append_system_reminders()` L962-1001：

```python
if cfg.get("identifier"):
    system_parts.append(f"User ID: {user_id}, Nickname: {user_nickname}")
if cfg.get("group_name_display") and group_id:
    system_parts.append(f"Group name: {group_name}")
if cfg.get("datetime_system_prompt"):
    system_parts.append(f"Current datetime: {current_time}, Weekday: {weekday}")
if system_parts:
    system_content = "<system_reminder>" + "\n".join(system_parts) + "</system_reminder>"
    # → req.extra_user_content_parts
```

**只描述当前发言人，无"上一条 bot 回复回给谁"信息**（R1 根因①）。
AstrNa 的 identity_metadata 正是替换这个块（识别 `User ID:` + `Nickname:` 同行）。

### 3.2 群聊上下文块

文件：`astrbot/builtin_stars/astrbot/group_chat_context.py`（336 行）

```
L22-27:  GROUP_HISTORY_HEADER = "<system_reminder>You are in a group chat.
         Belows are group chat context after your last reply:--- BEGIN CONTEXT---"
         FOOTER = "--- END CONTEXT ---</system_reminder>"
L199-272: _format_message() → "[{nickname}/{HH:MM:SS}]: 内容"
    Plain → 原文
    Image → " [Image]" 或 " [Image: {caption}]"
    At → " [At: {name}]"，@bot 前插 "⚠️[DIRECTED AT YOU] "
    Reply → " [Quote({sender_nickname}: {截断文本})]" 或 " [Quote]"
    Json → " [Shared Card{...}]"
    ⚠️ Face/mface/Record/Video/File：_format_message 里没有分支 → 静默丢失
L261-270: Quote 无 message_str 但有 chain → _describe_chain()
L278-305: _describe_chain(): Face → "[Sticker: {id}]"（纯数字，模型看不懂）
L323-331: _trim_left：超 group_message_max_cnt 从左淘汰
L334-336: _format_group_history_block() = HEADER + records + FOOTER
L162-197: on_req_llm()：注入"上次回复之前"的记录（records_to_inject），
           注入后清空已注入部分
```

**R1 根因②**：平铺 `[昵称/时间]`，无角色绑定、无"这是 bot 回给谁的"。
**R3 补充**：Face 在群上下文里也丢了（`_format_message` 无 Face 分支）。

### 3.3 处理时序（插件钩子能插哪）

`astrbot/core/pipeline/process_stage/stage.py`：

```
activated_handlers 存在 → star_request_sub_stage（插件指令/handler）
    handler 返回 ProviderRequest → set_extra("provider_request") → agent_sub_stage
之后：provider enable + is_at_or_wake_command → agent_sub_stage
```

`agent_sub_stages/internal.py`：`InternalAgentSubStage.process()` 组装请求：
`has_provider_request` / `has_valid_message(message_str)` / `has_media_content` /
`has_reply` 四者全无才跳过 → **光有 Reply 也会触发 LLM**（与 R2 相关）。

**on_llm_request 钩子在请求发出前触发**（AstrBot 官方文档），可改 `req`。
AstrNa 用 `priority=1000`；XBNEXT 用更低数值（数值小=靠前？需 P1 实测确认方向，
AstrNa 文档说"先于其他执行"对应 priority 更小/更大要实测）。

---

## 4. 存储 API

`docs/zh/dev/star/guides/storage.md`：

```python
# KV（>= 4.9.2，插件维度独立空间）
await self.put_kv_data(key, value)
value = await self.get_kv_data(key, default)
await self.delete_kv_data(key)

# 大文件
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
plugin_data_path = Path(get_astrbot_data_path()) / "plugin_data" / self.name
```

AstrNa 的用法：`Star.__init__` 传 `kv_store=self`（Star 自身就是 KV 代理）。

---

## 5. 官方开发红线（原文摘录）

来自 `docs/zh/dev/star/plugin.md`"原则"节（对 LLM 有强制标注）：

1. 功能需经过测试
2. 需包含良好的注释
3. 持久化数据存 `data` 目录，非插件自身目录
4. 良好错误处理，不要让插件因一个错误而崩溃
5. 提交前用 ruff 格式化
6. 禁 `requests`，用 `aiohttp`/`httpx`
7. 扩展现有插件优先提 PR 而非另开插件
8. 借鉴设计/创意要在 README 注明来源并附链接
9. 使用他人代码/资源须遵守原协议并保留版权声明

> ⚠️ 原则 8/9 对 XBNEXT 有约束：**README 必须注明借鉴 AstrNa 与三个参考插件**，
> 并链接它们的仓库。

---

## 6. 插件 Pages / Bridge（R5 关键）

来自 `docs.astrbot.app/dev/star/guides/plugin-pages.html`：

- `pages/<page_name>/index.html` 被 Dashboard 以受限 iframe 加载；
  bridge SDK 由服务端自动注入（`/api/plugin/page/bridge-sdk.js`）。
- `bridge.ready()` → context（含 `isDark`、`locale`、`pluginName`…）；
  `apiGet/apiPost/upload/download/subscribeSSE(endpoint, …)`，
  endpoint 为插件内相对路径（不带插件名前缀、不带 `/` 开头、不拼 query）。
- 返回值约定：`{"status":"ok","data":v}` → resolve `v`；`{"status":"error"}` → reject。
  **推荐直接返回业务 JSON**。
- 主题：SDK 维护 `<html data-theme>`；CSS 变量响应。
- 静态资源相对路径即可（服务端重写 + asset_token，勿手动拼）。
- 安全：iframe 不能碰 Dashboard cookie/localStorage/父 DOM；
  handler 必须自行校验输入，文件落盘用安全目录 + 白名单改名。
