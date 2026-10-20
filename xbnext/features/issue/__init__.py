# -*- coding: utf-8 -*-
"""用户问题提交与截图反馈功能。"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, List

from ...eventids import gid_of, name_of, platform_of, uid_of, umo_of
import logging
logger = logging.getLogger('astrbot_plugin_xbnext')
from ..base import Feature
from . import service


class IssueFeature(Feature):
    """用户问题提交与截图反馈功能。"""

    key: str = "enable_issue"
    name: str = "用户问题反馈"
    description: str = "提供 /xbnext issue 渠道供用户提交问题与截图，记录日志并自动私信管理员。"
    order: int = 96
    command: str = "issue"

    def __init__(self) -> None:
        self._runtime: Any = None

    async def on_load(self, runtime: Any) -> None:
        self._runtime = runtime

    async def handle_command(self, event: Any, args: List[str], conf: Any) -> str:
        """处理 ``/xbnext issue ...`` 指令。"""
        images = service.extract_images_from_event(event)
        text_content = " ".join(args).strip()

        if not text_content and not images:
            return (
                "📮【XBNEXT · 问题反馈渠道】\n"
                "━━━━━━━━━━━━━━\n"
                "💡 使用格式：\n"
                "• /xbnext issue <问题描述> [可直接在消息内附带截图]\n"
                "\n"
                "示例：\n"
                "• /xbnext issue 精灵商店有时候叫不出来（附带截图）\n"
                "• 引用报错消息并发送：/xbnext issue 出现该异常\n"
                "━━━━━━━━━━━━━━\n"
                "提交后系统将记录时间与日志，并自动私信通知管理员跟进处理！"
            )

        if not text_content and images:
            text_content = "（用户仅附带了截图，未填写详细文字描述）"

        uid = uid_of(event) or "未知用户"
        uname = name_of(event) or ""
        gid = gid_of(event) or ""
        platform = platform_of(event) or "aiocqhttp"

        # 尝试匹配群昵称
        gname = ""
        if gid and self._runtime is not None:
            seen_groups = getattr(self._runtime, "_seen_groups", {})
            if isinstance(seen_groups, dict) and gid in seen_groups:
                gname = str(seen_groups[gid].get("group_name") or "")

        now = datetime.now()
        time_str = now.strftime("%Y-%m-%d %H:%M:%S")
        issue_id = f"ISS-{now.strftime('%Y%m%d%H%M')}-{os.urandom(2).hex()}"

        issue_record = {
            "id": issue_id,
            "user_id": uid,
            "user_name": uname,
            "group_id": gid,
            "group_name": gname,
            "platform": platform,
            "time": time_str,
            "timestamp": int(now.timestamp()),
            "content": text_content,
            "images": images,
            "status": "pending",  # pending / resolved / closed
        }

        # 1. 记录日志
        logger.info(
            f"[XBNEXT] [ISSUE] 收到来自用户 {uid}{f'({uname})' if uname else ''} "
            f"在 {gname or (f'群 {gid}' if gid else '私聊')} 提交的问题: "
            f"{text_content} | 截图: {len(images)} 张 | ID: {issue_id}"
        )

        # 2. 持久化存盘
        issues = service.load_issues()
        issues.insert(0, issue_record)
        # 上限保留最近 500 条
        if len(issues) > 500:
            issues = issues[:500]
        service.save_issues(issues)

        # 3. 提取接收人配置
        raw_receivers = conf.list("issue_receivers") or []
        if isinstance(raw_receivers, str):
            raw_receivers = [r.strip() for r in raw_receivers.replace(",", " ").replace(";", " ").split() if r.strip()]
        receivers = [str(r).strip() for r in raw_receivers if str(r).strip().isdigit()]

        # 若未配置专门的问题接收人，则尝试兜底回退到更新通知人 QQ
        if not receivers:
            fb_qq = str(conf.text("update_notify_qq", "") or "").strip()
            if fb_qq.isdigit():
                receivers = [fb_qq]

        # 4. 私信通知管理员
        ctx = getattr(event, "context", None)
        if ctx is None and self._runtime is not None:
            ctx = getattr(self._runtime, "ctx", None)
        forward_cnt = await service.forward_issue_to_receivers(ctx, issue_record, receivers)

        # 5. 回复用户确认
        summary = text_content if len(text_content) <= 60 else (text_content[:57] + "...")
        notify_hint = (
            f"已自动私信提醒管理员（{forward_cnt} 位）查阅"
            if forward_cnt > 0
            else "已成功录入系统待办列表"
        )

        return (
            "✅【问题反馈已成功提交】\n"
            "━━━━━━━━━━━━━━\n"
            f"🆔 反馈编号：{issue_id}\n"
            f"📅 提交时间：{time_str}\n"
            f"📝 问题概要：{summary}\n"
            f"🖼️ 附带截图：{len(images)} 张\n"
            "━━━━━━━━━━━━━━\n"
            f"💡 {notify_hint}，感谢您的反馈！"
        )
