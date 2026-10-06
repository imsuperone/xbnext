# -*- coding: utf-8 -*-
"""统一注入出口。

**红线**：全插件只有本模块可以构造 ``TextPart`` 并塞进
``req.extra_user_content_parts``。功能模块一律通过
:func:`inject_text` / :meth:`xbnext.context.RequestContext.inject` 注入。

规则（见 aidoc/02-架构设计.md §3）：

1. 一律 ``mark_as_temp()`` —— 不写会话历史、不进 summarize；
2. 身份 / 档案类字段必须先过 :func:`sanitize`；
3. 绝不修改 ``req.system_prompt``（保护 prompt cache）；
4. astrbot 导入全部**懒执行**，本模块在裸 Python 环境也能 import（单测需要）。
"""

from __future__ import annotations

import re
from typing import Any, Optional

TAG = "xbnext"

# 控制字符（保留 \t \n \r）→ 空格
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# 零宽 / 方向控制字符，肉眼看不见但会干扰模型
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")
# 尖括号全角化：防止用户内容里的 <xxx> 被模型当成标签
_ANGLE_RE = re.compile(r"<\s*([^<>]{1,64}?)\s*>")
_MULTI_BLANK_RE = re.compile(r"[ \t\u3000]{2,}")
_TAG_ANY_RE = re.compile(r"</?\s*xbnext\s*>", re.IGNORECASE)


# ---------------------------------------------------------------------------
# 清洗
# ---------------------------------------------------------------------------
def sanitize(text: Any, max_len: int = 128) -> str:
    """把任意用户内容压成一段安全、短、可注入的文本。

    步骤：转 str → 去控制字符 → 去零宽字符 → 尖括号全角化 →
    折叠空白（保留换行）→ strip → 按 ``max_len`` 截断。

    任何异常都降级为 ``""``（红线：写入异常一律返回安全值，不裸抛）。
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        try:
            text = str(text)
        except Exception:  # noqa: BLE001
            return ""
    try:
        text = _CTRL_RE.sub(" ", text)
        text = _ZERO_WIDTH_RE.sub("", text)
        text = _ANGLE_RE.sub(lambda m: "《" + m.group(1) + "》", text)
        text = _MULTI_BLANK_RE.sub(" ", text)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = "\n".join(line.strip() for line in text.split("\n"))
        text = text.strip()
        if max_len and len(text) > max_len:
            text = text[:max_len].rstrip() + "…"
        return text
    except Exception:  # noqa: BLE001
        return ""


def strip_xbnext(text: Any) -> str:
    """清掉文本里残留的 ``<xbnext>`` 标记（兜底）。

    正常情况下注入走 temp part 不会落库；一旦模型输出或历史里混进了标记，
    用这个函数洗掉，避免后续解析把它当成结构。
    """
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else ""
    try:
        return _TAG_ANY_RE.sub("", text).strip()
    except Exception:  # noqa: BLE001
        return text


# ---------------------------------------------------------------------------
# 注入
# ---------------------------------------------------------------------------
def _load_text_part():
    """懒加载 ``TextPart``；不可用时返回 ``None``（测试环境即如此）。"""
    try:
        from astrbot.core.agent.message import TextPart

        return TextPart
    except Exception:  # noqa: BLE001
        return None


def make_temp_part(text: str, text_part_cls: Any = None) -> Any:
    """构造一个 ``mark_as_temp()`` 的文本部件；失败返回 ``None``。

    :param text_part_cls: 便于单测注入假实现；生产留空走 astrbot 真身。
    """
    if not isinstance(text, str) or not text.strip():
        return None
    cls = text_part_cls if text_part_cls is not None else _load_text_part()
    if cls is None:
        return None
    try:
        part = cls(text=text)
        marker = getattr(part, "mark_as_temp", None)
        if callable(marker):
            marked = marker()
            if marked is not None:
                part = marked
        return part
    except Exception:  # noqa: BLE001
        return None


def inject_text(req: Any, text: str, text_part_cls: Any = None) -> bool:
    """把一段文本作为 temp part 追加到请求上；成功返回 ``True``。

    任何异常都降级为 ``False``，绝不让插件把请求打断。
    """
    if not isinstance(text, str) or not text.strip() or req is None:
        return False
    part = make_temp_part(text, text_part_cls=text_part_cls)
    if part is None:
        return False
    try:
        parts = getattr(req, "extra_user_content_parts", None)
        if parts is None:
            req.extra_user_content_parts = []
            parts = req.extra_user_content_parts
        parts.append(part)
        return True
    except Exception:  # noqa: BLE001
        return False


def wrap(body: str, title: str = "") -> str:
    """给注入内容套统一的 ``<xbnext>`` 标签块。

    标签只是**给模型看的分节提示**，不是可信协议 —— 不要用它做解析依据。
    """
    body = (body or "").strip()
    if not body:
        return ""
    if title:
        return f"<xbnext>\n[{title}]\n{body}\n</xbnext>"
    return f"<xbnext>\n{body}\n</xbnext>"


__all__ = ["TAG", "sanitize", "strip_xbnext", "make_temp_part", "inject_text", "wrap"]
