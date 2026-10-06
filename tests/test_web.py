# -*- coding: utf-8 -*-
"""配置写入（switches.apply_conf / coerce_value）与 Web 端点载荷的单测。

这一层是 WebUI 唯一能改插件配置的入口，必须锁住三件事：

1. 类型按 schema 收敛，非法值在写入前就被拒；
2. 落盘失败必须**回滚内存**——页面上看到的必须等于实际存下来的；
3. 键白名单挡住任意键写入。
"""

from __future__ import annotations

import asyncio
import unittest

from conftest import _ROOT  # noqa: F401  保证 sys.path 已就位

from xbnext import switches
from xbnext.config import SCHEMA, Config
from xbnext.web import build_state, handle_setting


class _RawConf:
    """只有 dict 读写、没有任何持久化方法的配置对象（旧版 AstrBot）。"""

    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def __setitem__(self, key, value):
        self.data[key] = value

    def __delitem__(self, key):
        del self.data[key]

    def __getitem__(self, key):
        return self.data[key]


class Saver(_RawConf):
    """带同步持久化能力的假配置对象。"""

    def __init__(self, fail: bool = False):
        super().__init__()
        self.fail = fail
        self.saved = 0

    def save_config(self):
        if self.fail:
            raise IOError("disk full")
        self.saved += 1
        return True


class ReadOnly(_RawConf):
    """没有 ``save_*`` 方法的配置对象 —— ``getattr`` 探测必须落空。"""



class FakeRuntime:
    """build_state / handle_setting 需要的最小 runtime。"""

    def __init__(self, raw=None):
        self.conf = Config(raw if raw is not None else Saver())

    def status(self):
        return {
            "version": "9.9.9",
            "loaded": True,
            "features": {},
            "astrna": {"installed": False, "switches": {}},
            "kv_usable": True,
        }


class CoerceTest(unittest.TestCase):
    def test_bool_accepts_native_and_strings(self):
        self.assertIs(switches.coerce_value("debug_log", True), True)
        self.assertIs(switches.coerce_value("debug_log", "TRUE"), True)
        self.assertIs(switches.coerce_value("debug_log", "off"), False)
        self.assertIs(switches.coerce_value("debug_log", 1), True)

    def test_bool_rejects_garbage(self):
        with self.assertRaises(ValueError):
            switches.coerce_value("debug_log", "maybe")

    def test_int_coerces_and_rejects(self):
        self.assertEqual(switches.coerce_value("reply_scope_depth", "5"), 5)
        with self.assertRaises(ValueError):
            switches.coerce_value("reply_scope_depth", "abc")

    def test_string_coerces(self):
        self.assertEqual(
            switches.coerce_value("face_format", 123), "123"
        )

    def test_unknown_key_rejected(self):
        with self.assertRaises(ValueError):
            switches.coerce_value("not_a_real_key", 1)

    def test_every_schema_key_is_coercible(self):
        """schema 里每个键都得能被识别，否则 WebUI 会写不动。"""
        for key in SCHEMA:
            with self.subTest(key=key):
                default = SCHEMA[key].get("default")
                self.assertEqual(switches.coerce_value(key, default), default)

    def test_writable_keys_cover_schema(self):
        self.assertEqual(set(switches.WRITABLE_KEYS), set(SCHEMA))


class ApplyConfTest(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_write_and_persist(self):
        saver = Saver()
        conf = Config(saver)
        out = self._run(switches.apply_conf(conf, "debug_log", True))
        self.assertTrue(out["persisted"])
        self.assertIs(out["value"], True)
        self.assertIs(saver.data["debug_log"], True)
        self.assertEqual(saver.saved, 1)
        self.assertIsNone(out["error"])

    def test_rolls_back_when_save_fails(self):
        """落盘失败必须回滚 —— 否则页面显示的值和磁盘不一致。"""
        saver = Saver(fail=True)
        conf = Config(saver)
        out = self._run(switches.apply_conf(conf, "debug_log", True))
        self.assertFalse(out["persisted"])
        self.assertIn("未保存", out["error"])
        self.assertNotIn("debug_log", saver.data)
        self.assertEqual(saver.saved, 0)

    def test_rolls_back_to_previous_value(self):
        """原本就有值的键，回滚要恢复旧值而不是删掉。"""
        saver = Saver(fail=True)
        saver.data["face_format"] = "[旧:{name}]"
        conf = Config(saver)
        out = self._run(switches.apply_conf(conf, "face_format", "[新:{name}]"))
        self.assertFalse(out["persisted"])
        self.assertEqual(saver.data["face_format"], "[旧:{name}]")
        self.assertEqual(out["value"], "[旧:{name}]")

    def test_rejects_bad_value_without_touching_raw(self):
        saver = Saver()
        conf = Config(saver)
        out = self._run(switches.apply_conf(conf, "reply_scope_depth", "abc"))
        self.assertFalse(out["persisted"])
        self.assertIn("整数", out["error"])
        self.assertEqual(saver.data, {})

    def test_unknown_key_never_writes(self):
        saver = Saver()
        conf = Config(saver)
        out = self._run(switches.apply_conf(conf, "__proto__", 1))
        self.assertFalse(out["persisted"])
        self.assertEqual(saver.data, {})

    def test_config_without_save_capability_reports_error(self):
        conf = Config(ReadOnly())
        out = self._run(switches.apply_conf(conf, "debug_log", True))
        self.assertFalse(out["persisted"])
        self.assertIn("持久化", out["error"])

    def test_none_raw_is_reported_not_raised(self):
        conf = Config(None)
        out = self._run(switches.apply_conf(conf, "debug_log", True))
        self.assertFalse(out["persisted"])
        self.assertIn("配置对象", out["error"])


class SaveConfigTest(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_prefers_async_save(self):
        class AsyncSaver(Saver):
            async def save_config_async(self):
                self.saved += 1
                return True

        saver = AsyncSaver()
        self.assertTrue(self._run(switches.save_config(saver)))
        self.assertEqual(saver.saved, 1)

    def test_sync_save_runs_in_thread(self):
        saver = Saver()
        self.assertTrue(self._run(switches.save_config(saver)))
        self.assertEqual(saver.saved, 1)

    def test_missing_capability_raises(self):
        with self.assertRaises(RuntimeError):
            self._run(switches.save_config(ReadOnly()))

    def test_none_raises(self):
        with self.assertRaises(RuntimeError):
            self._run(switches.save_config(None))


class BuildStateTest(unittest.TestCase):
    def test_contains_everything_frontend_needs(self):
        data = build_state(FakeRuntime())
        self.assertEqual(data["version"], "9.9.9")
        self.assertTrue(data["loaded"])
        self.assertIn("config", data)
        self.assertIn("schema", data)
        self.assertEqual(data["hook_priority"], 1000)
        self.assertEqual(set(data["writable_keys"]), set(SCHEMA))

    def test_schema_exposes_type_and_condition(self):
        data = build_state(FakeRuntime())
        item = data["schema"]["quote_placeholder_action"]
        self.assertEqual(item["type"], "string")
        self.assertEqual(item["condition"], {"enable_quote_clean": True})

    def test_config_defaults_present_even_when_raw_empty(self):
        data = build_state(FakeRuntime(Saver()))
        self.assertEqual(data["config"]["face_format"], "[表情:{name}]")
        self.assertIs(data["config"]["enable_quote_clean"], True)


class HandleSettingTest(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_rejects_non_object_body(self):
        out = self._run(handle_setting(FakeRuntime(), ["not", "a", "dict"]))
        self.assertFalse(out["ok"])

    def test_rejects_missing_key(self):
        out = self._run(handle_setting(FakeRuntime(), {"value": 1}))
        self.assertFalse(out["ok"])
        self.assertIn("key", out["error"])

    def test_rejects_unknown_key(self):
        out = self._run(handle_setting(FakeRuntime(), {"key": "rm -rf", "value": 1}))
        self.assertFalse(out["ok"])
        self.assertIn("未知配置项", out["error"])

    def test_rejects_missing_value(self):
        out = self._run(handle_setting(FakeRuntime(), {"key": "debug_log"}))
        self.assertFalse(out["ok"])
        self.assertIn("value", out["error"])

    def test_writes_valid_value(self):
        saver = Saver()
        out = self._run(handle_setting(FakeRuntime(saver), {"key": "debug_log", "value": "yes"}))
        self.assertTrue(out["ok"])
        self.assertIs(out["data"]["value"], True)
        self.assertTrue(out["data"]["persisted"])
        self.assertIs(saver.data["debug_log"], True)

    def test_persistence_failure_surfaces_as_not_ok(self):
        out = self._run(
            handle_setting(FakeRuntime(Saver(fail=True)), {"key": "debug_log", "value": True})
        )
        self.assertFalse(out["ok"])
        self.assertIn("未保存", out["error"])


if __name__ == "__main__":
    unittest.main()
