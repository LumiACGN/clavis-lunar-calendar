#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 LumiACGN
# SPDX-License-Identifier: GPL-3.0-or-later
#
# 数据层冒烟（真 QML 引擎，offscreen）——把 scripts/dev/smoke.sh 纳入测试套件，
# 并给断言装上「牙齿」：把数据层故意改坏，smoke 必须非零退出。
#
# 缺 qml（qt6-declarative 未装）时**跳过**（@skip 风格：打印警告 + rc 0）——
# CI 的 ubuntu-latest 默认没装 qt6-declarative，本机（Arch/CachyOS）则必须真跑。
# 用 QML_BIN 可指定解释器（与 scripts/dev/smoke.sh 同一套探测顺序）。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SMOKE_SH="$REPO/scripts/dev/smoke.sh"
SMOKE_QML="$REPO/tests/smoke.qml"
PAYLOAD="$REPO/qml/ChineseCalendar.js"
EXPECTED_CHECKS=18   # tests/smoke.qml 的 check(...) 条数（docs/CONTRIBUTING 也写作 18 条断言）

fail() { echo "FAIL: $*" >&2; exit 1; }

[ -f "$SMOKE_SH" ] || fail "缺少 $SMOKE_SH"
[ -f "$SMOKE_QML" ] || fail "缺少 $SMOKE_QML"
[ -f "$PAYLOAD" ] || fail "缺少数据层产物 $PAYLOAD"

QML="$(
    if [ -n "${QML_BIN:-}" ]; then
        printf '%s' "$QML_BIN"
    elif command -v qml >/dev/null 2>&1; then
        command -v qml
    elif [ -x /usr/lib/qt6/bin/qml ]; then
        printf '%s' /usr/lib/qt6/bin/qml
    fi
)"
if [ -z "$QML" ]; then
    echo "SKIP: 未找到 qml（qt6-declarative 未装）——跳过真 QML 引擎冒烟（CI 的 ubuntu-latest 上属正常）"
    exit 0
fi
[ -x "$QML" ] || fail "QML 解释器不可执行：$QML"

checks="$(grep -c 'check(' "$SMOKE_QML" || true)"
[ "$checks" -ge 1 ] || fail "tests/smoke.qml 里找不到 check(...) 断言"
[ "$checks" -eq "$EXPECTED_CHECKS" ] \
    || echo "WARN: tests/smoke.qml 的断言数从 $EXPECTED_CHECKS 变成 $checks（同步更新本脚本）" >&2

T="$(mktemp -d /tmp/smoke-test.XXXXXX)"
trap 'rm -rf "$T"' EXIT

echo "### 1) scripts/dev/smoke.sh（真 QML 引擎，offscreen；解释器 $QML）"
out="$(bash "$SMOKE_SH" 2>&1)" || { printf '%s\n' "$out" >&2; fail "scripts/dev/smoke.sh 非零退出"; }
grep -q -- "---- RESULT: PASS ----" <<<"$out" || { printf '%s\n' "$out" >&2; fail "smoke 未报告 PASS"; }
if grep -q '^qml: FAIL' <<<"$out"; then
    printf '%s\n' "$out" >&2
    fail "smoke 报告了 FAIL 行"
fi
ok_count="$(grep -c '^qml: ok ' <<<"$out" || true)"
[ "$ok_count" -eq "$checks" ] \
    || fail "通过的断言数（$ok_count）与 smoke.qml 的 check() 条数（$checks）不一致"
grep -q '^qml: dataRange=' <<<"$out" || fail "smoke 应打印 dataRange()（数据层自报告版本/截止日）"

echo "### 2) 负例：数据层被改坏 → smoke 必须非零退出（断言有牙齿）"
mkdir -p "$T/tests" "$T/qml"
cp "$SMOKE_QML" "$T/tests/smoke.qml"
python3 - "$PAYLOAD" "$T/qml/ChineseCalendar.js" <<'PY'
import pathlib, sys
src, dst = (pathlib.Path(p) for p in sys.argv[1:3])
text = src.read_text(encoding="utf-8")
assert "国庆节" in text, "数据层里找不到用于打坏断言的字面量"
dst.write_text(text.replace("国庆节", "國慶節"), encoding="utf-8")
PY
rc=0
out2="$(cd "$T/tests" && QT_QPA_PLATFORM=offscreen QT_FORCE_STDERR_LOGGING=1 \
        timeout 60 "$QML" smoke.qml 2>&1)" || rc=$?
[ "$rc" -ne 0 ] || { printf '%s\n' "$out2" >&2; fail "数据层被改坏时 smoke 仍以 0 退出"; }
grep -q -- "---- RESULT: FAIL" <<<"$out2" || { printf '%s\n' "$out2" >&2; fail "smoke 失败时未报告 RESULT: FAIL"; }

echo "全部通过 ✓"
