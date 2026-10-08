"""命令行入口：登录教务系统（或读取离线 HTML）并把课表转换为 .ics。"""

from __future__ import annotations

import argparse
import getpass
import os
from datetime import date
from pathlib import Path
from typing import Optional

from .academic import (
    AcademicClient,
    AcademicError,
    AcademicSession,
    LoginError,
    _decode_body,
    academic_week_for_date,
)
from .ics import build_ics, validate_ics
from .models import MAX_ACADEMIC_WEEK, MIN_ACADEMIC_WEEK, SemesterInfo
from .parser import GlutScheduleParser, extract_section_times
from .periods import PROFILES, default_profile
from .weektext import derived_academic_max_week


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="glut-ics",
        description="桂林理工大学课表 → 标准 iCalendar(.ics)，可直接导入 Google/Apple/Outlook 日历。",
    )
    parser.add_argument("--username", "-u", help="教务系统学号")
    parser.add_argument(
        "--password", "-p",
        help="教务系统密码（可用环境变量 GLUT_PASSWORD；不传则交互输入）",
    )
    parser.add_argument("--campus", choices=("guilin", "nanning"), default="guilin",
                        help="校区：桂林(默认)/南宁")
    parser.add_argument("--year", type=int, help="门户年份，如 2026")
    parser.add_argument("--season", choices=("spring", "autumn"), help="学期：春/秋")
    parser.add_argument("--student-id", help="内部学号（跳过自动解析）")
    parser.add_argument("--semester-start", help="第 1 周周一，格式 YYYY-MM-DD")
    parser.add_argument("--max-week", type=int, help="学期总周数，默认自动推断")
    parser.add_argument(
        "--profile",
        choices=PROFILES,
        help="作息档案：yanshan=雁山(桂林默认)/pingfeng=屏风/nanning=南宁。"
             "注意：教务页面固定显示屏风时间，无法据此判断校区，需手动指定。",
    )
    parser.add_argument("--calendar-name", help="导出日历的名称")
    parser.add_argument("--html", help="离线模式：读取已保存的课表 HTML 文件")
    parser.add_argument("--output", "-o", default="schedule.ics", help="输出文件（默认 schedule.ics）")
    parser.add_argument("--stdout", action="store_true", help="把 ICS 输出到标准输出")
    parser.add_argument(
        "--expand", action="store_true",
        help="把每一次课写成独立事件（不用 RRULE），兼容不展开重复规则的日历程序",
    )
    parser.add_argument("--validate-only", action="store_true",
                        help="只校验已有的 .ics 文件（配合 --output）")
    return parser


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise SystemExit(f"日期格式非法：{value!r}，应为 YYYY-MM-DD")


def _clamp_week(value: int) -> int:
    return max(MIN_ACADEMIC_WEEK, min(MAX_ACADEMIC_WEEK, value))


def _resolve_max_week(session, courses, override: Optional[int]) -> int:
    if override:
        return _clamp_week(override)
    if session.end_date and session.start_monday:
        return _clamp_week(academic_week_for_date(session.end_date, session.start_monday))
    if session.weekly_max_week:
        return _clamp_week(session.weekly_max_week)
    derived = derived_academic_max_week(
        occurrence.week_text
        for course in courses
        for occurrence in course.occurrences
    )
    return _clamp_week(derived) if derived else 20


def _load_offline_session(args) -> AcademicSession:
    # 按字节读取后统一解码：教务导出的页面可能是 GBK，直接按 UTF-8 读会全部乱码。
    html = _decode_body(Path(args.html).read_bytes())
    season = args.season or "spring"
    year = args.year or date.today().year
    session = AcademicSession(
        campus=args.campus,
        year=year,
        season=season,
        year_id="",
        term_id="",
        student_id="",
        timetable_html=html,
        display_name=f"{year}·{'春' if season == 'spring' else '秋'}",
    )
    session.start_monday = _parse_date(args.semester_start)
    if session.start_monday is None:
        raise SystemExit("离线模式必须提供 --semester-start（例：--semester-start 2026-03-09）")
    return session


def _load_online_session(args) -> AcademicSession:
    if not args.username:
        raise SystemExit("在线模式需要 --username（或改用 --html 离线解析）")
    password = args.password or os.environ.get("GLUT_PASSWORD")
    if not password:
        password = getpass.getpass("教务系统密码：")

    client = AcademicClient(campus=args.campus)
    try:
        client.login(args.username, password)
    except LoginError as error:
        raise SystemExit(f"登录失败：{error}")
    except AcademicError as error:
        raise SystemExit(f"网络错误：{error}")

    try:
        return client.fetch_semester(
            year=args.year,
            season=args.season,
            student_id=args.student_id,
            start_monday=_parse_date(args.semester_start),
        )
    except AcademicError as error:
        raise SystemExit(f"抓取课表失败：{error}")


def _run_validate_only(args) -> int:
    try:
        with open(args.output, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError as error:
        raise SystemExit(f"无法读取 {args.output}：{error}")
    issues = validate_ics(text)
    if issues:
        print(f"校验未通过，发现 {len(issues)} 个问题：")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    print(f"校验通过：{args.output}")
    return 0


def main(argv=None) -> int:
    args = build_argument_parser().parse_args(argv)

    if args.validate_only:
        return _run_validate_only(args)

    session = _load_offline_session(args) if args.html else _load_online_session(args)

    parser = GlutScheduleParser()
    courses = parser.parse_personal_schedule(session.timetable_html)
    adjustments = parser.parse_adjustments(session.timetable_html)

    max_week = _resolve_max_week(session, courses, args.max_week)

    # 作息只能由用户指定，不能从课表页面推断：学校官方说明"教务管理信息系统上的课表
    # 只能显示一个校区（屏风校区）上课时间"，即页面时间恒为屏风，不携带校区信息。
    # 因此桂林默认按雁山生成，页面时间仅用于提示，不参与计算。
    profile = args.profile or default_profile(args.campus)

    semester = SemesterInfo(
        campus=session.campus,
        year=session.year,
        season=session.season,
        year_id=session.year_id,
        term_id=session.term_id,
        start_monday=session.start_monday,
        max_week=max_week,
        display_name=session.display_name,
        end_date=session.end_date,
    )

    result = build_ics(
        courses, semester, profile=profile, calendar_name=args.calendar_name,
        has_noon="中午" in session.timetable_html, expand=args.expand,
    )
    issues = validate_ics(result.text)

    if args.stdout:
        print(result.text, end="")
    else:
        with open(args.output, "w", encoding="utf-8", newline="") as handle:
            handle.write(result.text)

    print("=" * 56)
    print(f"学期        : {session.display_name or '-'}（{session.campus}）")
    print(f"第 1 周周一 : {semester.start_monday}")
    print(f"学期总周数  : {semester.max_week}")
    print(f"作息档案    : {profile}")
    if session.campus == "guilin" and profile == "yanshan":
        page_first = extract_section_times(session.timetable_html).get(1)
        if page_first:
            print(f"[提示] 教务页面固定显示屏风作息（第 1 节 {page_first[0]}-{page_first[1]}），"
                  f"不能据此判断校区；当前按雁山生成。"
                  f"若你在屏风校区，请改用 --profile pingfeng。")
    print(f"课程门数    : {len(courses)}")
    print(f"课次数      : {result.occurrence_count}")
    print(f"日历事件数  : {result.event_count}")
    if adjustments:
        print(f"调课记录    : {len(adjustments)}")
    if not courses:
        print("[警告] 未解析到任何课程：请检查 --campus/--year/--season 是否正确、"
              "该学期课表是否已开放；若页面结构有变化请反馈。")
    if result.unparsed_week_texts:
        print(f"[警告] {len(result.unparsed_week_texts)} 条课次周次无法识别，已跳过：")
        for item in result.unparsed_week_texts[:10]:
            print(f"        - {item}")
    if result.missing_periods:
        print(f"[警告] {len(result.missing_periods)} 条课次节次超出作息表，已跳过：")
        for item in result.missing_periods[:10]:
            print(f"        - {item}")
    if issues:
        print(f"[校验] 未通过，{len(issues)} 个问题：")
        for issue in issues[:10]:
            print(f"        - {issue}")
    else:
        print("[校验] 通过（RFC 5545 结构 / 时区 / UID 唯一性）")
    if not args.stdout:
        print(f"已写入      : {args.output}")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
