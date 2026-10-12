#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 LumiACGN
# SPDX-License-Identifier: GPL-3.0-or-later
#
# 双组件共存彩排（假 HOME + fixture 源树，零风险）。
#
# 用 tests/fixtures/dummy-kit 现场组装出第二个「组件」（复制一份补丁器 + 迷你 manifest
# + payload），验证引擎 v2 的多 kit 语义：
#   1) 两套 marker 同时在位，且**共享文件**（Modules/Sidebars/Dashboard/infoTools/
#      CalendarWidget.qml）里并存——dummy 与本仓同 order 20，按 id 字母序 dummy 先重放；
#   2) 两个组件各自 verify 退出码 0；
#   3) 交替 sync 四轮后 overlay 整树 sha256 稳定（共享注册表 + 重放幂等）；
#   4) 负例：未注册 group 的孤儿标记 → verify 响亮失败；
#   5) 负例：某组件的 repo 被删/搬走 → sync 响亮失败并提示 --forget 逃生舱，
#      --forget + sync 后恢复；重新注册后 overlay 回到初始状态；
#   6) 组件级卸载只摘自己（dummy 走人，本仓的补丁与 2 个载荷完好）；
#   7) 卸最后一个组件 → overlay 目录消失、clavis-overlay 状态目录（含备份）保留。
#
# 全部命令带 --skip-service（引擎的「假 HOME 自动跳过服务控制」是第二道保险），
# 期望值全部从两个 manifest 派生。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="$REPO/patches/manifest.json"
DUMMY_MANIFEST="$REPO/tests/fixtures/dummy-kit/manifest.json"

mfield() { python3 -c "import json,sys; print($1)" "$2"; }
FIXTURE="$REPO/tests/fixtures/upstream-$(mfield 'json.load(open(sys.argv[1]))["upstream"]["version"]' "$MANIFEST")"
CAL_KIT="$(mfield 'json.load(open(sys.argv[1]))["kit"]["id"]' "$MANIFEST")"
CAL_GROUP="$(mfield 'sorted({e["group"] for e in json.load(open(sys.argv[1]))["files"]})[0]' "$MANIFEST")"
DUMMY_KIT="$(mfield 'json.load(open(sys.argv[1]))["kit"]["id"]' "$DUMMY_MANIFEST")"
DUMMY_GROUP="$(mfield 'sorted({e["group"] for e in json.load(open(sys.argv[1]))["files"]})[0]' "$DUMMY_MANIFEST")"
SHARED="$(mfield 'json.load(open(sys.argv[1]))["files"][0]["path"]' "$DUMMY_MANIFEST")"
shared_edits() {
    python3 -c "import json,sys; print(len([e for e in json.load(open(sys.argv[1]))['files'] if e['path'] == sys.argv[2]][0]['edits']))" \
        "$1" "$SHARED"
}
CAL_SHARED_EDITS="$(shared_edits "$MANIFEST")"
DUMMY_SHARED_EDITS="$(shared_edits "$DUMMY_MANIFEST")"

T="$(mktemp -d /tmp/coexist-test.XXXXXX)"
trap 'rm -rf "$T"' EXIT

# 假 Clavis 源树（pristine 上游 + 一个 shell.qml 桩）
mkdir -p "$T/src/quickshell/clavis"
cp -r "$FIXTURE/"* "$T/src/quickshell/clavis/"
printf '// stub shell.qml\n' > "$T/src/quickshell/clavis/shell.qml"
SRC="$T/src/quickshell/clavis"

# 组装 dummy kit（一个「长得像完整组件仓库」的目录）
mkdir -p "$T/dummy-kit/scripts" "$T/dummy-kit/patches"
cp "$REPO/scripts/patch_clavis_kit.py" "$T/dummy-kit/scripts/"
cp "$DUMMY_MANIFEST" "$T/dummy-kit/patches/manifest.json"
cp -r "$REPO/tests/fixtures/dummy-kit/qml" "$T/dummy-kit/qml"
DUMMY_HOME="$T/dummy-kit"

ENVV=(env HOME="$T/home" XDG_CONFIG_HOME="$T/home/.config" XDG_STATE_HOME="$T/home/.state")
OV="$T/home/.config/quickshell/clavis"
STATE_ROOT="$T/home/.state/clavis-overlay"
KITS_DIR="$STATE_ROOT/kits"

fail() { echo "FAIL: $*" >&2; exit 1; }
treehash() { find "$1" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1; }
begin() { echo "// BEGIN clavis-$1"; }

cal() { "${ENVV[@]}" "$REPO/install.sh" "$@"; }
dummy() { "${ENVV[@]}" python3 "$REPO/scripts/clavis_overlay.py" "$@"; }

echo "### 1) 注册两个组件（$CAL_KIT order 20 / $DUMMY_KIT order 20，同 order 按 id 字母序）"
cal --tree "$SRC" --skip-service --no-config-migrate >/dev/null
dummy install --repo-root "$DUMMY_HOME" --tree "$SRC" --skip-service >"$T/dummy_install.out" 2>&1
grep -q "$DUMMY_KIT(order 20) → $CAL_KIT(order 20)" "$T/dummy_install.out" \
    || fail "同 order 时重放顺序应为 id 字母序（dummy 在前）：$(grep 重放顺序 "$T/dummy_install.out" || true)"
for group in "$CAL_GROUP" "$DUMMY_GROUP"; do
    grep -q "$(begin "$group")" "$OV/$SHARED" || fail "共享文件里缺少 $group 的标记"
done
[ "$(grep -c "$(begin "$CAL_GROUP")" "$OV/$SHARED")" -eq "$CAL_SHARED_EDITS" ] \
    || fail "共享文件里本仓的标记数不对"
[ "$(grep -c "$(begin "$DUMMY_GROUP")" "$OV/$SHARED")" -eq "$DUMMY_SHARED_EDITS" ] \
    || fail "共享文件里 dummy 的标记数不对"
[ -f "$OV/Common/functions/DummyProbe.js" ] || fail "dummy payload 缺失"
[ -f "$KITS_DIR/$CAL_KIT.json" ] || fail "本仓 kit 状态缺失"
[ -f "$KITS_DIR/$DUMMY_KIT.json" ] || fail "dummy kit 状态缺失"
python3 - "$KITS_DIR/$CAL_KIT.json" "$KITS_DIR/$DUMMY_KIT.json" "$MANIFEST" "$DUMMY_MANIFEST" "$SHARED" <<'PY' \
    || fail "两个 kit 的状态文件内容不正确"
import json, sys
cal, dummy, cal_mf, dm_mf = (json.load(open(p)) for p in sys.argv[1:5])
shared = sys.argv[5]
assert cal["schema"] == 2 and dummy["schema"] == 2
assert cal["order"] == cal_mf["kit"]["order"] == 20, cal["order"]
assert dummy["order"] == dm_mf["kit"]["order"] == 20, dummy["order"]
# 本仓纯 QML：没有后端脚本，状态里 bin 必须是 None
assert cal["bin_name"] is None and cal["bin"] is None, (cal["bin_name"], cal["bin"])
assert dummy["bin_name"] is None and dummy["bin"] is None, (dummy["bin_name"], dummy["bin"])
assert len(cal["digests"]["payload"]) == len(cal_mf["payload"]) == 2, cal["digests"]["payload"]
assert sorted(dummy["digests"]["payload"]) == sorted(p["dest"] for p in dm_mf["payload"])
assert sorted(cal["groups"]) == sorted({e["group"] for e in cal_mf["files"]})
assert sorted(dummy["groups"]) == sorted({e["group"] for e in dm_mf["files"]})
# 共享文件的终态字节：两个 kit 的摘要记录必须一致（同一份字节）
assert shared in cal["digests"]["patched"] and shared in dummy["digests"]["patched"]
assert cal["digests"]["patched"][shared] == dummy["digests"]["patched"][shared]
PY
H0="$(treehash "$OV")"

echo "### 2) 两个组件各自 verify 通过"
cal --verify --skip-service >"$T/v_cal.out" 2>&1 || { cat "$T/v_cal.out" >&2; fail "本仓 verify 非零退出"; }
grep -q "verify ok" "$T/v_cal.out" || fail "本仓 verify 未报告成功"
grep -q "$DUMMY_KIT" "$T/v_cal.out" || fail "verify 应逐组件报告（其中应有 $DUMMY_KIT）"
dummy verify --repo-root "$DUMMY_HOME" --skip-service >"$T/v_dummy.out" 2>&1 \
    || { cat "$T/v_dummy.out" >&2; fail "dummy verify 非零退出"; }
grep -q "verify ok" "$T/v_dummy.out" || fail "dummy verify 未报告成功"
grep -q "无孤儿标记" "$T/v_dummy.out" || fail "verify 应报告无孤儿标记"

echo "### 3) 交替 sync 四轮后 overlay 整树 sha256 稳定"
for round in 1 2; do
    cal --sync --tree "$SRC" --skip-service >/dev/null || fail "第 $round 轮本仓 sync 失败"
    [ "$(treehash "$OV")" = "$H0" ] || fail "第 $round 轮本仓 sync 后整树变化"
    dummy sync --repo-root "$DUMMY_HOME" --tree "$SRC" --skip-service >/dev/null \
        || fail "第 $round 轮 dummy sync 失败"
    [ "$(treehash "$OV")" = "$H0" ] || fail "第 $round 轮 dummy sync 后整树变化"
done
grep -q "$(begin "$CAL_GROUP")" "$OV/$SHARED" || fail "四轮 sync 后本仓补丁丢失"
grep -q "$(begin "$DUMMY_GROUP")" "$OV/$SHARED" || fail "四轮 sync 后 dummy 补丁丢失"

echo "### 4) 负例：overlay 里塞入未注册 group 的标记 → verify 报孤儿标记"
cp "$OV/$SHARED" "$T/shared.keep"
printf '\n// BEGIN clavis-manual-probe\n' >> "$OV/$SHARED"
if cal --verify --skip-service >"$T/orphan.out" 2>&1; then
    fail "存在孤儿标记时 verify 不应成功"
fi
grep -q "孤儿标记" "$T/orphan.out" || fail "verify 应报告孤儿标记"
grep -q "manual-probe" "$T/orphan.out" || fail "孤儿标记报告应点名未注册的 group"
cp "$T/shared.keep" "$OV/$SHARED"
[ "$(treehash "$OV")" = "$H0" ] || fail "恢复后 overlay 应回到原状"

echo "### 5) 负例：某组件的 repo 被删/搬走 → sync 响亮失败并提示 --forget"
python3 - "$KITS_DIR/$DUMMY_KIT.json" <<'PY'
import json, sys
path = sys.argv[1]
kit = json.load(open(path))
kit["repo_root"] = "/nonexistent/clavis-dummy-kit"
json.dump(kit, open(path, "w"), ensure_ascii=False, indent=2)
PY
if cal --sync --tree "$SRC" --skip-service >"$T/missing.out" 2>&1; then
    fail "有组件仓库缺失时 sync 不应成功"
fi
grep -q "$DUMMY_KIT" "$T/missing.out" || fail "失败信息应点名缺失的组件"
grep -q "forget" "$T/missing.out" || fail "失败信息应提示 forget 逃生舱"
[ "$(treehash "$OV")" = "$H0" ] || fail "失败的 sync 不应改动 overlay"

cal --forget "$DUMMY_KIT" >/dev/null || fail "--forget 失败"
[ -e "$KITS_DIR/$DUMMY_KIT.json" ] && fail "--forget 后 kit 状态文件应被移除"
cal --sync --tree "$SRC" --skip-service >/dev/null || fail "forget 后本仓 sync 应恢复成功"
grep -q "$(begin "$CAL_GROUP")" "$OV/$SHARED" || fail "forget+sync 后本仓的补丁应仍在"
grep -q "$(begin "$DUMMY_GROUP")" "$OV/$SHARED" \
    && fail "未注册组件的补丁不应被重放（overlay 里应已清掉）"
[ -e "$OV/Common/functions/DummyProbe.js" ] && fail "未注册组件的载荷不应被重放"
cal --verify --skip-service >/dev/null 2>&1 || fail "forget+sync 后本仓 verify 应通过"

echo "### 5b) dummy 重新注册（--repo-root 重绑）"
dummy install --repo-root "$DUMMY_HOME" --tree "$SRC" --skip-service >/dev/null \
    || fail "dummy 重新注册失败"
grep -q "$(begin "$DUMMY_GROUP")" "$OV/$SHARED" || fail "重新注册后 dummy 补丁应回来"
[ -f "$OV/Common/functions/DummyProbe.js" ] || fail "重新注册后 dummy 载荷应回来"
[ "$(treehash "$OV")" = "$H0" ] || fail "重新注册后 overlay 应与初始状态逐字节一致"

echo "### 6) 组件级卸载只摘自己（dummy 走人，本仓完好）"
dummy uninstall --repo-root "$DUMMY_HOME" --skip-service >/dev/null || fail "dummy 卸载失败"
grep -q "$(begin "$DUMMY_GROUP")" "$OV/$SHARED" && fail "dummy 的标记应已消失"
[ -e "$OV/Common/functions/DummyProbe.js" ] && fail "dummy 的载荷应已消失"
[ -e "$KITS_DIR/$DUMMY_KIT.json" ] && fail "dummy 的 kit 状态文件应已移除"
[ -f "$KITS_DIR/$CAL_KIT.json" ] || fail "本仓的 kit 状态文件应保留"
python3 - "$MANIFEST" "$OV" <<'PY' || fail "卸载 dummy 后本仓的补丁/载荷应完好"
import json, pathlib, sys
mf = json.load(open(sys.argv[1])); ov = pathlib.Path(sys.argv[2])
for entry in mf["files"]:
    begin = mf["marker"]["begin"].replace("{group}", entry["group"])
    text = (ov / entry["path"]).read_text(encoding="utf-8")
    assert text.count(begin) == len(entry["edits"]), (entry["path"], text.count(begin))
missing = [p["dest"] for p in mf["payload"] if not (ov / p["dest"]).is_file()]
assert not missing, missing
PY
cal --verify --skip-service >/dev/null 2>&1 || fail "卸载 dummy 后本仓 verify 应通过"
cal --list >"$T/list.out" 2>&1 || fail "--list 失败"
grep -q "$CAL_KIT" "$T/list.out" || fail "--list 应仍列出 $CAL_KIT"
grep -q "$DUMMY_KIT" "$T/list.out" && fail "--list 不应再列出 $DUMMY_KIT"

echo "### 7) 卸最后一个组件 → overlay 目录消失、状态目录（含备份）保留"
cal --uninstall --skip-service >/dev/null || fail "本仓卸载失败"
[ -e "$OV" ] && fail "最后一个组件卸载后 overlay 应消失"
[ -e "$STATE_ROOT/kits/$CAL_KIT.json" ] && fail "kit 状态文件应被移除"
[ -d "$STATE_ROOT" ] || fail "clavis-overlay 状态目录应保留"
[ -d "$STATE_ROOT/backups" ] || fail "状态目录里的 backups 应保留"
find "$STATE_ROOT/backups" -name '*.tar.gz' -print -quit | grep -q . || fail "backups 里应有备份"
[ -e "$T/home/.local/bin" ] && [ -n "$(ls -A "$T/home/.local/bin")" ] \
    && fail "本组件没有后端脚本，~/.local/bin 不应有任何新文件"

echo "全部通过 ✓"
