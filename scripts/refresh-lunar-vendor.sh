#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 LumiACGN
# SPDX-License-Identifier: GPL-3.0-or-later
#
# refresh-lunar-vendor.sh — 更新/重建 vendored lunar-javascript 与 qml/ChineseCalendar.js
#
# 用法：
#   ./scripts/refresh-lunar-vendor.sh                   # 用现有 vendor 版本重建 payload（离线）
#   ./scripts/refresh-lunar-vendor.sh --version 1.8.0   # 下载指定新版本 → 校验 → 重建
#   ./scripts/refresh-lunar-vendor.sh --check           # 重建后跑 qmllint + 真引擎冒烟
#
# 次年节假日安排（通常 11~12 月公布）发布后：
#   1. 查上游是否有新版： ./scripts/check-lunar-data.sh
#   2. 升级并验证：       ./scripts/refresh-lunar-vendor.sh --version <新版本> --check
#   3. 提交后装进机器：   ./install.sh --sync     （或等新版包）
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SELF_DIR/.." && pwd)"
VENDOR_DIR="$REPO/lunar/vendor"
VER=""
CHECK=0
while [ $# -gt 0 ]; do
    case "$1" in
        --version) shift; VER="${1:?--version 需要一个版本号}" ;;
        --check) CHECK=1 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
    shift
done

die() { echo "ERROR: $*" >&2; exit 1; }

# npm 最新版提示（失败不影响流程）
NPM_LATEST="$(curl -fsSL --max-time 20 https://registry.npmjs.org/lunar-javascript/latest 2>/dev/null | jq -r .version 2>/dev/null || true)"
[ -n "$NPM_LATEST" ] && echo "npm 最新版: $NPM_LATEST"

if [ -z "$VER" ]; then
    VER="$(ls "$VENDOR_DIR" | sed -n 's/^lunar-\(.*\)\.js$/\1/p' | sort -V | tail -1)"
    [ -n "$VER" ] || die "vendor 目录没有任何 lunar-*.js"
fi

VENDOR="$VENDOR_DIR/lunar-$VER.js"
if [ ! -f "$VENDOR" ]; then
    echo "== 下载 lunar-javascript $VER"
    URL="https://cdn.jsdelivr.net/npm/lunar-javascript@$VER/lunar.js"
    curl -fsSL --retry 3 --max-time 120 "$URL" -o "$VENDOR.tmp" || die "下载失败：$URL（检查网络/代理）"
    SIZE="$(wc -c < "$VENDOR.tmp")"
    [ "$SIZE" -gt 400000 ] || die "文件过小（$SIZE 字节），疑似下载错误"
    head -c 24 "$VENDOR.tmp" | grep -q ';(function(root,factory){' || die "UMD 头部不符，上游结构已变"
    grep -q 'HolidayUtil:HolidayUtil' "$VENDOR.tmp" || die "未找到 HolidayUtil 导出"

    # 供应链校验：npm registry 的 dist.integrity（sha512）与下载文件比对
    INTEGRITY="$(curl -fsSL --max-time 30 "https://registry.npmjs.org/lunar-javascript/$VER" 2>/dev/null | jq -r '.dist.integrity // empty' 2>/dev/null || true)"
    if [ -n "$INTEGRITY" ]; then
        GOT="sha512-$(python3 - "$VENDOR.tmp" <<'PY'
import base64, hashlib, sys
print(base64.b64encode(hashlib.sha512(open(sys.argv[1], "rb").read()).digest()).decode())
PY
)"
        [ "$GOT" = "$INTEGRITY" ] || die "npm integrity 校验失败：期望 $INTEGRITY，实得 $GOT"
        echo "npm integrity 校验通过（sha512）"
    else
        echo "WARN: 取不到 npm dist.integrity，已跳过（仍做了大小/UMD/HolidayUtil 三道校验）" >&2
    fi

    mv "$VENDOR.tmp" "$VENDOR"
    {
        echo "lunar-javascript $VER  url=$URL  fetched=$(date +%F)"
        (cd "$VENDOR_DIR" && sha256sum "lunar-$VER.js")
    } >> "$VENDOR_DIR/SHA256SUMS"
fi

echo "== 重建 qml/ChineseCalendar.js（vendor $VER）"
python3 "$SELF_DIR/build_chinese_calendar.py" --vendor "$VENDOR" --version "$VER" \
    || die "构建失败"

if [ "$CHECK" = 1 ]; then
    echo "== 门禁：qmllint（对 442KB 产物，node 编译会崩，勿换工具）"
    QMLLINT="$(command -v qmllint || true)"
    [ -n "$QMLLINT" ] || [ -x /usr/lib/qt6/bin/qmllint ] && QMLLINT="${QMLLINT:-/usr/lib/qt6/bin/qmllint}"
    if [ -n "$QMLLINT" ]; then
        "$QMLLINT" "$REPO/qml/ChineseCalendar.js" || die "qmllint 失败"
    else
        echo "WARN: 未找到 qmllint（qt6-declarative），跳过" >&2
    fi
    echo "== 门禁：真实 QML 引擎冒烟"
    bash "$REPO/scripts/dev/smoke.sh" || die "冒烟测试未通过"
fi

echo
echo "下一步：git diff 检查 → 提交；装进本机：./install.sh --sync"
