# -*- coding: utf-8 -*-
"""指令文本解析 + 菜单词表（纯函数，不 import astrbot）。

AstrBot 的 command handler 拿到的 ``event.message_str`` 形态不固定：

- 可能保留完整形式 ``/xbnext profile 称呼 小明``；
- 可能只剥掉根命令名 ``profile 称呼 小明``；
- 可能把根命令与子命令都剥掉 ``称呼 小明``；
- 前面还可能挂着 CQ 码 / @ 前缀 token。

**逐个形态兜底**，保证总能取出 ``(子命令, 剩余参数)``。
（解析思路照抄本机 ``xbdoc/main.py::doc_cmd``，那是真机跑过的实现。）

另有 :data:`HELP_WORDS` / :data:`STATUS_WORDS` / :data:`MENU` ——
单指令分发（``runtime.dispatch``）用的词表与菜单文案，
排版照抄 xbdoc / xbimg 的成熟形态。
"""

from __future__ import annotations

import re
from typing import List, Tuple

__all__ = ["tokenize", "split", "HELP_WORDS", "STATUS_WORDS", "MENU"]

#: 裸指令与帮助词：命中回菜单（``split`` 返回的 sub 已小写化）
HELP_WORDS = ("", "help", "h", "?", "？", "菜单", "帮助")
#: 状态子指令（含中文别名）
STATUS_WORDS = ("status", "状态")

#: 指令菜单 —— 排版照抄本机 xbdoc / xbimg（标题 + 分隔线 + 分节 + 圆点）。
#: 真机反馈「/xbnext 还是很丑」后，从 3 行纯文本换成这个形态。
MENU = (
    "🧩【XBNEXT · 指令菜单】\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "\n"
    "📊 状态\n"
    "• /xbnext status — 各功能开关与 KV 可用性\n"
    "\n"
    "👤 用户档案（按群分开存）\n"
    "• /xbnext profile — 查看我的档案\n"
    "• /xbnext profile 称呼 小明 — 改称呼（别名：名字 / 昵称；冒号=等号写法都认）\n"
    "• /xbnext profile 自述 <内容> — 改自述（别名：信息 / 描述）\n"
    "• /xbnext profile 称呼: — 删掉单个字段（字段后跟冒号留空）\n"
    "• /xbnext profile 清空 — 删除我的档案\n"
    "\n"
    "📊 Token 用量显示\n"
    "• /xbnext token — 本会话开 / 关 token 用量显示（总开关需先在 WebUI 打开）\n"
    "\n"
    "💡 档案与「用户档案」开关解耦：关着开关也能查能改，只是不喂给模型。"
)


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
