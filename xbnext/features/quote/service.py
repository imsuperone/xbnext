# -*- coding: utf-8 -*-
"""R2 · 引用占位清洗 —— 纯逻辑部分。

**为什么需要**：AstrBot core 在处理引用链时，取不到内容就直接塞字面量
占位符进 prompt（``[Empty Text]`` / ``[Image unavailable]`` / ``[Image]``），
失败的图片路径还可能残留在 ``req.image_urls``。模型看到这些就"脑补"出
一张空白图片，于是回复跑偏。

**prompt 只是半张地图**（真机第六轮「单引用+@无消息还是会说有图、附件」的
根因）：core 的引用块 ``<Quoted Message>...`` 与附件标记
``[File Attachment in quoted message: ...]`` 全部 append 进
``req.extra_user_content_parts``（`req.prompt` 只是 ``event.message_str``）。
单引用 + @ 且无正文时 prompt 几乎为空 —— **模型看到的唯一内容就是那些块**，
所以 :func:`clean_parts` 必须与 :func:`clean_prompt` 同规则清洗它们。

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
    r"|Voice\s+unavailable"
    r")\s*\]",
    re.IGNORECASE,
)

#: 形态完好但没有内容描述的图片占位
BARE_IMAGE_RE = re.compile(r"\[\s*Image\s*\]")

#: 附件引用标记（core 把引用链压成带本地路径的英文标记）。
#: 形如 ``[File Attachment in quoted message: name X, path ...]``、
#: ``[Audio Attachment: path ...]``、``[Video Attachment in quoted message:
#: name X, ref ...]`` —— 模型分不清"谁的附件"，还会拿无意义的本地路径说事。
ATTACHMENT_RE = re.compile(
    r"\[\s*(?P<kind>File|Audio|Video|Voice)\s+Attachment"
    r"(?P<quoted>\s+in\s+quoted\s+message)?\s*:\s*"
    r"(?:name\s+(?P<name>[^,\]]+?)\s*,\s*)?"
    r"(?:path|ref)\s+(?P<ref>[^\]]*?)"
    r"\s*\]",
    re.IGNORECASE,
)

#: 已失效图片的中文说明（替换 DEGRADED_RE 命中项）
DEGRADED_LABEL = "［引用的图片/附件已失效］"

#: 引用块被清空后的事实说明（不留半截「(昵称):」空壳）
QUOTED_EMPTY_NOTE = "（此消息没有文字内容）"

#: core 引用块：``<Quoted Message>\\n正文\\n</Quoted Message>``
QUOTE_BLOCK_RE = re.compile(
    r"<Quoted Message>\n(?P<body>.*?)\n</Quoted Message>",
    re.DOTALL,
)

_ATTACHMENT_KIND_ZH = {
    "file": "文件",
    "audio": "语音",
    "video": "视频",
    "voice": "语音",
}


def _label_attachment(match: "re.Match[str]") -> str:
    """附件标记 → 中文标签：保留文件名（如果有），丢掉本地路径噪声。"""
    kind = _ATTACHMENT_KIND_ZH.get((match.group("kind") or "").lower(), "文件")
    scope = "引用消息中的" if match.group("quoted") else "消息中的"
    name = (match.group("name") or "").strip()
    if name:
        return f"［{scope}{kind}附件：{name}］"
    return f"［{scope}{kind}附件］"


def find_placeholders(text: str) -> Dict[str, List[str]]:
    """扫描文本里的 core 占位符，返回 ``{类别: [命中原文, ...]}``。

    类别：``noise``（纯噪声）、``degraded``（可证实失败）、``bare_image``、
    ``attachment``（附件引用标记）。
    """
    empty: Dict[str, List[str]] = {
        "noise": [],
        "degraded": [],
        "bare_image": [],
        "attachment": [],
    }
    result: Dict[str, List[str]] = dict(empty)
    if not isinstance(text, str) or not text:
        return result
    try:
        result["noise"] = NOISE_RE.findall(text)
        result["degraded"] = DEGRADED_RE.findall(text)
        result["bare_image"] = BARE_IMAGE_RE.findall(text)
        result["attachment"] = [
            m.group(0) for m in ATTACHMENT_RE.finditer(text)
        ]
    except Exception:  # noqa: BLE001
        return dict(empty)
    return result


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
    empty = {"noise": 0, "degraded": 0, "bare_image": 0, "attachment": 0}
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
            out = ATTACHMENT_RE.sub("", out)
        else:  # label（默认）
            out = DEGRADED_RE.sub(DEGRADED_LABEL, text)
            out = NOISE_RE.sub("", out)
            out = ATTACHMENT_RE.sub(_label_attachment, out)
            # 裸 [Image] 默认保留：它至少说明"这里曾有一张图"，删掉反而误导；
            # 只有上层证实图片路径确实已失效时才降级
            if degrade_bare:
                out = BARE_IMAGE_RE.sub(DEGRADED_LABEL, out)
        # 占位符删空引用块后补事实说明（strip / label 两档都需要）
        out = repair_quote_block(out)
        return _tidy(out), stats
    except Exception:  # noqa: BLE001
        return text, stats


def repair_quote_block(text: str) -> str:
    """引用块正文被清洗掏空后补一句事实说明，不留半截「(昵称):」空壳。

    core 的引用块形如 ``<Quoted Message>\\n(昵称): [Empty Text]\\n</Quoted
    Message>``；``[Empty Text]`` 删掉后只剩 ``(昵称): `` 空壳 —— 模型看到
    残缺结构会自行脑补（"图/附件"的来源之一）。正文非空的块原样返回；
    非字符串 / 无标记的文本原样返回。
    """
    if not isinstance(text, str) or "<Quoted Message>" not in text:
        return text

    def _fix(match: "re.Match[str]") -> str:
        body = match.group("body")
        head = ""
        inner = body
        hm = re.match(r"^\((?P<nick>[^)]*)\):\s*(?P<rest>.*)$", body, re.DOTALL)
        if hm:
            head = f"({hm.group('nick')}): "
            inner = hm.group("rest")
        if inner.strip():
            return match.group(0)
        return f"<Quoted Message>\n{head}{QUOTED_EMPTY_NOTE}\n</Quoted Message>"

    try:
        return QUOTE_BLOCK_RE.sub(_fix, text)
    except Exception:  # noqa: BLE001
        return text


def clean_parts(
    parts: Any, action: str = "label", degrade_bare: bool = False
) -> Tuple[int, int]:
    """就地清洗 ``req.extra_user_content_parts`` 里各 ``TextPart`` 的文本。

    与 :func:`clean_prompt` **同规则**（label / strip / keep、``degrade_bare``），
    包含引用块空壳修复。改写后变成空串的独立块会被剔除（留着空文本段只会
    让模型困惑）；**未命中的他方 part 一律原样保留**（对象引用与 ``temp``
    标记都不动，见 ``tests/test_coexist.py`` 共存红线）。

    :return ``(改写数, 移除数)``；``parts`` 不是列表时 ``(0, 0)``。
    """
    if not isinstance(parts, list) or not parts:
        return 0, 0
    changed = 0
    removed = 0
    keep: List[Any] = []
    for part in parts:
        text = getattr(part, "text", None)
        if not isinstance(text, str) or not text:
            keep.append(part)
            continue
        try:
            cleaned, _ = clean_prompt(text, action=action, degrade_bare=degrade_bare)
        except Exception:  # noqa: BLE001
            keep.append(part)
            continue
        if cleaned == text:
            keep.append(part)
            continue
        if not cleaned.strip():
            removed += 1
            continue
        try:
            part.text = cleaned
        except Exception:  # noqa: BLE001
            keep.append(part)
            continue
        changed += 1
        keep.append(part)
    if removed:
        parts[:] = keep
    return changed, removed


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
    "ATTACHMENT_RE",
    "DEGRADED_LABEL",
    "QUOTED_EMPTY_NOTE",
    "QUOTE_BLOCK_RE",
    "find_placeholders",
    "clean_prompt",
    "repair_quote_block",
    "clean_parts",
    "report",
    "is_dead_image_url",
    "split_image_urls",
    "dead_image_urls",
]
