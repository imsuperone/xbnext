# -*- coding: utf-8 -*-
"""WebUI 后端 API。

只做两件事（P6 之前保持最小）：

- ``GET  /astrbot_plugin_xbnext/xbnext/ping``  —— 前端探测 bridge/HTTP 前缀用
- ``GET  /astrbot_plugin_xbnext/xbnext/state`` —— 运行状态（开关、AstrNa、KV）

注册方式对齐 AstrNa / 本机三个参考插件：``context.register_web_api``，
旧版 AstrBot 没有该方法时**静默跳过**，不影响插件主体功能。

> 路由前缀必须用 **metadata.yaml 里保留大小写的插件名**，
> 因为 Plugin Page Bridge 是按 metadata 名请求接口的
> （AstrBot 会把插件类上的 name 规范成小写，两者不一致）。
"""

from __future__ import annotations

from typing import Any

# 注意：本模块自身就是 ``xbnext.web`` 包，``from . import`` 只能取到
# ``xbnext.web`` 自己，跨层取常量必须写两个点 ``from .. import``。
from .. import PLUGIN_NAME, __version__


def register_web_api(context: Any, runtime: Any) -> bool:
    """注册 XBNEXT 的 Web API；不可用时返回 ``False``。"""
    register = getattr(context, "register_web_api", None)
    if not callable(register):
        return False
    try:
        from astrbot.api import web as astrbot_web
    except Exception:  # noqa: BLE001  旧版无 web API
        return False

    base = f"/{PLUGIN_NAME}/xbnext"

    async def ping() -> Any:
        return astrbot_web.json_response({"status": "ok", "version": __version__})

    async def state() -> Any:
        try:
            return astrbot_web.json_response(runtime.status())
        except Exception as exc:  # noqa: BLE001
            return astrbot_web.error_response(str(exc), status_code=500)

    try:
        register(f"{base}/ping", ping, ["GET"], "XBNEXT 存活探测")
        register(f"{base}/state", state, ["GET"], "XBNEXT 运行状态")
    except Exception:  # noqa: BLE001  重复注册等，交给调用方记日志
        return False
    return True


__all__ = ["register_web_api"]
