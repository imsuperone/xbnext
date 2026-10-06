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

import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

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
def clean_prompt(
    text: str, action: str = "label", degrade_bare: bool = False
) -> Tuple[str, Dict[str, int]]:
    """按 ``action`` 改写提示词。

    - ``label``：噪声删除，可证实失败替换成中文说明（推荐）；
    - ``strip``：三类全部删除；
    - ``keep``：不动文本，只返回统计（用于调试）。

    :param degrade_bare: 为 ``True`` 时把裸 ``[Image]`` 也降级成失效说明。
        只在**已确认 ``req.image_urls`` 里存在死路径**时才传 ``True`` ——
        否则裸 ``[Image]`` 至少说明"这里曾有一张图"，删掉反而误导。

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
            # 裸 [Image] 默认保留：它至少说明"这里曾有一张图"，删掉反而误导；
            # 只有上层证实图片路径确实已失效时才降级
            if degrade_bare:
                out = BARE_IMAGE_RE.sub(DEGRADED_LABEL, out)
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


#: 任何 ``scheme://`` 形态的引用（http/https/data/file/其他）
_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*)://")


def _file_url_path(value: str) -> str:
    """把 ``file:///C:/x.png`` / ``file:///data/x.png`` 还原成本地路径。"""
    try:
        path = unquote(urlparse(value).path or "")
    except Exception:  # noqa: BLE001
        path = value[7:]
    # Windows 的 ``file:///C:/...`` 会带一个多余前导斜杠
    if re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    return path


def is_dead_image_url(value: Any, exists: Optional[Callable[[str], bool]] = None) -> bool:
    """判断 ``req.image_urls`` 的一项是否**可证实已失效**。

    判定规则（保守，宁可漏判也不误删）：

    ============================= ===== ==================================
    取值                          结果  理由
    ============================= ===== ==================================
    非字符串 / 空白               否    不是我们能理解的类型，不碰
    ``http://`` / ``https://``    否    需要联网验证，离线一律保留
    ``data:``                     否    内联数据本身就在
    其他 ``scheme://``            否    离线无法验证，保留
    ``file://``                   看存在性  还原成路径后 ``os.path.exists``
    普通本地路径                  看存在性  同上
    ============================= ===== ==================================

    :param exists: 注入判定函数（单测用），默认 ``os.path.exists``。
    """
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    if text.startswith(("http://", "https://", "data:")):
        return False
    m = _SCHEME_RE.match(text)
    if m:
        if m.group(1).lower() == "file":
            path = _file_url_path(text)
        else:
            return False
    else:
        path = text
    checker = exists if exists is not None else os.path.exists
    try:
        return not bool(checker(path))
    except Exception:  # noqa: BLE001
        return False


def split_image_urls(
    image_urls: Any, exists: Optional[Callable[[str], bool]] = None
) -> Tuple[List[str], List[str]]:
    """把 ``req.image_urls`` 拆成 ``(保留, 已失效)``，顺序不变。"""
    if not isinstance(image_urls, (list, tuple)):
        return [], []
    alive: List[str] = []
    dead: List[str] = []
    for item in image_urls:
        if is_dead_image_url(item, exists=exists):
            dead.append(item)
        else:
            alive.append(item)
    return alive, dead


def dead_image_urls(
    image_urls: Any, exists: Optional[Callable[[str], bool]] = None
) -> List[str]:
    """筛出 ``req.image_urls`` 里**可证实已失效**的项。

    core 的死路径残留在这里会让模型以为还有一张图 —— 见
    :func:`is_dead_image_url` 的判定表。
    """
    return split_image_urls(image_urls, exists=exists)[1]


__all__ = [
    "NOISE_RE",
    "DEGRADED_RE",
    "BARE_IMAGE_RE",
    "DEGRADED_LABEL",
    "find_placeholders",
    "has_placeholders",
    "clean_prompt",
    "report",
    "is_dead_image_url",
    "split_image_urls",
    "dead_image_urls",
]
