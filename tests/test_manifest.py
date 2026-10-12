#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 LumiACGN
# SPDX-License-Identifier: GPL-3.0-or-later
"""manifest 自检：schema、锚点在 fixture 上游树中的唯一性、哈希一致性、payload 存在性。

这个测试保证「patches/manifest.json 的声明」与「tests/fixtures 里的基线」永远一致——
锚点漂移修复（CONTRIBUTING）改错一处，这里会立刻红。

本仓特有的重点：7 个 edit 里有 2 个是 **block 模式**（`cw-delegate-ymd` / `cw-daybutton`）。
block 的 anchor 不是「拿去精确命中」的，而是「结构定位的期望结果」——所以这里
用**与补丁器完全相同的算法**（importlib 加载 scripts/patch_clavis_kit.py 后直接调
extract_block）验证：

  * 从基线提取出的块逐字节等于 `anchor`；
  * `anchor` 自身再定位一次仍等于 `anchor`（自洽）；
  * 在 `replace` 上用回滚路径（unique_line_post/sanity_post）定位等于 `replace`；
  * **硬约束**：`replace` 里的 BEGIN/END 标记落在所定位组件的花括号内
    （否则回滚会残留标记——用「对 replace 提取块再断言标记在块内」机器校验）。

另含 tests/fixtures/dummy-kit（多组件共存彩排用的假组件）的自检：它的锚点同样必须
在基线里恰好命中一次、且不落在本仓任何 edit 的 span 内，否则 tests/test_coexist.sh
会以难以定位的方式失败。
"""
import hashlib
import importlib.util
import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO / "patches" / "manifest.json"
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
FIXTURE = REPO / "tests" / "fixtures" / f"upstream-{MANIFEST['upstream']['version']}"

PATCHER = REPO / "scripts" / "patch_clavis_kit.py"
DUMMY_PATH = REPO / "tests" / "fixtures" / "dummy-kit" / "manifest.json"

KIT_ID = "clavis-lunar-calendar"
GROUP = "lunar-calendar"

# 本仓的 block edit（结构与 patches/README.md 的「block 模式维护手记」对应）
BLOCK_EDITS = ("cw-delegate-ymd", "cw-daybutton")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_patcher():
    """按文件路径加载补丁器（它不是包），保证用的是真算法而不是复刻版。"""
    spec = importlib.util.spec_from_file_location("patch_clavis_kit_for_tests", PATCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


patcher = _load_patcher()


def edit_span(entry, edit, text, phase="apply"):
    """按补丁器的算法给出该 edit 在当前文本里的 [start, stop)。

    phase="apply" 用 anchor/unique_line+sanity；phase="revert" 用
    unique_line_post/sanity_post（本仓未登记 *_post 时回落到前者——与 locate_span 一致）。
    """
    block = edit.get("block") or {}
    mode = edit.get("mode", "exact")
    if mode == "exact":
        target = edit["anchor"] if phase == "apply" else edit["replace"]
        count = text.count(target)
        if count != 1:
            raise AssertionError(f"{entry['path']} / {edit['id']}: 文本命中 {count} 次（期望 1）")
        start = text.index(target)
        return start, start + len(target)
    unique_line = block.get("unique_line", "")
    sanity = block.get("sanity") or []
    if phase == "revert":
        unique_line = block.get("unique_line_post") or unique_line
        sanity = block.get("sanity_post") or sanity
    return patcher.extract_block(text, entry["path"], unique_line, sanity,
                                 int(block.get("opener_lookback", 8)),
                                 anchor=edit.get("anchor"))


def block_edits():
    """(entry, edit) 的全部 block 条目。"""
    for entry in MANIFEST["files"]:
        for edit in entry["edits"]:
            if edit.get("mode", "exact") == "block":
                yield entry, edit


class ManifestTests(unittest.TestCase):
    def test_schema_and_shape(self):
        self.assertEqual(MANIFEST["schema"], 1)
        for key in ("tool_version", "kit", "upstream", "marker", "files", "payload", "gates"):
            self.assertIn(key, MANIFEST)
        for tpl in (MANIFEST["marker"]["begin"], MANIFEST["marker"]["end"]):
            self.assertIn("{group}", tpl)
        self.assertTrue(re.fullmatch(r"\d+\.\d+\.\d+", MANIFEST["tool_version"]))
        self.assertTrue(FIXTURE.is_dir(), f"缺少基线目录：{FIXTURE}")

    def test_kit_section_shape(self):
        """kit 段：引擎靠它注册组件（缺了引擎会直接 die）。本仓无后端脚本、无配置。"""
        kit = MANIFEST["kit"]
        self.assertIsInstance(kit, dict, "manifest 必须声明 kit 段")
        self.assertEqual(kit["id"], KIT_ID)
        self.assertEqual(kit["order"], 20)
        self.assertIsInstance(kit["order"], int)
        self.assertNotIsInstance(kit["order"], bool, "kit.order 必须是整数")
        self.assertIsNone(kit["bin_name"], "本仓是纯 QML 组件——没有后端脚本")
        self.assertIsNone(kit["config_dir"])
        self.assertIsNone(kit["cache_dir"])
        self.assertIsNone(kit["legacy_config"])
        self.assertIsInstance(kit.get("post_install_notes"), list)
        self.assertTrue(kit["post_install_notes"], "安装后应给用户提示（数据截止日/更新方式）")

    def test_gates_section_shape(self):
        """gates 段：overrides 是按路径覆盖门禁工具的映射；smoke 指向 tests/smoke.qml。"""
        gates = MANIFEST["gates"]
        self.assertIsInstance(gates, dict, "manifest 必须声明 gates 段")
        self.assertIsInstance(gates.get("overrides"), dict)
        self.assertTrue(all(isinstance(k, str) and isinstance(v, str)
                            for k, v in gates["overrides"].items()))
        for rel in gates["overrides"]:
            self.assertIn(rel, [p["dest"] for p in MANIFEST["payload"]],
                          "overrides 只针对安装进 Clavis 树的路径（payload dest）")
        # 442 KB 的构建产物用 node 编译会崩——必须显式覆盖成 qmllint
        self.assertEqual(gates["overrides"].get("Common/functions/ChineseCalendar.js"), "qmllint")

        smoke = gates.get("smoke")
        self.assertIsInstance(smoke, dict, "本仓有真引擎冒烟门禁，smoke 不应为 null")
        self.assertTrue((REPO / smoke["file"]).is_file(), f"smoke 文件缺失：{smoke['file']}")
        self.assertGreaterEqual(smoke.get("timeout_s", 0), 1)
        self.assertTrue(smoke.get("stage"), "smoke 必须 stage overlay 里已安装的产物字节")
        for tmp_rel, overlay_rel in smoke["stage"].items():
            self.assertIn(overlay_rel, [p["dest"] for p in MANIFEST["payload"]],
                          "stage 的源必须是 payload 装进 overlay 的路径")

    def test_edit_inventory(self):
        """本仓登记的 edit 清单与两种定位模式的分布（docs/CONTRIBUTING 写的是 7 个）。"""
        modes = {}
        ids = set()
        for entry in MANIFEST["files"]:
            for edit in entry["edits"]:
                ids.add(edit["id"])
                modes.setdefault(edit.get("mode", "exact"), []).append(edit["id"])
        self.assertEqual(len(ids), sum(len(e["edits"]) for e in MANIFEST["files"]),
                         "edit id 必须唯一")
        self.assertEqual(sum(len(e["edits"]) for e in MANIFEST["files"]), 7)
        self.assertEqual(len(MANIFEST["files"]), 2)
        self.assertEqual(sorted(modes.get("block", [])), sorted(BLOCK_EDITS))

    def test_fixture_tree_exists(self):
        for entry in MANIFEST["files"]:
            self.assertTrue((FIXTURE / entry["path"]).is_file(),
                            f"基线缺少锚点文件：{entry['path']}")

    def test_anchors_unique_and_hashed(self):
        """每个 edit 的 anchor 在基线里恰好命中一次，且 sha256 与登记值一致。"""
        for entry in MANIFEST["files"]:
            text = (FIXTURE / entry["path"]).read_text(encoding="utf-8")
            for edit in entry["edits"]:
                self.assertEqual(text.count(edit["anchor"]), 1,
                                 f"{entry['path']} / {edit['id']}: 锚点在基线中不唯一")
                self.assertEqual(sha256(edit["anchor"]), edit["anchor_sha256"],
                                 f"{entry['path']} / {edit['id']}: anchor_sha256 不一致")

    def test_upstream_file_hashes(self):
        for entry in MANIFEST["files"]:
            text = (FIXTURE / entry["path"]).read_text(encoding="utf-8")
            self.assertEqual(sha256(text), entry["upstream_sha256"],
                             f"{entry['path']}: upstream_sha256 与基线不一致")

    def test_replace_contains_markers(self):
        for entry in MANIFEST["files"]:
            begin = MANIFEST["marker"]["begin"].replace("{group}", entry["group"])
            end = MANIFEST["marker"]["end"].replace("{group}", entry["group"])
            for edit in entry["edits"]:
                self.assertEqual(edit["replace"].count(begin), 1,
                                 f"{entry['path']} / {edit['id']}: 替换文本缺少/重复 BEGIN 标记")
                self.assertEqual(edit["replace"].count(end), 1,
                                 f"{entry['path']} / {edit['id']}: 替换文本缺少/重复 END 标记")

    def test_block_edits_locate_to_anchor(self):
        """block 的核心契约：结构定位提取出的块 == manifest 里的 anchor。

        anchor 同时是「apply 时被替换掉的原文」与「revert 时的还原目标」，
        所以它必须能由块定位重新算出来——这就是 patches/README.md 说的
        「anchor 是提取结果，不是手工抄的文本」。
        """
        self.assertEqual(sorted(edit["id"] for _e, edit in block_edits()), sorted(BLOCK_EDITS))
        for entry, edit in block_edits():
            text = (FIXTURE / entry["path"]).read_text(encoding="utf-8")
            where = f"{entry['path']} / {edit['id']}"
            block = edit["block"]

            self.assertEqual(text.count(block["unique_line"]), 1,
                             f"{where}: block.unique_line 在基线中不唯一")
            self.assertIn(edit["anchor"], text, f"{where}: anchor 不是基线的子串")
            self.assertIsInstance(block.get("opener_lookback"), int)

            span = edit_span(entry, edit, text, "apply")
            extracted = text[span[0]:span[1]]
            self.assertEqual(extracted, edit["anchor"],
                             f"{where}: 从基线提取的块与 anchor 不一致")

            # sanity 兜底子串必须都在块内（否则块定位会误认「还是那个组件」）
            for needle in block.get("sanity") or []:
                self.assertIn(needle, extracted, f"{where}: sanity 子串不在块内：{needle!r}")

            # 自洽：把 anchor 当作文本再定位一次，仍应等于 anchor
            again = edit_span(entry, edit, edit["anchor"], "apply")
            self.assertEqual(edit["anchor"][again[0]:again[1]], edit["anchor"],
                             f"{where}: 对 anchor 再定位一次不等于 anchor")

    def test_block_revert_path_locate_to_replace(self):
        """回滚路径（unique_line_post/sanity_post）在 replace 上定位应恰好等于 replace。

        补丁器 revert 时把换过的整块替换回 anchor，所以「在补丁后的文本里能定位到
        同一个块」是回滚成立的前提；sanity_post 则保证定位到的确实是补丁后的组件。
        """
        for entry, edit in block_edits():
            where = f"{entry['path']} / {edit['id']}"
            span = edit_span(entry, edit, edit["replace"], "revert")
            self.assertEqual(edit["replace"][span[0]:span[1]], edit["replace"],
                             f"{where}: 在 replace 上定位不等于 replace")
            for needle in edit["block"].get("sanity_post") or []:
                self.assertIn(needle, edit["replace"],
                              f"{where}: sanity_post 子串不在 replace 里：{needle!r}")

    def test_block_markers_inside_braces(self):
        """硬约束：block 的 replace 里 BEGIN/END 必须落在所定位组件的花括号内。

        机器校验办法：对 replace 重新提取块（回滚路径），再断言标记落在提取到的
        span 之内。标记若跑到块外，回滚就会残留标记（见 patches/README.md）。
        """
        for entry, edit in block_edits():
            where = f"{entry['path']} / {edit['id']}"
            begin = MANIFEST["marker"]["begin"].replace("{group}", entry["group"])
            end = MANIFEST["marker"]["end"].replace("{group}", entry["group"])
            span = edit_span(entry, edit, edit["replace"], "revert")
            region = edit["replace"][span[0]:span[1]]
            for marker in (begin, end):
                self.assertIn(marker, region,
                              f"{where}: {marker!r} 不在所定位组件的花括号内")

    def test_edits_registered_in_file_order(self):
        """edit 必须按文件位置升序登记且互不重叠（补丁器的 assert_spans 契约）。

        运行时由 assert_spans 兜底，静态校验放在这里能在改 manifest 时立刻报错。
        """
        for entry in MANIFEST["files"]:
            text = (FIXTURE / entry["path"]).read_text(encoding="utf-8")
            spans = [(edit_span(entry, edit, text, "apply")[0], edit["id"])
                     for edit in entry["edits"]]
            self.assertEqual([s for s, _ in spans], sorted(s for s, _ in spans),
                             f"{entry['path']}: edits 未按文件位置升序登记：{spans}")
            prev = None
            for edit in entry["edits"]:
                start, stop = edit_span(entry, edit, text, "apply")
                if prev is not None:
                    self.assertGreaterEqual(start, prev,
                                            f"{entry['path']} / {edit['id']}: 与前一个 edit 重叠")
                prev = stop

    def test_payload_sources_exist(self):
        for item in MANIFEST["payload"]:
            self.assertTrue((REPO / "qml" / item["src"]).is_file(),
                            f"payload 源文件缺失：qml/{item['src']}")
        self.assertEqual([item["dest"] for item in MANIFEST["payload"]],
                         ["Common/functions/ChineseCalendar.js",
                          "licenses/lunar-javascript-MIT.txt"],
                         "载荷落点：数据层 + 它的 MIT 许可证原文")

    def test_group_single_and_marker_group_placeholder(self):
        """本组件的所有 entry 共用同一个 group（引擎按 group 做标记归属与回滚）。"""
        groups = {entry["group"] for entry in MANIFEST["files"]}
        self.assertEqual(groups, {GROUP})
        for entry in MANIFEST["files"]:
            self.assertNotIn("{group}", entry["group"])
        # 引擎的 scan_markers 按行首匹配 BEGIN 行来归属 group——渲染结果必须能被它认出来
        marker_re = re.compile(r"^\s*(?://|#|/\*)\s*BEGIN\s+clavis-([A-Za-z0-9._-]+)\s*$", re.M)
        for entry in MANIFEST["files"]:
            rendered = MANIFEST["marker"]["begin"].replace("{group}", entry["group"])
            match = marker_re.match(rendered)
            self.assertIsNotNone(match, f"引擎的行首匹配认不出 BEGIN 标记：{rendered!r}")
            self.assertEqual(match.group(1), entry["group"])


class DummyKitFixtureTests(unittest.TestCase):
    """tests/fixtures/dummy-kit：多组件共存彩排夹具的 manifest 自检。"""

    def setUp(self):
        self.manifest = json.loads(DUMMY_PATH.read_text(encoding="utf-8"))
        self.fixture = FIXTURE

    def test_shape(self):
        self.assertEqual(self.manifest["schema"], 1)
        kit = self.manifest["kit"]
        self.assertEqual(kit["id"], "clavis-dummy-kit")
        self.assertIsInstance(kit["order"], int)
        self.assertNotIsInstance(kit["order"], bool)
        self.assertEqual(kit["order"], MANIFEST["kit"]["order"],
                         "夹具刻意与本仓同 order（覆盖 (order, id) 的字母序 tie-break）")
        self.assertIsNone(kit["bin_name"], "夹具刻意不带后端脚本（覆盖无 bin 分支）")
        self.assertEqual(self.manifest["marker"], MANIFEST["marker"],
                         "夹具的 marker 模板应与本仓一致")
        self.assertEqual(self.manifest["upstream"]["version"],
                         MANIFEST["upstream"]["version"])
        self.assertEqual(sorted({e["group"] for e in self.manifest["files"]}),
                         ["dummy"], "夹具的 group 必须与本仓不同（标记不能撞车）")

    def test_anchor_hits_fixture_exactly_once(self):
        for entry in self.manifest["files"]:
            text = (self.fixture / entry["path"]).read_text(encoding="utf-8")
            self.assertEqual(sha256(text), entry["upstream_sha256"],
                             f"{entry['path']}: 夹具登记的 upstream_sha256 与基线不一致")
            for edit in entry["edits"]:
                self.assertEqual(text.count(edit["anchor"]), 1,
                                 f"{entry['path']} / {edit['id']}: 锚点在基线中不唯一")
                self.assertEqual(sha256(edit["anchor"]), edit["anchor_sha256"])

    def test_shared_file_with_calendar_kit(self):
        """夹具刻意与本仓打同一个文件（共享文件是共存里最难的场景）。"""
        dummy_files = [entry["path"] for entry in self.manifest["files"]]
        self.assertEqual(dummy_files,
                         ["Modules/Sidebars/Dashboard/infoTools/CalendarWidget.qml"])
        self.assertIn(dummy_files[0], [entry["path"] for entry in MANIFEST["files"]])

    def test_anchor_outside_every_calendar_edit(self):
        """夹具的锚点必须落在本仓所有 edit 的 span 之外——谁先重放都成立。"""
        for entry in self.manifest["files"]:
            text = (self.fixture / entry["path"]).read_text(encoding="utf-8")
            for edit in entry["edits"]:
                index = text.index(edit["anchor"])
                for cal_entry in MANIFEST["files"]:
                    if cal_entry["path"] != entry["path"]:
                        continue
                    for cal_edit in cal_entry["edits"]:
                        start, stop = edit_span(cal_entry, cal_edit, text, "apply")
                        self.assertFalse(start <= index < stop,
                                         f"夹具锚点落在本仓 edit {cal_edit['id']} 的 span 内")
                        self.assertNotIn(edit["anchor"], cal_edit["replace"][start:stop])

    def test_replace_markers_and_payload(self):
        for entry in self.manifest["files"]:
            begin = self.manifest["marker"]["begin"].replace("{group}", entry["group"])
            end = self.manifest["marker"]["end"].replace("{group}", entry["group"])
            for edit in entry["edits"]:
                self.assertEqual(edit["replace"].count(begin), 1)
                self.assertEqual(edit["replace"].count(end), 1)
        payload_dests = []
        for item in self.manifest["payload"]:
            self.assertTrue((DUMMY_PATH.parent / "qml" / item["src"]).is_file(),
                            f"夹具 payload 缺失：{item['src']}")
            payload_dests.append(item["dest"])
        self.assertTrue(set(payload_dests).isdisjoint(
            p["dest"] for p in MANIFEST["payload"]),
            "夹具载荷不能与本仓载荷抢同一个安装路径")

    def test_readme_marks_it_as_fixture(self):
        readme = (DUMMY_PATH.parent / "README.md").read_text(encoding="utf-8")
        self.assertIn("测试夹具", readme)


if __name__ == "__main__":
    unittest.main()
