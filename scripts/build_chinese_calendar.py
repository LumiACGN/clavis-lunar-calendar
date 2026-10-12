#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 LumiACGN
# SPDX-License-Identifier: GPL-3.0-or-later
"""组装 qml/ChineseCalendar.js = .pragma library 头 + CommonJS shim + vendor 原文 + 适配层。

产物由 overlay 安装引擎装到 Clavis 树的 Common/functions/ChineseCalendar.js
（payload 映射见 patches/manifest.json；本脚本只负责生成仓库里的那一份）。

用法（默认值全部相对本仓库，可零参数运行）:
    python3 scripts/build_chinese_calendar.py
    python3 scripts/build_chinese_calendar.py --version 1.8.0 --vendor lunar/vendor/lunar-1.8.0.js
"""
import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

HEADER = """.pragma library
// ============================================================================
// ChineseCalendar.js — Clavis 侧边栏日历的农历/节气/节日/法定节假日休班数据层
//
// 本文件由 scripts/build_chinese_calendar.py 生成，请勿手改！
// 修改请改 lunar/adapter.js（适配层），然后重跑：
//     python3 scripts/build_chinese_calendar.py
//     ./install.sh --sync          # 装进 overlay；打包用户请等新版包
// 上游数据换新版本：scripts/refresh-lunar-vendor.sh --version <ver>
//
// 组成：
//   1) 6tail/lunar-javascript {version}（MIT，https://github.com/6tail/lunar-javascript）
//      —— vendor 原文逐字节 + 一行 CommonJS shim（QML 下顶层 this 不可靠，
//         强制 UMD 走 module.exports 分支，绕开全局污染）。
//   2) 适配层（文件尾部 END vendor 之后）：对外暴露 dayInfo(ymd)/dataRange()/clearCache()。
//
// 内置节假日数据截止：{data_end}（HolidayUtil._DATA 最后一条记录；共 {record_count} 条）
// 次年安排公布后，升级 lunar-javascript 并重跑 refresh 脚本即可。
// ============================================================================
// ---- BEGIN vendor: lunar-javascript {version} (verbatim, MIT) ----
var module = {{ exports: {{}} }};
"""

VENDOR_TAIL = """
var _LUNAR = module.exports;
// ---- END vendor ----
// ---- BEGIN adapter (adapter.js) ----
var LUNAR_JS_VERSION = "{version}";
var HOLIDAY_DATA_END = "{data_end}";
"""


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


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


def detect_vendor(vendor_dir: Path) -> Path:
    candidates = sorted(vendor_dir.glob("lunar-*.js"))
    if not candidates:
        fail(f"vendor 目录里没有 lunar-*.js：{vendor_dir}")
    if len(candidates) > 1:
        fail("vendor 目录有多个 lunar-*.js，请显式用 --vendor 指定："
             + ", ".join(p.name for p in candidates))
    return candidates[0]


def default_version(vendor_path: Path) -> str:
    match = re.fullmatch(r"lunar-(.+)\.js", vendor_path.name)
    if not match:
        fail(f"无法从文件名推断版本号：{vendor_path.name}（请用 --version 指定）")
    return match.group(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendor", default=None,
                        help="vendored lunar.js 原文（默认 lunar/vendor 里唯一的 lunar-*.js）")
    parser.add_argument("--adapter", default=str(REPO_ROOT / "lunar" / "adapter.js"),
                        help="adapter.js 路径（默认: %(default)s）")
    parser.add_argument("--out", default=str(REPO_ROOT / "qml" / "ChineseCalendar.js"),
                        help="输出路径（默认: %(default)s）")
    parser.add_argument("--version", default=None,
                        help="lunar-javascript 版本号（默认从 vendor 文件名推断）")
    args = parser.parse_args()

    vendor_path = Path(args.vendor) if args.vendor else detect_vendor(REPO_ROOT / "lunar" / "vendor")
    adapter_path = Path(args.adapter)
    out_path = Path(args.out)
    version = args.version or default_version(vendor_path)
    if not vendor_path.is_file():
        fail(f"vendor not found: {vendor_path}")
    if not adapter_path.is_file():
        fail(f"adapter not found: {adapter_path}")

    vendor = vendor_path.read_text(encoding="utf-8")
    if not vendor.startswith(";(function(root,factory){"):
        fail("vendor head changed — expected UMD start ';(function(root,factory){'")
    if "HolidayUtil:HolidayUtil" not in vendor:
        fail("vendor does not export HolidayUtil — wrong file?")
    if "module.exports = factory()" not in vendor:
        fail("vendor UMD no longer has the CommonJS branch — shim assumptions broken")

    match = re.search(r"_DATA='(\d+)'", vendor)
    if not match:
        fail("cannot locate HolidayUtil._DATA — upstream data format changed?")
    data = match.group(1)
    if len(data) % 18 != 0:
        fail(f"_DATA length {len(data)} not divisible by record size 18 — format changed?")
    record_count = len(data) // 18
    last_day = data[-18:][:8]
    data_end = f"{last_day[:4]}-{last_day[4:6]}-{last_day[6:]}"

    adapter = adapter_path.read_text(encoding="utf-8").rstrip() + "\n"

    content = (
        HEADER.format(version=version, data_end=data_end, record_count=record_count)
        + vendor.rstrip()
        + "\n"
        + VENDOR_TAIL.format(version=version, data_end=data_end)
        + adapter
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(out_path, content)

    print(f"built  {out_path.relative_to(REPO_ROOT)}")
    print(f"  version          {version}")
    print(f"  holiday records  {record_count} (data end {data_end})")
    print(f"  size             {out_path.stat().st_size} bytes")
    assert '.pragma library' in content.splitlines()[0]
    assert "function dayInfo(" in content


if __name__ == "__main__":
    main()
