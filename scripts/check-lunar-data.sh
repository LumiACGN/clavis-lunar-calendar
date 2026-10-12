#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 LumiACGN
# SPDX-License-Identifier: GPL-3.0-or-later
#
# check-lunar-data.sh — 只读检查（定时器用）：当前 vendored 农历库版本、内置
# 法定节假日数据截止日、上游 npm 是否有新版。**绝不改动任何文件**。
#
# 用法：
#   ./scripts/check-lunar-data.sh            # 打印报告
#   ./scripts/check-lunar-data.sh --notify   # 有新版数据时发桌面通知（notify-send）
#
# 退出码：0 = 正常（含"有新版"——定时器不应因有新版本而标红）；
#         1 = 检查本身失败（缺文件等硬错误）；2 = 缺 curl/jq。
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SELF_DIR/.." && pwd)"
NOTIFY=0
[ "${1:-}" = "--notify" ] && NOTIFY=1

for tool in curl jq; do
    command -v "$tool" >/dev/null 2>&1 || { echo "ERROR: 需要 $tool（见 README 的 optdepends）" >&2; exit 2; }
done

CUR="$(ls "$REPO/lunar/vendor" 2>/dev/null | sed -n 's/^lunar-\(.*\)\.js$/\1/p' | sort -V | tail -1)"
[ -n "$CUR" ] || { echo "ERROR: lunar/vendor 里没有 lunar-*.js" >&2; exit 1; }

BUILT="$REPO/qml/ChineseCalendar.js"
[ -f "$BUILT" ] || { echo "ERROR: 缺少 $BUILT（跑 scripts/build_chinese_calendar.py 重建）" >&2; exit 1; }
END="$(grep -o 'HOLIDAY_DATA_END = "[0-9-]*"' "$BUILT" | sed -n 's/.*"\([0-9-]*\)".*/\1/p' | head -1)"

LATEST="$(curl -fsSL --max-time 20 https://registry.npmjs.org/lunar-javascript/latest 2>/dev/null | jq -r .version 2>/dev/null || true)"

echo "clavis-lunar-calendar 数据检查"
echo "  当前 vendor 版本 : lunar-javascript $CUR"
echo "  节假日数据截止   : ${END:-未知}"
if [ -z "$LATEST" ]; then
    echo "  上游最新版       : （查询失败——离线或网络受限；这不影响日历本身）"
    exit 0
fi
echo "  上游最新版       : $LATEST"

if [ "$CUR" = "$LATEST" ]; then
    echo "结论：已是最新，无需操作。"
    exit 0
fi

echo "结论：上游有新版 $LATEST（当前 $CUR）。"
echo "  升级步骤（见 docs/DATA.md）："
echo "    ./scripts/refresh-lunar-vendor.sh --version $LATEST --check"
echo "    git diff → 提交 → ./install.sh --sync（或等新版包）"
if [ "$NOTIFY" = 1 ] && command -v notify-send >/dev/null 2>&1; then
    notify-send -a clavis-lunar-calendar "农历数据可更新" \
        "上游 lunar-javascript 已发布 $LATEST（当前 $CUR）。节假日数据通常 11~12 月随新版发布。"
fi
exit 0
