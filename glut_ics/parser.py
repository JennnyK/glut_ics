"""课表 HTML 解析器。

逐条移植自参考仓库 ``service/parser/AcademicScheduleParser.kt`` 的
``GlutAcademicScheduleParser``。解析入口 :func:`parse_personal_schedule` 与仓库一致，
一次完成"网格课程 + 底部调课表移除 + 补课时段追加"。

教务大节课表（``showTimetable.do?...&sectionType=BASE``）的单元格形如::

    <<课程名>>;课序号
    教室
    教师
    周次
    学时类型

一门课在同一格内会重复出现多个 ``<<课程名>>`` 块。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import replace
from typing import Dict, List, Optional, Tuple

from .models import CourseOccurrence, ScheduleAdjustment, ScheduleCourse
from .sections import map_display_section, parse_display_section_range
from .weektext import (
    is_week_text_active,
    normalize_week_text,
    week_text_without_week,
)

# --------------------------------------------------------------------------- #
# 正则
# --------------------------------------------------------------------------- #
CELL_REGEX = re.compile(r"(?is)<td\b([^>]*)>(.*?)</td>")
TIMETABLE_TABLE_REGEX = re.compile(
    r"(?is)<table\b(?=[^>]*\bid\s*=\s*[\"']timetable[\"'])[^>]*>.*?</table>"
)
ROW_REGEX = re.compile(r"(?is)<tr\b[^>]*>.*?</tr>")
TABLE_CELL_REGEX = re.compile(r"(?is)<t[dh]\b[^>]*>(.*?)</t[dh]>")
TABLE_CELL_WITH_ATTRS_REGEX = re.compile(r"(?is)<t[dh]\b([^>]*)>(.*?)</t[dh]>")
CELL_ID_REGEX = re.compile(r"\bid\s*=\s*[\"']([1-7])-[0-9]+[\"']")
GLUT_COURSE_TITLE_REGEX = re.compile(r"<<\s*(.+?)\s*>>")
ARRANGEMENT_PREFIX_REGEX = re.compile(
    r"((?:单周|双周|全周|[第0-9][第0-9,，、\-~－—至单双周\s]*?)?)"
    r"\s*星期([一二三四五六日天])"
)
PERIOD_NUMBER_REGEX = re.compile(r"第?\s*([0-9]{1,2})\s*[节大]")
SECTION_RANGE_REGEX = re.compile(
    r"第\s*([0-9]{1,2})\s*(?:[、,，]|至|~|-|－|—)\s*([0-9]{1,2})\s*节"
)
COMPACT_WEEK_TEXT_REGEX = re.compile(
    r"^[0-9]{1,2}(?:[-－—][0-9]{1,2})?(?:\s*[,，]\s*[0-9]{1,2}(?:[-－—][0-9]{1,2})?)*$"
)
WEEK_NUMBER_REGEX = re.compile(r"([0-9]{1,2})")
WEEK_SPAN_REGEX = re.compile(r"[0-9]{1,2}(?:[-－—][0-9]{1,2})?")
FRAGMENTED_ODD_EVEN_RE = re.compile(r"^[0-9]{1,2}(?:[-－—][0-9]{1,2})?(?:单|双)?(?:周)?$")
CONTINUATION_ROW_DATE_REGEX = re.compile(r"^[0-9]{2}-[0-9]{2}$")
TEXT_BASED_REGEX = re.compile(
    r"([一-龥a-zA-Z()+]+(?:[A-DB]|[Ⅰ-Ⅻ]|[1-9]|[一二三四五六七八九十]))"
    r"\s*[：:]?\s*([一-龥]{2,4}(?:老师)?)?[，,\s]*"
    r"([^，,\n]*(?:星期[一二三四五六日天]\s*第\s*[0-9]{1,2}[、,，至~\-－—]\s*[0-9]{1,2}\s*节[^，,\n]*)+)"
)

KNOWN_ADJUSTMENT_TYPES = {"调课", "补课", "停课", "代课"}
COURSE_TITLE_HEADERS = {"课程", "课程名", "课程名称"}
DAY_NAMES = ["一", "二", "三", "四", "五", "六", "日"]


# --------------------------------------------------------------------------- #
# 通用工具
# --------------------------------------------------------------------------- #
def html_to_lines(html: str) -> List[str]:
    """HTML 片段 → 去标签、解码实体、按行切分的文本行。

    顺序必须与仓库一致：**先去标签、后解码实体**。若提前解码，
    被转义的 ``&lt;&lt;课程名&gt;&gt;`` 会变成 ``<<课程名>>``，
    随后被 ``<[^>]+>`` 当成标签整段删掉。
    """
    text = re.sub(r"(?is)<(script|style|noscript).*?</\1>", "", html)
    text = re.sub(r"(?i)<br\s*/?>|</div>|</p>|</li>|</td>|</th>|</tr>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&#13;", "\n")
        .replace("&#10;", "\n")
    )
    lines = [line.strip() for line in text.splitlines()]
    return [line for line in lines if line and line != "-"]


def stable_id(value: str) -> str:
    """与仓库一致的稳定 ID：MD5 十六进制取前 12 位。"""
    return hashlib.md5(value.encode("utf-8")).hexdigest()[:12]


def normalize_room_key(value: str) -> str:
    """教室匹配键：去掉全部空白并转大写（不做模糊匹配）。"""
    return re.sub(r"\s+", "", value or "").upper()


def normalize_title_key(value: str) -> str:
    """课程名匹配键：去掉全部空白。"""
    return re.sub(r"\s+", "", value or "")


def looks_like_room(value: str) -> bool:
    clean = unicodedata.normalize("NFKC", (value or "").removeprefix("@").strip())
    if re.fullmatch(r"[0-9]{4,8}[A-Za-z]?", clean):
        return True
    if "线上" in clean or "馆" in clean or "教室" in clean or "楼" in clean:
        return True
    if 4 <= len(clean) <= 10 and any(c.isdigit() for c in clean) and any(c.isalpha() for c in clean):
        return True
    return False


def looks_like_explicit_week_text(value: str) -> bool:
    clean = (value or "").strip()
    if clean in ("单周", "双周"):
        return True
    return ("周" in clean or "单周" in clean or "双周" in clean) and bool(
        WEEK_SPAN_REGEX.search(clean)
    )


def looks_like_compact_week_text(value: str) -> bool:
    clean = (value or "").strip()
    if not COMPACT_WEEK_TEXT_REGEX.match(clean):
        return False
    for token in re.split(r"[,，]", clean):
        parts = re.split(r"[-－—]", token.strip())
        start = _to_int(parts[0]) if parts else None
        end = _to_int(parts[1]) if len(parts) > 1 else start
        if start is None or end is None or not (1 <= start <= 22) or not (1 <= end <= 22):
            return False
        if start > end:
            return False
    return True


def looks_like_fragmented_odd_even_week_text(value: str) -> bool:
    clean = (value or "").strip()
    if "单" not in clean and "双" not in clean:
        return False
    return all(FRAGMENTED_ODD_EVEN_RE.match(frag.strip()) for frag in re.split(r"[,，]", clean))


def looks_like_week_text(value: str) -> bool:
    return (
        looks_like_explicit_week_text(value)
        or looks_like_compact_week_text(value)
        or looks_like_fragmented_odd_even_week_text(value)
    )


def looks_like_class_hour_type(value: str) -> bool:
    return any(
        tag in value
        for tag in ("讲课学时", "实验学时", "上机学时", "实践学时", "课程学时")
    )


def day_of_week(value: str) -> Optional[int]:
    return {
        "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7,
    }.get((value or "").strip())


def parse_weekday_text(value: str) -> int:
    for name in DAY_NAMES:
        if name in value:
            return day_of_week(name) or 0
    return 0


def teacher_matches(adjustment_teacher: str, course_teacher: str, tolerant: bool) -> bool:
    """调课记录的教师与该课次教师是否指同一位老师。"""
    left = (adjustment_teacher or "").strip()
    right = (course_teacher or "").strip()
    if left == right:
        return True
    if not tolerant or not left or not right:
        return False
    right_tokens = set(re.split(r"\s+", right))
    return any(token and token in right_tokens for token in re.split(r"\s+", left))


def _to_int(value: str) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _read_int_attribute(tag: str, name: str) -> Optional[int]:
    match = re.search(rf"{name}\s*=\s*[\"']?([0-9]+)[\"']?", tag, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _read_day_attribute(tag: str) -> int:
    for index, name in enumerate(DAY_NAMES):
        if (
            f'data-day="{index + 1}"' in tag
            or f'day="{index + 1}"' in tag
            or f"星期{name}" in tag
            or f"周{name}" in tag
        ):
            return index + 1
    return 0


def _cell(cells: List[str], index: int) -> str:
    return cells[index] if 0 <= index < len(cells) else ""


def _first_int(text: str) -> int:
    match = WEEK_NUMBER_REGEX.search(text or "")
    return int(match.group(1)) if match else 0


def _index_of(items: List[str], predicate) -> int:
    for index, item in enumerate(items):
        if predicate(item):
            return index
    return -1


TIME_OF_DAY_REGEX = re.compile(r"([0-2][0-9]):([0-5][0-9])")


def extract_section_times(html: str, has_noon: Optional[bool] = None) -> Dict[int, Tuple[str, str]]:
    """从课表页的节次行首直接读取每个节次的起止时间。

    页面每行形如 ``<th>第1节<br>08:20<br>┆<br>09:05</th>``，其中已带真实时间。
    直接采信页面的时间比硬编码作息档案更可靠——同一学校不同校区（雁山 08:30 / 屏风 08:20）
    时间并不相同，靠猜容易整体偏移。

    返回 ``{内部节次号: ("HH:MM", "HH:MM")}``；页面没有时间信息时返回空字典。
    """
    if has_noon is None:
        has_noon = "中午" in html
    table = TIMETABLE_TABLE_REGEX.search(html)
    scope = table.group() if table else html

    times: Dict[int, Tuple[str, str]] = {}
    for row in ROW_REGEX.finditer(scope):
        cells = list(TABLE_CELL_WITH_ATTRS_REGEX.finditer(row.group()))
        if not cells:
            continue
        lines = html_to_lines(cells[0].group(2))
        if not lines:
            continue
        joined = " ".join(lines)
        section = map_display_section(joined, has_noon)
        if section is None:
            continue
        clock = TIME_OF_DAY_REGEX.findall(joined)
        if len(clock) < 2:
            continue
        times[section] = (f"{clock[0][0]}:{clock[0][1]}", f"{clock[-1][0]}:{clock[-1][1]}")
    return times


# --------------------------------------------------------------------------- #
# 解析器
# --------------------------------------------------------------------------- #
class GlutScheduleParser:
    """桂林理工大学课表解析器（大节课表 / 个人课表通用）。

    与参考仓库一致：一次解析即完成
    "网格课程 → 按调课表移除被调周次 → 去重追加补课时段"。
    """

    # ------------------------------------------------------------------ #
    # 入口
    # ------------------------------------------------------------------ #
    def parse_personal_schedule(self, html: str) -> List[ScheduleCourse]:
        if not html or not html.strip():
            raise ValueError("课表 HTML 不能为空")
        if self.looks_like_non_timetable_page(html):
            return []

        has_noon = "中午" in html
        adjustments = self.parse_adjustments(html, has_noon)

        grid = (
            self._parse_explicit_cells(html)
            + self._parse_glut_student_timetable_grid(html, has_noon)
            + self._parse_course_arrangement_rows(html, has_noon)
        )
        after_removal = self._apply_adjustment_removals(
            grid, adjustments, tolerant_teacher=True
        )

        deduped_makeups: List[ScheduleCourse] = []
        for adj in adjustments:
            if (
                adj.makeup_week <= 0
                or adj.makeup_day <= 0
                or adj.makeup_start_section <= 0
                or adj.makeup_end_section < adj.makeup_start_section
            ):
                continue
            makeup = self._to_makeup_course(adj)
            occ = makeup.occurrences[0]
            if not self._is_makeup_covered_by_grid(
                after_removal, makeup.title, occ.day_of_week,
                occ.start_section, occ.end_section, occ.note,
                adj.makeup_week, ignore_room=True,
            ):
                deduped_makeups.append(makeup)

        primary = self._merge_compatible_courses(after_removal + deduped_makeups)
        if primary:
            return primary

        secondary = self._distinct_by_id(
            self._parse_grid_table(html, has_noon) + self._parse_simple_table(html)
        )
        if secondary:
            return secondary

        return self._parse_text_based(html)

    def parse_adjustments(self, html: str, has_noon: Optional[bool] = None):
        """解析底部的调课 / 补课 / 停课 / 代课表。"""
        if not html:
            return []
        if has_noon is None:
            has_noon = "中午" in html
        return self._parse_supplemental_adjustment_rows(html, has_noon)

    @staticmethod
    def looks_like_non_timetable_page(html: str) -> bool:
        text = " ".join(html_to_lines(html))
        return (
            "综合审查结果" in text
            or "累计学分审查" in text
            or "学籍处理" in text
            or "成绩查询" in text
            or ("登录" in text and "密码" in text and "课表" not in text and "课程" not in text)
        )

    # ------------------------------------------------------------------ #
    # 主网格：id="timetable"
    # ------------------------------------------------------------------ #
    def _parse_glut_student_timetable_grid(self, html: str, has_noon: bool):
        match = TIMETABLE_TABLE_REGEX.search(html)
        if not match:
            return []
        timetable = match.group()
        rows = list(ROW_REGEX.finditer(timetable))[1:]
        if not rows:
            return []

        courses: List[ScheduleCourse] = []
        for row in rows:
            cells = list(TABLE_CELL_WITH_ATTRS_REGEX.finditer(row.group()))
            if len(cells) < 2:
                continue
            period_text = " ".join(html_to_lines(cells[0].group(2)))
            section = map_display_section(period_text, has_noon)
            if section is None:
                continue
            for index, cell in enumerate(cells[1:]):
                attrs = cell.group(1)
                lines = html_to_lines(cell.group(2))
                if not lines:
                    continue
                id_match = CELL_ID_REGEX.search(attrs)
                day = int(id_match.group(1)) if id_match else (index + 1)
                courses.extend(self._parse_glut_timetable_cell(lines, day, section))

        return self._merge_course_occurrences(courses)

    def _parse_glut_timetable_cell(self, lines, day, section):
        title_indexes = [i for i, line in enumerate(lines) if GLUT_COURSE_TITLE_REGEX.search(line)]
        if not title_indexes:
            return []

        results = []
        for position, title_index in enumerate(title_indexes):
            next_index = title_indexes[position + 1] if position + 1 < len(title_indexes) else len(lines)
            match = GLUT_COURSE_TITLE_REGEX.search(lines[title_index])
            title = match.group(1).strip() if match else ""
            if not title:
                continue

            raw_detail = [line.strip() for line in lines[title_index + 1:next_index]]
            raw_detail = [line for line in raw_detail if line]
            detail = [line for line in raw_detail if not looks_like_class_hour_type(line)]

            room_candidate = detail[0] if detail else ""
            room = room_candidate if looks_like_room(room_candidate) else ""
            teacher_start = 0 if not room else 1
            content = detail[teacher_start:]

            week_text = self._pick_grid_week_text(content, raw_detail)
            teacher = " ".join(
                line for line in content
                if line != week_text
                and not looks_like_room(line)
                and not looks_like_week_text(line)
                and not any(ch.isdigit() for ch in line)
            )
            hour_type = next((line for line in raw_detail if looks_like_class_hour_type(line)), "")

            course_id = "import-" + stable_id(f"glut-grid-{title}-{room}-{teacher}-{week_text}")
            results.append(self._build_course(
                course_id, title, room, teacher or "待确认",
                day, section, section, week_text, hour_type,
            ))
        return results

    @staticmethod
    def _pick_grid_week_text(content, raw_detail):
        """周次查找顺序（重要）：优先带"周"的显式周次，避免把实验课的课序"2-1"当成周次。"""
        rest = content[1:]
        for predicate in (
            looks_like_explicit_week_text,
            looks_like_fragmented_odd_even_week_text,
            looks_like_compact_week_text,
        ):
            found = next((line for line in rest if predicate(line)), None)
            if found is not None:
                return found
        found = next((line for line in raw_detail if looks_like_explicit_week_text(line)), None)
        if found is not None:
            return found
        for predicate in (looks_like_fragmented_odd_even_week_text, looks_like_compact_week_text):
            found = next((line for line in content if predicate(line)), None)
            if found is not None:
                return found
        if len(content) >= 2:
            return content[-1]
        return ""

    # ------------------------------------------------------------------ #
    # 次网格
    # ------------------------------------------------------------------ #
    def _parse_grid_table(self, html: str, has_noon: bool):
        rows = list(ROW_REGEX.finditer(html))
        if len(rows) < 2:
            return []
        first_row_text = rows[0].group()
        if not any(name in first_row_text for name in DAY_NAMES):
            return []

        courses: List[ScheduleCourse] = []
        for row_index, row in enumerate(rows[1:]):
            cells = [m.group(1) for m in TABLE_CELL_REGEX.finditer(row.group())]
            period_cell = " ".join(html_to_lines(cells[0])) if cells else ""
            section = map_display_section(period_cell, has_noon)
            if section is None:
                number = PERIOD_NUMBER_REGEX.search(period_cell)
                section = int(number.group(1)) if number else (row_index + 1)

            for col_index, cell_html in enumerate(cells[1:]):
                if col_index >= 7:
                    break
                lines = html_to_lines(cell_html)
                if not lines:
                    continue
                courses.extend(self._extract_courses_from_cell(lines, col_index + 1, section))

        return self._merge_course_occurrences(courses)

    def _extract_courses_from_cell(self, lines, day, section):
        non_empty = [line for line in lines if line.strip()]
        if not non_empty:
            return []
        title = non_empty[0]
        if not title:
            return []
        room_line = next((line for line in non_empty if looks_like_room(line)), None)
        room = room_line.removeprefix("@") if room_line else ""
        teacher = next(
            (line for line in non_empty
             if line != title and line != room.removeprefix("@") and not looks_like_week_text(line)),
            "",
        )
        week_text = next((line for line in non_empty if looks_like_week_text(line)), "")
        course_id = "import-" + stable_id(f"grid-{title}-{room}-{teacher}-{day}")
        return [self._build_course(
            course_id, title, room, teacher or "待确认", day, section, section, week_text
        )]

    def _parse_explicit_cells(self, html: str):
        results = []
        for match in CELL_REGEX.finditer(html):
            course = self._parse_cell(match.group(1), match.group(2))
            if course is not None:
                results.append(course)
        return results

    def _parse_cell(self, raw_cell, raw_body):
        day = (
            _read_int_attribute(raw_cell, "data-day")
            or _read_int_attribute(raw_cell, "day")
            or _read_int_attribute(raw_cell, "data-col")
            or _read_int_attribute(raw_cell, "col")
        )
        if not day:
            return None
        start = (
            _read_int_attribute(raw_cell, "data-start")
            or _read_int_attribute(raw_cell, "start")
            or _read_int_attribute(raw_cell, "data-section")
        )
        if not start:
            return None
        end = (
            _read_int_attribute(raw_cell, "data-end")
            or _read_int_attribute(raw_cell, "end")
            or _read_int_attribute(raw_cell, "data-end-section")
            or start
        )

        lines = html_to_lines(raw_body)
        if not lines:
            return None

        title = next(
            (line for line in lines
             if not line.startswith("@") and not looks_like_room(line) and not looks_like_week_text(line)),
            "",
        )
        if not title:
            return None
        room_line = next((line for line in lines if line.startswith("@") or looks_like_room(line)), None)
        room = room_line.removeprefix("@") if room_line else ""
        teacher = next(
            (line for line in lines
             if line != title and line.removeprefix("@") != room
             and not looks_like_week_text(line) and line.strip()),
            "",
        )
        week_text = next((line for line in lines if looks_like_week_text(line)), "")
        course_id = "import-" + stable_id(f"{title}-{room}-{teacher}-{day}-{start}-{end}")
        return self._build_course(
            course_id, title, room, teacher or "待确认", day, start, end, week_text
        )

    def _parse_course_arrangement_rows(self, html: str, has_noon: bool):
        title_index, teacher_index, time_index = 2, 3, 9
        results: List[ScheduleCourse] = []

        for row in ROW_REGEX.finditer(html):
            raw_cells = [m.group(1) for m in TABLE_CELL_REGEX.finditer(row.group())]
            if not raw_cells:
                continue
            cells = [" ".join(html_to_lines(cell)) for cell in raw_cells]

            header_title_index = _index_of(cells, lambda c: "课程名称" in c)
            header_time_index = _index_of(cells, lambda c: "上课时间" in c and "地点" in c)
            if header_title_index >= 0 and header_time_index >= 0:
                title_index = header_title_index
                found_teacher = _index_of(cells, lambda c: "任课教师" in c or "教师" in c)
                teacher_index = found_teacher if found_teacher >= 0 else teacher_index
                time_index = header_time_index
                continue

            title = _cell(cells, title_index).strip()
            if not title or title.replace(" ", "") in COURSE_TITLE_HEADERS:
                continue
            time_text = _cell(cells, time_index)
            teacher = _cell(cells, teacher_index).strip() or "待确认"

            base_id = "import-" + stable_id(f"{title}-{teacher}-{time_text}")
            occurrences = self._parse_arrangement_occurrences(base_id, time_text, has_noon)
            if not occurrences:
                continue
            results.append(ScheduleCourse(
                id=base_id, title=title, room=occurrences[0].note,
                teacher=teacher, occurrences=occurrences,
            ))
        return results

    def _parse_arrangement_occurrences(self, course_id, text, has_noon=True):
        prefixes = list(ARRANGEMENT_PREFIX_REGEX.finditer(text))
        occurrences = []
        for index, match in enumerate(prefixes):
            tail_end = prefixes[index + 1].start() if index + 1 < len(prefixes) else len(text)
            tail = text[match.end():tail_end].strip()
            tail_parts = re.split(r"\s+", tail, maxsplit=1)
            section_text = tail_parts[0] if tail_parts else ""
            week_text = match.group(1).strip() or "全周"
            day = day_of_week(match.group(2))
            if day is None:
                continue
            section_range = parse_display_section_range(section_text, has_noon)
            if section_range is None:
                continue
            start, end = section_range
            room = tail_parts[1].strip() if len(tail_parts) > 1 else ""
            occurrences.append(CourseOccurrence(
                id=f"{course_id}-occurrence-{index}",
                course_id=course_id,
                day_of_week=day,
                start_section=min(max(start, 1), 14),
                end_section=min(max(end, start), 14),
                week_text=week_text,
                note=room,
            ))
        return occurrences

    def _parse_simple_table(self, html: str):
        courses: List[ScheduleCourse] = []
        current_day = 0
        for row in ROW_REGEX.finditer(html):
            cells = [html_to_lines(m.group(1)) for m in TABLE_CELL_REGEX.finditer(row.group())]
            for lines in cells:
                joined = " ".join(lines)
                for index, name in enumerate(DAY_NAMES):
                    if f"星期{name}" in joined or f"周{name}" in joined:
                        current_day = index + 1
                row_day = _read_day_attribute(row.group())
                if row_day > 0:
                    current_day = row_day
                if current_day == 0:
                    continue
                title = next(
                    (line for line in lines
                     if 2 <= len(line) <= 30
                     and not line.startswith("@")
                     and not looks_like_room(line)
                     and not looks_like_week_text(line)
                     and not any(f"星期{n}" in line or f"周{n}" in line for n in DAY_NAMES)),
                    None,
                )
                if title is None:
                    continue
                room_line = next((line for line in lines if looks_like_room(line)), None)
                room = room_line.removeprefix("@") if room_line else ""
                week_text = next((line for line in lines if looks_like_week_text(line)), "")
                course_id = "import-" + stable_id(f"simple-{title}-{room}-{current_day}")
                courses.append(self._build_course(
                    course_id, title, room, "待确认", current_day, 0, 0, week_text
                ))
        return [course for course in courses if course.occurrences]

    def _parse_text_based(self, html: str):
        courses: List[ScheduleCourse] = []
        text = " ".join(html_to_lines(html))
        has_noon = "中午" in html
        for match in TEXT_BASED_REGEX.finditer(text):
            title = match.group(1).strip()
            teacher = match.group(2).strip() or "待确认"
            time_text = match.group(3)
            course_id = "import-" + stable_id(f"text-{title}-{teacher}")
            occurrences = self._parse_arrangement_occurrences(course_id, time_text, has_noon)
            if occurrences:
                courses.append(ScheduleCourse(
                    id=course_id, title=title, room=occurrences[0].note,
                    teacher=teacher, occurrences=occurrences,
                ))
        return courses

    # ------------------------------------------------------------------ #
    # 调课 / 补课 / 停课 / 代课
    # ------------------------------------------------------------------ #
    def _parse_supplemental_adjustment_rows(self, html: str, has_noon: bool):
        results: List[ScheduleAdjustment] = []
        pending_type = pending_title = pending_teacher = ""

        for row in ROW_REGEX.finditer(html):
            cells = [
                " ".join(html_to_lines(m.group(1))).strip()
                for m in TABLE_CELL_REGEX.finditer(row.group())
            ]
            if len(cells) < 5:
                continue
            type_cell = cells[0]

            if type_cell in KNOWN_ADJUSTMENT_TYPES and len(cells) >= 12:
                title = _cell(cells, 2)
                if not title.strip() or "课程名" in title:
                    continue
                teacher = _cell(cells, 4).strip() or "待确认"
                pending_type, pending_title, pending_teacher = type_cell, title, teacher
                adjustment = self._parse_adjustment_row(cells, type_cell, title, teacher, has_noon)
                if adjustment is not None:
                    if type_cell == "代课":
                        keep = adjustment.original_week > 0 or adjustment.makeup_week > 0
                    elif type_cell == "补课":
                        keep = adjustment.makeup_week > 0
                    else:
                        keep = True
                    if keep:
                        results.append(adjustment)
            elif (
                CONTINUATION_ROW_DATE_REGEX.match(type_cell)
                and len(cells) >= 10
                and pending_title
            ):
                adjustment = self._parse_continuation_row(
                    cells, pending_type, pending_title, pending_teacher, has_noon
                )
                if adjustment is not None:
                    results.append(adjustment)
        return results

    def _parse_adjustment_row(self, cells, type_, title, teacher, has_noon):
        base = len(cells) - 5
        original_week = _first_int(_cell(cells, base - 4))
        original_day = parse_weekday_text(_cell(cells, base - 3))
        original_range = parse_display_section_range(_cell(cells, base - 2), has_noon) or (0, 0)
        original_start, original_end = original_range
        original_room = _cell(cells, base - 1)
        makeup_week = _first_int(_cell(cells, base + 1))
        makeup_day = parse_weekday_text(_cell(cells, base + 2))
        makeup_range = parse_display_section_range(_cell(cells, base + 3), has_noon) or (0, 0)
        makeup_start, makeup_end = makeup_range
        makeup_room = _cell(cells, base + 4)

        if type_ == "补课" and makeup_week == 0 and original_week > 0:
            makeup_week, makeup_day = original_week, original_day
            makeup_start, makeup_end = original_start, original_end
            makeup_room = original_room
            original_week = original_day = original_start = original_end = 0
            original_room = ""

        has_valid = (original_week > 0 and original_day > 0) or (makeup_week > 0 and makeup_day > 0)
        if not has_valid and type_ != "停课":
            return None
        return ScheduleAdjustment(
            type=type_, title=title, teacher=teacher,
            original_week=original_week, original_day=original_day,
            original_start_section=original_start, original_end_section=original_end,
            original_room=original_room,
            makeup_week=makeup_week, makeup_day=makeup_day,
            makeup_start_section=makeup_start, makeup_end_section=makeup_end,
            makeup_room=makeup_room,
        )

    def _parse_continuation_row(self, cells, type_, title, teacher, has_noon):
        first_week = _first_int(_cell(cells, 1))
        first_day = parse_weekday_text(_cell(cells, 2))
        first_range = parse_display_section_range(_cell(cells, 3), has_noon) or (0, 0)
        first_room = _cell(cells, 4)
        second_week = _first_int(_cell(cells, 6))
        second_day = parse_weekday_text(_cell(cells, 7))
        second_range = parse_display_section_range(_cell(cells, 8), has_noon) or (0, 0)
        second_room = _cell(cells, 9)

        second_valid = second_week > 0 and second_day > 0
        first_valid = first_week > 0 and first_day > 0
        first_group_is_makeup = (
            type_ not in ("停课", "代课") and not second_valid and first_valid
        )

        if first_group_is_makeup:
            original_week = original_day = 0
            original_start = original_end = 0
            original_room = ""
            makeup_week, makeup_day = first_week, first_day
            makeup_start, makeup_end = first_range
            makeup_room = first_room
        else:
            original_week, original_day = first_week, first_day
            original_start, original_end = first_range
            original_room = first_room
            makeup_week, makeup_day = second_week, second_day
            makeup_start, makeup_end = second_range
            makeup_room = second_room

        has_valid = (original_week > 0 and original_day > 0) or (makeup_week > 0 and makeup_day > 0)
        if not has_valid and type_ != "停课":
            return None
        return ScheduleAdjustment(
            type=type_, title=title, teacher=teacher,
            original_week=original_week, original_day=original_day,
            original_start_section=original_start, original_end_section=original_end,
            original_room=original_room,
            makeup_week=makeup_week, makeup_day=makeup_day,
            makeup_start_section=makeup_start, makeup_end_section=makeup_end,
            makeup_room=makeup_room,
        )

    def _apply_adjustment_removals(self, courses, adjustments, require_original_room=False, tolerant_teacher=False):
        if not adjustments:
            return courses
        removal = [a for a in adjustments if a.type not in ("代课", "停课")]
        if not removal:
            return courses

        result = []
        for course in courses:
            updated: List[CourseOccurrence] = []
            for occurrence in course.occurrences:
                weeks = {
                    adj.original_week
                    for adj in removal
                    if self._adjustment_matches(adj, course, occurrence, require_original_room, tolerant_teacher)
                }
                if not weeks:
                    updated.append(occurrence)
                    continue
                remaining = [occurrence]
                for week in sorted(weeks):
                    expanded = []
                    for item in remaining:
                        expanded.extend(self._occurrence_without_week(item, week))
                    remaining = expanded
                updated.extend(remaining)
            if updated:
                result.append(replace(course, occurrences=updated))
        return result

    @staticmethod
    def _adjustment_matches(adj, course, occurrence, require_original_room, tolerant_teacher):
        occurrence_room = occurrence.note or course.room
        if require_original_room:
            room_matched = bool(adj.original_room) and (
                normalize_room_key(adj.original_room) == normalize_room_key(occurrence_room)
            )
        else:
            room_matched = occurrence_room.strip() == adj.original_room.strip()
        return (
            course.title.strip() == adj.title.strip()
            and teacher_matches(adj.teacher, course.teacher, tolerant_teacher)
            and occurrence.day_of_week == adj.original_day
            and occurrence.start_section == adj.original_start_section
            and occurrence.end_section == adj.original_end_section
            and room_matched
            and is_week_text_active(occurrence.week_text, adj.original_week)
        )

    @staticmethod
    def _occurrence_without_week(occurrence, week):
        return [
            replace(occurrence, id=f"{occurrence.id}-adjusted-{index}", week_text=text)
            for index, text in enumerate(week_text_without_week(occurrence.week_text, week))
        ]

    @staticmethod
    def _to_makeup_course(adj: ScheduleAdjustment):
        course_id = "import-" + stable_id(f"supplemental-{adj.title}-{adj.teacher}-{adj.makeup_room}")
        return ScheduleCourse(
            id=course_id, title=adj.title, room=adj.makeup_room, teacher=adj.teacher,
            occurrences=[CourseOccurrence(
                id=f"{course_id}-makeup-{adj.makeup_week}-{adj.makeup_day}-"
                   f"{adj.makeup_start_section}-{adj.makeup_end_section}",
                course_id=course_id,
                day_of_week=adj.makeup_day,
                start_section=adj.makeup_start_section,
                end_section=adj.makeup_end_section,
                week_text=f"第{adj.makeup_week}周",
                note=adj.makeup_room,
            )],
        )

    @staticmethod
    def _is_makeup_covered_by_grid(grid_courses, title, day, start, end, note, week, ignore_room=False):
        if week <= 0:
            return False
        for course in grid_courses:
            if course.title.strip() != title.strip():
                continue
            for occurrence in course.occurrences:
                if (
                    occurrence.day_of_week == day
                    and occurrence.start_section == start
                    and occurrence.end_section == end
                    and (ignore_room or occurrence.note.strip() == (note or "").strip())
                    and is_week_text_active(occurrence.week_text, week)
                ):
                    return True
        return False

    # ------------------------------------------------------------------ #
    # 合并
    # ------------------------------------------------------------------ #
    @staticmethod
    def _distinct_by_id(courses):
        seen = set()
        result = []
        for course in courses:
            if course.id in seen:
                continue
            seen.add(course.id)
            result.append(course)
        return result

    def _merge_course_occurrences(self, courses):
        groups = {}
        order = []
        for course in courses:
            if course.id not in groups:
                groups[course.id] = []
                order.append(course.id)
            groups[course.id].append(course)
        result = []
        for course_id in order:
            group = groups[course_id]
            first = group[0]
            merged = self._merge_adjacent_occurrences(
                [occurrence for course in group for occurrence in course.occurrences]
            )
            result.append(replace(first, occurrences=merged))
        return result

    def _merge_compatible_courses(self, courses):
        groups = {}
        order = []
        for course in courses:
            for split in self._split_course_by_occurrence_room(course):
                key = f"{split.title.strip()}|{split.teacher.strip()}|{split.room.strip()}"
                if key not in groups:
                    groups[key] = []
                    order.append(key)
                groups[key].append(split)

        result = []
        for key in order:
            group = groups[key]
            first = group[0]
            seen = set()
            occurrences = []
            for course in group:
                for occurrence in course.occurrences:
                    signature = (
                        f"{occurrence.day_of_week}|{occurrence.start_section}|"
                        f"{occurrence.end_section}|{occurrence.note}|{occurrence.week_text}"
                    )
                    if signature in seen:
                        continue
                    seen.add(signature)
                    occurrences.append(occurrence)
            merged = self._merge_adjacent_occurrences(occurrences)
            merged = [
                replace(o, id=f"{first.id}-occurrence-{i}", course_id=first.id)
                for i, o in enumerate(merged)
            ]
            result.append(replace(first, room=first.room, occurrences=merged))
        return result

    @staticmethod
    def _split_course_by_occurrence_room(course):
        groups = {}
        order = []
        for occurrence in course.occurrences:
            room = occurrence.note.strip() or course.room.strip()
            if room not in groups:
                groups[room] = []
                order.append(room)
            groups[room].append(occurrence)

        result = []
        for room in order:
            occurrences = groups[room]
            course_id = "import-" + stable_id(
                f"room-bound-{course.title}-{course.teacher}-{room}"
            )
            result.append(replace(
                course, id=course_id, room=room,
                occurrences=[
                    replace(o, id=f"{course_id}-occurrence-{i}", course_id=course_id, note=room)
                    for i, o in enumerate(occurrences)
                ],
            ))
        return result

    @staticmethod
    def _merge_adjacent_occurrences(occurrences):
        merged: List[CourseOccurrence] = []
        for occurrence in sorted(occurrences, key=lambda o: (o.day_of_week, o.start_section)):
            previous = merged[-1] if merged else None
            if (
                previous is not None
                and previous.day_of_week == occurrence.day_of_week
                and previous.week_text == occurrence.week_text
                and previous.note == occurrence.note
                and previous.end_section + 1 == occurrence.start_section
            ):
                merged[-1] = replace(previous, end_section=occurrence.end_section)
            elif (
                previous is None
                or previous.day_of_week != occurrence.day_of_week
                or previous.start_section != occurrence.start_section
                or previous.end_section != occurrence.end_section
                or previous.week_text != occurrence.week_text
                or previous.note != occurrence.note
            ):
                merged.append(occurrence)
        return merged

    @staticmethod
    def _build_course(course_id, title, room, teacher, day, start_section, end_section,
                      week_text, hour_type=""):
        effective_end = start_section if end_section == 0 else end_section
        effective_start = effective_end if start_section == 0 else start_section
        return ScheduleCourse(
            id=course_id, title=title, room=room, teacher=teacher or "待确认",
            hour_type=hour_type,
            occurrences=[CourseOccurrence(
                id=f"{course_id}-occurrence",
                course_id=course_id,
                day_of_week=min(max(day, 1), 7),
                start_section=min(max(effective_start, 1), 14),
                end_section=min(max(effective_end, effective_start), 14),
                week_text=week_text or "全周",
                note=room,
            )],
        )


def parse_personal_schedule(html: str) -> List[ScheduleCourse]:
    """便捷函数：解析课表 HTML 返回课程列表。"""
    return GlutScheduleParser().parse_personal_schedule(html)
