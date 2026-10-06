# -*- coding: utf-8 -*-
"""调度中心。

**所有钩子的实现都在这里**，``main.py`` 只做转发。

``on_llm_request`` 内的固定执行顺序（见 aidoc/02-架构设计.md §2）::

    1. 开关判定              每个功能进门前先 ctx.enabled(key)，关着就直接跳过
    2. quote_clean           先清垃圾（后面的注入不再看见脏内容）
    3. face_translate        表情翻译
    4. reply_attribution     回复指向三方说明
    5. user_profile          当前发言人档案（最后注入，不会被本插件自己清洗掉）

理由：**清洗在前、注入在后**；档案放最后 ⇒ 它绝不会被本插件的清洗删掉。

异常纪律（AstrBot 官方原则 4）：单个功能炸掉只记日志，不影响其他功能，
更不能让整个请求失败。
"""

from __future__ import annotations

import inspect
from typing import Any, Dict, List

from . import commands, features
from .config import Config
from .context import RequestContext
from .storage import KV


class XbnextRuntime:
    """XBNEXT 运行时：装配功能、对账开关、按序调度。"""

    def __init__(
        self,
        config: Any = None,
        kv_store: Any = None,
        logger: Any = None,
    ):
        self.conf = Config(config)
        self.log = logger
        self.kv = KV(owner=kv_store, logger=logger)
        #: 按 ``order`` 升序排列的功能实例（来源：features 注册表）
        self.features: List[Any] = features.all_features()
        self._loaded = False

    # ==================================================================
    # 生命周期
    # ==================================================================
    async def on_loaded(self) -> None:
        """AstrBot 加载完成后：初始化各功能（**幂等**，重复调用直接返回）。

        幂等是必须的 —— ``ensure_loaded`` 会在每个入口兜底调用一次，
        不能再跑一遍 ``on_load``（会重建各功能的存储句柄）。
        """
        if self._loaded:
            return
        for feat in self.features:
            await self._call(feat, "on_load", self)
        self._loaded = True
        self._info(
            "已加载 "
            + ", ".join(f"{f.key}={'on' if self.conf.enabled(f.key) else 'off'}"
                        for f in self.features)
        )

    async def ensure_loaded(self) -> None:
        """懒初始化兜底。

        AstrBot 的 ``on_astrbot_loaded`` **只在核心启动收尾时广播一次**，
        启动之后才装上/热更新的插件永远收不到 —— 真机上表现为 WebUI 显示
        「未加载」、``/xbnext profile`` 报「档案存储还没就绪」。
        因此每个异步入口（LLM 请求 / 回复落库 / 子指令 / Web state）都先过这里。
        """
        if not self._loaded:
            await self.on_loaded()

    def get_feature(self, key: str) -> Any:
        """按配置键取功能实例（WebUI 端点用）；找不到返回 ``None``。"""
        for feat in self.features:
            if feat.key == key:
                return feat
        return None

    async def terminate(self) -> None:
        """插件卸载：逆序释放各功能。"""
        for feat in reversed(self.features):
            await self._call(feat, "on_unload")
        self.kv.invalidate()
        self._loaded = False
        self._info("已卸载")

    # ==================================================================
    # 主入口
    # ==================================================================
    async def handle_llm_request(self, event: Any, req: Any) -> None:
        """按固定顺序执行本轮该跑的功能。"""
        await self.ensure_loaded()
        ctx = RequestContext(event=event, req=req, conf=self.conf, runtime=self)
        for feat in self.features:
            try:
                if not ctx.enabled(feat.key):
                    continue
                await self._invoke(feat.on_llm_request, ctx)
            except Exception as exc:  # noqa: BLE001  单功能失败不许拖垮请求
                self._warn(f"{feat.key} 执行失败：{exc!r}")
        if self.conf.bool("debug_log"):
            ctx.flush_notes(self.log)
            if ctx.injected:
                self._debug(f"本轮注入 {ctx.injected} 段")

    async def handle_message_sent(self, event: Any) -> None:
        """bot 回复已发出 → 通知关心落库的功能（R1）。"""
        await self.ensure_loaded()
        ctx = RequestContext(event=event, req=None, conf=self.conf, runtime=self)
        for feat in self.features:
            try:
                if not feat.uses_sent_hook or not ctx.enabled(feat.key):
                    continue
                await self._invoke(feat.on_message_sent, ctx)
            except Exception as exc:  # noqa: BLE001
                self._warn(f"{feat.key} 记录回复失败：{exc!r}")

    # ==================================================================
    # 外挂点
    # ==================================================================
    def register_commands(self, star: Any) -> None:
        """装配期把 ``Star`` 实例交给需要它（读写 KV、发消息）的功能。

        指令本身仍然静态写在 ``main.py`` —— AstrBot 的钩子扫描发生在类
        加载阶段，动态挂载子命令不可靠。
        """
        for feat in self.features:
            hook = getattr(feat, "bind", None)
            if callable(hook):
                try:
                    hook(self, star)
                except Exception as exc:  # noqa: BLE001
                    self._warn(f"{feat.key} 绑定失败：{exc!r}")

    async def handle_command(self, name: str, event: Any) -> str:
        """把 ``/xbnext <name> ...`` 的剩余参数交给对应功能处理。

        **不经过 ``ctx.enabled``** —— 子指令是维护入口，不能因为功能开关
        关着就用不了（见 ``_conf_schema.json`` 里 ``enable_user_profile`` 的 hint）。

        返回要发给用户的文本；异常返回带原因的兜底文案，**不裸抛**。
        """
        await self.ensure_loaded()
        try:
            sub, args = commands.split(str(getattr(event, "message_str", "") or ""))
        except Exception as exc:  # noqa: BLE001
            return f"指令解析失败：{exc!r}"
        # AstrBot 可能已把 "xbnext profile" 一并剥掉 → 把 sub 当第一个参数回填
        if sub != name:
            args = ([sub] if sub else []) + args

        feat = features.get_feature_by_command(name)
        if feat is None:
            return f"没有 /xbnext {name} 这个子指令。"
        handler = getattr(feat, "handle_command", None)
        if not callable(handler):
            return f"/xbnext {name} 没有实现处理逻辑。"
        try:
            result = await handler(event, args, self.conf)
        except Exception as exc:  # noqa: BLE001
            self._warn(f"/xbnext {name} 执行失败：{exc!r}")
            return f"指令执行失败：{exc!r}"
        return str(result or "")

    # ==================================================================
    # 状态（/xbnext status 与 WebUI 共用）
    # ==================================================================
    def status(self) -> Dict[str, Any]:
        """导出运行状态：版本、功能开关、KV 可用性。"""
        return {
            "version": self._version(),
            "loaded": self._loaded,
            "features": {
                f.key: {
                    "name": f.name,
                    "description": f.description,
                    "enabled": self.conf.enabled(f.key),
                }
                for f in self.features
            },
            "kv_usable": self.kv.usable,
        }

    def status_lines(self) -> List[str]:
        """把 :meth:`status` 压成给群里看的纯文本行。"""
        st = self.status()
        lines = [f"XBNEXT v{st['version']}" + ("（已加载）" if st["loaded"] else "（未加载）")]
        for key, item in st["features"].items():
            mark = "✅" if item["enabled"] else "❌"
            lines.append(f"{mark} {item['name']}")
        lines.append("KV：" + ("可用" if st["kv_usable"] else "不可用"))
        return lines

    # ==================================================================
    # 内部
    # ==================================================================
    async def _invoke(self, fn: Any, ctx: RequestContext) -> None:
        """调用功能钩子，自动 await 协程版。"""
        result = fn(ctx)
        if inspect.isawaitable(result):
            await result

    async def _call(self, feat: Any, name: str, *args: Any) -> None:
        """安全调用功能上的可选生命周期钩子。"""
        hook = getattr(feat, name, None)
        if not callable(hook):
            return
        try:
            result = hook(*args)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:  # noqa: BLE001
            self._warn(f"{getattr(feat, 'key', name)} {name} 失败：{exc!r}")

    def _version(self) -> str:
        from . import __version__

        return __version__

    # -- 日志 ---------------------------------------------------------
    def _emit(self, level: str, message: str) -> None:
        method = getattr(self.log, level, None)
        if callable(method):
            try:
                method(message)
            except Exception:  # noqa: BLE001
                pass

    def _info(self, message: str) -> None:
        self._emit("info", f"[XBNEXT] {message}")

    def _debug(self, message: str) -> None:
        self._emit("debug", f"[XBNEXT] {message}")

    def _warn(self, message: str) -> None:
        self._emit("warning", f"[XBNEXT] {message}")


__all__ = ["XbnextRuntime"]
