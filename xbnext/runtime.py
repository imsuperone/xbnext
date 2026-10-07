# -*- coding: utf-8 -*-
"""调度中心。

**所有钩子的实现都在这里**，``main.py`` 只做转发。

两个入口，跑在管线的不同阶段：

1. :meth:`XbnextRuntime.handle_adapter_message` —— ``event_message_type(ALL)``
   早期钩子，**在 core 判定「空消息」之前**跑（纯表情补写 ``message_str``）；
2. :meth:`XbnextRuntime.handle_llm_request` —— ``on_llm_request``，
   按固定顺序执行（见 aidoc/02-架构设计.md §2）::

       1. 开关判定              每个功能进门前先 ctx.enabled(key)，关着就直接跳过
       2. image_slim            历史旧图换占位（R8，省输入 token）
       3. quote_clean           先清垃圾（后面的注入不再看见脏内容）
       4. face_translate        表情翻译
       5. reply_attribution     回复指向三方说明
       6. user_profile          当前发言人档案（最后注入，不会被本插件自己清洗掉）

   （recall 走适配器早期钩子、tokenline 走 ``on_llm_response`` +
   ``on_decorating_result``，都不在这条主链里。）

理由：**清洗在前、注入在后**；档案放最后 ⇒ 它绝不会被本插件的清洗删掉。

**日志纪律**：``handle_llm_request`` 只在**本轮真有动作**时打一条 INFO 汇总
（谁做了什么），没动作就一声不吭 —— 详见 :meth:`XbnextRuntime.handle_llm_request`
的 docstring。详细备注仍然只在 ``debug_log`` 打开时逐条落 DEBUG。

异常纪律（AstrBot 官方原则 4）：单个功能炸掉只记日志，不影响其他功能，
更不能让整个请求失败。
"""

from __future__ import annotations

import inspect
from typing import Any, Dict, List

from . import commands, features, inject_log, injector
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
        """按固定顺序执行本轮该跑的功能，**有动作时打一条 INFO 汇总**。

        日志纪律（真机第二轮反馈：「档案查询/注入日志没看到 …但是这样有点乱」）：

        - **只在本轮真有动作时**打一条汇总行，形如::

              [XBNEXT] 本轮 引用占位清洗·改写正文、用户档案·注入1段

          每个功能一段，用「·」连接它的动作（注入N段 / 改写正文 /
          清洗N块 / 清图N）；没有任何功能动作 ⇒ 一行不打，日志不被
          正常请求刷屏；
        - 详细备注仍然只在 ``debug_log`` 打开时逐条落 DEBUG（不变）；
        - 单个功能失败仍只打 WARNING，不影响其他功能（AstrBot 原则 4）。
        """
        await self.ensure_loaded()
        ctx = RequestContext(event=event, req=req, conf=self.conf, runtime=self)
        actions: List[str] = []
        for feat in self.features:
            try:
                if not ctx.enabled(feat.key):
                    continue
                inj_before = ctx.injected
                prompt_before = ctx.prompt()
                urls_before = self._image_url_count(req)
                parts_before = ctx.parts_cleaned
                slim_before = ctx.slimmed_images
                await self._invoke(feat.on_llm_request, ctx)
            except Exception as exc:  # noqa: BLE001  单功能失败不许拖垮请求
                self._warn(f"{feat.key} 执行失败：{exc!r}")
                continue
            acts = []
            if ctx.injected > inj_before:
                acts.append(f"注入{ctx.injected - inj_before}段")
            if ctx.prompt() != prompt_before:
                acts.append("改写正文")
            if ctx.parts_cleaned > parts_before:
                acts.append(f"清洗{ctx.parts_cleaned - parts_before}块")
            if ctx.slimmed_images > slim_before:
                acts.append(f"瘦历史图{ctx.slimmed_images - slim_before}张")
            urls_after = self._image_url_count(req)
            if urls_after != urls_before:
                acts.append(f"清图{urls_before}→{urls_after}")
            if acts:
                actions.append(f"{feat.name}·{'、'.join(acts)}")
        if self.conf.bool("debug_log"):
            ctx.flush_notes(self.log)
        if actions:
            self._info("本轮 " + "、".join(actions))

        # P16 · 注入记录：把发给模型的最终态存一份，供 WebUI「运行状态」
        # 底部的「提示词注入记录」入口查看。KV 不可用时照常降级进内存缓存
        # （本期可读、重启丢），只有 debug_log 打开才吭声 —— 不给日志加噪。
        try:
            entry = inject_log.build_entry(event, actions, req)
            if not await inject_log.record(self.kv, entry) and self.conf.bool("debug_log"):
                self._debug("注入记录未落盘（KV 不可用，本期仍可查看）")
        except Exception as exc:  # noqa: BLE001
            if self.conf.bool("debug_log"):
                self._debug(f"注入记录失败：{exc!r}")

    async def handle_llm_response(self, event: Any, resp: Any) -> None:
        """``on_llm_response`` 钩子：把本轮用量交给关心它的功能（R9）。

        只有标了 ``uses_llm_response_hook`` 的功能会被调用；用量取数、
        白名单判定都在功能自己（``tokenline``）。单功能失败只记警告，
        钩子本身绝不冒泡 —— core 会把异常变成发给用户的消息。
        """
        try:
            await self.ensure_loaded()
        except Exception as exc:  # noqa: BLE001
            self._warn(f"用量钩子初始化失败：{exc!r}")
            return
        ctx = RequestContext(event=event, req=None, conf=self.conf, runtime=self)
        for feat in self.features:
            try:
                if not getattr(feat, "uses_llm_response_hook", False):
                    continue
                if not ctx.enabled(feat.key):
                    continue
                result = feat.on_llm_response(ctx, resp)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # noqa: BLE001
                self._warn(f"{feat.key} 记录用量失败：{exc!r}")

    async def handle_adapter_message(self, event: Any) -> None:
        """适配器早期钩子（``event_message_type(ALL)``）。

        跑在 core 判定 ``has_valid_message`` **之前** —— 此时 ``req`` 还不存在，
        所以 :class:`~xbnext.context.RequestContext` 的 ``req`` 是 ``None``
        （纯补写不走注入）。只有标了 ``uses_adapter_hook`` 的功能会被调用。

        整体异常自吞：AstrBot 的 ``call_handler`` 会把钩子异常变成一条
        发给用户的错误消息，我们不希望出现这种噪音。
        """
        try:
            await self.ensure_loaded()
        except Exception as exc:  # noqa: BLE001
            self._warn(f"早期钩子初始化失败：{exc!r}")
            return
        before = str(getattr(event, "message_str", "") or "")
        ctx = RequestContext(event=event, req=None, conf=self.conf, runtime=self)
        for feat in self.features:
            try:
                if not getattr(feat, "uses_adapter_hook", False):
                    continue
                if not ctx.enabled(feat.key):
                    continue
                await self._invoke(feat.on_adapter_message, ctx)
            except Exception as exc:  # noqa: BLE001
                self._warn(f"{feat.key} 早期钩子失败：{exc!r}")
        after = str(getattr(event, "message_str", "") or "")
        if after and after != before:
            self._info(f"纯表情补写正文：{after}")
        if self.conf.bool("debug_log"):
            ctx.flush_notes(self.log)

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

    async def handle_decorating_result(self, event: Any) -> None:
        """发送前输出面处理（``on_decorating_result`` 钩子）。

        模型偶尔会把注入用的 ``<xbnext>`` 注入体原样复述出来 —— 请求面已有
        清洗（quote_clean / injector.sanitize），**输出面此前没有落点**
        （AstrNa 对照④）。这里在 core 发出消息链之前，把纯文本组件的
        注入体整块剥掉（``injector.clean_output``）；随后把接力棒交给
        标了 ``uses_decorating_hook`` 的功能（R9 token 用量行）。

        约定：

        - 只认 ``Plain``（按**类型名 + ``text`` 属性**判定，不 import core
          内部类，测试也好造假件）；图片、卡片等其它组件一律不碰；
        - 必须排在消息转图插件（xbimg, priority=99999）**之前** ——
          它会把整段文本渲染成图片后 ``result.chain = new_chain`` 丢掉所有
          Plain，我们晚一步就只能对着图片干瞪眼。优先级写在 ``main.py``；
        - 清洗在前、功能行在后 —— 功能追加的内容不会被自己剥掉；
        - 异常自吞：处理失败绝不能挡住发消息。
        """
        try:
            result = event.get_result()
        except Exception:  # noqa: BLE001
            result = None
        chain = getattr(result, "chain", None) if result is not None else None
        changed = False
        emptied: List[Any] = []
        if chain:
            for comp in list(chain):
                if type(comp).__name__ != "Plain":
                    continue
                text = getattr(comp, "text", None)
                if not isinstance(text, str) or "<" not in text:
                    continue
                cleaned = injector.clean_output(text)
                if cleaned == text:
                    continue
                comp.text = cleaned
                changed = True
                if not cleaned:
                    emptied.append(comp)
            if changed:
                # 清完变空的组件从链里摘掉 —— core 对空链会跳过发送，
                # 比发出一条空消息干净
                for comp in emptied:
                    try:
                        chain.remove(comp)
                    except ValueError:  # noqa: BLE001
                        pass

        # 功能行（R9 token 用量等）：清洗之后、xbimg 之前
        ctx = RequestContext(event=event, req=None, conf=self.conf, runtime=self)
        for feat in self.features:
            try:
                if not getattr(feat, "uses_decorating_hook", False):
                    continue
                if not ctx.enabled(feat.key):
                    continue
                await self._invoke(feat.on_decorating_result, ctx)
            except Exception as exc:  # noqa: BLE001
                self._warn(f"{feat.key} 输出面处理失败：{exc!r}")

        if changed:
            self._info("输出面清洗：已剥除模型复述的 <xbnext> 注入体")

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
            # 兜底文案给人看，repr 只进日志（真机反馈：回复里出现 Python repr 很乱）
            self._warn(f"指令解析失败：{exc!r}")
            return "指令没看懂，请换种写法：/xbnext status 或 /xbnext profile"
        # AstrBot 可能已把 "xbnext profile" 一并剥掉 → 把 sub 当第一个参数回填
        if sub != name:
            args = ([sub] if sub else []) + args

        feat = features.get_feature_by_command(name)
        if feat is None:
            return f"没有 /xbnext {name} 这个子指令。可用子指令：status、profile。"
        handler = getattr(feat, "handle_command", None)
        if not callable(handler):
            return f"/xbnext {name} 没有实现处理逻辑。"
        try:
            result = await handler(event, args, self.conf)
        except Exception as exc:  # noqa: BLE001
            self._warn(f"/xbnext {name} 执行失败：{exc!r}")
            return f"/xbnext {name} 执行出错（已记录日志），请稍后再试。"
        return str(result or "")

    async def dispatch(self, event: Any) -> str:
        """``/xbnext ...`` 的**统一入口**（单指令分发，xbdoc / xbimg 同款形态）。

        形态矩阵::

            /xbnext           → 指令菜单（commands.MENU）
            /xbnext help|菜单 → 同上
            /xbnext status    → 状态行（别名：状态）
            /xbnext profile…  → handle_command（档案子逻辑）
            /xbnext <其它>    → 一句「未知子指令」提示

        **全部由插件自己答**：不依赖 AstrBot 指令组的「参数不足」树
        （旧版 core 上裸指令会漏给 LLM 乱答，新版会甩一脸技术树，
        真机反馈「/xbnext 还是很丑」的根子就在这），打错的子指令
        也不会再触发 LLM 乱回。
        """
        await self.ensure_loaded()
        text = str(getattr(event, "message_str", "") or "")
        try:
            sub, _ = commands.split(text)
        except Exception as exc:  # noqa: BLE001
            self._warn(f"指令解析失败：{exc!r}")
            return "指令没看懂，请换种写法：/xbnext status 或 /xbnext profile"
        if sub in commands.HELP_WORDS:
            return commands.MENU
        if sub in commands.STATUS_WORDS:
            return "\n".join(self.status_lines())
        if sub == "profile":
            return await self.handle_command("profile", event)
        return f"❓ 未知子指令「{sub}」，发送 /xbnext 可查看可用指令菜单。"

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

    @staticmethod
    def _image_url_count(req: Any) -> int:
        """``req.image_urls`` 的条数（拿不到就算 0，只用于日志汇总）。"""
        try:
            return len(getattr(req, "image_urls", None) or [])
        except Exception:  # noqa: BLE001
            return 0

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
