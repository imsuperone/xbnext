# -*- coding: utf-8 -*-
"""问题反馈服务层（存储、提取图片与向管理员推送私信）。"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ...eventids import gid_of, name_of, platform_of, uid_of, umo_of
import logging
logger = logging.getLogger('astrbot_plugin_xbnext')
from ..base import Feature
from ..updater import send_private_notification

_DATA_DIR = Path("data/plugin_data/astrbot_plugin_xbnext")
_ISSUES_FILE = _DATA_DIR / "issues.json"
_MEDIA_DIR = _DATA_DIR / "issues_media"

_IMG_URL_RE = re.compile(r"\[CQ:image,[^\]]*?(?:url|file)=([^\],]+)", re.IGNORECASE)


def _ensure_dirs() -> None:
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def load_issues() -> List[Dict[str, Any]]:
    """读取全部已记录的问题反馈列表（按时间降序）。"""
    _ensure_dirs()
    if not _ISSUES_FILE.is_file():
        return []
    try:
        with open(_ISSUES_FILE, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if isinstance(data, list):
            return data
    except Exception as exc:
        logger.warning(f"[XBNEXT] 读取 issues.json 异常: {exc!r}")
    return []


def save_issues(issues: List[Dict[str, Any]]) -> bool:
    """持久化保存问题反馈列表。"""
    _ensure_dirs()
    try:
        temp = _ISSUES_FILE.with_suffix(".tmp")
        with open(temp, "w", encoding="utf-8") as fp:
            json.dump(issues, fp, ensure_ascii=False, indent=2)
        temp.replace(_ISSUES_FILE)
        return True
    except Exception as exc:
        logger.warning(f"[XBNEXT] 写入 issues.json 异常: {exc!r}")
        return False


def _save_local_image_copy(src_path_or_url: str) -> str:
    """将图片本地缓存至 issues_media，防止临时文件被清理或外链失效。"""
    _ensure_dirs()
    try:
        src = str(src_path_or_url or "").strip()
        if not src:
            return ""
        # 本地文件
        p = Path(src.replace("file:///", "").replace("file://", ""))
        if p.is_file():
            suffix = p.suffix or ".png"
            dest_name = f"issue_{int(time.time()*1000)}_{os.urandom(3).hex()}{suffix}"
            dest = _MEDIA_DIR / dest_name
            shutil.copy2(p, dest)
            return f"/astrbot_plugin_xbnext/issue_media/{dest_name}"
        # http/https 外链保持原样（或记录外链）
        if src.startswith(("http://", "https://", "base64://", "/astrbot_plugin_xbnext/")):
            return src
        return src
    except Exception:
        return src_path_or_url


def extract_images_from_event(event: Any) -> List[str]:
    """从消息事件中提取所有图片路径或外链（兼容 AstrBot 组件链、CQ 码、回复消息）。"""
    images: List[str] = []
    seen = set()

    def _add(ref: str):
        ref_s = str(ref or "").strip()
        if ref_s and ref_s not in seen:
            seen.add(ref_s)
            saved = _save_local_image_copy(ref_s)
            images.append(saved)

    # 1. 从 event.message_obj.message 组件链提取
    mo = getattr(event, "message_obj", None)
    comps = getattr(mo, "message", None) or []
    for c in comps:
        cname = getattr(c, "__class__", type(c)).__name__
        if cname == "Image":
            for attr in ("file", "url", "path"):
                val = getattr(c, attr, None)
                if val:
                    _add(str(val))
                    break
        elif isinstance(c, dict) and c.get("type") == "image":
            data = c.get("data") or {}
            val = data.get("file") or data.get("url")
            if val:
                _add(str(val))

    # 2. 从 raw_message / message_str 的 CQ 码正则提取
    for text_attr in ("message_str", "raw_message"):
        raw_text = str(getattr(mo, text_attr, "") or getattr(event, text_attr, "") or "")
        if "[CQ:image" in raw_text:
            for m in _IMG_URL_RE.finditer(raw_text):
                _add(m.group(1))

    # 3. 检查是否引用了某条带图的消息（Reply 消息）
    try:
        reply_obj = getattr(mo, "reply", None) or getattr(event, "reply", None)
        if reply_obj is not None:
            r_comps = getattr(reply_obj, "message", None) or []
            for rc in r_comps:
                rcname = getattr(rc, "__class__", type(rc)).__name__
                if rcname == "Image":
                    for attr in ("file", "url", "path"):
                        val = getattr(rc, attr, None)
                        if val:
                            _add(str(val))
                            break
    except Exception:
        pass

    return images


async def forward_issue_to_receivers(
    context: Any,
    issue: Dict[str, Any],
    receivers: List[str],
) -> int:
    """向配置的接收人列表群发私信通知。"""
    if not receivers:
        return 0

    succ_cnt = 0
    now_str = issue.get("time") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user_disp = f"{issue.get('user_name', '')} ({issue.get('user_id', '')})" if issue.get("user_name") else str(issue.get("user_id", ""))
    grp_disp = issue.get("group_name") or (f"群 {issue.get('group_id')}" if issue.get("group_id") else "私聊会话")
    img_cnt = len(issue.get("images") or [])

    msg_lines = [
        "📮【XBNEXT · 用户问题反馈提醒】",
        "━━━━━━━━━━━━━━",
        f"🆔 反馈编号：{issue.get('id')}",
        f"📅 提交时间：{now_str}",
        f"👤 提交用户：{user_disp}",
        f"👥 来源会话：{grp_disp}",
        f"📝 反馈内容：\n{issue.get('content', '')}",
    ]
    if img_cnt > 0:
        msg_lines.append(f"🖼️ 附带截图：共 {img_cnt} 张（可在 WebUI 集中查看）")
    msg_lines.append("━━━━━━━━━━━━━━")
    msg_lines.append("💡 请登录 XBNEXT 控制台【问题反馈】面板查阅与处理。")

    notify_text = "\n".join(msg_lines)

    for recv_qq in receivers:
        recv_clean = str(recv_qq or "").strip()
        if not recv_clean.isdigit():
            continue
        try:
            ok, err = await send_private_notification(context, recv_clean, notify_text)
            if ok:
                succ_cnt += 1
                logger.info(f"[XBNEXT] [ISSUE] 已向管理员 QQ {recv_clean} 推送问题反馈通知")
            else:
                logger.warning(f"[XBNEXT] [ISSUE] 向管理员 QQ {recv_clean} 推送通知失败: {err}")
        except Exception as exc:
            logger.warning(f"[XBNEXT] [ISSUE] 向管理员 QQ {recv_clean} 推送异常: {exc!r}")

    return succ_cnt
