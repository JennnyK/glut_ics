"""数据模型。

字段口径移植自参考仓库 hzhkdh/glut-schedule：
  - data/model/ScheduleModels.kt
  - data/model/AcademicSemesterModels.kt
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

MIN_ACADEMIC_WEEK = 1
MAX_ACADEMIC_WEEK = 22


@dataclass(frozen=True)
class ClassPeriod:
    """一个节次的起止时间，``section`` 为内部节次号。

    桂林本部的内部节次号含"中午偏移"：第1-4节→1-4，中午1/2→5/6，第5-12节→7-14。
    """

    section: int
    starts_at: str  # "HH:MM"
    ends_at: str    # "HH:MM"


@dataclass
class CourseOccurrence:
    """课程的一个具体课次（星期 + 节次区间 + 周次文本）。"""

    id: str
    course_id: str
    day_of_week: int          # 1=周一 ... 7=周日
    start_section: int        # 内部节次号（桂林含中午偏移）
    end_section: int
    week_text: str            # 原始周次文本，如 "1-12周" / "3-12,16-17"
    note: str = ""            # 该课次的教室（覆盖课程级教室）

    @property
    def section_span(self) -> int:
        return self.end_section - self.start_section + 1


@dataclass
class ScheduleCourse:
    id: str
    title: str
    room: str
    teacher: str
    occurrences: List[CourseOccurrence] = field(default_factory=list)
    hour_type: str = ""       # 讲课学时 / 实验学时 / 上机学时 / 课程学时


@dataclass
class ScheduleAdjustment:
    """调课 / 补课 / 停课 / 代课记录。"""

    type: str
    title: str
    teacher: str
    original_week: int = 0
    original_day: int = 0
    original_start_section: int = 0
    original_end_section: int = 0
    original_room: str = ""
    makeup_week: int = 0
    makeup_day: int = 0
    makeup_start_section: int = 0
    makeup_end_section: int = 0
    makeup_room: str = ""


@dataclass
class SemesterInfo:
    """一次导入所需的学期上下文。"""

    campus: str                 # "guilin" | "nanning"
    year: int                   # 门户年份，如 2026
    season: str                 # "spring" | "autumn"
    year_id: str                # 教务 yearid，通常为 year - 1980
    term_id: str                # 教务 termid，春=1，秋(桂林)=2 / 秋(南宁)=3
    start_monday: date          # 第 1 周周一
    max_week: int               # 学期总周数
    display_name: str = ""
    end_date: Optional[date] = None
