// SPDX-FileCopyrightText: 2026 LumiACGN
// SPDX-License-Identifier: GPL-3.0-or-later
//
// tests/fixtures/dummy-kit 的载荷（测试夹具，不随本项目发布）：
// 内容刻意最小——它只需要能通过引擎的 node 语法门禁，并被装到 overlay 的
// Common/functions/DummyProbe.js（多组件共存彩排里用来验证「kit 级卸载只摘自己」）。
.pragma library

function probe() {
    return "clavis-dummy-kit";
}
