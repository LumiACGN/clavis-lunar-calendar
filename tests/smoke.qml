// smoke.qml — 用真实 QML V4 引擎冒烟测试 ChineseCalendar.js（不进系统树）
// 跑法：cd tests && QT_QPA_PLATFORM=offscreen timeout 60 /usr/lib/qt6/bin/qml smoke.qml; echo $?
import QtQuick
import "../qml/ChineseCalendar.js" as CC

Window {
    width: 1
    height: 1
    visible: false

    Component.onCompleted: {
        let failed = 0;
        const check = (name, cond, actual) => {
            if (!cond) {
                failed += 1;
                console.log("FAIL " + name + "  actual=" + actual);
            } else {
                console.log("ok   " + name);
            }
        };

        // 2026-10 月视图的 42 格（周一开头：9-28 起）
        const days = [];
        const p = n => (n < 10 ? "0" : "") + n;
        for (let i = 0; i < 42; i++) {
            const d = new Date(2026, 8, 28 + i); // 2026-09-28 起
            days.push(d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()));
        }

        let t = Date.now();
        const first = days.map(x => CC.dayInfo(x));
        const cold = Date.now() - t;
        t = Date.now();
        days.forEach(x => CC.dayInfo(x));
        const warm = Date.now() - t;
        console.log("TIMING cold=" + cold + "ms warm=" + warm + "ms");
        console.log("dataRange=" + JSON.stringify(CC.dataRange()));

        console.log("---- 42 cells ----");
        days.forEach((x, i) => console.log(x, first[i].subKind, first[i].subText, first[i].badge, first[i].jieQi));

        const at = y => CC.dayInfo(y);
        check("10-01 sub=国庆节", at("2026-10-01").subText === "国庆节", at("2026-10-01").subText);
        check("10-01 badge=休", at("2026-10-01").badge === "休", at("2026-10-01").badge);
        check("10-08 jieQi=寒露", at("2026-10-08").jieQi === "寒露", at("2026-10-08").jieQi);
        check("10-08 badge empty", at("2026-10-08").badge === "", at("2026-10-08").badge);
        check("10-10 badge=班", at("2026-10-10").badge === "班", at("2026-10-10").badge);
        check("10-10 holidayName=国庆节", at("2026-10-10").holidayName === "国庆节", at("2026-10-10").holidayName);
        check("02-16 sub=除夕", at("2026-02-16").subText === "除夕", at("2026-02-16").subText);
        check("02-17 sub=春节", at("2026-02-17").subText === "春节", at("2026-02-17").subText);
        check("02-17 badge=休", at("2026-02-17").badge === "休", at("2026-02-17").badge);
        check("09-25 sub=中秋节", at("2026-09-25").subText === "中秋节", at("2026-09-25").subText);
        check("09-25 badge=休", at("2026-09-25").badge === "休", at("2026-09-25").badge);
        check("2027-01-01 badge empty", at("2027-01-01").badge === "", at("2027-01-01").badge);
        check("bad input null", CC.dayInfo("bad") === null, CC.dayInfo("bad"));
        check("2024-02-10 春节(旧年份)", at("2024-02-10").subText === "春节", at("2024-02-10").subText);
        check("tooltip multi-line", at("2026-10-01").tooltip.indexOf("\n") > 0, at("2026-10-01").tooltip);
        check("10-05 世界住房日已过滤", at("2026-10-05").subText === "廿五", at("2026-10-05").subText);
        check("10-31 别名万圣夜", at("2026-10-31").subText === "万圣夜", at("2026-10-31").subText);
        check("tooltip 含星期六", at("2026-10-10").tooltip.indexOf("星期六") > 0, at("2026-10-10").tooltip);

        console.log("---- tooltip sample 2026-10-10 ----");
        console.log(at("2026-10-10").tooltip);
        console.log("---- RESULT: " + (failed === 0 ? "PASS" : "FAIL(" + failed + ")") + " ----");
        Qt.exit(failed === 0 ? 0 : 1);
    }
}
