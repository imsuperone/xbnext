# 调研证据 · AstrNa（v1.6.8）

> 仓库：`github.com/Sisyphbaous-DT-Project/astrbot_plugin_AstrNa`
> 本地克隆：`%TEMP%\astrna_ref`（2026-10-06）
> 定位：AstrBot 优化插件，21 个功能默认全关、运行时补丁式实现。
> 用途：XBNEXT 借鉴其机制、规避其教训；**不照抄代码**（README 需注明来源）。

---

## 1. 整体架构（可借鉴的骨架）

```
main.py（AstrNa Star，~320 行）
 ├─ __init__：new AstrNaRuntime + 保留共享 config + register_web_api
 ├─ 事件适配层：全部钩子 priority=1000，只转发到 runtime
 │    on_llm_request → runtime.sanitize_request（唯一主入口）
 │    on_astrbot_loaded / on_llm_response / on_agent_begin / on_agent_done
 │    on_decorating_result / after_message_sent / on_plugin_error
 ├─ 指令层：command_group "astrna"（issue 子命令组）
 ├─ Web API：/{插件名}/dashboard/{state,switch,setting}（3 条）
 └─ terminate() → runtime.terminate()
astrna/runtime.py（977 行）
 ├─ DEFAULT_CONFIG：功能清单（每功能一个 bool 键）
 ├─ __init__：构造全部模块 + 条件 install()
 ├─ sanitize_request()：每请求对账/编排（调度中心）
 ├─ update_dashboard_switch/setting：热开关
 └─ terminate：同步拆补丁链 → gather 排空在途任务
astrna/modules/*.py：每功能一个自治模块（install/terminate/restore_patch）
astrna/utils/patching.py：补丁栈基座
```

**要点**：
1. **一个 `on_llm_request` 做所有事**（清洗→补图→对账→注入，顺序固定、单点可测）
   —— XBNEXT `runtime.sanitize_request` 直接采用此模式。
2. **每请求对账开关**（`:399-610`）：每次请求重读配置，幂等 install/terminate，
   配置热改无需重启 —— 采纳。
3. **默认配置即注册表**，schema 键与 DEFAULT_CONFIG 键一一对应 —— 采纳。
4. **生命周期令牌** `_lifecycle_token` + weakref 防热重载串台（`:142-145, :900-904`）
   —— 采纳。

## 2. 补丁栈机制（AstrNa 核心技术，XBNEXT 慎用）

`utils/patching.py`：monkey-patch AstrBot 内部类/方法，多模块叠同一入口时
**"停用"= 把 wrapper 标记 inactive 让它直通原函数**，而不是拔层
（`mark_wrapper_active/inactive`、`same_callable` 校验、`unwrap_inactive_wrapper`）。

> **XBNEXT 立场**：尽量用官方钩子（on_llm_request 等），只在万不得已
> （如需要拦截 `_save_to_history`）才打补丁；要打就复用 AstrNa 这套
> active/inactive 协议，且必须 `same_callable` 确认后才还原。

## 3. R1 相关：reply_target_history.py（1524 行）

### 机制三件套

1. **KV 回复指向索引**（不写对话内容）
   - 键：`reply_target_history_state_v2`（`:26`）
   - 结构：`session_key -> [{hash: sha256(回复文本), metadata}]`
   - `session_key = umo#conversation.cid`（`:1319`）
   - `metadata = {scope: group/private, user:{user_id,nickname}, group:{group_id}}`（`:631-647`）
   - 上限：每会话 200 条、全局 300 会话（`:26-28`），`trim_reply_target_sessions`（`:1360`）
   - 写入点：拦截 `InternalAgentSubStage._save_to_history`，找最后一条可落库
     assistant 消息 → `remember_reply_target()`（`:507-538`）
2. **引用 bot 旧回复时注入三方中文说明**
   - 拦截 `astr_main_agent._process_quote_message`（`:430-485`）
   - `build_reply_direction_hint()`（`:660-701`）输出：
     "当前发言人 X / 被引用消息发送者 Y / 被引用回复原接收者 Z；
      这不代表当前发言人是 Y；你这次需要回复 X"
   - `create_temp_text_part + mark_part_as_temp`（`:1413-1454`），不入库
3. **三处清标记**（`<astrna_*>` 正则，`:29-33`）
   - 请求前 `sanitize_request`（`:186-231`）、落历史前（`:155-184`）、
     模型输出后 `sanitize_llm_response`（`:233-273`）
   - 清洗 clone 而非原地改（`clone_message/clone_text_part` `:1462-1477`，保 pydantic）

### 引用匹配策略（重要教训）

`find_quoted_reply_target_metadata`（`:540-561`）：
① KV hash 精确匹配 + `unique_metadata_matches` **要求唯一，歧义即放弃**；
② 回退历史遗留标记 `find_unique_reply_target_metadata`（`:1217-1246`）：
   assistant 正文完全相等优先；`len>=8` 包含匹配**仅唯一时采纳**。

### XML 标记已成遗留路径

`prepend_marker_to_message`/`inject_quoted_markers` 主流程已无调用（grep 只剩定义），
现行方案=纯临时说明。→ **XBNEXT 直接跳过标记方案**，省一层清洗负担。

### 兼容性限制（README 原文）

- QQ 官方 Bot 的引用消息**缺被引用者信息**，无法安全区分 → 不支持，NapCat/aiocqhttp 才行。
- `semantic_enabled` 开关每请求同步一次（`runtime.py:404-407`），关掉后只清标记不注入。

## 4. R2 相关：quoted_image_input.py（约 1494 行）

**没有像素级空白检测**。三类"可证实失败"：

| 判定 | 位置 | 口径 |
| :--- | :---: | :--- |
| 本地路径失效 | `is_usable_image_ref` `:1007-1017` | http/https/base64/data:image 可用；本地路径须 `os.path.exists` |
| 上游"已收图"标记≠真有图 | `:63`, `:59-62` 注释 | `[Image Attachment in quoted message:` 只证明经过收集 |
| 准备表判定 | `:793-804` | `prepared[path] is None` 才算失败；缺失/歧义返回哨兵不猜（`:826-844`） |

两层恢复：
- **层1**（有独立图片准备入口）：包装 `prepare_request_images` + `collect_initial_request`
  （`:137-269`），`recover_failed_quoted_images`（`:435-527`）用 OneBot
  `get_msg/get_image/get_file` 回取 → `_prepare_recovered_images`（`:529-613`）
  副本上重新准备、`replace_source_prepared_result` 让旧失败引用指向成功结果。
- **层2**（第三方自建请求）：`optimize(event, req)`（`:615-751`）在 on_llm_request：
  清死路径（`remove_invalid_current_reply_refs` `:1058`）→ `extract_quoted_message_images`
  → 分桶（`split_usable_image_refs` `:996-1004`）→ OneBot 兜底（`:1118-1133`）
  → 注入临时提示 `QUOTED_IMAGE_INPUT_NOTICE`（`:57`，"引用了 N 张图片已作为视觉输入"，不入库）。

边界（README）：只补当前引用、不恢复历史、不展开群友合并转发内部图、
不改 `req.prompt`/`req.contexts`/`conversation.history`、可能增加图 token 故默认关。

**跨事件隔离**：`request_image_state`（`:929`）校验 `event_ref() is event`；
每步检查 `_is_current(token)`（`:393`）与 `event_requests_stop(event)`。

## 5. R4 相关：identity_metadata.py（约 640 行）

- **替换而非凭空造**：`remove_builtin_identity_parts()`（`:529-567`）摘 AstrBot
  内置行（识别 `User ID:`+`Nickname:` 同现行、`Group name:` 开头行，限
  `<system_reminder>` 包裹的 part）；同时用 `is_astrna_identity_part`（`:613`）
  摘自己上轮注入防堆积；**没检测到内置身份就 return**（`:58-59`）。
- JSON 结构 `build_identity_metadata`（`:145-204`）：user（id/nickname/
  account_nickname/birthday）+ group（id/name/member{role,level,title}/owner/admins）。
- 字段卫生 `sanitize_metadata_value`（`:486-493`）：控字符→空格、去零宽、
  `<>` 全角、压空白、截断 128。`format_metadata_json`（`:501`）被 reply_target 复用。
- 注入（`:86-92`）：`<system_reminder>\nAstrNa identity metadata: {json}\n</system_reminder>`
  → `create_text_part().mark_as_temp()`（`:505-513`，兜底 FallbackTextPart `:629-638`）。
- 群主/管理员名单 24h TTL 缓存（`:117-142`，256 条上限，失败不写缓存）。
- 双轨：主动注入 JSON + `group_identity_tools.py` 4 个 `@llm_tool` 按需查
  （`install` 用 `context.add_llm_tools` 注册 `:52-80`）。

## 6. 配置 schema 模式（R5 配置页可复用）

`_conf_schema.json`（394 行）：

```json
主开关: {"type":"bool","description":"…","hint":"做什么/不做什么/依赖什么/默认关闭","default":false}
子项:   {"type":"bool","collapsed":true,
         "condition":{"父开关键": true}}        ← 多条件为 AND
扩展:   "_special":"select_provider|select_persona"、"options"+"labels" 多选、
        "invisible":true（只能 Dashboard 改）、"secret":true
```

## 7. Web API 注册模式

`main.py::_register_dashboard_apis`：

```python
register = getattr(self.context, "register_web_api", None)
if not callable(register) or astrbot_web is None: return   # 旧版容错
base = f"/{DASHBOARD_PLUGIN_NAME}/dashboard"
register(f"{base}/state",  self._webapi_dashboard_state,  ["GET"],  "…")
register(f"{base}/switch", self._webapi_dashboard_switch, ["POST"], "…")
# handler: astrbot_web.request.json(default={}) / json_response / error_response(status_code=400)
```

`metadata.yaml` 注意：AstrNa 的插件名是 `astrbot_plugin_AstrNa`（含大写），
Dashboard 路由用全名；**bridge endpoint 不带插件名前缀**（官方文档）。

## 8. AstrNa 没做的事（XBNEXT 的机会）

- ❌ QQ 表情/face/mface 处理（全仓 0 命中）→ **R3 全新**
- ❌ 用户自定义档案（只有平台客观身份）→ **R4 全新**
- ❌ 占位符文本翻译（`[Empty Text]` 等它不动，只处理图片路径层）→ R2 可差异化
- 它有但 XBNEXT 不做：合并转发拆包、输出字数限制、缓存清理、内置指令白名单、
  Issue 助手、并发工具、供应商会话头、群聊并发……

## 9. 冲突面（XBNEXT 必须处理）

| AstrNa 功能 | 与 XBNEXT |
| :--- | :--- |
| `optimize_reply_target_history` | 与 R1 同域 → 检测到开则 R1 让路 |
| `optimize_quoted_image_input` | 与 R2 互补（它恢复、我们清洗）→ 可共存，勿重复注入提示 |
| `optimize_identity_metadata` | 与 R4 独立 → 共存（它清内置身份行，我们注入档案） |
| `optimize_dynamic_system_prompt` | 它会接管 system_prompt 注入位 → 我们反正不动 system_prompt，无冲突 |

## 10. 教训清单

1. **大文件之殇**：runtime 977 行、单模块 40-84KB、测试 122KB —— 它们靠
   2.5 万行测试扛住。XBNEXT 规模小，模块必须更早拆分（<800 行/文件）。
2. **monkey-patch 脆弱**：曾出现"多个补丁共享同一方法时链式恢复"连环 bug
   （CHANGELOG L236）→ 能用官方钩子就别 patch。
3. **占位符/标记必须全链路清洗**：请求前、落库前、输出后三处，漏一处就污染历史。
4. **歧义即放弃**是身份匹配的唯一安全策略。
5. **临时内容协议统一**：所有"给模型看但不入库"的注入都是
   `extra_user_content_parts + mark_as_temp`。
