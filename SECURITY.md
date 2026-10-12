# 安全说明

## 本项目处理什么

- **不处理任何凭据**：没有 API key、token、账号、密码，也没有配置文件——运行时零网络，
  不存在"凭据泄漏"的攻击面。
- **只写用户目录**：安装/卸载只动 `~/.config/quickshell/clavis/`（overlay）与
  `~/.local/state/clavis-overlay/`（状态与备份），**从不写 `/etc`、从不需要 sudo**。
- **运行时无副作用**：QML 侧是纯展示组件，不创建 `Process`、不执行系统命令、不访问网络
  （符合上游 Clavis `AGENTS.md` 的约束）；联网只发生在你手动执行的
  `scripts/check-lunar-data.sh` / `scripts/refresh-lunar-vendor.sh` 里（只读检查与下载 vendor）。

## 供应链

本仓库**包含第三方代码的字节**（vendored lunar-javascript，见 [NOTICE](NOTICE)），
因此供应链是这里最实际的攻击面，四道防线：

1. **下载时校验**：`scripts/refresh-lunar-vendor.sh` 从 npm CDN 取 `lunar.js` 后，
   先比对 npm registry 的 `dist.integrity`（**sha512**），再做过大小（> 400 KB）、
   UMD 头部、`HolidayUtil` 导出三道结构校验；任一不符直接失败且不落盘。
   取不到 `dist.integrity` 时只告警（此时仅剩结构校验），PR 里必须人工复核 diff。
2. **入库后校验**：`lunar/vendor/SHA256SUMS` 记录每个 vendor 文件的 sha256 与来源 URL；
   更新 vendor 会在 PR 里体现为可 review 的 diff（版本号、数据截止日、记录条数都会变）。
3. **产物自洽**：`qml/ChineseCalendar.js` 是构建产物，发版时 `release.yml` 重跑构建脚本并
   **逐字节 `cmp`**——改了源却忘了重建、或产物被手改，发版会直接失败。
4. **更新只经人工**：定时器（`clavis-lunar-calendar-refresh.timer`）**只读检查 + 发通知**，
   **绝不修改任何文件**；数据与代码的更新一律走 PR/发版（见 [CONTRIBUTING.md](CONTRIBUTING.md)）。

## 报告问题

发现安全问题时，请优先通过 GitHub 的私密渠道报告：

- 仓库 → Security → Report a vulnerability（GitHub Security Advisory），或
- 若该入口不可用，开一个不含敏感细节的 issue 说明意图，另行沟通细节。

如果问题出在 vendored 的 **lunar-javascript** 上游，也欢迎直接告知——我们会跟进上游修复并
更新 vendor（此类更新会走 [docs/DATA.md](docs/DATA.md#三年度更新步骤次年放假安排公布后) 的流程）。
