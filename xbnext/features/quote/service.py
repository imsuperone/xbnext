# -*- coding: utf-8 -*-
"""R2 · 引用占位清洗 —— 纯逻辑部分。

**为什么需要**：AstrBot core 在处理引用链时，取不到内容就直接塞字面量
占位符进 prompt（``[Empty Text]`` / ``[Image unavailable]`` / ``[Image]``），
失败的图片路径还可能残留在 ``req.image_urls``。模型看到这些就"脑补"出
一张空白图片，于是回复跑偏。

**本模块只做"可证实的失败"**，不做像素级空白检测（见 aidoc/01 §R2）。

本文件**不 import astrbot**，输入输出全是普通 ``str`` / ``list``，可直接 pytest。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

# ---------------------------------------------------------------------------
# 占位符识别
# ---------------------------------------------------------------------------

#: 纯噪声：删掉对模型毫无信息量
NOISE_RE = re.compile(
    r"\[\s*Empty\s+Text\s*\]",
    re.IGNORECASE,
)

#: 可证实失败：删掉会丢信息，改写成中文说明
DEGRADED_RE = re.compile(
    r"\[\s*(?:"
    r"Image\s+unavailable"
    r"|Attachment\s+unavailable"
    r"|Video\s+unavailable"
    r"|Audio\s+unavailable"
    r"|File\s+unavailable"
    r")\s*\]",
    re.IGNORECASE,
)

#: 形态完好但没有内容描述的图片占位
BARE_IMAGE_RE = re.compile(r"\[\s*Image\s*\]")

#: 已失效图片的中文说明（替换 DEGRADED_RE 命中项）
DEGRADED_LABEL = "［引用的图片/附件已失效］"


def find_placeholders(text: str) -> Dict[str, List[str]]:
    """扫描文本里的 core 占位符，返回 ``{类别: [命中原文, ...]}``。

    类别：``noise``（纯噪声）、``degraded``（可证实失败）、``bare_image``。
    """
    result: Dict[str, List[str]] = {"noise": [], "degraded": [], "bare_image": []}
    if not isinstance(text, str) or not text:
        return result
    try:
        result["noise"] = NOISE_RE.findall(text)
        result["degraded"] = DEGRADED_RE.findall(text)
        result["bare_image"] = BARE_IMAGE_RE.findall(text)
    except Exception:  # noqa: BLE001
        return {"noise": [], "degraded": [], "bare_image": []}
    return result


def has_placeholders(text: str) -> bool:
    """文本里是否存在任意已知占位符。"""
    return any(find_placeholders(text).values())


# ---------------------------------------------------------------------------
# 改写
# ---------------------------------------------------------------------------
def clean_prompt(text: str, action: str = "label") -> Tuple[str, Dict[str, int]]:
    """按 ``action`` 改写提示词。

    - ``label``：噪声删除，可证实失败替换成中文说明（推荐）；
    - ``strip``：三类全部删除；
    - ``keep``：不动文本，只返回统计（用于调试）。

    返回 ``(新文本, 各类命中计数)``。任何异常都原样返回。
    """
    empty = {"noise": 0, "degraded": 0, "bare_image": 0}
    # 非字符串一律先归一：**绝不允许把非空输入变成 ""**，否则会把 prompt 清空
    if text is None:
        return "", empty
    if not isinstance(text, str):
        try:
            text = str(text)
        except Exception:  # noqa: BLE001
            return "", empty
    if not text:
        return "", empty
    stats = {k: len(v) for k, v in find_placeholders(text).items()}
    if action == "keep" or not any(stats.values()):
        return text, stats
    try:
        if action == "strip":
            out = DEGRADED_RE.sub("", text)
            out = NOISE_RE.sub("", out)
            out = BARE_IMAGE_RE.sub("", out)
        else:  # label（默认）
            out = DEGRADED_RE.sub(DEGRADED_LABEL, text)
            out = NOISE_RE.sub("", out)
            # 裸 [Image] 保留：它至少说明"这里曾有一张图"，删掉反而误导
        return _tidy(out), stats
    except Exception:  # noqa: BLE001
        return text, stats


def _tidy(text: str) -> str:
    """压掉替换后留下的多余空行与行尾空白。"""
    try:
        lines = [ln.rstrip() for ln in text.split("\n")]
        out: List[str] = []
        blank = False
        for ln in lines:
            if not ln.strip():
                if blank:
                    continue
                blank = True
            else:
                blank = False
            out.append(ln)
        return "\n".join(out).strip()
    except Exception:  # noqa: BLE001
        return text


def report(stats: Dict[str, int]) -> str:
    """把统计压成一行调试文案。"""
    if not stats or not any(stats.values()):
        return "无占位符"
    return "、".join(f"{k}×{v}" for k, v in stats.items() if v)


def dead_image_urls(image_urls: Any) -> List[str]:
    """筛出 ``req.image_urls`` 里明显无效的项（非 http/https/data 的路径）。

    core 的死路径残留在这里会让模型以为还有一张图。
    """
    if not isinstance(image_urls, (list, tuple)):
        return []
    bad: List[str] = []
    for item in image_urls:
        if not isinstance(item, str) or not item.strip():
            continue
        value = item.strip()
        if value.startswith(("http://", "https://", "data:")):
            continue
        # 本地文件路径：交给上层判断是否存在，这里只标出"非 URL"
        bad.append(value)
    return bad


__all__ = [
    "NOISE_RE",
    "DEGRADED_RE",
    "BARE_IMAGE_RE",
    "DEGRADED_LABEL",
    "find_placeholders",
    "has_placeholders",
    "clean_prompt",
    "report",
    "dead_image_urls",
]
