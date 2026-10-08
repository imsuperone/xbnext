# -*- coding: utf-8 -*-
"""结构自检：版本号同步、配置 schema 完整性、功能注册表一致性。

这些是最容易"改一处漏两处"的地方，用机械校验代替肉眼核对。
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from conftest import _ROOT  # noqa: F401  触发 sys.path 注入

import xbnext
from xbnext import config as xbconfig
from xbnext import features as xbfeatures


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestVersionSync(unittest.TestCase):
    """版本号必须四处一致（metadata / CHANGELOG / README / index.html）。"""

    def test_metadata_version(self):
        text = _read(_ROOT / "metadata.yaml")
        match = re.search(r'^version:\s*["\']?([^"\'\n]+)["\']?\s*$', text, re.M)
        self.assertIsNotNone(match, "metadata.yaml 缺少 version 行")
        self.assertEqual(match.group(1).strip(), xbnext.__version__)

    def test_version_read_from_metadata(self):
        """版本号唯一来源是 metadata.yaml，读失败会回退成 0.0.0。"""
        self.assertNotEqual(xbnext.__version__, "0.0.0", "metadata.yaml 版本号读取失败")

    def test_no_hardcoded_version_in_python(self):
        """Python 源码里不许出现版本字面量（aidoc/03 红线 §2）。"""
        pattern = re.compile(r'__version__\s*=\s*["\']\d+\.\d+\.\d+["\']')
        for path in (_ROOT / "xbnext").rglob("*.py"):
            with self.subTest(path=path.name):
                self.assertIsNone(
                    pattern.search(_read(path)), f"{path} 硬编码了版本字面量"
                )

    def test_changelog_first_entry(self):
        path = _ROOT / "CHANGELOG.md"
        self.assertTrue(path.exists(), "缺少 CHANGELOG.md")
        first = re.search(r"^##\s+v?([0-9]+\.[0-9]+\.[0-9]+)", _read(path), re.M)
        self.assertIsNotNone(first, "CHANGELOG.md 缺少 ## vX.Y.Z 首条")
        self.assertEqual(first.group(1), xbnext.__version__)

    def test_readme_version(self):
        path = _ROOT / "README.md"
        self.assertTrue(path.exists(), "缺少 README.md")
        found = re.search(r"v(\d+\.\d+\.\d+)", _read(path))
        self.assertIsNotNone(found, "README.md 缺少版本号")
        self.assertEqual(found.group(1), xbnext.__version__)

    def test_semver_format(self):
        self.assertRegex(xbnext.__version__, r"^\d+\.\d+\.\d+$")

    def test_index_html_version(self):
        """WebUI 静态版本位与缓存戳也必须跟版本走（第四处）。"""
        path = _ROOT / "pages" / "manager" / "index.html"
        self.assertTrue(path.exists(), "缺少 pages/manager/index.html")
        text = _read(path)
        found = re.search(r'data-ver="(\d+\.\d+\.\d+)"', text)
        self.assertIsNotNone(found, "index.html 缺少 data-ver 版本位")
        self.assertEqual(found.group(1), xbnext.__version__)
        stamps = re.findall(r"\?v=(\d+\.\d+\.\d+)", text)
        self.assertTrue(stamps, "index.html 缺少 ?v= 缓存戳")
        for stamp in stamps:
            self.assertEqual(stamp, xbnext.__version__, "?v= 缓存戳没跟版本走")


class TestConfSchema(unittest.TestCase):
    """_conf_schema.json 每项必须有 type / description / hint / default。"""

    REQUIRED = ("type", "description", "hint", "default")
    ALLOWED_TYPES = {"bool", "string", "int", "list", "object"}

    def test_schema_loads(self):
        self.assertTrue(xbconfig.SCHEMA, "_conf_schema.json 没读到内容")

    def test_every_entry_complete(self):
        for key, item in xbconfig.SCHEMA.items():
            with self.subTest(key=key):
                self.assertIsInstance(item, dict, f"{key} 不是对象")
                for field in self.REQUIRED:
                    self.assertIn(field, item, f"{key} 缺少 {field}")
                self.assertIn(
                    item["type"], self.ALLOWED_TYPES, f"{key} 类型非法: {item['type']}"
                )

    def test_condition_keys_exist(self):
        for key, item in xbconfig.SCHEMA.items():
            cond = item.get("condition")
            if not cond:
                continue
            for dep in cond:
                with self.subTest(key=key, dep=dep):
                    self.assertIn(dep, xbconfig.SCHEMA, f"{key} 依赖了不存在的 {dep}")

    def test_bool_defaults_are_bool(self):
        for key, item in xbconfig.SCHEMA.items():
            if item["type"] == "bool":
                self.assertIsInstance(item["default"], bool, f"{key} 默认值不是 bool")

    def test_as_dict_covers_all_keys(self):
        conf = xbconfig.Config({})
        self.assertEqual(set(conf.as_dict()), set(xbconfig.SCHEMA))


class TestFeatureRegistry(unittest.TestCase):
    """功能注册表必须与配置 schema、开关表对得上。"""

    def test_features_non_empty(self):
        self.assertGreaterEqual(len(xbfeatures.FEATURES), 4)

    def test_keys_unique(self):
        keys = [f.key for f in xbfeatures.FEATURES]
        self.assertEqual(len(keys), len(set(keys)), f"功能 key 重复: {keys}")

    def test_keys_exist_in_schema(self):
        for feat in xbfeatures.FEATURES:
            with self.subTest(feat=feat.key):
                self.assertIn(
                    feat.key, xbconfig.SCHEMA, f"{feat.key} 没有对应配置项"
                )
                self.assertEqual(
                    xbconfig.SCHEMA[feat.key]["type"], "bool", f"{feat.key} 必须是开关"
                )

    def test_meta_fields_filled(self):
        for feat in xbfeatures.FEATURES:
            with self.subTest(feat=feat.key):
                self.assertTrue(feat.name, f"{feat.key} 缺 name")
                self.assertTrue(feat.description, f"{feat.key} 缺 description")
                self.assertGreater(feat.order, 0, f"{feat.key} 缺 order")

    def test_execution_order(self):
        orders = [f.order for f in xbfeatures.all_features()]
        self.assertEqual(orders, sorted(orders), "执行顺序未按 order 升序")
        # 注入类必须排在清洗类之后
        self.assertLess(
            xbfeatures.get_feature("enable_quote_clean").order,
            xbfeatures.get_feature("enable_user_profile").order,
            "档案注入必须排在清洗之后",
        )

    def test_describe(self):
        for feat in xbfeatures.FEATURES:
            meta = feat.describe()
            self.assertEqual(meta["key"], feat.key)


class TestLayout(unittest.TestCase):
    """目录与声明文件的基本完整性。"""

    def test_required_files(self):
        for rel in (
            "main.py",
            "metadata.yaml",
            "_conf_schema.json",
            "README.md",
            "CHANGELOG.md",
        ):
            with self.subTest(path=rel):
                self.assertTrue((_ROOT / rel).exists(), f"缺少 {rel}")

    def test_pages_declared_and_exist(self):
        text = _read(_ROOT / "metadata.yaml")
        self.assertIn("pages:", text, "metadata.yaml 必须显式声明 pages:")
        match = re.search(r"^\s*path:\s*(\S+)\s*$", text, re.M)
        self.assertIsNotNone(match, "pages 里缺少 path")
        self.assertTrue(
            (_ROOT / match.group(1)).exists(), f"pages 指向的文件不存在: {match.group(1)}"
        )

    def test_main_is_thin(self):
        """main.py 只许转发，不许堆业务（防止骨架被写胖）。"""
        source = _read(_ROOT / "main.py")
        for forbidden in ("def clean_prompt", "class ReplyTargetStore", "def sanitize"):
            self.assertNotIn(forbidden, source, f"main.py 出现业务实现: {forbidden}")

    def test_aidoc_present(self):
        self.assertTrue((_ROOT / "aidoc" / "README.md").exists(), "缺少 aidoc/README.md")

    def test_every_module_importable(self):
        """全包可导入 —— 抓相对导入层级错误（compileall 查不出这类问题）。"""
        import importlib
        import pkgutil

        import xbnext

        broken = []
        for info in pkgutil.walk_packages(xbnext.__path__, prefix="xbnext."):
            try:
                importlib.import_module(info.name)
            except Exception as exc:  # noqa: BLE001
                broken.append(f"{info.name}: {exc!r}")
        self.assertEqual(broken, [], "以下模块导入失败：\n" + "\n".join(broken))

    def test_web_api_registrable(self):
        """web 层能拿到版本号与插件名（相对导入层级正确的旁证）。"""
        from xbnext import web

        self.assertEqual(web.PLUGIN_NAME, xbnext.PLUGIN_NAME)
        self.assertEqual(web.__version__, xbnext.__version__)

    def test_features_all_importable(self):
        from xbnext import features

        for feat in features.all_features():
            self.assertTrue(callable(feat.on_llm_request), feat.key)


if __name__ == "__main__":
    unittest.main()
