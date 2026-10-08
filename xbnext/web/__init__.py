# -*- coding: utf-8 -*-
"""WebUI 后端 API。

三个配置端点 + 三个档案端点（路由用 **metadata.yaml 里保留大小写的插件名**，
因为 Plugin Page Bridge 是按 metadata 名请求接口的——AstrBot 会把插件类上的
name 规范成小写，两者不一致时 bridge 会打偏）::

    GET  /astrbot_plugin_xbnext/ping           —— 前端探测 bridge / HTTP 前缀用
    GET  /astrbot_plugin_xbnext/state          —— 运行状态 + 全量配置（一次回填）
    POST /astrbot_plugin_xbnext/setting        —— 写单个配置项（开关 / 表单 / 主题）
    GET  /astrbot_plugin_xbnext/profiles       —— 列出全部用户档案
    POST /astrbot_plugin_xbnext/profile_save   —— 写一份档案（全空即删除）
    POST /astrbot_plugin_xbnext/profile_delete —— 删一份档案
    GET  /astrbot_plugin_xbnext/inject_log     —— 最近 10 轮提示词注入记录（P16）
    GET  /astrbot_plugin_xbnext/groups         —— aiocqhttp 所在群列表（R9 一键填白名单）

注册方式对齐 AstrNa 与本机三个参考插件：``context.register_web_api``；
旧版 AstrBot 没有该方法时**静默跳过**，不影响插件主体功能。

**响应形状统一为** ``{"ok": bool, "data": ...}`` / ``{"ok": false, "error": ...}``。
走 bridge 与走 HTTP 拿到的是同一份 JSON，前端一个 :func:`unwrap` 就能归一。
"""

from __future__ import annotations

from typing import Any, Dict, List

# 注意：本模块自身就是 ``xbnext.web`` 包，``from . import`` 只能取到
# ``xbnext.web`` 自己，跨层取常量必须写两个点 ``from .. import``。
from .. import HOOK_PRIORITY, PLUGIN_NAME, __version__
from .. import inject_log, switches
from ..config import SCHEMA
from ..features.face import updater as face_updater


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
    data["face"] = _face_stats(runtime)
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


#: 档案功能的配置键（WebUI 档案页签经 runtime 找到对应 Feature）
PROFILE_KEY = "enable_user_profile"

#: 表情翻译功能的配置键（状态卡读启用态与 :meth:`FaceFeature.stats`）
FACE_KEY = "enable_face_translate"


def _profile_feature(runtime: Any) -> Any:
    feat = runtime.get_feature(PROFILE_KEY) if hasattr(runtime, "get_feature") else None
    if feat is None:
        raise RuntimeError("用户档案功能未注册")
    return feat


def _face_stats(runtime: Any) -> Dict[str, Any]:
    """``GET state`` 的表情表统计（P17 · D）；任何失败回落到空统计。"""
    data: Dict[str, Any] = {"builtin": 0, "overlay": 0, "updated_at": None}
    try:
        feat = runtime.get_feature(FACE_KEY) if hasattr(runtime, "get_feature") else None
        if feat is not None and hasattr(feat, "stats"):
            data.update(feat.stats())
        else:
            data.update(face_updater.stats(None))
    except Exception:  # noqa: BLE001  状态页不许被统计失败拖垮
        pass
    enabled = False
    try:
        conf = getattr(runtime, "conf", None)
        if conf is not None:
            enabled = bool(conf.enabled(FACE_KEY))
    except Exception:  # noqa: BLE001
        enabled = False
    data["enabled"] = enabled
    return data


async def _prepare(runtime: Any) -> None:
    """懒初始化兜底（真 runtime 一定有；测试替身可以没有）。"""
    ensure = getattr(runtime, "ensure_loaded", None)
    if callable(ensure):
        await ensure()


async def handle_profiles(runtime: Any) -> Dict[str, Any]:
    """``GET profiles``：列出全部档案（存储未就绪时返回 ok:false）。"""
    await _prepare(runtime)
    rows = await _profile_feature(runtime).web_list()
    return {"ok": True, "data": rows}


async def handle_profile_save(runtime: Any, payload: Any) -> Dict[str, Any]:
    """``POST profile_save``：写一份档案。"""
    await _prepare(runtime)
    data = await _profile_feature(runtime).web_save(payload)
    return {"ok": True, "data": data}


async def handle_profile_delete(runtime: Any, payload: Any) -> Dict[str, Any]:
    """``POST profile_delete``：删一份档案。"""
    await _prepare(runtime)
    data = await _profile_feature(runtime).web_delete(payload)
    return {"ok": True, "data": data}


async def handle_inject_log(runtime: Any) -> Dict[str, Any]:
    """``GET inject_log``：最近 10 轮的提示词注入记录（永远 ok）。"""
    await _prepare(runtime)
    items = await inject_log.load(getattr(runtime, "kv", None))
    return {"ok": True, "data": {"items": items}}


async def handle_groups(context: Any) -> Dict[str, Any]:
    """``GET groups``：机器人所在群列表（R9 · token 白名单一键填充）。

    只认 **aiocqhttp（OneBot）** 平台实例：``get_client().call_action(
    "get_group_list")`` 拉群列表，每项返回 ``{group_id, group_name, umo}``。

    ``umo`` 用**该实例的平台 id** 拼（``{platform_id}:GroupMessage:{群号}``）
    —— 与核心 ``unified_msg_origin`` 的拼法一致；平台 id 不一定是
    ``aiocqhttp``（用户可在配置里改），写死会拼出永远匹配不上的 UMO。
    """
    try:
        pm = getattr(context, "platform_manager", None)
        insts: List[Any] = list(getattr(pm, "platform_insts", None) or [])
    except Exception:  # noqa: BLE001
        insts = []
    groups: List[Dict[str, str]] = []
    errors: List[str] = []
    for inst in insts:
        try:
            meta = inst.meta()
            if str(getattr(meta, "name", "") or "") != "aiocqhttp":
                continue  # 群列表只有 OneBot 有，别碰其它平台
            pid = str(getattr(meta, "id", "") or "aiocqhttp")
        except Exception:  # noqa: BLE001
            continue
        getter = getattr(inst, "get_client", None)
        client = getter() if callable(getter) else getattr(inst, "bot", None)
        call = getattr(client, "call_action", None)
        if not callable(call):
            continue
        try:
            rows = await call("get_group_list")
        except Exception as exc:  # noqa: BLE001  未连上 / 超时
            errors.append(str(exc))
            continue
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            gid = str(row.get("group_id") or "").strip()
            if not gid:
                continue
            groups.append(
                {
                    "group_id": gid,
                    "group_name": str(row.get("group_name") or ""),
                    "umo": f"{pid}:GroupMessage:{gid}",
                }
            )
    if not groups and errors:
        return {"ok": False, "error": f"获取群列表失败：{errors[0]}"}
    return {"ok": True, "data": groups}


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

    async def read_json() -> tuple:
        """读请求体，返回 ``(payload, err_response)``，两者只会有一个非 ``None``。"""
        try:
            value = await astrbot_web.request.json(default={})
        except Exception:  # noqa: BLE001
            return None, _err("请求体不是合法 JSON", astrbot_web)
        if not isinstance(value, dict):
            return None, _err("请求体必须是 JSON 对象", astrbot_web)
        return value, None

    async def _profile_post(handler: Any) -> Any:
        try:
            payload, err = await read_json()
            if err is not None:
                return err
            result = await handler(runtime, payload)
            if not result.get("ok"):
                return _err(result.get("error") or "请求失败", astrbot_web, result.get("data"))
            return _ok(result.get("data"), astrbot_web)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, astrbot_web)

    async def ping() -> Any:
        return _ok(
            {"version": __version__, "plugin": PLUGIN_NAME, "priority": HOOK_PRIORITY},
            astrbot_web,
        )

    async def state() -> Any:
        try:
            # 兜底初始化：热装的插件收不到 on_astrbot_loaded，必须在这里补上，
            # 否则页面会永远显示"未加载"。
            await runtime.ensure_loaded()
            return _ok(build_state(runtime), astrbot_web)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, astrbot_web)

    async def setting() -> Any:
        try:
            payload, err = await read_json()
            if err is not None:
                return err
            result = await handle_setting(runtime, payload)
            if not result.get("ok"):
                return _err(result.get("error") or "写入失败", astrbot_web, result.get("data"))
            return _ok(result.get("data"), astrbot_web)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, astrbot_web)

    async def profiles() -> Any:
        try:
            result = await handle_profiles(runtime)
            if not result.get("ok"):
                return _err(result.get("error") or "请求失败", astrbot_web, result.get("data"))
            return _ok(result.get("data"), astrbot_web)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, astrbot_web)

    async def profile_save() -> Any:
        return await _profile_post(handle_profile_save)

    async def profile_delete() -> Any:
        return await _profile_post(handle_profile_delete)

    async def inject_log_route() -> Any:
        try:
            result = await handle_inject_log(runtime)
            return _ok(result.get("data"), astrbot_web)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, astrbot_web)

    async def groups_route() -> Any:
        try:
            result = await handle_groups(context)
            if not result.get("ok"):
                return _err(result.get("error") or "请求失败", astrbot_web, result.get("data"))
            return _ok(result.get("data"), astrbot_web)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, astrbot_web)

    try:
        register(f"{base}/ping", ping, ["GET"], "XBNEXT 存活探测")
        register(f"{base}/state", state, ["GET"], "XBNEXT 运行状态")
        register(f"{base}/setting", setting, ["POST"], "XBNEXT 写入配置项")
        register(f"{base}/profiles", profiles, ["GET"], "XBNEXT 用户档案列表")
        register(f"{base}/profile_save", profile_save, ["POST"], "XBNEXT 写入用户档案")
        register(f"{base}/profile_delete", profile_delete, ["POST"], "XBNEXT 删除用户档案")
        register(f"{base}/inject_log", inject_log_route, ["GET"], "XBNEXT 提示词注入记录")
        register(f"{base}/groups", groups_route, ["GET"], "XBNEXT 所在群列表（token 白名单）")
    except Exception:  # noqa: BLE001  重复注册等，交给调用方记日志
        return False
    return True


__all__ = [
    "register_web_api",
    "build_state",
    "handle_setting",
    "handle_profiles",
    "handle_profile_save",
    "handle_profile_delete",
    "handle_inject_log",
    "handle_groups",
    "PROFILE_KEY",
]
