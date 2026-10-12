# 项目级说明（给自动化流程/代理）

本文件覆盖 `/home/lumi/GitHub-Projects/CLAUDE.md`（父目录规则）中与本仓库冲突的部分。
**冲突时以本文件为准。**

## 许可证是既定事实，不要"自动修复"

- 本项目**既定以 GPL-3.0-or-later 发布**：它是 Clavis Shell（GPL-3.0-or-later）的衍生作品，
  补丁内容包含其经修改的代码片段。见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。
- 父目录规则里的这几条**不适用于本仓库**，禁止执行：
  - 「默认创建 MIT LICENSE / 自动补 MIT」——`LICENSE` 就是 GPL-3.0 原文，**不要替换、不要新增 MIT LICENSE**；
  - 「依赖许可证为 GPL/AGPL 时自动移除依赖」——这里的 GPL 是**本项目的许可证**，不是可移除的依赖；
  - 「自动从项目中移除 GPL/AGPL 依赖」不得用于 `lunar/vendor/lunar-1.7.7.js`（它是 **MIT**，
    只是与本项目一起分发）或 `qml/ChineseCalendar.js`（含其字节的构建产物）。
- 新增源码文件的 SPDX 头沿用：

  ```
  SPDX-FileCopyrightText: 2026 LumiACGN
  SPDX-License-Identifier: GPL-3.0-or-later
  ```

  （**不是** MIT——父目录规则里的 MIT 模板在本仓库一律换成 GPL-3.0-or-later。）
- 删除、替换或"精简" vendored 第三方代码/许可证原文（`lunar/vendor/`、
  `qml/lunar-javascript-MIT.txt`、`NOTICE` 的条目）属于**阻断级**操作：需要人类明确指示。

## 防泄漏四层防线（与 clavis-ai-usage 同构，不要削弱任何一层）

1. `.gitignore`：用户数据、凭据类文件、临时产物一律不入库；
2. `.githooks/pre-commit`（`git config core.hooksPath .githooks`）：gitleaks 对 staged 内容扫描；
3. CI `.github/workflows/lint.yml`：gitleaks **全历史**扫描（用 CLI 直跑，不用 gitleaks-action）；
4. [SECURITY.md](SECURITY.md)：凭据/副作用承诺 + GitHub Security Advisory 报告渠道。

配套约束：提交作者邮箱用 `git config user.email` 里的 no-reply 地址；代码、注释、文档、提交信息
中不出现真实姓名/邮箱/密钥；示例一律占位符。若新增文件类型可能含敏感数据，**先加进 `.gitignore`**，
再谈提交。

## 产物与生成物

- `qml/ChineseCalendar.js`（442 KB）是**刻意入库**的构建产物（AUR 源包需要自包含，且要求离线可重建），
  不算"生成物不该入库"的违规。
- 但它是**生成物**：禁止手工编辑。改动一律落在 `lunar/adapter.js`（唯一手改源）或
  `lunar/vendor/`，然后 `python3 scripts/build_chinese_calendar.py` 重建。
  `release.yml` 会重跑构建并逐字节 `cmp`，不自洽就发版红。
- `packaging/aur/clavis-lunar-calendar/.SRCINFO` 用 `makepkg --printsrcinfo` 生成，不要手改。

## 版本号与联动

- 版本三处必须一致：`patches/manifest.json` 的 `tool_version`、`CHANGELOG.md` 的小节、
  PKGBUILD 的 `pkgver`（`release.yml` 逐个校验）。
- `scripts/clavis_overlay.py` 与 `scripts/patch_clavis_kit.py` 与姊妹仓
  [clavis-ai-usage](https://github.com/LumiACGN/clavis-ai-usage) **逐字节相同**：
  改其中一个必须同步另一个（同一引擎、两份副本）。

## 硬边界

- **不写 `/etc`、不碰包管理器领地**：安装只写 `~/.config/quickshell/clavis/` 与
  `~/.local/state/clavis-overlay/`；`/etc/xdg/quickshell/clavis` 只读。
- 调试验证用**假 HOME**（`HOME=<临时目录>`）；跑 `install.sh` 会停/起 `clavis-shell`，
  不要在用户真实桌面上试装，除非用户明确要求。
- 不要新增"运行时联网""创建 `Process`""执行系统命令"的 QML 代码——上游 Clavis `AGENTS.md`
  明确禁止展示组件这么做，本组件全部本地计算。
