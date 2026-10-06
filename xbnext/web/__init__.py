# -*- coding: utf-8 -*-
"""WebUI 后端 API。

三个端点（路由用 **metadata.yaml 里保留大小写的插件名**，因为 Plugin Page
Bridge 是按 metadata 名请求接口的——AstrBot 会把插件类上的 name 规范成小写，
两者不一致时 bridge 会打偏）::

    GET  /astrbot_plugin_xbnext/ping      —— 前端探测 bridge / HTTP 前缀用
    GET  /astrbot_plugin_xbnext/state     —— 运行状态 + 全量配置（一次回填）
    POST /astrbot_plugin_xbnext/setting   —— 写单个配置项（开关 / 表单 / 主题）

注册方式对齐 AstrNa 与本机三个参考插件：``context.register_web_api``；
旧版 AstrBot 没有该方法时**静默跳过**，不影响插件主体功能。

**响应形状统一为** ``{"ok": bool, "data": ...}`` / ``{"ok": false, "error": ...}``。
走 bridge 与走 HTTP 拿到的是同一份 JSON，前端一个 :func:`unwrap` 就能归一。
"""

from __future__ import annotations

from typing import Any, Dict

# 注意：本模块自身就是 ``xbnext.web`` 包，``from . import`` 只能取到
# ``xbnext.web`` 自己，跨层取常量必须写两个点 ``from .. import``。
from .. import HOOK_PRIORITY, PLUGIN_NAME, __version__
from .. import switches
from ..config import SCHEMA


def _ok(data: Any, astrbot_web: Any) -> Any:
    return astrbot_web.json_response({"ok": True, "data": data})


def _err(message: Any, astrbot_web: Any, extra: Any = None) -> Any:
    """统一错误体。

    不传 ``status_code``：母版里 ``json_response`` 只见到单参数用法，
    第二个位置参数签名不确定，宁可让 HTTP 恒 200、靠 body 的 ``ok:false``
    报错——bridge 与 HTTP 两条通路拿到的形状完全一致。
    """
    body: Dict[str, Any] = {"ok": False, "error": str(message)}
    if extra is not None:
        body["data"] = extra
    return astrbot_web.json_response(body)


def build_state(runtime: Any) -> Dict[str, Any]:
    """组装 ``GET state`` 的载荷。

    ``config`` 是全部 schema 键的当前值，前端靠它一次性回填所有控件
    （唯一真相源在服务端，页面不留本地副本——与母版一致）。
    """
    data: Dict[str, Any] = runtime.status()
    data["config"] = runtime.conf.as_dict()
    data["schema"] = {
        key: {
            "type": item.get("type"),
            "condition": item.get("condition"),
            "default": item.get("default"),
        }
        for key, item in SCHEMA.items()
        if isinstance(item, dict)
    }
    data["hook_priority"] = HOOK_PRIORITY
    data["writable_keys"] = list(switches.WRITABLE_KEYS)
    return data


async def handle_setting(runtime: Any, payload: Any) -> Dict[str, Any]:
    """处理 ``POST setting``；返回体直接给 :func:`_ok` 包装。

    校验顺序：请求体 → 键白名单 → 类型转换 → 写内存 → 落盘（失败回滚）。
    """
    if not isinstance(payload, dict):
        return {"ok": False, "error": "请求体必须是 JSON 对象"}
    if "key" not in payload:
        return {"ok": False, "error": "缺少 key"}
    key = str(payload.get("key") or "").strip()
    if key not in SCHEMA:
        return {"ok": False, "error": f"未知配置项: {key or '(空)'}"}
    if "value" not in payload:
        return {"ok": False, "error": "缺少 value"}
    result = await switches.apply_conf(runtime.conf, key, payload.get("value"))
    if result.get("error"):
        return {"ok": False, "error": result["error"], "data": result}
    return {"ok": True, "data": result}


def register_web_api(context: Any, runtime: Any) -> bool:
    """注册 XBNEXT 的 Web API；不可用时返回 ``False``。"""
    register = getattr(context, "register_web_api", None)
    if not callable(register):
        return False
    try:
        from astrbot.api import web as astrbot_web
    except Exception:  # noqa: BLE001  旧版无 web API
        return False

    base = f"/{PLUGIN_NAME}"

    async def ping() -> Any:
        return _ok(
            {"version": __version__, "plugin": PLUGIN_NAME, "priority": HOOK_PRIORITY},
            astrbot_web,
        )

    async def state() -> Any:
        try:
            return _ok(build_state(runtime), astrbot_web)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, astrbot_web)

    async def setting() -> Any:
        try:
            try:
                payload = await astrbot_web.request.json(default={})
            except Exception:  # noqa: BLE001
                return _err("请求体不是合法 JSON", astrbot_web)
            result = await handle_setting(runtime, payload)
            if not result.get("ok"):
                return _err(result.get("error") or "写入失败", astrbot_web, result.get("data"))
            return _ok(result.get("data"), astrbot_web)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, astrbot_web)

    try:
        register(f"{base}/ping", ping, ["GET"], "XBNEXT 存活探测")
        register(f"{base}/state", state, ["GET"], "XBNEXT 运行状态")
        register(f"{base}/setting", setting, ["POST"], "XBNEXT 写入配置项")
    except Exception:  # noqa: BLE001  重复注册等，交给调用方记日志
        return False
    return True


__all__ = ["register_web_api", "build_state", "handle_setting"]
