# -*- coding: utf-8 -*-
"""插件单元测试的公共夹具。

**关键约束：单测不依赖 astrbot。** 本机没装 AstrBot 本体（在云端），
所以任何 ``import astrbot`` 都必须在被测代码里惰性发生，
测试侧一律用假对象（fake event / fake req / fake TextPart）。

运行::

    python -X utf8 -m pytest tests -q
    # 或无 pytest 时
    python -X utf8 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
from pathlib import Path

# 让 ``import xbnext`` 可用（插件根目录加入 sys.path）
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ---------------------------------------------------------------------------
# 假对象
# ---------------------------------------------------------------------------
class FakeTextPart:
    """模拟 ``astrbot.core.agent.message.TextPart``。"""

    def __init__(self, text: str = ""):
        self.text = text
        self.temp = False

    def mark_as_temp(self):
        self.temp = True
        return self


class FakeReq:
    """模拟 ``ProviderRequest`` 的最小字段集。"""

    def __init__(self, prompt: str = "", image_urls=None):
        self.prompt = prompt
        self.image_urls = list(image_urls) if image_urls else []
        self.extra_user_content_parts = []


class FakeEvent:
    """模拟 ``AstrMessageEvent`` 的最小字段集。

    ``message_str`` / ``is_at_or_wake_command`` / ``call_llm`` 是 core 判定
    「要不要调 LLM」的三个开关（真机第二轮反馈的纯表情 bug 就出在这里），
    缺一个都测不到早期钩子的守卫。
    """

    def __init__(self, session_id: str = "aiocqhttp:GroupMessage:1", message=None,
                 message_str: str = ""):
        self._session_id = session_id
        self.message = message or []
        self.message_str = message_str
        #: core：本轮是否 @到 bot / 命中唤醒前缀
        self.is_at_or_wake_command = False
        #: core：``True`` = 禁止默认 LLM 请求
        self.call_llm = False

    def get_session_id(self) -> str:
        return self._session_id

    def get_sender_id(self) -> str:
        return "10001"

    def get_platform_name(self) -> str:
        return "aiocqhttp"


class FakeKV:
    """模拟 Star 的插件 KV 代理。"""

    def __init__(self):
        self.data = {}

    async def get_kv_data(self, key, default=None):
        return self.data.get(key, default)

    async def put_kv_data(self, key, value):
        self.data[key] = value

    async def delete_kv_data(self, key):
        self.data.pop(key, None)


__all__ = ["FakeTextPart", "FakeReq", "FakeEvent", "FakeKV", "_ROOT"]
