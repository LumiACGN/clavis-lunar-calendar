// ==== adapter: lunar-javascript -> Clavis 日历格信息（本文件是唯一手改源码） ====
// 约定：
//  * 只允许 ES5；不访问 window/document/Qt；任何异常都降级为 null，绝不让 QML 绑定炸掉。
//  * 对外只暴露 dayInfo(ymd)；ymd 为 "YYYY-MM-DD"（来自 calendar_layout.js 的本地历）。
//  * 时区：一律 Solar.fromYmd(显式整数)，不用 fromDate，避免 UTC 差一天。
//  * 依赖构建脚本在文件前部注入：_LUNAR、LUNAR_JS_VERSION、HOLIDAY_DATA_END。
var _cache = {};
var _cacheSize = 0;
var _CACHE_MAX = 2048; // 42 格/月，约 4 年翻页量
var _warned = false;

function _parseYmd(ymd) {
    if (typeof ymd !== "string" || ymd.length !== 10 || ymd.charAt(4) !== "-" || ymd.charAt(7) !== "-")
        return null;
    var y = parseInt(ymd.substring(0, 4), 10);
    var m = parseInt(ymd.substring(5, 7), 10);
    var d = parseInt(ymd.substring(8, 10), 10);
    if (!(y >= 1900 && y <= 2100) || !(m >= 1 && m <= 12) || !(d >= 1 && d <= 31))
        return null;
    return { "y": y, "m": m, "d": d };
}

// 公历节日别名：显示更短、更符合日历惯例的叫法
var _SOLAR_ALIAS = {
    "万圣节前夜": "万圣夜"
};

// solar.getFestivals() 会混入 WEEK_FESTIVAL（世界住房日/全国助残日等"XX日"类），
// 日历格上属噪声，过滤掉；母亲节/父亲节/感恩节等保留。
function _keepSolarFestival(name) {
    if (/^(世界|国际|全国|全民)/.test(name))
        return false;
    return true;
}

function _mergeFestivals(lunarFestivals, solarFestivals) {
    var out = [];
    var i;
    var name;
    for (i = 0; i < lunarFestivals.length; i++)
        if (out.indexOf(lunarFestivals[i]) < 0)
            out.push(lunarFestivals[i]);
    for (i = 0; i < solarFestivals.length; i++) {
        if (!_keepSolarFestival(solarFestivals[i]))
            continue;
        name = _SOLAR_ALIAS[solarFestivals[i]] || solarFestivals[i];
        if (out.indexOf(name) < 0)
            out.push(name);
    }
    return out;
}

function _shorten(text) {
    if (text.length <= 4)
        return text;
    return text.substring(0, 3) + "…"; // 超长节日名，QML 侧还有 elide 兜底
}

// 第二行优先级：节日（农历 > 公历）> 节气 > 初一(月名) > 农历日
function _pickSub(dayName, monthName, jieQi, festivals) {
    if (festivals.length > 0)
        return { "text": _shorten(festivals[0]), "kind": "festival" };
    if (jieQi !== "")
        return { "text": jieQi, "kind": "jieqi" };
    if (dayName === "初一")
        return { "text": monthName, "kind": "lunarMonth" };
    return { "text": dayName, "kind": "lunarDay" };
}

function dayInfo(ymd) {
    var parsed = _parseYmd(ymd);
    if (parsed === null)
        return null;
    var hit = _cache[ymd];
    if (hit !== undefined)
        return hit;

    var info = null;
    try {
        var solar = _LUNAR.Solar.fromYmd(parsed.y, parsed.m, parsed.d);
        var lunar = solar.getLunar();
        var dayName = lunar.getDayInChinese();
        var monthName = lunar.getMonthInChinese() + "月"; // 闰月为 "闰四月"
        var jieQi = lunar.getJieQi();
        var festivals = _mergeFestivals(lunar.getFestivals(), solar.getFestivals());
        // 普通周末/无数据年份都返回 null -> 不显示角标（正确行为）
        var holiday = _LUNAR.HolidayUtil.getHoliday(parsed.y, parsed.m, parsed.d);
        var sub = _pickSub(dayName, monthName, jieQi, festivals);

        var solarText = solar.getYear() + "年" + solar.getMonth() + "月" + solar.getDay() + "日";
        var lines = [
            solarText + " 星期" + solar.getWeekInChinese(),
            "农历" + monthName + dayName + " · " + lunar.getYearInGanZhi() + "年"
        ];
        if (jieQi !== "")
            lines.push("节气：" + jieQi);
        if (festivals.length > 0)
            lines.push("节日：" + festivals.join("、"));
        if (holiday !== null)
            lines.push(holiday.getName() + (holiday.isWork() ? " 调休上班" : " 放假"));

        info = {
            "ymd": ymd,
            "solarText": solarText,
            "weekday": "星期" + solar.getWeekInChinese(),
            "lunarDay": dayName,
            "lunarMonth": monthName,
            "lunarText": "农历" + monthName + dayName,
            "ganZhi": lunar.getYearInGanZhi() + "年",
            "jieQi": jieQi,
            "festivals": festivals,
            "holidayName": holiday !== null ? holiday.getName() : "",
            "holidayKind": holiday !== null ? (holiday.isWork() ? "work" : "rest") : "",
            "subText": sub.text,
            "subKind": sub.kind,
            "badge": holiday !== null ? (holiday.isWork() ? "班" : "休") : "",
            "tooltip": lines.join("\n")
        };
    } catch (error) {
        if (!_warned) {
            _warned = true;
            console.warn("ChineseCalendar.dayInfo failed:", error);
        }
        return null; // 单格降级为原样式
    }

    if (_cacheSize >= _CACHE_MAX) {
        _cache = {};
        _cacheSize = 0;
    }
    _cache[ymd] = info;
    _cacheSize += 1;
    return info;
}

// 供 --verify / 排障查询：vendor 版本与内置节假日数据截止日
function dataRange() {
    return {
        "vendor": "lunar-javascript",
        "version": LUNAR_JS_VERSION,
        "holidayDataEnd": HOLIDAY_DATA_END
    };
}

function clearCache() {
    _cache = {};
    _cacheSize = 0;
}
