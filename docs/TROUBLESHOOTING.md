# 常见问题

先跑一遍体检，多数问题它会直接告诉你原因：

```bash
./install.sh --verify     # 补丁是否在位 / overlay 是否陈旧 / 孤儿标记 / 定时器路径
./install.sh --check-data # 农历库与节假日数据是不是最新的（需 curl + jq）
```

## 安装后日历上没有农历/角标

1. `./install.sh --verify`：本组件那一段的「补丁」行应显示 `在位`（显示 `缺失/异常` 就是没打上）；
2. 确认 overlay 真的生效：`qs list --all` 里 `Config path` 应指向
   `~/.config/quickshell/clavis/shell.qml`（而不是 `/etc/xdg/...`）；
3. 看外壳日志：`journalctl --user -u clavis-shell -n 50 --no-pager`；
4. 只有**某一格**没有第二行/角标是正常的——那格的农历日与节气缺省不显示、或该年不在
   节假日数据表范围内（见 [DATA.md](DATA.md#二数据边界重要)）。

## Clavis 升级后农历/角标整个消失

正常现象——overlay 是全树遮蔽，升级后必须对齐：

```bash
./install.sh --sync
```

## sync 失败：`anchor matched 0 times`（锚点漂移）

Clavis 改了上游文本，属预期内的维护工作。你的桌面不受影响——sync 失败会自动恢复旧
overlay 并继续跑旧版。按 [patches/README.md](../patches/README.md) 的流程修锚点：

```bash
python3 scripts/patch_clavis_kit.py --explain-drift \
    --manifest patches/manifest.json --target <你的 Clavis 源码树> --json
```

block 模式的条目（`cw-delegate-ymd`、`cw-daybutton`）报的是
`block 定位失败`（`unique_line` 命中 0/多次、向上找不到开括号行、花括号不平衡或 `sanity`
缺失），错误文案会直接指出是哪种。修完记得重跑
`python3 -m unittest discover -s tests -v` 与 `python3 tests/test_manifest.py`。

## sync 失败：语法门禁 / smoke 门禁

报错形如：

```
  门禁 FAIL (qmllint) Common/functions/ChineseCalendar.js
  门禁 FAIL (smoke) rc=1
WARN: 重放失败：clavis-lunar-calendar 语法门禁失败 —— 已恢复到同步前状态（含其它已注册组件，桌面继续可用）
```

- **门禁 FAIL (qmlformat / qmllint)**：说明 overlay 里的文件语法不合法。这通常是你（或某个
  组件）动了 `patches/manifest.json` 里的 `replace` 文本却没跑测试；先
  `./install.sh --sync --dry-run` 看补丁后的 diff，再本地跑
  `python3 tests/test_manifest.py` 定位。
- **门禁 FAIL (node)**：`calendar_layout.js` 的替换文本语法有误（少了逗号/括号）。
- **门禁 FAIL (smoke)**：数据层跑不过 18 条断言——多半是重建产物时出了问题。
  单独复现：

  ```bash
  ./scripts/dev/smoke.sh                      # 仓库内的那一份 payload
  python3 scripts/build_chinese_calendar.py   # 重建后再跑；改了 lunar/adapter.js 必须重建
  ```

- 失败后 overlay 已经**自动恢复**，不需要手工回滚；修完再 `--sync` 一次即可。

## `WARN: 未找到 qmllint，跳过门禁` / `未找到 qml，跳过 smoke 门禁`

本机没装 `qt6-declarative`（或不在 PATH 上；引擎也会找 `/usr/lib/qt6/bin/`）。
门禁只是**跳过**，不阻断安装——但本地开发建议装上，否则补丁语法错误要等外壳崩了才发现：

```bash
sudo pacman -Syu --needed qt6-declarative
```

## 定时器不生效 / 提示「定时器指向的脚本不存在」

git clone 安装时，`./install.sh --with-timer` 写的 `ExecStart` 是本仓库的**绝对路径**；
仓库被移动/改名后它就失效了（`./install.sh --verify` 会打印
`WARN: 定时器指向的脚本不存在：…（仓库搬过家？重跑 ./install.sh --with-timer）`）。
修复：

```bash
./install.sh --with-timer                 # 在新位置重写并启用
systemctl --user list-timers clavis-lunar-calendar-refresh.timer
```

其它检查点：定时器只在每年的 **11 月与 12 月**触发（错过会补跑一次），需要 `curl` + `jq`；
`check-lunar-data.sh` 是只读的，它**不会**替你更新数据（更新步骤见
[DATA.md](DATA.md#三年度更新步骤次年放假安排公布后)）。

## 多组件（多 kit）

同机的所有 clavis 组件共享一个 overlay：每次 sync 都会**按 order 重放全部已注册组件**
（本组件 `order` 20），所以谁 sync 谁负责把大家都重新打上，不存在"后装的把先装的覆盖掉"。
`./install.sh --list` 看已注册组件、order 与各自仓库路径。

### 某个组件的仓库被删了 / 搬走了

sync 会**响亮失败**（这是设计：宁可停下也不静默丢掉一个组件）：

```
ERROR: 以下已注册组件的仓库文件缺失（repo 被删/搬走？）：
  - <kit-id>: 仓库目录不存在：/…/<kit-id>
```

按提示四选一：

1. 把仓库移回原路径；
2. 或到**新位置重跑 `./install.sh`**——同 kit id 会自动重绑 `repo_root` 后重新对齐；
3. 或 `./install.sh --forget <kit-id>` 把它从注册表摘除（**不碰 overlay**；它留在 overlay 里的
   补丁会变成"孤儿标记"，下次 sync 镜像时被清掉）；
4. 应急：本次加 `--skip-missing` 跳过它（overlay 里它的补丁会被清掉，桌面回到不含该组件的状态）。

### 孤儿标记（verify 报「不属于任何已注册组件」）

`verify` 末尾的「孤儿标记检查」会报告 overlay 里存在、但不属于任何已注册组件的
`BEGIN clavis-*` 标记——多半是你手工改过 overlay，或某个组件被 `forget` 后残留。
不想要就手工从对应文件里删掉，或直接 `./install.sh --sync`（全量镜像会把 overlay 重置回
干净状态；若那些文件同时也在某个 kit 的摘要里，sync 会先要求 `--force`）。

### sync 要求 `--force` 是什么意思

只有**无法归因的差异**才会拦你：

```
ERROR: overlay 中以下文件被改动过（不归属于任何已注册组件）：… --force 才会覆盖（覆盖前仍会整体备份）
```

即：某个 kit 记录的文件与上次成功同步的摘要对不上、又不是**兄弟 kit** 的补丁改写的
——那很可能意味着你自己手改过 overlay。`--force` 只覆盖这一类差异（覆盖前仍会整体备份，
也不跳过语法门禁）；被其它组件补丁改写的共享文件不会触发 `--force`。

> 注意：**不要**手改 overlay 里本组件的文件——下次 `--sync` 会重放补丁把它盖回去。
> 要改行为就改 `patches/manifest.json` 的 `replace` 文本（见
> [ARCHITECTURE.md](ARCHITECTURE.md#字号策略)），或直接在仓库里改 `lunar/adapter.js` 后重建。

## 外壳起不来（进入 failed 状态）

语法门禁会在"启用坏补丁"之前阻止安装/同步，所以正常情况下不会发生。
如果手动改过 overlay 导致崩溃循环：

```bash
systemctl --user stop clavis-shell
rm -rf ~/.config/quickshell/clavis        # 移除 overlay，立刻回到系统原生
systemctl --user reset-failed clavis-shell
systemctl --user start clavis-shell
```

（之后想恢复本项目：`./install.sh`）

## 我只想要新的节假日数据，不想升级组件

两者可以分开：数据更新永远先发生在仓库里（`docs/DATA.md` 的更新链），装进机器靠
`./install.sh --sync`（git clone）或等新版包（AUR）。`--sync` 不改你的任何个人配置。
