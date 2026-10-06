# -*- coding: utf-8 -*-
"""指令文本解析（纯函数，不 import astrbot）。

AstrBot 的 command handler 拿到的 ``event.message_str`` 形态不固定：

- 可能保留完整形式 ``/xbnext profile 称呼 小明``；
- 可能只剥掉根命令名 ``profile 称呼 小明``；
- 可能把根命令与子命令都剥掉 ``称呼 小明``；
- 前面还可能挂着 CQ 码 / @ 前缀 token。

**逐个形态兜底**，保证总能取出 ``(子命令, 剩余参数)``。
（解析思路照抄本机 ``xbdoc/main.py::doc_cmd``，那是真机跑过的实现。）
"""

from __future__ import annotations

import re
from typing import List, Tuple

__all__ = ["tokenize", "split"]


def tokenize(text: str) -> List[str]:
    """按空白切词并丢空串；非字符串返回 ``[]``。"""
    if not isinstance(text, str) or not text.strip():
        return []
    return [t for t in re.split(r"\s+", text.strip()) if t]


def split(text: str, root: str = "xbnext") -> Tuple[str, List[str]]:
    """把指令文本切成 ``(子命令, 参数列表)``。

    ``root`` 匹配大小写不敏感，允许带 ``/`` 前缀。
    找不到根命令时（前缀已被 AstrBot 剥掉），把整串当参数，
    此时 ``子命令`` 是第一个词；如果第一个词就是 ``root`` 会一并剥掉。

    例子::

        split("/xbnext profile 称呼 小明")  -> ("profile", ["称呼", "小明"])
        split("profile 称呼 小明")          -> ("profile", ["称呼", "小明"])
        split("称呼 小明")                   -> ("称呼", ["小明"])
        split("")                           -> ("", [])
    """
    tokens = tokenize(text)
    if not tokens:
        return "", []

    keys = {root.lower(), "/" + root.lower()}
    index = next((i for i, t in enumerate(tokens) if t.lower() in keys), None)
    if index is None:
        # 没有根命令 → 找第一个以 / 开头的 token（CQ/@ 前缀在它之前）
        index = next((i for i, t in enumerate(tokens) if t.startswith("/")), -1)
    rest = tokens[index + 1:]

    # 根命令名被剥掉一半时可能出现两个 root
    if rest and rest[0].lower() in keys:
        rest = rest[1:]

    sub = rest[0].lower() if rest else ""
    return sub, rest[1:]
