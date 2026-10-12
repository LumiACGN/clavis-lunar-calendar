#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 LumiACGN
# SPDX-License-Identifier: GPL-3.0-or-later
"""把 clavis 组件接入 Clavis shell 树（manifest 驱动的幂等锚点补丁器）。

设计说明：
  * 所有锚点/替换文本/载荷映射都在 patches/manifest.json —— 本脚本只是通用引擎
    （kit 无关；同一份脚本在 clavis-ai-usage 与 clavis-lunar-calendar 两仓逐字节相同）。
  * 两种定位模式：
      - exact：整段 anchor 必须精确命中一次（最严；短行与希望被上游改动惊动的长块用它）
      - block：结构块定位（unique_line 唯一命中 → 向上找开括号行 → 朴素花括号配对
        → sanity 子串校验）。对上游「外观类改动」（例如全局字号 +1px 改了块内
        font.pixelSize）鲁棒——只要块结构在，锚点漂移不再直接击穿补丁。
        硬约束：block edit 的 replace 里 BEGIN/END 标记必须落在所定位组件的花括号内，
        否则回滚会残留标记（原因见 patches/README.md；test_manifest.py 会强制检查）。
  * 每个文件仅在「无任何标记」时打补丁；已打过补丁的文件会校验并跳过（幂等）。
  * 写入前先对全树做预校验（exact 唯一命中 / block 可定位且 sanity 通过），绝不半打。
  * 同一文件内的多个 edit 以「按文件位置升序、互不重叠」为契约（机器断言，
    manifest 登记顺序即文件顺序），按 span 降序拼接；exact 模式与 v0.1.0 行为逐字节等价。

用法:
    patch_clavis_kit.py [--manifest patches/manifest.json] [--target TREE]
                        [--payload qml/] [--dry-run] [--verify]
                        [--revert [--remove-payload]]
                        [--explain-drift [--json]]
                        [--expect-upstream VERSION]
                        [--group SLUG]... [--list-groups] [--list-edits]
"""
import argparse
import difflib
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = REPO_ROOT / "patches" / "manifest.json"
DEFAULT_PAYLOAD = REPO_ROOT / "qml"
PROG = Path(sys.argv[0]).name or "patch_clavis_kit.py"

MODES = ("exact", "block")


class BlockError(Exception):
    """block 模式定位失败（explain_drift 需要捕获并转成报告，而不是直接退出）。"""


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fail(message: str):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def load_manifest(path: Path) -> dict:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot load manifest {path}: {exc}")
    if manifest.get("schema") != 1:
        fail(f"unsupported manifest schema: {manifest.get('schema')!r}")
    for key in ("upstream", "marker", "files", "payload"):
        if key not in manifest:
            fail(f"manifest missing required key: {key}")
    kit = manifest.get("kit")
    if kit is not None:
        if not isinstance(kit, dict) or not kit.get("id"):
            fail("manifest kit section must be an object with a non-empty 'id'")
        order = kit.get("order", 50)
        if not isinstance(order, int) or isinstance(order, bool):
            fail(f"manifest kit.order must be an integer (got {order!r})")
    for entry in manifest["files"]:
        for edit in entry["edits"]:
            mode = edit.get("mode", "exact")
            if mode not in MODES:
                fail(f"{entry['path']} / {edit.get('id')}: unknown edit mode {mode!r} "
                     f"(expected one of {MODES})")
            if mode == "block":
                block = edit.get("block")
                if not isinstance(block, dict) or not block.get("unique_line"):
                    fail(f"{entry['path']} / {edit.get('id')}: block edit requires "
                         "block.unique_line")
                lookback = block.get("opener_lookback", 8)
                if not isinstance(lookback, int) or isinstance(lookback, bool) or lookback < 1:
                    fail(f"{entry['path']} / {edit.get('id')}: block.opener_lookback must be "
                         f"a positive integer (got {lookback!r})")
    return manifest


def marker_pair(manifest: dict, group: str):
    return (manifest["marker"]["begin"].replace("{group}", group),
            manifest["marker"]["end"].replace("{group}", group))


def atomic_write(path: Path, text: str):
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def anchor_miss_error(path: Path, edit: dict, count: int, upstream_sha: str | None,
                      phase: str = "apply"):
    head = edit["anchor"].splitlines()[0][:70]
    lines = [f"{path}: edit {edit['id']} anchor matched {count} times (expected 1): {head!r}"]
    lines.append(f"  anchor sha256:        {edit.get('anchor_sha256', '<unknown>')}")
    text = path.read_text(encoding="utf-8")
    if re.search(r"^\s*(?://|#|/\*)\s*BEGIN\s+clavis-", text, re.M):
        lines.append("  注：该文件已含 clavis-* 补丁标记——源树可能被手工/旧流程打过补丁。"
                     "正规做法是让对应组件注册后由 overlay 引擎重放（见 README / TROUBLESHOOTING）")
    current = sha256(text)
    lines.append(f"  current file sha256:  {current}")
    if upstream_sha:
        note = "（与已知上游版本一致——这不该发生，请提交 issue）" if current == upstream_sha \
            else "（与已知上游版本不同——上游文本很可能已变化）"
        lines.append(f"  known upstream sha256: {upstream_sha} {note}")
    lines.append(f"  -> 运行: {PROG} --explain-drift --manifest <manifest> --target <tree>"
                 "  查看新版上游的实际文本，然后更新 manifest（见 patches/README.md）")
    return "\n".join(lines)


def _brace_end(text: str, open_idx: int, path) -> int:
    depth = 0
    for i in range(open_idx, len(text)):
        char = text[i]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    raise BlockError(f"{path}: block 花括号不平衡——从 '{{' 起找不到配对的 '}}'")


def _indent(line: str) -> str:
    return line[:len(line) - len(line.lstrip())]


def extract_block(text: str, path, unique_line: str, sanity, lookback: int = 8,
                  anchor: str | None = None) -> tuple[int, int]:
    """结构块定位：unique_line 唯一命中 → 向上找开括号行（≤ lookback）→ 花括号配对。

    三道强化（相对 rice-work 参考实现）：
      ① 提取结果的末行 strip 后必须以 '}' 结尾（字符串/注释里的花括号导致截断时报警）；
      ② 末行缩进必须与 manifest anchor 的末行一致（花括号错配导致过度提取时报警）；
      ③ 报错文案区分四种失败原因（unique_line 命中数 / 找不到开括号行 / 不平衡 / sanity 缺失）。
    """
    count = text.count(unique_line)
    if count != 1:
        raise BlockError(f"{path}: block unique_line 命中 {count} 次（期望 1）："
                         f"{unique_line.strip()!r}——上游可能改写了此处，请看 --explain-drift")
    idx = text.index(unique_line)
    line_start = text.rfind("\n", 0, idx) + 1

    opener_start = None
    if text[line_start:text.index("\n", line_start)].rstrip().endswith("{"):
        opener_start = line_start
    else:
        cursor = line_start
        for _ in range(lookback):
            if cursor == 0:
                break
            start = text.rfind("\n", 0, cursor - 1) + 1
            if text[start:cursor].rstrip().endswith("{"):
                opener_start = start
                break
            cursor = start
    if opener_start is None:
        raise BlockError(f"{path}: block 定位失败——unique_line 之上 {lookback} 行内找不到以 "
                         f"'{{' 结尾的开括号行（unique_line: {unique_line.strip()!r}；"
                         "必要时用 block.opener_lookback 调整）")

    block_end = _brace_end(text, text.index("{", opener_start), path)
    block = text[opener_start:block_end]
    lines = block.splitlines()
    if not lines or not lines[-1].strip().endswith("}"):
        raise BlockError(f"{path}: 提取到的块末行不是 '}}'（疑似字符串/注释里的花括号错配）："
                         f"{lines[-1][:60]!r}" if lines else f"{path}: 提取到的块为空")
    if anchor:
        anchor_last = anchor.rstrip().splitlines()[-1]
        if _indent(lines[-1]) != _indent(anchor_last):
            raise BlockError(f"{path}: 块结束行缩进与 manifest anchor 不一致——疑似花括号错配导致"
                             f"过度提取（块末 {lines[-1]!r} vs anchor 末 {anchor_last!r}）")
    for needle in sanity or []:
        if needle not in block:
            raise BlockError(f"{path}: 提取到的块缺少 sanity 子串 {needle!r}——拒绝猜测")
    return opener_start, block_end


def locate_span(text: str, edit: dict, path: Path, phase: str) -> tuple[int, int]:
    """统一 span 出口。phase: apply | revert。"""
    mode = edit.get("mode", "exact")
    if mode == "exact":
        target = edit["anchor"] if phase == "apply" else edit["replace"]
        count = text.count(target)
        if count != 1:
            if phase == "apply":
                fail(anchor_miss_error(path, edit, count, None))
            fail(f"{path}: cannot revert edit {edit['id']}: replacement text not found exactly "
                 "once — file was modified after patching")
        start = text.index(target)
        return start, start + len(target)

    block = edit.get("block") or {}
    unique_line = block.get("unique_line", "")
    sanity = block.get("sanity") or []
    if phase == "revert":
        if block.get("unique_line_post"):
            unique_line = block["unique_line_post"]
        sanity = block.get("sanity_post") or sanity
    lookback = int(block.get("opener_lookback", 8))
    try:
        return extract_block(text, path, unique_line, sanity, lookback,
                             anchor=edit.get("anchor"))
    except BlockError as exc:
        fail(str(exc))


def assert_spans(path: Path, spans: list):
    """契约断言：同文件内的 edit 必须按文件位置升序登记、互不重叠（block 回滚依赖它）。"""
    prev_end = -1
    prev_id = None
    for start, stop, _payload, edit in spans:
        if start < prev_end:
            fail(f"{path}: edit {edit['id']} 与 {prev_id} 在文件中重叠/未按文件位置升序登记"
                 "——manifest 的 edits 必须按文件位置升序排列且互不重叠")
        prev_end, prev_id = stop, edit["id"]


def patch_file(path: Path, entry: dict, begin: str, end: str,
               dry_run: bool, verify: bool, results: list):
    edits = entry["edits"]
    text = path.read_text(encoding="utf-8")
    begin_count = text.count(begin)
    end_count = text.count(end)

    if begin_count or end_count:
        if begin_count != len(edits) or end_count != len(edits):
            fail(f"{path}: marker count mismatch (BEGIN={begin_count}, END={end_count}, "
                 f"expected={len(edits)}) — refusing to guess a partially patched file")
        results.append({"path": str(path), "status": "already-patched"})
        return

    if verify:
        results.append({"path": str(path), "status": "missing"})
        return

    # 先全量定位（写任何字节之前），并做契约断言
    spans = []
    for edit in edits:
        if edit.get("mode", "exact") == "block":
            replacement = edit["replace"]
            if replacement.count(begin) != 1 or replacement.count(end) != 1:
                fail(f"{path}: edit {edit['id']}: block 模式的 replace 必须各含 1 个 BEGIN/END 标记")
        span = locate_span(text, edit, path, "apply")
        spans.append((*span, edit["replace"], edit))
    assert_spans(path, spans)

    original = text
    for start, stop, replacement, _edit in sorted(spans, key=lambda s: s[0], reverse=True):
        text = text[:start] + replacement + text[stop:]

    if dry_run:
        print(f"--- would patch: {path}")
        for line in difflib.unified_diff(original.splitlines(), text.splitlines(),
                                         fromfile=str(path), tofile=str(path) + " (patched)", lineterm=""):
            print(line)
        results.append({"path": str(path), "status": "would-patch"})
    else:
        atomic_write(path, text)
        results.append({"path": str(path), "status": "patched"})


def revert_file(path: Path, entry: dict, begin: str, end: str,
                dry_run: bool, results: list):
    edits = entry["edits"]
    text = path.read_text(encoding="utf-8")
    begin_count = text.count(begin)
    end_count = text.count(end)
    if begin_count == 0 and end_count == 0:
        results.append({"path": str(path), "status": "not-patched"})
        return
    if begin_count != len(edits) or end_count != len(edits):
        fail(f"{path}: marker count mismatch (BEGIN={begin_count}, END={end_count}, "
             f"expected={len(edits)}) — refusing to revert a partially patched file")

    spans = []
    for edit in edits:
        span = locate_span(text, edit, path, "revert")
        if begin not in text[span[0]:span[1]]:
            fail(f"{path}: cannot revert edit {edit['id']}: 定位到的块不含本组 BEGIN 标记"
                 "——拒绝回滚一个不是本工具打出的块")
        spans.append((*span, edit["anchor"], edit))
    assert_spans(path, spans)

    original = text
    for start, stop, replacement, _edit in sorted(spans, key=lambda s: s[0], reverse=True):
        text = text[:start] + replacement + text[stop:]

    if dry_run:
        print(f"--- would revert: {path}")
        for line in difflib.unified_diff(original.splitlines(), text.splitlines(),
                                         fromfile=str(path), tofile=str(path) + " (reverted)", lineterm=""):
            print(line)
        results.append({"path": str(path), "status": "would-revert"})
    else:
        atomic_write(path, text)
        results.append({"path": str(path), "status": "reverted"})


def install_payload_file(target_root: Path, dest_rel: str, payload_path: Path,
                         dry_run: bool, verify: bool, results: list,
                         remove: bool = False):
    dest = target_root / dest_rel
    if remove:
        if not dest.is_file():
            results.append({"path": str(dest), "status": "absent"})
            return
        if not payload_path.is_file():
            fail(f"payload file missing: {payload_path}")
        if dest.read_text(encoding="utf-8") != payload_path.read_text(encoding="utf-8"):
            results.append({"path": str(dest), "status": "kept (modified)"})
            return
        if dry_run:
            print(f"--- would remove: {dest}")
            results.append({"path": str(dest), "status": "would-remove"})
        else:
            dest.unlink()
            results.append({"path": str(dest), "status": "removed"})
        return

    if not payload_path.is_file():
        fail(f"payload file missing: {payload_path}")
    content = payload_path.read_text(encoding="utf-8")
    if dest.is_file():
        if dest.read_text(encoding="utf-8") == content:
            results.append({"path": str(dest), "status": "unchanged"})
            return
        if verify:
            results.append({"path": str(dest), "status": "differs"})
            return
        print(f"  ! overwriting existing file with payload content: {dest}")
    elif verify:
        results.append({"path": str(dest), "status": "missing"})
        return
    if dry_run:
        print(f"--- would install: {dest}")
        results.append({"path": str(dest), "status": "would-install"})
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(dest, content)
    results.append({"path": str(dest), "status": "installed"})


def best_match(anchor_lines, file_lines):
    """在 file_lines 中找与 anchor 开头最相似的窗口，返回 (ratio, start_index)。"""
    probe = anchor_lines[:3]
    m = len(probe)
    best = (0.0, -1)
    for i in range(0, max(1, len(file_lines) - m + 1)):
        ratio = difflib.SequenceMatcher(None, probe, file_lines[i:i + m]).ratio()
        if ratio > best[0]:
            best = (ratio, i)
    return best


def explain_drift(target: Path, manifest: dict, as_json: bool) -> int:
    drift = []
    for entry in manifest["files"]:
        path = target / entry["path"]
        if not path.is_file():
            fail(f"expected file missing: {path}")
        text = path.read_text(encoding="utf-8")
        file_lines = text.splitlines()
        group = entry["group"]
        begin, _end = marker_pair(manifest, group)
        if text.count(begin) == len(entry["edits"]):
            continue  # 已打过补丁，跳过
        for edit in entry["edits"]:
            mode = edit.get("mode", "exact")
            if mode == "exact":
                count = text.count(edit["anchor"])
                if count == 1:
                    continue
                probe_text = edit["anchor"]
                detail = None
            else:
                block = edit.get("block") or {}
                unique_line = block.get("unique_line", "")
                count = text.count(unique_line)
                if count == 1:
                    # 直接调 extract_block（抛异常、不打印），保持 --json 的 stderr 干净
                    try:
                        extract_block(text, path, unique_line, block.get("sanity") or [],
                                      int(block.get("opener_lookback", 8)),
                                      anchor=edit.get("anchor"))
                        continue
                    except BlockError as exc:
                        detail = str(exc)
                        prefix = f"{path}: "
                        if detail.startswith(prefix):
                            detail = detail[len(prefix):]
                else:
                    detail = f"block unique_line 命中 {count} 次"
                probe_text = unique_line
            anchor_lines = probe_text.splitlines()
            ratio, start = best_match(anchor_lines, file_lines)
            lo = max(0, start - 3)
            hi = min(len(file_lines), start + len(anchor_lines) + 3)
            drift.append({
                "file": entry["path"],
                "edit": edit["id"],
                "mode": mode,
                "matched": count,
                "anchor_sha256": edit.get("anchor_sha256"),
                "best_match_ratio": round(ratio, 3),
                "best_match_line": start + 1 if start >= 0 else None,
                "context": "\n".join(file_lines[lo:hi]),
                **({"detail": detail} if detail else {}),
            })

    if as_json:
        print(json.dumps({"schema": 1, "upstream_version": manifest["upstream"]["version"],
                          "drift": drift}, ensure_ascii=False, indent=2))
    else:
        if not drift:
            print("no drift: all anchors match")
        for item in drift:
            print(f"\n=== {item['file']}  edit {item['edit']}  [{item['mode']}]  "
                  f"(matched {item['matched']} times) ===")
            print(f"  anchor sha256: {item['anchor_sha256']}")
            if item.get("detail"):
                print(f"  诊断: {item['detail']}")
            print(f"  最相似的窗口在第 {item['best_match_line']} 行（相似度 {item['best_match_ratio']}），"
                  "新版上游的实际文本如下（±3 行）：")
            for line in item["context"].splitlines():
                print(f"    | {line}")
    return 0 if not drift else 3


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST),
                        help="补丁清单（默认: %(default)s）")
    parser.add_argument("--target", default=None,
                        help="Clavis shell 源码树（默认取 manifest.upstream.tree_hint）")
    parser.add_argument("--payload", default=str(DEFAULT_PAYLOAD),
                        help="新增文件目录（默认: %(default)s）")
    parser.add_argument("--dry-run", action="store_true", help="只打印 diff，不写文件")
    parser.add_argument("--verify", action="store_true", help="只校验补丁是否在位")
    parser.add_argument("--revert", action="store_true", help="把补丁还原回上游原文")
    parser.add_argument("--remove-payload", action="store_true",
                        help="配合 --revert：同时删除与 payload 一致的已安装文件")
    parser.add_argument("--explain-drift", action="store_true",
                        help="诊断锚点漂移：打印新版上游在锚点附近的实际文本")
    parser.add_argument("--expect-upstream", default=None,
                        help="期望的 Clavis 版本（不符仅告警；锚点匹配才是判据）")
    parser.add_argument("--group", action="append", default=None, help="只处理某组（可重复）")
    parser.add_argument("--list-groups", action="store_true", help="列出补丁组")
    parser.add_argument("--list-edits", action="store_true",
                        help="按文件位置列出 edit（id/mode/首行），用于校准 manifest 顺序")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args()

    manifest = load_manifest(Path(args.manifest))
    payload_dir = Path(args.payload)
    target = Path(args.target or manifest["upstream"]["tree_hint"])
    groups = sorted({entry["group"] for entry in manifest["files"]})

    if args.list_groups:
        for slug in groups:
            files = [e["path"] for e in manifest["files"] if e["group"] == slug]
            print(f"{slug}: {', '.join(files)}")
        return

    if args.list_edits:
        for entry in manifest["files"]:
            print(f"{entry['path']}  [{entry['group']}]")
            for edit in entry["edits"]:
                mode = edit.get("mode", "exact")
                head = (edit.get("block") or {}).get("unique_line", "") if mode == "block" \
                    else edit["anchor"].splitlines()[0]
                print(f"  {edit['id']:<20} {mode:<6} {head.strip()[:70]!r}")
        return

    if not target.is_dir():
        fail(f"target tree not found: {target}")

    if args.expect_upstream and args.expect_upstream != manifest["upstream"]["version"]:
        print(f"WARN: 期望的上游版本 {args.expect_upstream} 与 manifest 记录的 "
              f"{manifest['upstream']['version']} 不同（仅提示；以锚点匹配为准）", file=sys.stderr)

    if args.explain_drift:
        sys.exit(explain_drift(target, manifest, args.json))

    selected = set(args.group) if args.group else set(groups)
    results: list = []

    # 全树预校验：写任何字节之前，确保所有目标文件要么已打补丁、要么全部 edit 可定位
    if not args.verify:
        for entry in manifest["files"]:
            if entry["group"] not in selected:
                continue
            path = target / entry["path"]
            text = path.read_text(encoding="utf-8")
            begin, _end = marker_pair(manifest, entry["group"])
            bc, ec = text.count(begin), text.count(_end)
            if args.revert:
                if bc == 0 and ec == 0:
                    continue
                if bc != len(entry["edits"]) or ec != len(entry["edits"]):
                    fail(f"{path}: marker count mismatch (BEGIN={bc}, END={ec}, "
                         f"expected={len(entry['edits'])}) — refusing to revert a partially patched file")
            else:
                if bc or ec:
                    if bc != len(entry["edits"]) or ec != len(entry["edits"]):
                        fail(f"{path}: marker count mismatch (BEGIN={bc}, END={ec}, "
                             f"expected={len(entry['edits'])}) — refusing to guess a partially patched file")
                    continue
            for edit in entry["edits"]:
                locate_span(text, edit, path, "revert" if args.revert else "apply")

    for entry in manifest["files"]:
        if entry["group"] not in selected:
            continue
        path = target / entry["path"]
        if not path.is_file():
            fail(f"expected file missing: {path}")
        begin, end = marker_pair(manifest, entry["group"])
        if args.revert:
            revert_file(path, entry, begin, end, args.dry_run, results)
        else:
            patch_file(path, entry, begin, end, args.dry_run, args.verify, results)

    for item in manifest["payload"]:
        if args.revert and not args.remove_payload:
            continue
        install_payload_file(target, item["dest"], payload_dir / item["src"],
                             args.dry_run, args.verify, results, remove=args.revert)

    if args.json:
        ok = all(r["status"] in ("already-patched", "unchanged", "reverted", "removed",
                                 "not-patched", "absent", "kept (modified)") for r in results)
        print(json.dumps({"schema": 1, "target": str(target),
                          "upstream_version": manifest["upstream"]["version"],
                          "ok": ok, "results": results}, ensure_ascii=False, indent=2))
    else:
        for item in results:
            print(f"{item['status']:>14}  {item['path']}")

    if args.verify:
        good = ("already-patched", "unchanged")
        bad = [r["path"] for r in results if r["status"] not in good]
        if bad:
            fail("verify failed: " + ", ".join(bad))
        print("verify ok: patch present")
    if args.revert and not args.dry_run:
        leftover = [r["path"] for r in results
                    if r["status"] in ("patched", "installed", "differs", "missing")]
        if leftover:
            fail("revert incomplete: " + ", ".join(leftover))


if __name__ == "__main__":
    main()
