"""RFC 5545 iCalendar（.ics）生成器与导入校验。

设计要点：
  * 时区：所有事件使用 ``TZID=Asia/Shanghai``（UTC+8，无夏令时），并内嵌 VTIMEZONE；
  * 重复事件：按周重复用 ``RRULE:FREQ=WEEKLY``。单一连续周次区间用 ``COUNT``；
    单/双周（步长为 2）用 ``INTERVAL=2``；不规则的周次集合会被拆成多条规则
    （因为 RFC 5545 规定一个 VEVENT 只能有一个 RRULE）；
  * 行折叠：任意内容行超过 75 个八位组时按 §3.1 折叠，续行以单个空格开头；
  * 文本转义：``\\`` ``;`` ``,`` 换行按 §3.3.11 转义。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .models import ClassPeriod, ScheduleCourse, SemesterInfo
from .periods import period_map
from .sections import display_section_range_label
from .weektext import academic_weeks_for_text

PRODID = "-//glut-ics//GLUT Schedule to iCalendar//CN"
TIMEZONE = "Asia/Shanghai"
WEEKDAY_CODES = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
_CRLF = "\r\n"


# --------------------------------------------------------------------------- #
# 基础编码工具
# --------------------------------------------------------------------------- #
def escape_text(value: str) -> str:
    """转义 iCalendar 文本值（§3.3.11）。"""
    return (
        (value or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def fold_line(line: str, limit: int = 75) -> str:
    """按 75 个八位组折叠一行，续行以单个空格开头（§3.1）。

    折叠处不能劈开多字节 UTF-8 字符，因此按字符累积字节数。
    """
    if len(line.encode("utf-8")) <= limit:
        return line

    chunks: List[str] = []
    buffer = ""
    buffer_len = 0
    current_limit = limit
    for char in line:
        char_len = len(char.encode("utf-8"))
        if buffer_len + char_len > current_limit:
            chunks.append(buffer)
            buffer = ""
            buffer_len = 0
            current_limit = limit - 1  # 续行首个空格也算在 75 个八位组内
        buffer += char
        buffer_len += char_len
    chunks.append(buffer)
    return (_CRLF + " ").join(chunks)


def _render(lines: List[str]) -> str:
    return _CRLF.join(fold_line(line) for line in lines) + _CRLF


def _format_local(value: dt.datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%S")


def _parse_hhmm(value: str) -> dt.time:
    hour, minute = value.split(":")
    return dt.time(int(hour), int(minute))


# --------------------------------------------------------------------------- #
# 周次 → 重复规则
# --------------------------------------------------------------------------- #
def split_week_runs(weeks: List[int]) -> List[Tuple[int, int, int]]:
    """把周次集合拆成 ``(起始周, 步长, 个数)`` 的等差数列。

    步长只会是 1（连续周）或 2（单周/双周）。
    """
    ordered = sorted(set(weeks))
    runs: List[Tuple[int, int, int]] = []
    index = 0
    total = len(ordered)
    while index < total:
        step = ordered[index + 1] - ordered[index] if index + 1 < total else 1
        if step not in (1, 2):
            step = 1
        end = index + 1
        while end < total and ordered[end] - ordered[end - 1] == step:
            end += 1
        runs.append((ordered[index], step, end - index))
        index = end
    return runs


def _rrule(weekday_code: str, step: int, count: int) -> str:
    """按 RFC 5545 §3.3.10 的规范顺序输出：FREQ → COUNT → INTERVAL → BYDAY。

    部分严格实现按 ABNF 顺序解析，乱序虽被宽松解析器接受，仍以规范顺序更稳妥。
    """
    parts = ["FREQ=WEEKLY", f"COUNT={count}"]
    if step == 2:
        parts.append("INTERVAL=2")
    parts.append(f"BYDAY={weekday_code}")
    return ";".join(parts)


# --------------------------------------------------------------------------- #
# 生成
# --------------------------------------------------------------------------- #
@dataclass
class IcsBuildResult:
    text: str
    event_count: int
    occurrence_count: int
    unparsed_week_texts: List[str] = field(default_factory=list)
    missing_periods: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _vtimezone_lines() -> List[str]:
    return [
        "BEGIN:VTIMEZONE",
        f"TZID:{TIMEZONE}",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        "TZOFFSETFROM:+0800",
        "TZOFFSETTO:+0800",
        "TZNAME:CST",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]


def build_ics(
    courses: List[ScheduleCourse],
    semester: SemesterInfo,
    profile: str = "yanshan",
    calendar_name: Optional[str] = None,
    now: Optional[dt.datetime] = None,
    section_times: Optional[Dict[int, Tuple[str, str]]] = None,
    has_noon: bool = True,
    expand: bool = False,
) -> IcsBuildResult:
    """把课程列表渲染成 ICS 文本。

    :param profile: 作息档案（``yanshan`` / ``pingfeng`` / ``nanning``）。
    :param section_times: 课表页面实测的节次时间 ``{节次: (起, 止)}``。
        提供时优先采信页面数据（比硬编码档案更准），否则回退到 ``profile``。
    :param has_noon: 页面是否含"中午1/中午2"时段（桂林 True，南宁 False），
        仅影响 DESCRIPTION 里节次的中文表述。
    :param expand: ``True`` 时把每一次课都写成独立 VEVENT（不使用 RRULE）。
        用于不展开重复规则的日历程序，代价是事件数变多（一学期约 160+ 条）。
    """
    if section_times:
        periods = {
            section: ClassPeriod(section, start, end)
            for section, (start, end) in section_times.items()
        }
    else:
        periods = period_map(profile)
    stamp = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    stamp_text = stamp.strftime("%Y%m%dT%H%M%SZ")

    lines: List[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_text(calendar_name or f'桂林理工大学课表 {semester.display_name}'.strip())}",
        f"X-WR-TIMEZONE:{TIMEZONE}",
        *_vtimezone_lines(),
    ]

    event_count = 0
    occurrence_count = 0
    unparsed: List[str] = []
    missing: List[str] = []
    warnings: List[str] = []

    for course in courses:
        for occurrence in course.occurrences:
            weeks = academic_weeks_for_text(occurrence.week_text, semester.max_week)
            if not weeks:
                unparsed.append(f"{course.title}（周次：{occurrence.week_text!r}）")
                continue

            start_period = periods.get(occurrence.start_section)
            end_period = periods.get(occurrence.end_section) or start_period
            if start_period is None or end_period is None:
                missing.append(
                    f"{course.title}（节次 {occurrence.start_section}-{occurrence.end_section}）"
                )
                continue

            start_time = _parse_hhmm(start_period.starts_at)
            end_time = _parse_hhmm(end_period.ends_at)
            weekday_code = WEEKDAY_CODES[occurrence.day_of_week - 1]
            room = (occurrence.note or course.room or "").strip()
            location = room or "待定"

            description = _build_description(course, occurrence, start_period, end_period, has_noon)
            occurrence_count += 1

            # expand 模式：每次课一条 VEVENT，兼容不展开 RRULE 的日历程序
            segments = [(week, 1, 1) for week in weeks] if expand else split_week_runs(weeks)

            for start_week, step, count in segments:
                first_day = semester.start_monday + dt.timedelta(
                    days=(start_week - 1) * 7 + (occurrence.day_of_week - 1)
                )
                dtstart = dt.datetime.combine(first_day, start_time)
                dtend = dt.datetime.combine(first_day, end_time)
                uid = f"{course.id}-{occurrence.id}-w{start_week}@glut-ics"

                event_lines = [
                    "BEGIN:VEVENT",
                    f"UID:{uid}",
                    f"DTSTAMP:{stamp_text}",
                    f"DTSTART;TZID={TIMEZONE}:{_format_local(dtstart)}",
                    f"DTEND;TZID={TIMEZONE}:{_format_local(dtend)}",
                ]
                if not expand:
                    event_lines.append(f"RRULE:{_rrule(weekday_code, step, count)}")
                event_lines.extend([
                    f"SUMMARY:{escape_text(course.title)}",
                    f"LOCATION:{escape_text(location)}",
                    f"DESCRIPTION:{escape_text(description)}",
                    "STATUS:CONFIRMED",
                    "TRANSP:OPAQUE",
                    "SEQUENCE:0",
                    "END:VEVENT",
                ])
                lines.extend(event_lines)
                event_count += 1

    lines.append("END:VCALENDAR")
    return IcsBuildResult(
        text=_render(lines),
        event_count=event_count,
        occurrence_count=occurrence_count,
        unparsed_week_texts=unparsed,
        missing_periods=missing,
        warnings=warnings,
    )


def _build_description(course, occurrence, start_period, end_period, has_noon=True) -> str:
    section_label = display_section_range_label(
        occurrence.start_section, occurrence.end_section, has_noon
    )
    parts = [
        f"教师：{course.teacher}",
        f"周次：{occurrence.week_text}",
        f"节次：{section_label}（{start_period.starts_at}-{end_period.ends_at}）",
    ]
    if course.hour_type:
        parts.append(f"学时类型：{course.hour_type}")
    room = (occurrence.note or course.room or "").strip()
    if room:
        parts.append(f"地点：{room}")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# 导入校验
# --------------------------------------------------------------------------- #
def unfold(text: str) -> List[str]:
    """把折叠的 ICS 行还原成逻辑行（§3.1）。"""
    logical: List[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and logical:
            logical[-1] += raw[1:]
        elif raw:
            logical.append(raw)
    return logical


def validate_ics(text: str) -> List[str]:
    """校验 ICS 文本，返回问题列表；空列表表示通过。

    检查项：组件配对、必需属性、时间格式、UID 唯一性、RRULE 合法性。
    """
    issues: List[str] = []
    lines = unfold(text)

    if not lines or lines[0] != "BEGIN:VCALENDAR":
        issues.append("缺失或错误的文件头 BEGIN:VCALENDAR")
    if not lines or lines[-1] != "END:VCALENDAR":
        issues.append("缺失或错误的文件尾 END:VCALENDAR")

    stack: List[str] = []
    has_version = False
    has_prodid = False
    uid_seen = set()
    in_event = False
    event_props = set()

    for line in lines:
        if line.startswith("BEGIN:"):
            name = line[6:].strip()
            stack.append(name)
            if name == "VEVENT":
                in_event = True
                event_props = set()
        elif line.startswith("END:"):
            name = line[4:].strip()
            if not stack or stack[-1] != name:
                issues.append(f"组件不配对：END:{name}")
            else:
                stack.pop()
            if name == "VEVENT":
                in_event = False
                for required in ("UID", "DTSTAMP", "DTSTART", "SUMMARY"):
                    if required not in event_props:
                        issues.append(f"VEVENT 缺少必需属性 {required}")
        else:
            name, _, value = line.partition(":")
            base_name = name.split(";")[0].upper()
            if base_name == "VERSION":
                has_version = True
            elif base_name == "PRODID":
                has_prodid = True
            if in_event:
                event_props.add(base_name)
                if base_name == "UID":
                    if value in uid_seen:
                        issues.append(f"UID 重复：{value}")
                    uid_seen.add(value)
                elif base_name == "DTSTART":
                    issues.extend(_validate_datetime(name, value))
                elif base_name == "RRULE":
                    issues.extend(_validate_rrule(value))

    if stack:
        issues.append(f"存在未闭合组件：{', '.join(stack)}")
    if not has_version:
        issues.append("缺失 VERSION 属性")
    if not has_prodid:
        issues.append("缺失 PRODID 属性")

    return issues


def _validate_datetime(name: str, value: str) -> List[str]:
    try:
        dt.datetime.strptime(value, "%Y%m%dT%H%M%S")
    except ValueError:
        return [f"DTSTART 时间格式非法：{value}"]
    if "TZID" not in name and not value.endswith("Z"):
        # 无时区且非 UTC，本地时间将按客户端默认时区解释。
        return [f"DTSTART 缺少时区信息：{name}"]
    return []


def _validate_rrule(value: str) -> List[str]:
    parts = dict(
        chunk.split("=", 1) for chunk in value.split(";") if "=" in chunk
    )
    if parts.get("FREQ", "").upper() != "WEEKLY":
        return [f"RRULE 非按周重复：{value}"]
    if "BYDAY" not in parts:
        return [f"RRULE 缺少 BYDAY：{value}"]
    if "COUNT" not in parts and "UNTIL" not in parts:
        return [f"RRULE 缺少 COUNT/UNTIL：{value}"]
    return []
