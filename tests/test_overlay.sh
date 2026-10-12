#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 LumiACGN
# SPDX-License-Identifier: GPL-3.0-or-later
#
# overlay 引擎 v2 端到端彩排（假 HOME + 假 Clavis 树，零风险；CI 与本地共用）。
# 覆盖：dry-run 零写入 → 安装 → 断言（5 + 2 个标记、2 个载荷、无后端脚本）→
#       verify → sync 幂等 → 源树 block 漂移（花括号陷阱）时干净失败且 overlay 逐字节不变
#       → uninstall → 源树里已烘进旧补丁时的规范化重放（6a 回滚重放）。
#
# 期望值（fixture 版本、kit 状态路径、marker 串、各文件标记数、载荷清单）全部从
# patches/manifest.json 派生，不硬编码。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="$REPO/patches/manifest.json"

mfield() { python3 -c "import json,sys; print($1)" "$MANIFEST"; }
FIXTURE="$REPO/tests/fixtures/upstream-$(mfield 'json.load(open(sys.argv[1]))["upstream"]["version"]')"
GROUP="$(mfield 'sorted({e["group"] for e in json.load(open(sys.argv[1]))["files"]})[0]')"
KIT_ID="$(mfield 'json.load(open(sys.argv[1]))["kit"]["id"]')"
WIDGET="$(mfield '[e["path"] for e in json.load(open(sys.argv[1]))["files"] if e["path"].endswith("CalendarWidget.qml")][0]')"
LAYOUT="$(mfield '[e["path"] for e in json.load(open(sys.argv[1]))["files"] if e["path"].endswith("calendar_layout.js")][0]')"
edits_in() {
    python3 -c "import json,sys; print(len([e for e in json.load(open(sys.argv[1]))['files'] if e['path'] == sys.argv[2]][0]['edits']))" \
        "$MANIFEST" "$1"
}
payload_dests() {
    python3 -c "import json,sys; [print(p['dest']) for p in json.load(open(sys.argv[1]))['payload']]" "$MANIFEST"
}
WIDGET_EDITS="$(edits_in "$WIDGET")"
LAYOUT_EDITS="$(edits_in "$LAYOUT")"
BEGIN="// BEGIN clavis-$GROUP"

T="$(mktemp -d /tmp/overlay-test.XXXXXX)"
trap 'rm -rf "$T"' EXIT

mkdir -p "$T/src/quickshell/clavis"
cp -r "$FIXTURE/"* "$T/src/quickshell/clavis/"
printf '// stub shell.qml\n' > "$T/src/quickshell/clavis/shell.qml"

ENVV=(env HOME="$T/home" XDG_CONFIG_HOME="$T/home/.config" XDG_STATE_HOME="$T/home/.state")
SRC="$T/src/quickshell/clavis"
OV="$T/home/.config/quickshell/clavis"
STATE_ROOT="$T/home/.state/clavis-overlay"
STATE="$STATE_ROOT/kits/$KIT_ID.json"
BIN_DIR="$T/home/.local/bin"

fail() { echo "FAIL: $*" >&2; exit 1; }
treehash() { find "$1" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1; }
assert_no_bin() {
    [ -e "$BIN_DIR" ] || return 0
    [ -z "$(ls -A "$BIN_DIR")" ] || fail "本组件没有后端脚本，~/.local/bin 不应有任何新文件"
}

echo "### 1) dry-run 不应产生任何写入"
"${ENVV[@]}" "$REPO/install.sh" --dry-run --tree "$SRC" --skip-service --no-config-migrate >"$T/dry.out" 2>&1
[ -e "$OV" ] && fail "dry-run 之后 overlay 竟存在"
[ -e "$STATE_ROOT" ] && fail "dry-run 之后 state 目录竟存在"
assert_no_bin

echo "### 2) 安装 + 断言（状态 schema v2，路径 $STATE）"
"${ENVV[@]}" "$REPO/install.sh" --tree "$SRC" --skip-service --no-config-migrate >"$T/install.out" 2>&1
grep -q "$BEGIN" "$OV/$WIDGET" || fail "补丁未打（$WIDGET）"
grep -q "$BEGIN" "$OV/$LAYOUT" || fail "补丁未打（$LAYOUT）"
[ "$(grep -c "$BEGIN" "$OV/$WIDGET")" -eq "$WIDGET_EDITS" ] || fail "$WIDGET 的标记数应等于 edit 数"
[ "$(grep -c "$BEGIN" "$OV/$LAYOUT")" -eq "$LAYOUT_EDITS" ] || fail "$LAYOUT 的标记数应等于 edit 数"
while read -r dest; do
    [ -f "$OV/$dest" ] || fail "payload 缺失：$dest"
done < <(payload_dests)
# 本组件纯 QML：一个后端脚本都不该出现
grep -q "本组件无后端脚本" "$T/install.out" || fail "安装日志应说明「无后端脚本」"
assert_no_bin
# 载荷进 overlay 的必须是仓库里的那一份字节（数据层 + 它的 MIT 许可证原文）
cmp "$OV/Common/functions/ChineseCalendar.js" "$REPO/qml/ChineseCalendar.js" \
    || fail "overlay 里的 ChineseCalendar.js 与仓库 qml/ChineseCalendar.js 不一致"
cmp "$OV/licenses/lunar-javascript-MIT.txt" "$REPO/qml/lunar-javascript-MIT.txt" \
    || fail "overlay 里的许可证原文与仓库 qml/lunar-javascript-MIT.txt 不一致"
[ -f "$STATE" ] || fail "kit 状态缺失：$STATE"
python3 - "$STATE" "$MANIFEST" "$KIT_ID" "$STATE_ROOT/registry.json" "$SRC" "$OV" <<'PY' || fail "kit 状态 / registry / 载荷内容不正确"
import json, pathlib, sys
state_path, manifest_path, kit_id, registry_path, source, overlay = sys.argv[1:7]
st = json.load(open(state_path))
mf = json.load(open(manifest_path))
assert st["schema"] == 2, st["schema"]
assert st["kit"] == kit_id, st["kit"]
assert st["order"] == mf["kit"]["order"] == 20, st["order"]
assert st["groups"] == sorted({e["group"] for e in mf["files"]}) == ["lunar-calendar"], st["groups"]
assert len(st["digests"]["patched"]) == len(mf["files"]) == 2, st["digests"]["patched"]
assert len(st["digests"]["payload"]) == len(mf["payload"]) == 2, st["digests"]["payload"]
assert st["bin_name"] is None and st["bin"] is None, (st["bin_name"], st["bin"])
assert st["migrated_from"] is None, st["migrated_from"]
assert st["repo_root"] and pathlib.Path(st["repo_root"]).is_dir(), st["repo_root"]
assert st["source_tree"] == source, (st["source_tree"], source)
assert st["overlay_dir"] == overlay, (st["overlay_dir"], overlay)
missing = [p["dest"] for p in mf["payload"] if not (pathlib.Path(overlay) / p["dest"]).is_file()]
assert not missing, missing
reg = json.load(open(registry_path))
assert reg["schema"] == 2 and reg["engine_revision"], reg
assert reg["kits"] == [kit_id], reg["kits"]
assert reg["last_sync_ok"] is True, reg
PY
H0="$(treehash "$OV")"

echo "### 3) verify 通过"
"${ENVV[@]}" "$REPO/install.sh" --verify --skip-service >"$T/verify.out" 2>&1 || {
    cat "$T/verify.out" >&2; fail "verify 非零退出"; }
grep -q "verify ok" "$T/verify.out" || fail "verify 未报告成功"
grep -q "无孤儿标记" "$T/verify.out" || fail "verify 应报告无孤儿标记"
grep -q "$KIT_ID" "$T/verify.out" || fail "verify 应点名本组件"

echo "### 4) sync 幂等重放"
"${ENVV[@]}" "$REPO/install.sh" --sync --tree "$SRC" --skip-service >/dev/null || fail "sync 失败"
[ "$(grep -c "$BEGIN" "$OV/$WIDGET")" -eq "$WIDGET_EDITS" ] || fail "sync 后标记数变化"
[ "$(grep -c "$BEGIN" "$OV/$LAYOUT")" -eq "$LAYOUT_EDITS" ] || fail "sync 后标记数变化"
H4="$(treehash "$OV")"
[ "$H4" = "$H0" ] || fail "sync 后 overlay 应与安装后逐字节一致（幂等）"

echo "### 5) sync 在 block 漂移（花括号陷阱）时干净失败且不动 overlay"
cp "$SRC/$WIDGET" "$T/widget.keep"
python3 - "$SRC/$WIDGET" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
text = p.read_text(encoding="utf-8")
old = '        property string day: ""\n'
assert text.count(old) == 1, text.count(old)
p.write_text(text.replace(old, old + '        property string trap: "{"\n'), encoding="utf-8")
PY
if "${ENVV[@]}" "$REPO/install.sh" --sync --tree "$SRC" --skip-service >"$T/drift.out" 2>&1; then
    fail "块内出现花括号陷阱时 sync 不应成功"
fi
grep -q "花括号" "$T/drift.out" || fail "失败信息应指出花括号错配（而不是写坏文件）"
grep -q "恢复到同步前状态" "$T/drift.out" || fail "失败时应提示已恢复到同步前状态"
[ "$(treehash "$OV")" = "$H4" ] || fail "sync 失败后 overlay 被改动（应保持原样）"
cp "$T/widget.keep" "$SRC/$WIDGET"
"${ENVV[@]}" "$REPO/install.sh" --verify --skip-service >/dev/null 2>&1 \
    || fail "恢复源树后 verify 应仍通过（失败未污染 overlay）"

echo "### 6) uninstall 清理（kit 状态移除、状态目录保留、无后端脚本残留）"
"${ENVV[@]}" "$REPO/install.sh" --uninstall --skip-service >/dev/null
[ -e "$OV" ] && fail "overlay 未移除"
[ -e "$STATE" ] && fail "kit 状态文件应被移除：$STATE"
[ -e "$STATE_ROOT/kits/$KIT_ID.json" ] && fail "kit 状态文件应被移除"
[ -d "$STATE_ROOT" ] || fail "clavis-overlay 状态目录应保留"
[ -d "$STATE_ROOT/backups" ] || fail "状态目录里的 backups 应保留"
find "$STATE_ROOT/backups" -name '*.tar.gz' -print -quit | grep -q . || fail "backups 里应有备份"
assert_no_bin

echo "### 7) 源树里已烘进旧补丁 → 安装后仍是「规范化后的单一版本」"
"${ENVV[@]}" python3 "$REPO/scripts/patch_clavis_kit.py" --manifest "$MANIFEST" \
    --payload "$REPO/qml" --target "$SRC" >/dev/null || fail "无法把旧补丁烘进源树"
grep -q "$BEGIN" "$SRC/$WIDGET" || fail "源树里应有已烘入的旧补丁"
"${ENVV[@]}" "$REPO/install.sh" --tree "$SRC" --skip-service --no-config-migrate >/dev/null \
    || fail "对已含旧补丁的源树安装失败"
python3 - "$MANIFEST" "$OV" <<'PY' || fail "overlay 里的补丁不是规范化后的单一版本（标记数翻倍？）"
import json, pathlib, sys
mf = json.load(open(sys.argv[1])); ov = pathlib.Path(sys.argv[2])
for entry in mf["files"]:
    begin = mf["marker"]["begin"].replace("{group}", entry["group"])
    end = mf["marker"]["end"].replace("{group}", entry["group"])
    text = (ov / entry["path"]).read_text(encoding="utf-8")
    assert text.count(begin) == len(entry["edits"]), (entry["path"], "BEGIN", text.count(begin))
    assert text.count(end) == len(entry["edits"]), (entry["path"], "END", text.count(end))
PY
[ "$(grep -c "$BEGIN" "$OV/$WIDGET")" -eq "$WIDGET_EDITS" ] || fail "锚点文件里标记数翻倍"
[ "$(treehash "$OV")" = "$H4" ] || fail "烘焙旧补丁后安装的 overlay 应与干净安装逐字节一致"

echo "全部通过 ✓"
