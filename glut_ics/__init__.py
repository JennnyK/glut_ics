"""glut-ics：把桂林理工大学课表数据转换为标准 iCalendar(.ics)。

纯标准库实现，可直接导入 Google Calendar / Apple Calendar / Outlook 等。

典型用法：

    from glut_ics import AcademicClient, GlutScheduleParser, build_ics, validate_ics

    client = AcademicClient(campus="guilin")
    client.login(username, password)
    session = client.fetch_semester()

    courses = GlutScheduleParser().parse_personal_schedule(session.timetable_html)
    result = build_ics(courses, semester, profile="yanshan")
    assert not validate_ics(result.text)
"""

from .academic import AcademicClient, AcademicError, AcademicSession, LoginError
from .ics import IcsBuildResult, build_ics, validate_ics
from .models import (
    ClassPeriod,
    CourseOccurrence,
    ScheduleAdjustment,
    ScheduleCourse,
    SemesterInfo,
)
from .parser import GlutScheduleParser, extract_section_times, parse_personal_schedule
from .periods import class_periods, default_profile, period_map
from .weektext import academic_weeks_for_text

__version__ = "1.0.0"

__all__ = [
    "AcademicClient",
    "AcademicError",
    "AcademicSession",
    "LoginError",
    "IcsBuildResult",
    "build_ics",
    "validate_ics",
    "ClassPeriod",
    "CourseOccurrence",
    "ScheduleAdjustment",
    "ScheduleCourse",
    "SemesterInfo",
    "GlutScheduleParser",
    "parse_personal_schedule",
    "extract_section_times",
    "class_periods",
    "default_profile",
    "period_map",
    "academic_weeks_for_text",
    "__version__",
]
