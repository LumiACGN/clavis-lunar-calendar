#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 LumiACGN
# SPDX-License-Identifier: GPL-3.0-or-later
"""Clavis overlay 安装引擎 v2（多 kit 感知；kit 无关，各组件仓库自带同一份）。

原理：Clavis（Quickshell 外壳）没有插件系统，唯一的官方用户扩展机制是
Quickshell 的 XDG 覆盖——`~/.config/quickshell/clavis/` 存在时整体优先于
`/etc/xdg/quickshell/clavis/`。**overlay 同时只能有一份镜像**，所以多个 clavis
组件必须共享它：本引擎用「共享注册表 + 重放」解决——

  * 每个组件（kit）在 `$XDG_STATE_HOME/clavis-overlay/kits/<kit-id>.json` 记一份
    自己的状态（repo 位置、order、补丁组、字节摘要），互不 read-modify-write；
  * sync 是**唯一写路径**：全量镜像源树 → 按 order 依次重放所有已注册 kit
    （每个 kit 先回滚自己烘进源树的旧补丁，再重新打）→ 并集语法门禁；
  * 任一步失败 → **整体恢复** sync 前的 overlay，且一个 kit 状态文件都不写（原子）。

子命令：
  install   注册本组件并重新对齐（overlay 已存在且不属于本工具时需 --adopt）
  sync      Clavis 升级/组件变更后重新对齐（失败时恢复旧 overlay——绝不炸桌面）
  verify    逐组件检查补丁/载荷/后端脚本 + 孤儿标记报告
  uninstall 默认只移除本组件（kit 级回滚，其余组件完好）；--all 整体拆除
  list      列出已注册组件
  forget    从注册表摘除某组件（不碰 overlay；repo 被删/搬走时的逃生舱）
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVICE = "clavis-shell.service"
ENGINE_REVISION = "2.0.0"
MANIFEST_SCHEMA_MAX = 1
KEEP_OVERLAY_BACKUPS = 3
PROG = Path(sys.argv[0]).name or "clavis_overlay.py"

# 组件仓库的标准布局（两仓逐字节相同）
KIT_LAYOUT = {
    "patcher_rel": "scripts/patch_clavis_kit.py",
    "manifest_rel": "patches/manifest.json",
    "payload_rel": "qml",
}
TOOL_CANDIDATES = {
    "qmlformat": ("qmlformat", "/usr/lib/qt6/bin/qmlformat"),
    "qmllint": ("qmllint", "/usr/lib/qt6/bin/qmllint"),
    "qml": ("qml", "/usr/lib/qt6/bin/qml"),
    "node": ("node",),
}
NODE_JS_SNIPPET = (
    'const fs=require("fs"); const src=fs.readFileSync(process.argv[1],"utf8")'
    '.replace(/^\\.pragma library\\s*$/m,"").replace(/^Qt\\.include\\(.*\\);?\\s*$/gm,"");'
    "new Function(src);"
)
MARKER_RE = re.compile(r"^\s*(?://|#|/\*)\s*BEGIN\s+clavis-([A-Za-z0-9._-]+)", re.M)

_SERVICE_ALLOWED = True


class PipelineError(Exception):
    """重放流水线失败（触发整体恢复）。"""


def log(msg):
    print(f"== {msg}")


def warn(msg):
    print(f"WARN: {msg}", file=sys.stderr)


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# manifest / 路径 / 状态
# ---------------------------------------------------------------------------

def load_manifest(repo: Path) -> dict:
    path = repo / KIT_LAYOUT["manifest_rel"]
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        die(f"无法读取 manifest {path}: {exc}")
    if not (manifest.get("kit") or {}).get("id"):
        die(f"manifest {path} 缺少 kit.id —— 本引擎要求组件声明 kit 段（见 §manifest schema）")
    if manifest.get("schema", 1) > MANIFEST_SCHEMA_MAX:
        die(f"manifest {path} 的 schema={manifest['schema']} 比本引擎支持的 "
            f"{MANIFEST_SCHEMA_MAX} 新——请先更新组件仓库（git pull / pacman -Syu）")
    return manifest


def kit_id_of(manifest: dict) -> str:
    return manifest["kit"]["id"]


def xdg_paths() -> dict:
    home = Path(os.environ.get("HOME") or Path.home())
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    state_home = Path(os.environ.get("XDG_STATE_HOME") or home / ".local" / "state")
    cache_home = Path(os.environ.get("XDG_CACHE_HOME") or home / ".cache")
    state_root = state_home / "clavis-overlay"
    return {
        "home": home,
        "config_home": config_home,
        "cache_home": cache_home,
        "overlay_default": config_home / "quickshell" / "clavis",
        "state_home": state_home,
        "state_root": state_root,
        "kits_dir": state_root / "kits",
        "backups_dir": state_root / "backups",
        "bin_dir": home / ".local" / "bin",
    }


def kit_state_path(paths: dict, kit_id: str) -> Path:
    return paths["kits_dir"] / f"{kit_id}.json"


def normalize_v1(paths: dict, kit_id: str, manifest: dict | None) -> dict | None:
    """v0.1.0 的 `$XDG_STATE_HOME/<kit>/state.json` → schema v2（旧文件原样保留不删）。"""
    legacy = paths["state_home"] / kit_id / "state.json"
    if not legacy.is_file():
        return None
    try:
        data = json.loads(legacy.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        die(f"旧 state 损坏（{legacy}）：{exc}")
    if data.get("schema") != 1 or data.get("mode") != "overlay":
        return None
    kitmeta = (manifest or {}).get("kit") or {}
    log(f"检测到 v0.1.0 状态：{legacy} → 迁移为 schema v2（旧文件保留不删）")
    return {
        "schema": 2,
        "kit": kit_id,
        "kit_version": data.get("tool_version"),
        "engine_revision": None,
        "manifest_schema": MANIFEST_SCHEMA_MAX,
        "installed_at": data.get("installed_at"),
        "updated_at": None,
        "migrated_from": str(legacy),
        "repo_root": str(REPO_ROOT),
        "patcher_rel": KIT_LAYOUT["patcher_rel"],
        "manifest_rel": KIT_LAYOUT["manifest_rel"],
        "payload_rel": KIT_LAYOUT["payload_rel"],
        "order": kitmeta.get("order", 10),
        "groups": sorted({e["group"] for e in (manifest or {}).get("files", [])}) or ["ai-view"],
        "overlay_dir": data.get("overlay_dir"),
        "source_tree": data.get("source_tree"),
        "upstream_version": data.get("upstream_version"),
        "upstream_version_source": data.get("upstream_version_source"),
        "bin_name": kitmeta.get("bin_name"),
        "config_dir": kitmeta.get("config_dir"),
        "cache_dir": kitmeta.get("cache_dir"),
        "legacy_config": kitmeta.get("legacy_config"),
        "etc_backup": data.get("etc_backup"),
        "bin": {"digest": data["binary_digest"]} if data.get("binary_digest") else None,
        "digests": {
            "patched": data.get("patched_digest") or {},
            "payload": data.get("payload_digest") or {},
        },
    }


def load_kit(paths: dict, kit_id: str, manifest: dict | None = None) -> dict | None:
    path = kit_state_path(paths, kit_id)
    if path.is_file():
        try:
            kit = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            die(f"组件状态损坏（{path}）：{exc}")
        if kit.get("schema", 2) > 2:
            die(f"组件状态 {path} 的 schema={kit['schema']} 比本引擎支持的 2 新——请更新引擎")
        kit.setdefault("kit", kit_id)
        return kit
    return normalize_v1(paths, kit_id, manifest)


def save_kit(paths: dict, kit: dict, dry_run: bool):
    path = kit_state_path(paths, kit["kit"])
    if dry_run:
        print(f"[dry-run] 将写入 {path}")
        return
    paths["kits_dir"].mkdir(parents=True, exist_ok=True)
    payload = {k: v for k, v in kit.items() if not k.startswith("_")}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def iter_kits(paths: dict) -> list:
    """全部已注册组件，按 (order, kit_id) 排序。"""
    if not paths["kits_dir"].is_dir():
        return []
    kits = []
    for path in sorted(paths["kits_dir"].glob("*.json")):
        try:
            kit = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            die(f"组件状态损坏（{path}）：{exc}")
        kit.setdefault("kit", path.stem)
        kits.append(kit)
    kits.sort(key=lambda k: (k.get("order", 50), k.get("kit", "")))
    return kits


def load_kit_manifest(kit: dict) -> dict | None:
    """读组件仓库里的 manifest（读不到返回 None；schema 过新直接响亮失败）。"""
    path = Path(kit.get("repo_root", ".")) / kit.get("manifest_rel", KIT_LAYOUT["manifest_rel"])
    if not path.is_file():
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if manifest.get("schema", 1) > MANIFEST_SCHEMA_MAX:
        die(f"组件 {kit.get('kit')} 的 manifest schema={manifest['schema']} 比本引擎支持的 "
            f"{MANIFEST_SCHEMA_MAX} 新——请先更新 `{kit.get('repo_root')}` 那一边")
    return manifest


def kit_repo_problem(kit: dict) -> str | None:
    repo = Path(kit.get("repo_root") or "")
    if not str(repo) or not repo.is_dir():
        return f"仓库目录不存在：{repo}"
    for key, attr in (("patcher_rel", "补丁器"), ("manifest_rel", "manifest")):
        rel = kit.get(key) or KIT_LAYOUT[key]
        if not (repo / rel).is_file():
            return f"{attr}缺失：{repo / rel}"
    payload_rel = kit.get("payload_rel") or KIT_LAYOUT["payload_rel"]
    if not (repo / payload_rel).is_dir():
        return f"payload 目录缺失：{repo / payload_rel}"
    return None


def load_registry(paths: dict) -> dict:
    path = paths["state_root"] / "registry.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_registry(paths: dict, overlay: Path, source: Path, kits: list, dry_run: bool):
    data = {
        "schema": 2,
        "engine_revision": ENGINE_REVISION,
        "overlay_dir": str(overlay),
        "source_tree": str(source),
        "last_sync_at": now_iso(),
        "last_sync_ok": True,
        "kits": [k["kit"] for k in kits],
    }
    path = paths["state_root"] / "registry.json"
    if dry_run:
        print(f"[dry-run] 将写入 {path}")
        return
    paths["state_root"].mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def update_registry_kits(paths: dict, kits: list, dry_run: bool):
    """把 registry 的 kits 列表对齐为当前存活集合（registry 不存在则不动；空则删除）。"""
    path = paths["state_root"] / "registry.json"
    if not path.is_file():
        return
    if not kits:
        if dry_run:
            print(f"[dry-run] 将移除 {path}（已无组件）")
            return
        path.unlink(missing_ok=True)
        return
    registry = load_registry(paths)
    registry.update({"schema": 2, "engine_revision": ENGINE_REVISION,
                     "kits": [k["kit"] for k in kits]})
    if dry_run:
        print(f"[dry-run] 将更新 {path}")
        return
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@contextlib.contextmanager
def state_lock(paths: dict, enabled: bool):
    if not enabled:
        yield
        return
    paths["state_root"].mkdir(parents=True, exist_ok=True)
    with open(paths["state_root"] / ".lock", "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# O0：外来标记检测（补丁归因）
# ---------------------------------------------------------------------------

def scan_markers(root: Path, rels=None) -> dict:
    """扫描 overlay 中的 BEGIN 标记：{group: [相对路径, …]}（group 不含 clavis- 前缀）。"""
    found: dict = {}
    if not root.is_dir():
        return found
    if rels is None:
        rels = [p.relative_to(root).as_posix() for p in root.rglob("*")
                if p.is_file() and p.suffix in (".qml", ".js")]
    for rel in rels:
        path = root / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in MARKER_RE.finditer(text):
            found.setdefault(match.group(1), []).append(rel)
    return found


def attribute_markers(found: dict, kits: list) -> dict:
    """group → 归属 kit_id（未注册/无法归属 → None）。"""
    owner = {}
    for kit in kits:
        manifest = kit.get("_manifest") or load_kit_manifest(kit)
        if not manifest:
            continue
        for entry in manifest["files"]:
            owner.setdefault(entry["group"], kit["kit"])
    return {group: owner.get(group) for group in found}


# ---------------------------------------------------------------------------
# 源码树 / 服务 / 文件操作
# ---------------------------------------------------------------------------

def detect_source_tree(explicit: str | None, manifest: dict) -> Path:
    candidate = Path(explicit or manifest["upstream"]["tree_hint"])
    if not (candidate / "shell.qml").is_file():
        die(f"Clavis 源码树不存在或缺少 shell.qml: {candidate}（用 --tree 指定）")
    return candidate


def detect_version(source_tree: Path):
    try:
        out = subprocess.run(["pacman", "-Q", "clavis-shell"], capture_output=True,
                             text=True, timeout=10, check=False)
        if out.returncode == 0 and out.stdout.strip():
            ver = out.stdout.split()[1]
            return ver.rsplit("-", 1)[0], "pacman"
    except (OSError, subprocess.TimeoutExpired, IndexError):
        pass
    release = source_tree / "RELEASE.json"
    if release.is_file():
        try:
            data = json.loads(release.read_text(encoding="utf-8"))
            ver = data.get("version") or data.get("release")
            if ver:
                return str(ver), "RELEASE.json"
        except json.JSONDecodeError:
            pass
    return "unknown", "unknown"


def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def set_service_guard(args):
    """假 HOME（彩排/测试）时自动跳过服务控制——忘写 --skip-service 也不会动真桌面。"""
    global _SERVICE_ALLOWED
    if getattr(args, "allow_service", False):
        _SERVICE_ALLOWED = True
        return
    try:
        real_home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    except (KeyError, OSError):
        _SERVICE_ALLOWED = True
        return
    home = Path(os.environ.get("HOME") or Path.home()).resolve()
    _SERVICE_ALLOWED = home == real_home
    if not _SERVICE_ALLOWED:
        log(f"检测到非真实 HOME（{home}）：自动跳过服务控制（彩排安全；确需控制加 --allow-service）")


def service_active() -> bool:
    if not have("systemctl"):
        return False
    out = subprocess.run(["systemctl", "--user", "is-active", SERVICE],
                         capture_output=True, text=True, check=False)
    return out.stdout.strip() == "active"


def stop_service(dry_run: bool, skip: bool) -> bool:
    """返回 was_active（供恢复用）。"""
    if skip:
        log("--skip-service：跳过停服务")
        return False
    if not _SERVICE_ALLOWED:
        return False
    if not have("systemctl"):
        warn("未找到 systemctl，跳过服务控制（请自行重启 clavis-shell）")
        return False
    was = service_active()
    if not was:
        log("clavis-shell 当前未运行，无需停")
        return False
    if dry_run:
        print(f"[dry-run] 将停止 {SERVICE}")
        return True
    log(f"停止 {SERVICE}（避免热重载读到半成品树）")
    subprocess.run(["systemctl", "--user", "stop", SERVICE], check=False)
    return True


def start_service(was_active: bool, dry_run: bool, skip: bool):
    if skip or not _SERVICE_ALLOWED or not have("systemctl"):
        return
    if dry_run:
        print(f"[dry-run] 将启动 {SERVICE}")
        return
    if not was_active and service_active():
        return
    log(f"启动 {SERVICE}")
    subprocess.run(["systemctl", "--user", "reset-failed", SERVICE], check=False)
    subprocess.run(["systemctl", "--user", "start", SERVICE], check=False)
    time.sleep(2)
    if service_active():
        log("clavis-shell 已运行")
    else:
        warn(f"{SERVICE} 未能进入 active 状态——请查看 journalctl --user -u {SERVICE}")


def mirror_tree(src: Path, dst: Path, dry_run: bool):
    excludes = ("__pycache__", ".git", "*.tmp")
    if have("rsync"):
        cmd = ["rsync", "-a", "--delete"]
        for e in excludes:
            cmd += ["--exclude", e]
        if dry_run:
            cmd.append("-n")
        cmd += [str(src) + "/", str(dst) + "/"]
        log(f"镜像 Clavis 树：{src} → {dst}" + ("（dry-run）" if dry_run else ""))
        if not dry_run:
            dst.mkdir(parents=True, exist_ok=True)
        out = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if out.returncode != 0:
            raise PipelineError(f"rsync 失败：{out.stderr.strip()}")
        if dry_run:
            changed = [l for l in out.stdout.splitlines() if l and not l.startswith(("sending", "sent"))]
            print(f"[dry-run] rsync 将同步 {len(changed)} 项")
    else:
        warn("未安装 rsync，改用整体复制（较慢）")
        if dry_run:
            print(f"[dry-run] 将重建 {dst}")
            return
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, symlinks=True,
                        ignore=shutil.ignore_patterns(*excludes))


def tarball(src: Path, out: Path, arcname: str, dry_run: bool) -> Path | None:
    if dry_run:
        print(f"[dry-run] 将备份 {src} → {out}")
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz") as tar:
        tar.add(src, arcname=arcname)
    return out


def backup_etc_anchors(kit: dict, source_tree: Path, paths: dict, dry_run: bool) -> str | None:
    manifest = kit.get("_manifest") or load_kit_manifest(kit)
    if not manifest:
        return None
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = paths["backups_dir"] / kit["kit"] / f"etc-anchors-{ts}.tar.gz"
    if dry_run:
        print(f"[dry-run] 将备份源码树的 {len(manifest['files'])} 个锚点文件 → {out}")
        return str(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz") as tar:
        for entry in manifest["files"]:
            path = source_tree / entry["path"]
            if path.is_file():
                tar.add(path, arcname=f"{source_tree.name}/{entry['path']}")
    backups = sorted(out.parent.glob("etc-anchors-*.tar.gz"))
    for old in backups[:-KEEP_OVERLAY_BACKUPS]:
        old.unlink(missing_ok=True)
    return str(out)


def backup_overlay(paths: dict, tag: str, dry_run: bool) -> Path | None:
    overlay = Path(paths["_overlay"]) if paths.get("_overlay") else None
    if overlay is None or not overlay.exists():
        return None
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = paths["backups_dir"] / f"overlay-{tag}-{ts}.tar.gz"
    result = tarball(overlay, out, overlay.name, dry_run)
    if not dry_run:
        backups = sorted(paths["backups_dir"].glob("overlay-*.tar.gz"))
        for old in backups[:-KEEP_OVERLAY_BACKUPS]:
            old.unlink(missing_ok=True)
    return result


def restore_overlay(backup: Path, overlay: Path, dry_run: bool):
    log(f"恢复旧 overlay：{backup} → {overlay}")
    if dry_run:
        return
    if overlay.exists():
        shutil.rmtree(overlay)
    overlay.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(backup, "r:gz") as tar:
        tar.extractall(path=overlay.parent, filter="data")


# ---------------------------------------------------------------------------
# 补丁器 / 门禁 / 后端脚本 / 配置
# ---------------------------------------------------------------------------

def run_patcher(kit: dict, target: Path, extra=(), dry_run=False, stream=True) -> int:
    repo = Path(kit["repo_root"])
    cmd = [sys.executable, str(repo / (kit.get("patcher_rel") or KIT_LAYOUT["patcher_rel"])),
           "--manifest", str(repo / (kit.get("manifest_rel") or KIT_LAYOUT["manifest_rel"])),
           "--payload", str(repo / (kit.get("payload_rel") or KIT_LAYOUT["payload_rel"])),
           "--target", str(target), *extra]
    if dry_run:
        cmd.append("--dry-run")
    out = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if stream or out.returncode != 0:
        sys.stdout.write(out.stdout)
        sys.stderr.write(out.stderr)
    return out.returncode


def _resolve_tool(tool: str) -> str | None:
    for candidate in TOOL_CANDIDATES.get(tool, ()):
        if "/" in candidate:
            if Path(candidate).is_file():
                return candidate
        elif have(candidate):
            return candidate
    return None


def run_gate(overlay: Path, kit: dict, gate_cache: set) -> bool:
    """语法门禁：manifest files + payload（含 .js）全体，按扩展名或 gates.overrides 选工具。

    gate_cache 保证同一路径一次 sync 内只检查一次（多 kit 场景即「并集门禁」）。
    """
    manifest = kit["_manifest"]
    overrides = (manifest.get("gates") or {}).get("overrides") or {}
    rels = [e["path"] for e in manifest["files"]] + [p["dest"] for p in manifest["payload"]]
    ok = True
    for rel in rels:
        if rel in gate_cache:
            continue
        gate_cache.add(rel)
        if rel in overrides:
            tool = overrides[rel]
        else:
            tool = {"qml": "qmlformat", "js": "node"}.get(Path(rel).suffix.lstrip("."))
        if not tool or tool == "skip":
            continue
        binary = _resolve_tool(tool)
        if binary is None:
            warn(f"未找到 {tool}，跳过门禁（{rel}）")
            continue
        path = overlay / rel
        if not path.is_file():
            print(f"  门禁 FAIL ({tool}) {rel}：文件不存在", file=sys.stderr)
            ok = False
            continue
        if tool == "node":
            cmd = ["node", "-e", NODE_JS_SNIPPET, str(path)]
        else:
            cmd = [binary, str(path)]
        out = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if out.returncode != 0:
            print(f"  门禁 FAIL ({tool}) {rel}\n{out.stderr.strip()}", file=sys.stderr)
            ok = False
    return ok


def run_smoke(overlay: Path, kit: dict, gate_cache: set) -> bool:
    """可选 smoke 门禁：把 overlay 里**已安装的那份字节** stage 到临时树跑 qml（offscreen）。"""
    smoke = (kit["_manifest"].get("gates") or {}).get("smoke")
    if not smoke:
        return True
    key = f"smoke:{kit['kit']}"
    if key in gate_cache:
        return True
    gate_cache.add(key)
    binary = _resolve_tool("qml")
    if binary is None:
        warn("未找到 qml，跳过 smoke 门禁（本地跑是必须项，可装 qt6-declarative）")
        return True
    repo = Path(kit["repo_root"])
    smoke_file = repo / smoke["file"]
    if not smoke_file.is_file():
        # 打包布局（AUR）下 tests/ 可能不随包提供；这是打包问题而非补丁问题，
        # qmllint/qmlformat 门禁仍在把关 → 跳过而不阻断安装。
        warn(f"{smoke_file} 不存在——跳过 smoke 门禁"
             "（git 克隆安装应带上 tests/；AUR 包也应随包提供该文件）")
        return True
    tmp = Path(tempfile.mkdtemp(prefix="clavis-smoke-"))
    try:
        staged = tmp / smoke["file"]
        staged.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(smoke_file, staged)
        for tmp_rel, overlay_rel in (smoke.get("stage") or {}).items():
            src = overlay / overlay_rel
            if not src.is_file():
                print(f"  门禁 FAIL (smoke)：待 stage 的文件不存在：{src}", file=sys.stderr)
                return False
            dst = tmp / tmp_rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1")
        out = subprocess.run([binary, str(staged)], capture_output=True, text=True,
                             timeout=smoke.get("timeout_s", 60), env=env, check=False)
        if out.returncode != 0:
            print(f"  门禁 FAIL (smoke) rc={out.returncode}\n{out.stdout}\n{out.stderr}",
                  file=sys.stderr)
            return False
        log(f"smoke 门禁通过（{kit['kit']}）")
        return True
    except subprocess.TimeoutExpired:
        print("  门禁 FAIL (smoke)：超时", file=sys.stderr)
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def install_bin(kit: dict, paths: dict, dry_run: bool, skip: bool) -> tuple[bool, str | None]:
    bin_name = kit.get("bin_name")
    if not bin_name:
        log("本组件无后端脚本，跳过")
        return False, None
    if skip:
        log("--skip-bin：跳过安装后端脚本")
        return False, None
    src = Path(kit["repo_root"]) / "bin" / bin_name
    if not src.is_file():
        raise PipelineError(f"后端脚本缺失：{src}")
    dest = paths["bin_dir"] / bin_name
    content = src.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if dest.is_file() and dest.read_bytes() == content:
        log(f"后端脚本已是最新：{dest}")
        return True, digest
    if dest.is_file() and dry_run:
        print(f"[dry-run] 将更新后端脚本 {dest}")
        return True, digest
    if dest.is_file():
        bak = dest.with_name(dest.name + f".bak-{time.strftime('%Y%m%d-%H%M%S')}")
        log(f"已存在的 {dest} 与仓库版本不同，先备份到 {bak.name}")
        shutil.copy2(dest, bak)
    if not dry_run:
        paths["bin_dir"].mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        dest.chmod(0o755)
    else:
        print(f"[dry-run] 将安装 {dest}")
    log(f"后端脚本：{dest}")
    return True, digest


def migrate_config(kit: dict, paths: dict, dry_run: bool, skip: bool):
    if skip:
        return
    config_dir = kit.get("config_dir")
    legacy_rel = kit.get("legacy_config")
    if not config_dir or not legacy_rel:
        return
    old = paths["config_home"] / legacy_rel
    new = paths["config_home"] / config_dir / "config.json"
    if new.is_file():
        log(f"配置文件已存在：{new}")
        return
    if old.is_file():
        if dry_run:
            print(f"[dry-run] 将迁移 {old} → {new}（保留旧文件）")
            return
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old, new)
        new.chmod(0o600)
        log(f"已迁移配置 {old} → {new}（旧文件保留；如需重新发行请检查内容）")


def collect_final_digests(overlay: Path, manifest: dict) -> tuple[dict, dict]:
    patched = {e["path"]: sha256_file(overlay / e["path"]) for e in manifest["files"]
               if (overlay / e["path"]).is_file()}
    payload = {p["dest"]: sha256_file(overlay / p["dest"]) for p in manifest["payload"]
               if (overlay / p["dest"]).is_file()}
    return patched, payload


def classify_mismatches(kits: list, overlay: Path) -> list:
    """返回**不可归因**的手改文件列表（被兄弟 kit 改写的文件归因为「预期」）。"""
    found = scan_markers(overlay)
    attribution = attribute_markers(found, kits)
    rel_owner = {rel: group for group, rels in found.items() for rel in rels}
    unattributed = []
    for kit in kits:
        digests = kit.get("digests") or {}
        rels = {**(digests.get("patched") or {}), **(digests.get("payload") or {})}
        for rel, digest in rels.items():
            path = overlay / rel
            if path.is_file() and sha256_file(path) == digest:
                continue
            group = rel_owner.get(rel)
            owner = attribution.get(group) if group else None
            if owner and owner != kit["kit"]:
                log(f"  {rel} 现由组件 {owner} 的补丁改写（预期内，跳过手改检查）")
                continue
            unattributed.append(f"{kit['kit']}: {rel}")
    return unattributed


def make_kit_state(repo: Path, manifest: dict, overlay: Path, source: Path,
                   version: str, version_src: str) -> dict:
    kitmeta = manifest["kit"]
    return {
        "schema": 2,
        "kit": kitmeta["id"],
        "kit_version": manifest.get("tool_version"),
        "engine_revision": ENGINE_REVISION,
        "manifest_schema": manifest.get("schema", 1),
        "installed_at": now_iso(),
        "updated_at": now_iso(),
        "migrated_from": None,
        "repo_root": str(repo),
        "patcher_rel": KIT_LAYOUT["patcher_rel"],
        "manifest_rel": KIT_LAYOUT["manifest_rel"],
        "payload_rel": KIT_LAYOUT["payload_rel"],
        "order": kitmeta.get("order", 50),
        "groups": sorted({e["group"] for e in manifest["files"]}),
        "overlay_dir": str(overlay),
        "source_tree": str(source),
        "upstream_version": version,
        "upstream_version_source": version_src,
        "bin_name": kitmeta.get("bin_name"),
        "config_dir": kitmeta.get("config_dir"),
        "cache_dir": kitmeta.get("cache_dir"),
        "legacy_config": kitmeta.get("legacy_config"),
        "etc_backup": None,
        "bin": None,
        "digests": {"patched": {}, "payload": {}},
    }


# ---------------------------------------------------------------------------
# 重放流水线（sync = 唯一写路径）
# ---------------------------------------------------------------------------

def run_pipeline(args, paths: dict, manifest: dict, repo: Path,
                 register: bool = False, self_kit: dict | None = None) -> int:
    self_id = kit_id_of(manifest)
    recorded_overlay = (self_kit or {}).get("overlay_dir")
    if recorded_overlay:
        overlay = Path(recorded_overlay)
        if args.overlay_dir and Path(args.overlay_dir) != overlay:
            warn(f"已注册状态记录的 overlay（{overlay}）与 --overlay-dir"
                 f"（{args.overlay_dir}）不同，以状态为准")
    else:
        overlay = Path(args.overlay_dir or paths["overlay_default"])
    source = detect_source_tree(args.tree, manifest)
    version, version_src = detect_version(source)
    dry = args.dry_run
    paths["_overlay"] = overlay

    kits = iter_kits(paths)
    if self_kit is None:
        self_kit = next((k for k in kits if k["kit"] == self_id), None)
    if self_kit is None and register:
        self_kit = make_kit_state(repo, manifest, overlay, source, version, version_src)
    if self_kit is None:
        die(f"本组件（{self_id}）尚未注册——请先运行 install（./install.sh）")
    if all(k["kit"] != self_id for k in kits):
        kits.append(self_kit)
    else:
        kits = [self_kit if k["kit"] == self_id else k for k in kits]
    kits.sort(key=lambda k: (k.get("order", 50), k.get("kit", "")))

    # 重名 group 会互相污染标记，直接响亮失败
    seen_groups = {}
    for kit in kits:
        kmanifest = load_kit_manifest(kit)
        if kmanifest:
            kit["_manifest"] = kmanifest
            for entry in kmanifest["files"]:
                other = seen_groups.get(entry["group"])
                if other and other != kit["kit"]:
                    die(f"组件 {kit['kit']} 与 {other} 使用了相同的 group "
                        f"{entry['group']!r}——补丁标记会互相污染，请改名后再试")
                seen_groups[entry["group"]] = kit["kit"]

    # 2) 每个 kit 的仓库文件必须在
    problems = [(k, kit_repo_problem(k)) for k in kits]
    problems = [(k, why) for k, why in problems if why]
    if problems:
        detail = "\n".join(f"  - {k['kit']}: {why}" for k, why in problems)
        if not args.skip_missing:
            die(f"以下已注册组件的仓库文件缺失（repo 被删/搬走？）：\n{detail}\n"
                f"  - 修复：把仓库移回原路径，或在它的新位置重跑 install.sh（自动重绑）\n"
                f"  - 或：{PROG} forget <kit-id> 从注册表摘除（不动 overlay）\n"
                "  - 或：本次加 --skip-missing 跳过（注意：overlay 里它的补丁会被清掉）")
        for kit, why in problems:
            warn(f"--skip-missing：跳过组件 {kit['kit']}（{why}）")
        skipped = {k["kit"] for k, _ in problems}
        if self_id in skipped:
            die(f"本组件（{self_id}）自身缺失，不能跳过")
        kits = [k for k in kits if k["kit"] not in skipped]

    log(f"源树：{source}（Clavis {version}，via {version_src}）")
    log(f"overlay：{overlay}")
    log("已注册组件（重放顺序）："
        + " → ".join(f"{k['kit']}(order {k.get('order', 50)})" for k in kits))

    if overlay.exists() and not dry:
        unattributed = classify_mismatches(kits, overlay)
        if unattributed and not args.force:
            die("overlay 中以下文件被改动过（不归属于任何已注册组件）：\n  - "
                + "\n  - ".join(unattributed)
                + "\n  --force 才会覆盖（覆盖前仍会整体备份）")

    for kit in kits:
        if kit.get("upstream_version") and kit["upstream_version"] != version:
            log(f"上游版本变化（{kit['kit']}）：{kit['upstream_version']} → {version}")

    if dry:
        print("[dry-run] 将：停 clavis-shell → 备份旧 overlay → 镜像源树 → "
              "按 order 重放全部已注册组件（先清掉源树里烘入的旧补丁）→ 并集门禁 → 更新状态")
        print(f"[dry-run] 补丁器对当前源树的预览（{self_id}）：")
        run_patcher(self_kit, source, dry_run=True, extra=("--expect-upstream", version))
        return 0

    was_active = stop_service(False, args.skip_service)
    backup = None
    etc_backup = None
    try:
        # 源树锚点文件快照（uninstall --restore-etc 的安全网；只读源树，不动它）
        etc_backup = backup_etc_anchors(self_kit, source, paths, False)
        if overlay.exists():
            backup = backup_overlay(paths, "pre-sync", False)
        mirror_tree(source, overlay, False)
        baked = scan_markers(overlay)
        gate_cache: set = set()
        for kit in kits:
            kmanifest = kit.get("_manifest") or load_kit_manifest(kit)
            if kmanifest is None:
                raise PipelineError(f"组件 {kit['kit']} 的 manifest 无法读取")
            kit["_manifest"] = kmanifest
            # 6a 源树里可能烘进了本组件的旧补丁（例如把已打补丁的 /etc 当 --tree）：先回滚
            for group in kit.get("groups") or sorted({e["group"] for e in kmanifest["files"]}):
                if group in baked:
                    log(f"源树已含 {kit['kit']} 的 {group} 补丁 → 先回滚再重放")
                    if run_patcher(kit, overlay, extra=("--revert", "--group", group)) != 0:
                        raise PipelineError(f"{kit['kit']} / {group} 旧补丁回滚失败")
            # 6b apply
            if run_patcher(kit, overlay, extra=("--expect-upstream", version)) != 0:
                raise PipelineError(f"{kit['kit']} 补丁失败")
            # 6c 语法门禁（逐 kit 立即跑，缓存去重 = 并集门禁）
            if not run_gate(overlay, kit, gate_cache):
                raise PipelineError(f"{kit['kit']} 语法门禁失败")
            # 6d 可选 smoke
            if not run_smoke(overlay, kit, gate_cache):
                raise PipelineError(f"{kit['kit']} smoke 门禁失败")

        # 7) 成功：为全部 kit 重采最终 digest，然后落盘
        for kit in kits:
            patched, payload = collect_final_digests(overlay, kit["_manifest"])
            kitmeta = kit["_manifest"].get("kit") or {}
            kit.update({
                "digests": {"patched": patched, "payload": payload},
                "engine_revision": ENGINE_REVISION,
                "manifest_schema": kit["_manifest"].get("schema", 1),
                "kit_version": kit["_manifest"].get("tool_version"),
                "order": kitmeta.get("order", kit.get("order", 50)),
                "groups": sorted({e["group"] for e in kit["_manifest"]["files"]}),
                "bin_name": kitmeta.get("bin_name"),
                "config_dir": kitmeta.get("config_dir"),
                "cache_dir": kitmeta.get("cache_dir"),
                "legacy_config": kitmeta.get("legacy_config"),
                "overlay_dir": str(overlay),
                "source_tree": str(source),
                "upstream_version": version,
                "upstream_version_source": version_src,
                "updated_at": now_iso(),
            })
            kit.setdefault("installed_at", now_iso())
        _, bin_digest = install_bin(self_kit, paths, False, args.skip_bin)
        if self_kit.get("bin_name"):
            self_kit["bin"] = {"name": self_kit["bin_name"], "digest": bin_digest}
        if etc_backup:
            self_kit["etc_backup"] = etc_backup
        migrate_config(self_kit, paths, False, getattr(args, "no_config_migrate", False))
        for kit in kits:
            save_kit(paths, kit, False)
        save_registry(paths, overlay, source, kits, False)
    except PipelineError as exc:
        if backup and backup.is_file():
            restore_overlay(backup, overlay, False)
            warn(f"重放失败：{exc} —— 已恢复到同步前状态（含其它已注册组件，桌面继续可用）")
        else:
            warn(f"重放失败：{exc} —— 没有旧 overlay 可恢复，overlay 保持镜像状态（补丁未启用）")
        warn("补丁失败通常意味着 Clavis 改动了上游文本——看上方错误提示，"
             "按 patches/README.md 更新锚点后再试")
        return 1
    except Exception as exc:  # 意外错误也恢复，绝不让 overlay 半死
        traceback.print_exc()
        if backup and backup.is_file():
            restore_overlay(backup, overlay, False)
            warn(f"发生意外错误（{exc}）——已恢复到同步前状态")
        return 1
    finally:
        start_service(was_active, False, args.skip_service)

    log("重放完成 ✅")
    for kit in kits:
        if kit["kit"] == self_id:
            for note in (kit["_manifest"].get("kit") or {}).get("post_install_notes") or []:
                print(f"  · {note}")
            kit.pop("_manifest", None)
    return 0


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------

def cmd_install(args, paths, manifest, repo):
    self_id = kit_id_of(manifest)
    overlay = Path(args.overlay_dir or paths["overlay_default"])
    existing = load_kit(paths, self_id, manifest)
    if existing:
        log("检测到本组件已注册 → 转为 sync 语义重新对齐")
        if str(repo) != existing.get("repo_root"):
            log(f"更新 repo_root：{existing.get('repo_root')} → {repo}（仓库搬家/换安装源）")
            existing["repo_root"] = str(repo)
            existing["patcher_rel"] = KIT_LAYOUT["patcher_rel"]
            existing["manifest_rel"] = KIT_LAYOUT["manifest_rel"]
            existing["payload_rel"] = KIT_LAYOUT["payload_rel"]
        return run_pipeline(args, paths, manifest, repo, register=False, self_kit=existing)

    others = iter_kits(paths)
    if overlay.exists() and not others:
        found = scan_markers(overlay)
        lines = [f"overlay 目录已存在，且不属于本工具（无任何已注册组件）：{overlay}"]
        if found:
            lines.append("其中检测到以下补丁标记——接管并重新镜像后会被先清掉：")
            for group, rels in sorted(found.items()):
                lines.append(f"  - {group}（{len(rels)} 个文件，如 {rels[0]}）")
            lines.append("    （若该补丁属于本工具家族的其它组件，请先安装那个组件，"
                         "它注册后会在重放中被保留）")
        lines.append("  - 若那是你自己的配置/开发目录：请先移走，或使用 --overlay-dir <其它路径>")
        lines.append("  - 若确认可以被本工具接管（会先整体备份）：加 --adopt 重试")
        if not args.adopt:
            die("\n".join(lines))
        for line in lines:
            log(line)
        paths["_overlay"] = overlay
        backup_overlay(paths, "adopted", args.dry_run)

    return run_pipeline(args, paths, manifest, repo, register=True)


def cmd_sync(args, paths, manifest, repo):
    self_id = kit_id_of(manifest)
    self_kit = load_kit(paths, self_id, manifest)
    if not self_kit:
        die(f"本组件（{self_id}）尚未注册（无状态文件）——请先运行 install")
    return run_pipeline(args, paths, manifest, repo, register=False, self_kit=self_kit)


def cmd_verify(args, paths, manifest):
    kits = iter_kits(paths)
    if not kits:
        print("未安装（无任何已注册组件）——运行 ./install.sh 安装")
        return 1
    problems = []
    overlay = None
    for kit in kits:
        kmanifest = load_kit_manifest(kit)
        print(f"\n=== {kit['kit']}（order {kit.get('order', 50)}，"
              f"v{kit.get('kit_version') or '?'}）===")
        ov = Path(kit.get("overlay_dir") or paths["overlay_default"])
        overlay = overlay or ov
        print(f"overlay      {ov}  {'存在' if ov.exists() else '缺失!'}")
        if not (ov / "shell.qml").is_file():
            problems.append(f"{kit['kit']}: overlay 缺少 shell.qml")
        if kmanifest is None:
            print(f"manifest     不可读（{kit.get('repo_root')}——repo 被删/搬走？）")
            problems.append(f"{kit['kit']}: manifest 不可读")
            continue
        kit["_manifest"] = kmanifest
        rc = run_patcher(kit, ov, extra=("--verify",), stream=False)
        print(f"补丁         {'在位' if rc == 0 else '缺失/异常'}")
        if rc != 0:
            problems.append(f"{kit['kit']}: 补丁不完整（运行 ./install.sh --sync 修复）")

        digests = {**(kit.get("digests") or {}).get("patched", {}),
                   **(kit.get("digests") or {}).get("payload", {})}
        modified = [rel for rel, digest in digests.items()
                    if not (ov / rel).is_file() or sha256_file(ov / rel) != digest]
        if modified:
            warn(f"{kit['kit']}: 以下文件与上次成功同步的记录不一致（手改？sync 时会要求 --force）："
                 + ", ".join(modified))

        bin_name = (kmanifest.get("kit") or {}).get("bin_name")
        if bin_name:
            dest = paths["bin_dir"] / bin_name
            if dest.is_file():
                if (kit.get("bin") or {}).get("digest") and \
                        sha256_file(dest) != kit["bin"]["digest"]:
                    warn(f"{kit['kit']}: {dest} 与安装时不一致（你或其它工具改过它？）")
                print(f"后端脚本     {dest}（在位）")
            else:
                problems.append(f"{kit['kit']}: 后端脚本缺失：{dest}")

        source = Path(kit.get("source_tree") or ".")
        try:
            version, _src = detect_version(source)
            if version != "unknown" and version != kit.get("upstream_version"):
                warn(f"{kit['kit']}: Clavis 已升级（{kit.get('upstream_version')} → {version}）："
                     "overlay 仍是旧代码，请 ./install.sh --sync 对齐")
            print(f"上游版本     {version}（安装时 {kit.get('upstream_version')}）")
        except OSError:
            pass

    if overlay and overlay.exists():
        print("\n=== 孤儿标记检查 ===")
        found = scan_markers(overlay)
        attribution = attribute_markers(found, kits)
        orphans = {g: rels for g, rels in found.items() if attribution.get(g) is None}
        if orphans:
            warn("overlay 中存在不属于任何已注册组件的补丁标记"
                 "（手工打过补丁？或某组件被 --forget 了）：")
            for group, rels in sorted(orphans.items()):
                print(f"  - {group}（{len(rels)} 个文件，如 {rels[0]}）")
            problems.append("存在孤儿标记")
        else:
            print(f"无孤儿标记（已注册组件的 group：{', '.join(sorted(attribution)) or '无'}）")

    if problems:
        print("")
        for p in problems:
            print(f"问题：{p}")
        return 1
    print("\nverify ok")
    return 0


def cmd_list(args, paths, manifest):
    kits = iter_kits(paths)
    if not kits:
        print("（无已注册组件）")
        return 0
    print(f"{'kit':<28} {'order':<6} {'version':<9} {'groups':<22} overlay")
    for kit in kits:
        ov = Path(kit.get("overlay_dir") or paths["overlay_default"])
        groups = ",".join(kit.get("groups") or []) or "-"
        print(f"{kit['kit']:<28} {str(kit.get('order', 50)):<6} "
              f"{str(kit.get('kit_version') or '?'):<9} {groups:<22} "
              f"{'ok' if ov.exists() else '缺失'}")
        print(f"{'':<28} repo: {kit.get('repo_root')}")
    return 0


def cmd_forget(args, paths, manifest):
    kit_id = args.kit_id
    path = kit_state_path(paths, kit_id)
    if not path.is_file():
        die(f"未注册：{kit_id}（已注册：{', '.join(k['kit'] for k in iter_kits(paths)) or '无'}）")
    if args.dry_run:
        print(f"[dry-run] 将摘除注册 {path}（overlay 不动）")
        return 0
    path.unlink()
    update_registry_kits(paths, iter_kits(paths), False)
    log(f"已摘除注册：{kit_id}（overlay 未改动——若它的补丁还在，verify 会报为孤儿标记）")
    return 0


def cmd_uninstall(args, paths, manifest, repo):
    self_id = kit_id_of(manifest)
    if args.all:
        return uninstall_all(args, paths)
    dry = args.dry_run
    kit = load_kit(paths, self_id, manifest)
    if not kit:
        die(f"本组件（{self_id}）未注册；若只是残留 overlay 请用 --all")
    overlay = Path(kit.get("overlay_dir") or paths["overlay_default"])
    paths["_overlay"] = overlay
    others = [k for k in iter_kits(paths) if k["kit"] != self_id]
    kmanifest = load_kit_manifest(kit)
    if kmanifest:
        kit["_manifest"] = kmanifest

    if args.restore_etc:
        backups = sorted((paths["backups_dir"] / self_id).glob("etc-anchors-*.tar.gz"))
        if not backups:
            die("没有 etc 备份可恢复")
        target_root = Path(kit.get("source_tree") or ".").parent
        log(f"从 {backups[-1].name} 恢复源码树锚点文件到 {target_root}（可能需要写权限）")
        if not dry:
            with tarfile.open(backups[-1], "r:gz") as tar:
                tar.extractall(path=target_root, filter="data")
        print(f"已恢复锚点文件{'（dry-run）' if dry else ''}")

    was_active = stop_service(dry, args.skip_service)
    try:
        if overlay.exists():
            if kmanifest:
                backup_overlay(paths, f"uninstall-{self_id}", dry)
                if run_patcher(kit, overlay, extra=("--revert", "--remove-payload")) != 0:
                    warn("回滚补丁失败——overlay 保持原样，请检查上方输出后重试")
                    return 1
            else:
                warn(f"manifest 不可读（{kit.get('repo_root')}）——跳过补丁回滚，仅移除注册")
        bin_name = kit.get("bin_name") or (kmanifest or {}).get("kit", {}).get("bin_name")
        if bin_name and not args.skip_bin:
            dest = paths["bin_dir"] / bin_name
            if dest.is_file():
                digest = (kit.get("bin") or {}).get("digest")
                if digest and sha256_file(dest) != digest:
                    warn(f"{dest} 已被修改，保留不删")
                else:
                    log(f"移除后端脚本：{dest}" + ("（dry-run）" if dry else ""))
                    if not dry:
                        dest.unlink()
        if not dry:
            kit_state_path(paths, self_id).unlink(missing_ok=True)
        log(f"已摘除注册：{self_id}" + ("（dry-run）" if dry else ""))

        if others and overlay.exists() and not dry:
            for other in others:
                omanifest = load_kit_manifest(other)
                if not omanifest:
                    continue
                patched, payload = collect_final_digests(overlay, omanifest)
                other["digests"] = {"patched": patched, "payload": payload}
                other["updated_at"] = now_iso()
                save_kit(paths, other, dry)
            log(f"已为剩余组件重采摘要：" + ", ".join(k["kit"] for k in others))
        update_registry_kits(paths, others, dry)

        if not others and overlay.exists():
            found = scan_markers(overlay)
            if found:
                warn("移除 overlay 前检测到以下补丁标记不属于任何已注册组件"
                     "（手工补丁？）：" + ", ".join(sorted(found)))
            log(f"移除 overlay：{overlay}" + ("（dry-run）" if dry else ""))
            if not dry:
                shutil.rmtree(overlay)
                parent = overlay.parent
                if parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
        elif not dry and paths["kits_dir"].is_dir():
            if not any(paths["kits_dir"].iterdir()):
                paths["kits_dir"].rmdir()

        if args.purge:
            targets = []
            config_dir = kit.get("config_dir")
            cache_dir = kit.get("cache_dir")
            if config_dir:
                targets.append(paths["config_home"] / config_dir)
            if cache_dir:
                targets.append(paths["cache_home"] / cache_dir)
            print("--purge 将删除（配置里可能有 API key，请确认）：")
            for t in targets:
                print(f"  - {t}")
            if not args.yes:
                die("如需继续请加 --yes")
            if not dry:
                for t in targets:
                    shutil.rmtree(t, ignore_errors=True)
        else:
            kept = [str(paths["config_home"] / d) for d in
                    (kit.get("config_dir"), kit.get("cache_dir")) if d]
            log("已保留：" + (" / ".join(kept) if kept else "（本组件无配置/缓存）")
                + f" / 状态与备份 {paths['state_root']}")
    finally:
        start_service(was_active, dry, args.skip_service)

    log(f"卸载完成：{self_id} 的补丁与载荷已移除，其余组件不受影响")
    return 0


def uninstall_all(args, paths):
    dry = args.dry_run
    kits = iter_kits(paths)
    registry = load_registry(paths)
    overlay = Path(registry.get("overlay_dir") or paths["overlay_default"])
    paths["_overlay"] = overlay
    was_active = stop_service(dry, args.skip_service)
    try:
        if args.restore_etc:
            for kit in kits:
                etc_backups = sorted((paths["backups_dir"] / kit["kit"]).glob("etc-anchors-*.tar.gz"))
                if not etc_backups:
                    warn(f"{kit['kit']}：没有 etc 备份可恢复")
                    continue
                target_root = Path(kit.get("source_tree") or ".").parent
                log(f"从 {etc_backups[-1].name} 恢复锚点文件到 {target_root}"
                    + ("（可能需要写权限）" if not dry else ""))
                if not dry:
                    with tarfile.open(etc_backups[-1], "r:gz") as tar:
                        tar.extractall(path=target_root, filter="data")
        if overlay.exists():
            backup_overlay(paths, "uninstall-all", dry)
            log(f"移除 overlay：{overlay}" + ("（dry-run）" if dry else ""))
            if not dry:
                shutil.rmtree(overlay)
                parent = overlay.parent
                if parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
        for kit in kits:
            kmanifest = load_kit_manifest(kit) or {}
            bin_name = (kmanifest.get("kit") or {}).get("bin_name") or kit.get("bin_name")
            if not bin_name or args.skip_bin:
                continue
            dest = paths["bin_dir"] / bin_name
            if dest.is_file():
                digest = (kit.get("bin") or {}).get("digest")
                if digest and sha256_file(dest) != digest:
                    warn(f"{dest} 已被修改，保留不删")
                else:
                    log(f"移除后端脚本：{dest}" + ("（dry-run）" if dry else ""))
                    if not dry:
                        dest.unlink()
        if args.purge:
            targets = []
            for kit in kits:
                kmanifest = load_kit_manifest(kit) or {}
                kitmeta = kmanifest.get("kit") or {}
                for key, base in (("config_dir", "config_home"), ("cache_dir", "cache_home")):
                    if kitmeta.get(key):
                        targets.append(paths[base] / kitmeta[key])
            print("--purge 将删除（配置里可能有 API key，请确认）：")
            for t in targets:
                print(f"  - {t}")
            print(f"  - {paths['state_root']}（全部组件状态与备份）")
            if not args.yes:
                die("如需继续请加 --yes")
            if not dry:
                for t in targets:
                    shutil.rmtree(t, ignore_errors=True)
                shutil.rmtree(paths["state_root"], ignore_errors=True)
        elif not dry:
            for kit in kits:
                kit_state_path(paths, kit["kit"]).unlink(missing_ok=True)
            if paths["kits_dir"].is_dir() and not any(paths["kits_dir"].iterdir()):
                paths["kits_dir"].rmdir()
            update_registry_kits(paths, [], dry)
            log(f"已保留：{paths['state_root']}（状态与备份；--purge --yes 可清除）")
    finally:
        start_service(True, dry, args.skip_service)
    log("整体卸载完成（Clavis 已回到系统原生版本）")
    return 0


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="clavis-overlay",
        description="Clavis 组件 overlay 安装引擎 v2（多 kit 感知；详见 README）")
    sub = parser.add_subparsers(dest="mode", required=True)

    def common(p):
        p.add_argument("--dry-run", action="store_true", help="只演示，不写任何文件、不停 shell")
        p.add_argument("--tree", default=None, help="Clavis 源码树（默认取 manifest 记录）")
        p.add_argument("--overlay-dir", default=None,
                       help="overlay 目录（默认 ~/.config/quickshell/clavis）")
        p.add_argument("--repo-root", default=None, help="组件仓库路径（默认：引擎所在仓库）")
        p.add_argument("--skip-service", action="store_true", help="不控制 clavis-shell 服务")
        p.add_argument("--skip-bin", action="store_true", help="不安装/不删除后端脚本")
        p.add_argument("--allow-service", action="store_true",
                       help="假 HOME 下也允许控制 clavis-shell（默认自动跳过）")

    p_install = sub.add_parser("install", help="注册本组件并重新对齐")
    common(p_install)
    p_install.add_argument("--adopt", action="store_true",
                           help="接管已存在的 overlay 目录（先整体备份）")
    p_install.add_argument("--no-config-migrate", action="store_true",
                           help="不从旧路径迁移配置")

    p_sync = sub.add_parser("sync", help="Clavis 升级/组件变更后重新对齐")
    common(p_sync)
    p_sync.add_argument("--force", action="store_true",
                        help="覆盖被手改过的 overlay（先整体备份）")
    p_sync.add_argument("--skip-missing", action="store_true",
                        help="本次跳过仓库缺失的组件（应急用）")

    p_verify = sub.add_parser("verify", help="检查安装状态")
    common(p_verify)

    p_uninstall = sub.add_parser("uninstall", help="移除本组件（--all 整体拆除）")
    common(p_uninstall)
    p_uninstall.add_argument("--all", action="store_true", help="移除全部组件与 overlay")
    p_uninstall.add_argument("--restore-etc", action="store_true",
                             help="同时从备份恢复源码树的锚点文件（若当初也改过它）")
    p_uninstall.add_argument("--purge", action="store_true",
                             help="同时删除本组件的配置/缓存（--all 时连状态与备份一起）")
    p_uninstall.add_argument("--yes", action="store_true", help="与 --purge 搭配确认")

    p_list = sub.add_parser("list", help="列出已注册组件")
    p_list.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)

    p_forget = sub.add_parser("forget", help="从注册表摘除某组件（不碰 overlay）")
    p_forget.add_argument("kit_id", help="组件 id（见 list）")
    p_forget.add_argument("--dry-run", action="store_true", help="只演示")

    args = parser.parse_args()
    set_service_guard(args)
    paths = xdg_paths()

    # list / forget 是诊断与逃生通道：不要求本仓 manifest 完好
    if args.mode == "list":
        return cmd_list(args, paths, {})
    if args.mode == "forget":
        with state_lock(paths, not args.dry_run):
            return cmd_forget(args, paths, {})

    repo = Path(args.repo_root).resolve() if getattr(args, "repo_root", None) else REPO_ROOT
    manifest = load_manifest(repo)
    if args.mode == "verify":
        return cmd_verify(args, paths, manifest)

    handlers = {"install": cmd_install, "sync": cmd_sync, "uninstall": cmd_uninstall}
    with state_lock(paths, not args.dry_run):
        return handlers[args.mode](args, paths, manifest, repo)


if __name__ == "__main__":
    sys.exit(main())
