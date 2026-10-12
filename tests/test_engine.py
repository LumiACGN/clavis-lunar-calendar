#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 LumiACGN
# SPDX-License-Identifier: GPL-3.0-or-later
"""overlay 引擎 v2 的纯 python 单测（全部在 tmp 目录里，零文件系统副作用）。

覆盖：v0.1.0 状态迁移（normalize_v1/load_kit）、标记扫描与归属（scan_markers /
attribute_markers）、kit 排序（iter_kits）、forget 逃生舱、假 HOME 下的服务守卫
（set_service_guard / stop_service / start_service 不碰真实 systemctl）。

本仓的 kit 特性（也是断言重点）：`order = 20`、单 group `lunar-calendar`、
**没有后端脚本**（`bin_name = null` → 状态里 `bin` 为 None，~/.local/bin 不落任何文件）。

引擎是用 importlib 从文件路径加载的（模块名 clavis_overlay），因为它不是包。
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
ENGINE = REPO / "scripts" / "clavis_overlay.py"
MANIFEST = REPO / "patches" / "manifest.json"
MANIFEST_DATA = json.loads(MANIFEST.read_text(encoding="utf-8"))
KIT_ID = MANIFEST_DATA["kit"]["id"]
GROUP = "lunar-calendar"
DUMMY_KIT = "clavis-dummy-kit"
WIDGET = "Modules/Sidebars/Dashboard/infoTools/CalendarWidget.qml"


def load_engine():
    spec = importlib.util.spec_from_file_location("clavis_overlay", ENGINE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


engine = load_engine()


class TmpHomeCase(unittest.TestCase):
    """每个用例一个假 HOME：HOME / XDG_CONFIG_HOME / XDG_STATE_HOME 全指向 tmp。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="engine-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "home").mkdir()
        self.env = {
            "HOME": str(self.tmp / "home"),
            "XDG_CONFIG_HOME": str(self.tmp / "home" / ".config"),
            "XDG_STATE_HOME": str(self.tmp / "home" / ".state"),
            "XDG_CACHE_HOME": str(self.tmp / "home" / ".cache"),
        }
        saved = {key: os.environ.get(key) for key in self.env}
        os.environ.update(self.env)

        def restore():
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.addCleanup(restore)
        self.paths = engine.xdg_paths()
        # 服务守卫是模块级全局，用例结束必须复位，避免污染其它测试
        self.addCleanup(setattr, engine, "_SERVICE_ALLOWED", True)

    def engine_cli(self, *args):
        """以假 HOME 起真实 CLI（模拟用户敲的那条命令）。"""
        return subprocess.run([sys.executable, str(ENGINE), *args],
                              capture_output=True, text=True, check=False)

    def quiet(self):
        """吞掉引擎自己打的 log（保持 unittest 输出干净）。"""
        return contextlib.redirect_stdout(io.StringIO())

    def write_kit_state(self, kit_id, order, **extra):
        self.paths["kits_dir"].mkdir(parents=True, exist_ok=True)
        data = {"schema": 2, "kit": kit_id, "order": order, **extra}
        (self.paths["kits_dir"] / f"{kit_id}.json").write_text(
            json.dumps(data), encoding="utf-8")


class LegacyStateMigrationTests(TmpHomeCase):
    LEGACY = {
        "schema": 1,
        "mode": "overlay",
        "tool_version": "0.1.0",
        "installed_at": "2026-10-01T10:00:00+0800",
        "overlay_dir": "/old/home/.config/quickshell/clavis",
        "source_tree": "/old/etc/xdg/quickshell/clavis",
        "upstream_version": "2026.9.25",
        "upstream_version_source": "pacman",
        "patched_digest": {
            WIDGET: "aa",
            "Modules/Sidebars/Dashboard/infoTools/calendar_layout.js": "bb",
        },
        "payload_digest": {"Common/functions/ChineseCalendar.js": "cc"},
        "binary_digest": None,
        "etc_backup": None,
    }

    def setUp(self):
        super().setUp()
        self.legacy = self.paths["state_home"] / KIT_ID / "state.json"
        self.legacy.parent.mkdir(parents=True)
        self.legacy.write_text(json.dumps(self.LEGACY), encoding="utf-8")

    def test_normalize_v1_maps_to_schema_v2(self):
        with self.quiet():
            kit = engine.normalize_v1(self.paths, KIT_ID, MANIFEST_DATA)
        self.assertIsNotNone(kit)
        self.assertEqual(kit["schema"], 2)
        self.assertEqual(kit["kit"], KIT_ID)
        self.assertEqual(kit["kit_version"], "0.1.0")
        self.assertEqual(kit["digests"]["patched"], self.LEGACY["patched_digest"])
        self.assertEqual(kit["digests"]["payload"], self.LEGACY["payload_digest"])
        self.assertEqual(kit["order"], 20)
        self.assertEqual(kit["order"], MANIFEST_DATA["kit"]["order"])
        self.assertEqual(kit["groups"], [GROUP])
        self.assertIsNone(kit["bin"], "本仓无后端脚本——旧状态里没有 binary_digest 时 bin 为 None")
        self.assertIsNone(kit["bin_name"])
        self.assertEqual(kit["migrated_from"], str(self.legacy))
        self.assertEqual(kit["overlay_dir"], self.LEGACY["overlay_dir"])
        self.assertEqual(kit["upstream_version"], "2026.9.25")
        self.assertTrue(self.legacy.is_file(), "旧 state.json 必须原样保留不删")

    def test_normalize_v1_maps_binary_digest_when_present(self):
        """通用迁移路径：旧状态里有 binary_digest 时要迁移进 bin.digest（引擎两仓同源）。"""
        self.legacy.write_text(json.dumps({**self.LEGACY, "binary_digest": "dd"}),
                               encoding="utf-8")
        with self.quiet():
            kit = engine.normalize_v1(self.paths, KIT_ID, MANIFEST_DATA)
        self.assertEqual(kit["bin"]["digest"], "dd")

    def test_load_kit_falls_back_to_legacy_state(self):
        """kits/<id>.json 不存在时，load_kit 应返回迁移结果（写盘交给流水线）。"""
        with self.quiet():
            kit = engine.load_kit(self.paths, KIT_ID, MANIFEST_DATA)
        self.assertEqual(kit["schema"], 2)
        self.assertEqual(kit["digests"]["patched"], self.LEGACY["patched_digest"])
        self.assertTrue(self.legacy.is_file(), "load_kit 不应删旧文件")

    def test_normalize_v1_ignores_non_legacy_state(self):
        self.legacy.write_text(json.dumps({**self.LEGACY, "schema": 2}), encoding="utf-8")
        with self.quiet():
            self.assertIsNone(engine.normalize_v1(self.paths, KIT_ID, MANIFEST_DATA))
            self.assertIsNone(engine.normalize_v1(self.paths, "clavis-other-kit", None))


class MarkerScanTests(TmpHomeCase):
    def setUp(self):
        super().setUp()
        self.tree = self.tmp / "overlay"
        (self.tree / "Modules" / "Sidebars" / "Dashboard" / "infoTools").mkdir(parents=True)
        (self.tree / WIDGET).write_text(
            f"// BEGIN clavis-{GROUP}\nx\n// END clavis-{GROUP}\n// BEGIN clavis-dummy\n",
            encoding="utf-8")
        (self.tree / "Common" / "functions").mkdir(parents=True)
        (self.tree / "Common" / "functions" / "ChineseCalendar.js").write_text(
            f"/* BEGIN clavis-{GROUP} */\n# BEGIN clavis-odd.group\n", encoding="utf-8")
        (self.tree / "notes.txt").write_text("// BEGIN clavis-not-scanned\n", encoding="utf-8")

    def test_groups_are_derived_without_clavis_prefix(self):
        found = engine.scan_markers(self.tree)
        self.assertEqual(sorted(found), ["dummy", GROUP, "odd.group"])
        self.assertEqual(sorted(found[GROUP]),
                         sorted([WIDGET, "Common/functions/ChineseCalendar.js"]))
        self.assertEqual(found["dummy"], [WIDGET])

    def test_only_qml_and_js_are_scanned_by_default(self):
        found = engine.scan_markers(self.tree)
        self.assertNotIn("not-scanned", found)

    def test_rel_filter_limits_scope(self):
        found = engine.scan_markers(self.tree, rels=[WIDGET])
        self.assertEqual(sorted(found), ["dummy", GROUP])
        self.assertEqual(found["dummy"], [WIDGET])

    def test_missing_root_is_empty(self):
        self.assertEqual(engine.scan_markers(self.tmp / "nope"), {})

    def test_attribute_markers_registered_vs_unknown(self):
        kits = [
            {"kit": KIT_ID, "_manifest": {"files": [{"group": GROUP}]}},
            {"kit": DUMMY_KIT, "_manifest": {"files": [{"group": "dummy"}]}},
        ]
        found = {GROUP: [WIDGET], "dummy": ["a.qml"], "manual": ["c.qml"]}
        self.assertEqual(engine.attribute_markers(found, kits),
                         {GROUP: KIT_ID, "dummy": DUMMY_KIT, "manual": None})


class KitRegistryTests(TmpHomeCase):
    def test_iter_kits_sorted_by_order_then_id(self):
        self.write_kit_state("clavis-b", 20)
        self.write_kit_state("clavis-a", 20)
        self.write_kit_state("clavis-z", 5)
        self.assertEqual([kit["kit"] for kit in engine.iter_kits(self.paths)],
                         ["clavis-z", "clavis-a", "clavis-b"])

    def test_iter_kits_empty_without_state_dir(self):
        self.assertEqual(engine.iter_kits(self.paths), [])

    def test_forget_dry_run_writes_nothing_and_keeps_overlay(self):
        self.write_kit_state(DUMMY_KIT, 20)
        overlay = Path(self.env["XDG_CONFIG_HOME"]) / "quickshell" / "clavis"
        (overlay / "Modules" / "Sidebars" / "Dashboard" / "infoTools").mkdir(parents=True)
        (overlay / "shell.qml").write_text("// stub\n", encoding="utf-8")
        (overlay / WIDGET).write_text("// BEGIN clavis-dummy\n", encoding="utf-8")

        r = self.engine_cli("forget", DUMMY_KIT, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.paths["kits_dir"] / f"{DUMMY_KIT}.json").is_file(),
                        "forget --dry-run 不应删任何文件")

        r = self.engine_cli("forget", DUMMY_KIT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.paths["kits_dir"] / f"{DUMMY_KIT}.json").exists())
        # 只摘注册，overlay 一个字节都不动（剩下的标记会由 verify 报为孤儿）
        self.assertEqual((overlay / WIDGET).read_text(), "// BEGIN clavis-dummy\n")
        self.assertTrue((overlay / "shell.qml").is_file())
        self.assertEqual(engine.iter_kits(self.paths), [])

    def test_forget_unknown_kit_fails(self):
        r = self.engine_cli("forget", "clavis-nope")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("未注册", r.stderr)


class ServiceGuardTests(TmpHomeCase):
    def test_fake_home_disables_service_control(self):
        with self.quiet():
            engine.set_service_guard(SimpleNamespace(allow_service=False))
        self.assertFalse(engine._SERVICE_ALLOWED,
                         "HOME 不是真实家目录时，守卫应关掉服务控制")

        calls = []

        def record(*args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(returncode=1, stdout="", stderr="")

        with mock.patch.object(engine.subprocess, "run", side_effect=record):
            # 忘写 --skip-service 也不许碰真实 systemctl
            self.assertFalse(engine.stop_service(False, False))
            self.assertFalse(engine.stop_service(True, False))
            engine.start_service(False, False, False)
            engine.start_service(True, False, False)
        self.assertEqual(calls, [], "假 HOME 下不应调用 systemctl")

    def test_skip_flag_and_allow_service(self):
        with self.quiet():
            engine.set_service_guard(SimpleNamespace(allow_service=True))
            self.assertTrue(engine._SERVICE_ALLOWED, "--allow-service 应显式放行")
            engine.set_service_guard(SimpleNamespace(allow_service=False))
            self.assertFalse(engine._SERVICE_ALLOWED)
            self.assertFalse(engine.stop_service(False, True), "skip=True 应直接返回 False")


if __name__ == "__main__":
    unittest.main()
