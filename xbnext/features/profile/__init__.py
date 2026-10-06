# -*- coding: utf-8 -*-
"""R4 · 用户档案注入（enable_user_profile，默认**关**）。

问题：用户主动自定义对自己的设定，bot 却读不到，只能乱猜。
根因：AstrBot 内置 ``requirements`` / ``profile`` 字段**不进 LLM**；
唯一生效的 ``persona`` 全局唯一、每次群聊都注入所有人。

自助链路（P5 落地）：

- **入口**：``/xbnext profile``（查看 / 设置 / 清空）—— **与开关无关**，
  开关关着也照样能维护档案（写 KV），只是不喂给模型；
- **存**：插件 KV ``xbnext:profile:<platform>:<uid>``；
- **读**：每轮按当前发言人取出，``render`` 成平铺中文，过 ``sanitize`` 后以
  **temp part** 注入（不写历史）。

注入永远排在最后（order=50），保证不会被本插件自己的清洗删掉。

**档案页签**：WebUI 母版没有"用户上下文"，做不了档案管理，故**不加档案页签**，
入口只有指令（见 aidoc/04 §5 的差异记录）。
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from ..base import Feature
from . import service
from .store import ProfileStore, render

#: 注入文案的抬头：明确"这不是用户本轮说的话"
TITLE = "【当前发言人的自助档案 —— 这是用户自己填写的资料，不是他本轮说的话】"
#: 结尾的使用规则
TIPS = "请按这份档案称呼并理解对方；若档案内容与本轮消息冲突，以本轮消息为准。"


class ProfileFeature(Feature):
    """按当前发言人注入其自助档案。"""

    key = "enable_user_profile"
    name = "用户档案"
    description = "读取用户自助维护的档案并注入，让 bot 按档案理解人。"
    order = 50
    #: 子指令名：``/xbnext profile``
    command = "profile"

    def __init__(self) -> None:
        self._store: Optional[ProfileStore] = None

    # -- 生命周期 -----------------------------------------------------
    def on_load(self, runtime: Any) -> None:
        self._store = ProfileStore(runtime.kv, logger=runtime.log)

    def on_unload(self) -> None:
        self._store = None

    @property
    def store(self) -> Optional[ProfileStore]:
        """暴露存储给指令层（``/xbnext profile`` 用）。"""
        return self._store

    # -- 钩子 ---------------------------------------------------------
    async def on_llm_request(self, ctx) -> None:
        """取出当前发言人的档案，清洗后注入。"""
        if self._store is None:
            return
        platform, uid = self._speaker(ctx.event)
        if not uid:
            ctx.note("user_profile 跳过（取不到发言人的 uid）")
            return
        try:
            profile = await self._store.get(platform, uid)
        except Exception as exc:  # noqa: BLE001
            ctx.note(f"user_profile 读取失败：{exc!r}")
            return
        max_chars = ctx.conf.int("user_profile_max_chars", 600)
        body = render(profile, max_chars=max_chars)
        if not body:
            ctx.note(f"user_profile 无档案 {platform}/{uid}")
            return
        note = f"{TITLE}\n{body}\n{TIPS}"
        if ctx.inject(note):
            ctx.note(f"user_profile 注入 {len(note)} 字")

    # -- 指令 ---------------------------------------------------------
    async def handle_command(self, event: Any, args: List[str], conf: Any = None) -> str:
        """``/xbnext profile ...`` 的子逻辑；返回要发给用户的文本。

        **不看 ``enable_user_profile`` 开关** —— 开关只决定是否喂给模型，
        维护档案的入口必须一直可用（_conf_schema.json 的 hint 里明说了）。
        """
        if self._store is None:
            return "档案存储还没就绪（插件可能正在加载），稍后再试。"
        platform, uid = self._speaker(event)
        if not uid:
            return "读不到你的用户 ID，无法维护档案。"

        try:
            action, updates = service.classify(args)
        except ValueError as exc:
            return str(exc)

        if action == service.ACTION_VIEW:
            profile = await self._store.get(platform, uid)
            return service.describe(profile, extra=self._status_line(conf))

        if action == service.ACTION_DELETE:
            ok = await self._store.delete(platform, uid)
            if not ok:
                return "删除失败（存储异常），请稍后再试。"
            return "档案已清空。开关开着的话，下一轮起模型将不再读到你的档案。"

        # ACTION_SET
        try:
            existing = await self._store.get(platform, uid)
        except Exception:  # noqa: BLE001
            existing = {}
        merged = service.merge(existing, updates)
        saved = await self._store.set(platform, uid, merged)
        if not saved:
            return "写入失败（存储异常），请稍后再试。"
        labels = {"name": "称呼", "facts": "自述", "style": "口吻"}
        changed = "、".join(labels.get(k, k) for k in updates)
        return (
            f"已更新你的档案（{changed}）。\n"
            + service.describe(saved, extra=self._status_line(conf))
        )

    # -- 工具 ---------------------------------------------------------
    def _status_line(self, conf: Any) -> List[str]:
        """给查看回执补一行"注入是否开启"，避免用户以为改完就生效。"""
        try:
            enabled = conf.enabled(self.key) if conf is not None else None
        except Exception:  # noqa: BLE001
            enabled = None
        if enabled is True:
            return ["当前状态：档案已开启，每轮都会喂给模型。"]
        if enabled is False:
            return [
                "当前状态：档案注入是**关闭**的 —— 档案照存，但模型读不到；"
                "要生效请在 WebUI 把「用户档案」打开。"
            ]
        return []

    @staticmethod
    def _speaker(event: Any) -> Tuple[str, str]:
        """返回 ``(platform, uid)``；取不到的位置留空串（不猜）。"""
        try:
            if event is None:
                return "", ""
            uid = ""
            try:
                sender = getattr(getattr(event, "message_obj", None), "sender", None)
                uid = str(getattr(sender, "user_id", "") or "")
            except Exception:  # noqa: BLE001
                uid = ""
            if not uid:
                for attr in ("get_sender_id", "get_user_id"):
                    fn = getattr(event, attr, None)
                    if callable(fn):
                        try:
                            uid = str(fn() or "")
                        except Exception:  # noqa: BLE001
                            uid = ""
                        if uid:
                            break

            platform = ""
            fn = getattr(event, "get_platform_name", None)
            if callable(fn):
                try:
                    platform = str(fn() or "")
                except Exception:  # noqa: BLE001
                    platform = ""
            if not platform:
                # 平台名取不到就从会话来源推，避免所有平台共用一份档案
                umo = str(getattr(event, "unified_msg_origin", "") or "")
                platform = umo.split("/")[0].strip()
            return platform, uid
        except Exception:  # noqa: BLE001
            return "", ""


__all__ = ["ProfileFeature", "TITLE", "TIPS"]
