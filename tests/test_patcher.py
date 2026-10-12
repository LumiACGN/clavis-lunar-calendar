#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 LumiACGN
# SPDX-License-Identifier: GPL-3.0-or-later
"""补丁器测试：在纯净上游 fixture 上验证 应用 / 幂等 / 校验 / 回滚 / 漂移拒绝。

无需 Clavis 环境（CI 也能跑）——fixture 是 2026.9.25 发行树中的两个锚点文件。

期望值（锚点文件清单、payload 清单、marker 串、各文件标记数）全部**从
patches/manifest.json 派生**，不硬编码：manifest 增删一处锚点，这里自动跟上。

block 定位模式（本仓 `cw-delegate-ymd` / `cw-daybutton` 两个 edit）与 exact 用
**同一份真实 manifest + 真实基线**覆盖：块内漂移容忍（全局字号补丁）、花括号陷阱、
unique_line 失配、sanity 失配、顺序契约、explain-drift —— 都是本仓最容易被上游
改坏的地方，所以不另造迷你树，直接拿真实文本测。
"""
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PATCHER = REPO / "scripts" / "patch_clavis_kit.py"
MANIFEST = REPO / "patches" / "manifest.json"
PAYLOAD = REPO / "qml"
FIXTURE = REPO / "tests" / "fixtures" / "upstream-2026.9.25"

MANIFEST_DATA = json.loads(MANIFEST.read_text(encoding="utf-8"))
ANCHOR_FILES = [entry["path"] for entry in MANIFEST_DATA["files"]]
PAYLOAD_FILES = [item["dest"] for item in MANIFEST_DATA["payload"]]

WIDGET = "Modules/Sidebars/Dashboard/infoTools/CalendarWidget.qml"
LAYOUT = "Modules/Sidebars/Dashboard/infoTools/calendar_layout.js"
BEGIN = MANIFEST_DATA["marker"]["begin"].replace("{group}", "lunar-calendar")


def edits_of(path):
    entry = next(e for e in MANIFEST_DATA["files"] if e["path"] == path)
    return entry["edits"]


def edit_of(path, edit_id):
    return next(e for e in edits_of(path) if e["id"] == edit_id)


def run(target, *args, manifest=MANIFEST, payload=PAYLOAD):
    return subprocess.run(
        [sys.executable, str(PATCHER), "--manifest", str(manifest),
         "--payload", str(payload), "--target", str(target), *args],
        capture_output=True, text=True)


def count_status(stdout, status):
    """数结果行里的状态词（结果行形如 '       patched  /path/file.qml'）。"""
    return sum(1 for line in stdout.splitlines() if line.split()[:1] == [status])


class PatcherCase(unittest.TestCase):
    """每个用例一棵 tmp 树（fixture 的副本），用例之间互不影响。"""

    def setUp(self):
        self.assertTrue(PATCHER.is_file(), f"补丁器不存在：{PATCHER}")
        self.tmp = Path(tempfile.mkdtemp(prefix="patcher-test-"))
        shutil.copytree(FIXTURE, self.tmp, dirs_exist_ok=True)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def read(self, rel):
        return (self.tmp / rel).read_text(encoding="utf-8")

    def write(self, rel, text):
        (self.tmp / rel).write_text(text, encoding="utf-8")

    def inject(self, rel, old, new, count=1):
        """在锚点文件里做一次（或若干次）替换，模拟上游/本机补丁造成的漂移。"""
        text = self.read(rel)
        self.assertEqual(text.count(old), count,
                         f"{rel}: 预置漂移的替换目标命中 {text.count(old)} 次（期望 {count}）")
        self.write(rel, text.replace(old, new))

    def snapshot(self):
        """锚点文件 + payload 的当前字节（缺文件记 None）。"""
        out = {}
        for rel in ANCHOR_FILES + PAYLOAD_FILES:
            path = self.tmp / rel
            out[rel] = path.read_bytes() if path.is_file() else None
        return out


class PatcherTests(PatcherCase):
    """真实 manifest + 上游基线 fixture（apply / 幂等 / verify / 回滚 / exact 漂移）。"""

    def test_apply_then_idempotent_then_verify(self):
        r = run(self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(count_status(r.stdout, "patched"), len(ANCHOR_FILES))
        self.assertEqual(count_status(r.stdout, "installed"), len(PAYLOAD_FILES))

        # 每个锚点文件里的 BEGIN 标记数 == manifest 登记的 edit 数（5 + 2）
        for entry in MANIFEST_DATA["files"]:
            begin = MANIFEST_DATA["marker"]["begin"].replace("{group}", entry["group"])
            text = self.read(entry["path"])
            self.assertEqual(text.count(begin), len(entry["edits"]),
                             f"{entry['path']}: BEGIN 标记数应等于其 edit 数")
        self.assertEqual(self.read(WIDGET).count(BEGIN), 5)
        self.assertEqual(self.read(LAYOUT).count(BEGIN), 2)
        for rel in PAYLOAD_FILES:
            src = next(p["src"] for p in MANIFEST_DATA["payload"] if p["dest"] == rel)
            self.assertEqual((self.tmp / rel).read_bytes(), (REPO / "qml" / src).read_bytes(),
                             f"{rel}: 安装的载荷应与 qml/{src} 逐字节一致")

        before = self.snapshot()
        r2 = run(self.tmp)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertEqual(count_status(r2.stdout, "already-patched"), len(ANCHOR_FILES))
        self.assertEqual(count_status(r2.stdout, "unchanged"), len(PAYLOAD_FILES))
        self.assertEqual(before, self.snapshot(), "重跑不应改变任何字节")

        r3 = run(self.tmp, "--verify")
        self.assertEqual(r3.returncode, 0, r3.stderr)
        self.assertIn("verify ok", r3.stdout)

    def test_verify_fails_before_apply(self):
        r = run(self.tmp, "--verify")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("verify failed", r.stderr)

    def test_revert_roundtrip(self):
        self.assertEqual(run(self.tmp).returncode, 0)
        r = run(self.tmp, "--revert", "--remove-payload")
        self.assertEqual(r.returncode, 0, r.stderr)
        for rel in ANCHOR_FILES:
            self.assertEqual((self.tmp / rel).read_bytes(), (FIXTURE / rel).read_bytes(),
                             f"{rel} 回滚后应与纯净上游逐字节一致")
        for rel in PAYLOAD_FILES:
            self.assertFalse((self.tmp / rel).exists(), f"{rel} 应已被移除")

    def test_drift_fails_before_any_write(self):
        """exact 锚点漂移（上游改了文本）→ 响亮失败，且全树不写一个字节。"""
        self.inject(WIDGET,
                    'import "../../../../Common/functions/DateFormat.js" as DateFormat',
                    'import "../../../../Common/functions/DateTimeFormat.js" as DateFormat')
        r = run(self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("anchor matched 0 times", r.stderr)
        self.assertIn("cw-import", r.stderr)
        # 全树预校验：即便另一个文件的锚点完好，也不允许发生任何写入
        for rel in ANCHOR_FILES:
            self.assertNotIn(BEGIN, self.read(rel), f"{rel}: 漂移失败时不应写入")
        for rel in PAYLOAD_FILES:
            self.assertFalse((self.tmp / rel).exists())

    def test_partial_marker_refused(self):
        self.assertEqual(run(self.tmp).returncode, 0)
        end = MANIFEST_DATA["marker"]["end"].replace("{group}", "lunar-calendar")
        lines = self.read(WIDGET).splitlines(keepends=True)
        index = next(i for i, line in enumerate(lines) if line.strip() == end)
        del lines[index]
        self.write(WIDGET, "".join(lines))
        r = run(self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("marker count mismatch", r.stderr)

    def test_explain_drift_json(self):
        self.inject(WIDGET,
                    'import "../../../../Common/functions/DateFormat.js" as DateFormat',
                    'import "../../../../Common/functions/DateTimeFormat.js" as DateFormat')
        r = run(self.tmp, "--explain-drift", "--json")
        self.assertEqual(r.returncode, 3, r.stderr)
        report = json.loads(r.stdout)
        self.assertEqual(report["schema"], 1)
        self.assertEqual(report["upstream_version"], MANIFEST_DATA["upstream"]["version"])
        self.assertTrue(report["drift"])
        item = report["drift"][0]
        self.assertEqual(item["file"], WIDGET)
        self.assertEqual(item["edit"], "cw-import")
        self.assertEqual(item["mode"], "exact")
        self.assertEqual(item["matched"], 0)
        self.assertIsNotNone(item["best_match_line"])
        self.assertIn("DateFormat", item["context"])
        self.assertIn("calendar_layout", item["context"])


class BlockModeTests(PatcherCase):
    """block 定位模式的边界用例（真实 manifest + 真实基线）。"""

    def test_block_apply_tolerates_drift_inside_block(self):
        """块内 font.pixelSize 被本机全局字号补丁改动 —— block 仍应成功（exact 会全灭）。"""
        edit = edit_of(WIDGET, "cw-daybutton")
        self.assertIn("font.pixelSize: 14", edit["anchor"])
        self.inject(WIDGET, "font.pixelSize: 14", "font.pixelSize: 15", count=2)
        r = run(self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(count_status(r.stdout, "patched"), len(ANCHOR_FILES))
        text = self.read(WIDGET)
        self.assertEqual(text.count(BEGIN), 5)
        # 漂移过的块被 manifest 里的 replace 整体换掉（BEGIN/END 落在花括号内）
        self.assertIn(edit["replace"], text)
        self.assertIn("font.pixelSize: dayButton.dayFontSize", text)
        self.assertNotIn("font.pixelSize: 15", edit["replace"], "漂移版本不应留在替换结果里")
        self.assertEqual(text.count("font.pixelSize: 15"), 1,
                         "块外的那个字号（HeaderButton）应照旧漂着不动")
        self.assertEqual(run(self.tmp, "--verify").returncode, 0)

    def test_block_brace_trap_fails_loud_and_writes_nothing(self):
        """块内插入带 '{' 的字符串 —— 朴素养花括号配对会错配，必须报错而不是写坏文件。"""
        self.inject(WIDGET, '        property string day: ""\n',
                    '        property string day: ""\n'
                    '        property string trap: "{"\n')
        r = run(self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("花括号", r.stderr)
        self.assertNotIn(BEGIN, self.read(WIDGET))
        for rel in PAYLOAD_FILES:
            self.assertFalse((self.tmp / rel).exists(), "失败时不应安装载荷")

    def test_block_unique_line_mismatch_reports_zero_hits(self):
        """unique_line 那一行被删 —— 报「命中 0 次」（报错点名了失配的 unique_line）。"""
        unique_line = edit_of(WIDGET, "cw-delegate-ymd")["block"]["unique_line"]
        self.inject(WIDGET, f"{unique_line}\n", "")
        r = run(self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("命中 0 次", r.stderr)
        self.assertIn(unique_line.strip(), r.stderr)
        self.assertNotIn(BEGIN, self.read(WIDGET))

    def test_block_sanity_mismatch_reports_missing_sanity(self):
        """unique_line 仍命中、但块内 sanity 子串被改名 —— 拒绝猜测，零写入。"""
        sanity = edit_of(WIDGET, "cw-delegate-ymd")["block"]["sanity"][1]
        self.inject(WIDGET, sanity, "todayState: parent.cell.isToday")
        r = run(self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("sanity", r.stderr)
        self.assertIn(sanity, r.stderr)
        self.assertNotIn(BEGIN, self.read(WIDGET))

    def test_edit_order_contract(self):
        """同一文件内的 edit 必须按文件位置升序登记（manifest 倒序 → 响亮失败）。"""
        data = copy.deepcopy(MANIFEST_DATA)
        entry = next(e for e in data["files"] if e["path"] == WIDGET)
        first, last = 0, len(entry["edits"]) - 1  # cw-import ←→ cw-daybutton
        self.assertEqual((entry["edits"][first]["id"], entry["edits"][last]["id"]),
                         ("cw-import", "cw-daybutton"))
        entry["edits"][first], entry["edits"][last] = entry["edits"][last], entry["edits"][first]
        swapped = self.tmp / "manifest-swapped.json"
        swapped.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        r = run(self.tmp, manifest=swapped)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("升序", r.stderr)
        self.assertNotIn(BEGIN, self.read(WIDGET), "顺序契约失败时不应写入任何字节")
        for rel in PAYLOAD_FILES:
            self.assertFalse((self.tmp / rel).exists())

    def test_explain_drift_json_block_mode(self):
        """unique_line 完全失配 → 报告 mode=block、matched=0 并给 unique_line 诊断。"""
        unique_line = edit_of(WIDGET, "cw-delegate-ymd")["block"]["unique_line"]
        self.inject(WIDGET, f"{unique_line}\n", "")
        r = run(self.tmp, "--explain-drift", "--json")
        self.assertEqual(r.returncode, 3, r.stderr)
        report = json.loads(r.stdout)
        items = [item for item in report["drift"] if item["edit"] == "cw-delegate-ymd"]
        self.assertEqual(len(items), 1, report)
        self.assertEqual(items[0]["mode"], "block")
        self.assertEqual(items[0]["matched"], 0)
        self.assertEqual(items[0]["file"], WIDGET)
        self.assertIn("unique_line", items[0].get("detail", ""))

    def test_explain_drift_json_block_sanity_failure(self):
        """unique_line 仍命中 1 次、但 sanity 失配 → 报告要给出 mode/诊断而不是退出。"""
        sanity = edit_of(WIDGET, "cw-delegate-ymd")["block"]["sanity"][1]
        self.inject(WIDGET, sanity, "todayState: parent.cell.isToday")
        r = run(self.tmp, "--explain-drift", "--json")
        self.assertEqual(r.returncode, 3, r.stderr)
        report = json.loads(r.stdout)
        item = next(i for i in report["drift"] if i["edit"] == "cw-delegate-ymd")
        self.assertEqual(item["mode"], "block")
        self.assertEqual(item["matched"], 1)
        self.assertIn("sanity", item.get("detail", ""))


if __name__ == "__main__":
    unittest.main()
