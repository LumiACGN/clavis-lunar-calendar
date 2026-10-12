# Changelog

本文件记录项目的显著变更。
格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.1.0] - 2026-10-12

首个公开版本。

- **日历格第二行**：节日（农历 > 公历）→ 节气 → 初一（月名）→ 农历日，按此优先级显示；
  过滤「世界…日」类纪念日噪声，超长节日名省略（QML 侧另有 `elide` 兜底）
- **休 / 班角标**：法定节假日显示「休」、调休上班显示「班」，普通周末与数据表覆盖范围外的
  年月不显示；悬停 tooltip 给出公历 + 星期、农历 + 干支年、节气、节日与假期名
- **跨零点跟随**：`viewingDate` / `calendarLayout` 读 `Time.now`（`qs.Services.Time`，30s tick），
  跨过零点后「今天」高亮与农历自动刷新
- **本地数据层**：vendored [lunar-javascript](https://github.com/6tail/lunar-javascript) **1.7.7**
  （MIT，逐字节原文 + 一行 CommonJS shim）与 148 行适配层组装成 442 KB 的
  `qml/ChineseCalendar.js`（`.pragma library`），运行时**零网络、零 `Process`、零系统命令**；
  非法输入或内部异常一律降级为原样式
- **overlay 安装**：镜像 Clavis 树到 `~/.config/quickshell/clavis` 叠加锚点补丁，不触碰 `/etc`；
  7 个锚点（5 exact + 2 block）、2 个 payload；`--sync`（失败自动回滚）、`--verify`、
  `--uninstall`、`--list`、`--forget`；与其它 clavis 组件按 `order` 共存（本组件 order 20）
- **年度更新链**：`check-lunar-data.sh`（只读检查上游版本与数据截止日）+ `refresh-lunar-vendor.sh`
  （npm `dist.integrity` sha512 校验 → 重建产物 → `qmllint`/冒烟门禁）+ 可选的年度提醒定时器
  （`--with-timer`，默认不装）
- 文档五篇（含数据边界与年度更新步骤）、AUR 打包元数据（PKGBUILD / `.SRCINFO` / systemd 用户单元，
  待提交）、CI（lint / test / compat-watch 手动巡检 / release 发版自检）
