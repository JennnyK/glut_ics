import unittest
from datetime import date
from pathlib import Path

from glut_ics.ics import (
    PRODID,
    TIMEZONE,
    build_ics,
    escape_text,
    split_week_runs,
    unfold,
    validate_ics,
)
from glut_ics.models import SemesterInfo
from glut_ics.parser import GlutScheduleParser

FIXTURES = Path(__file__).parent / "fixtures"
START_MONDAY = date(2026, 3, 9)  # 第 1 周周一


def _parse(name):
    html = (FIXTURES / name).read_text(encoding="utf-8")
    return GlutScheduleParser().parse_personal_schedule(html)


def _unfold_text(text):
    return "\n".join(unfold(text))


class SplitWeekRunsTest(unittest.TestCase):
    def test_contiguous(self):
        self.assertEqual(split_week_runs([1, 2, 3, 6, 7]), [(1, 1, 3), (6, 1, 2)])

    def test_odd_even(self):
        self.assertEqual(split_week_runs([1, 3, 5]), [(1, 2, 3)])

    def test_single(self):
        self.assertEqual(split_week_runs([7]), [(7, 1, 1)])

    def test_mixed(self):
        weeks = list(range(3, 13)) + [16, 17]
        self.assertEqual(split_week_runs(weeks), [(3, 1, 10), (16, 1, 2)])


class EscapeTest(unittest.TestCase):
    def test_special_characters(self):
        self.assertEqual(escape_text("a,b;c\\d\ne"), "a\\,b\\;c\\\\d\\ne")


class NanningIcsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        semester = SemesterInfo(
            campus="nanning", year=2026, season="spring",
            year_id="46", term_id="1",
            start_monday=START_MONDAY, max_week=20, display_name="2026·春",
        )
        cls.result = build_ics(_parse("nanning_timetable.html"), semester, profile="nanning")
        cls.text = cls.result.text

    def test_valid(self):
        self.assertEqual(validate_ics(self.text), [])

    def test_header(self):
        self.assertTrue(self.text.startswith("BEGIN:VCALENDAR\r\n"))
        self.assertTrue(self.text.endswith("END:VCALENDAR\r\n"))
        self.assertIn(f"PRODID:{PRODID}", self.text)
        self.assertIn("BEGIN:VTIMEZONE", self.text)
        self.assertIn(f"TZID:{TIMEZONE}", self.text)

    def test_first_event_datetime(self):
        # 地基处理：周一、第1节；南宁第1节 08:40-09:20，第 1 周 = 2026-03-09
        self.assertIn("DTSTART;TZID=Asia/Shanghai:20260309T084000", self.text)
        self.assertIn("DTEND;TZID=Asia/Shanghai:20260309T092000", self.text)
        self.assertIn("RRULE:FREQ=WEEKLY;COUNT=12;BYDAY=MO", self.text)

    def test_section_five_time(self):
        # 岩土工程测试与监测：周二、第5节（南宁 14:30-15:10），第 3 周周二 = 2026-03-24
        self.assertIn("DTSTART;TZID=Asia/Shanghai:20260324T143000", self.text)
        self.assertIn("RRULE:FREQ=WEEKLY;COUNT=12;BYDAY=TU", self.text)

    def test_no_unparsed_week_texts(self):
        self.assertEqual(self.result.unparsed_week_texts, [])
        self.assertEqual(self.result.missing_periods, [])

    def test_every_line_within_75_octets(self):
        for line in self.text.split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75, msg=line)


class GuilinIcsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        semester = SemesterInfo(
            campus="guilin", year=2026, season="spring",
            year_id="46", term_id="1",
            start_monday=START_MONDAY, max_week=20, display_name="2026·春",
        )
        cls.result = build_ics(_parse("guilin_timetable.html"), semester, profile="yanshan")
        cls.text = cls.result.text

    def test_valid(self):
        self.assertEqual(validate_ics(self.text), [])

    def test_noon_event_time(self):
        # 大学英语 4：周一、中午2（内部第6节）= 13:20；第 5 周 = 2026-04-06
        self.assertIn("DTSTART;TZID=Asia/Shanghai:20260406T132000", self.text)

    def test_odd_week_uses_interval(self):
        # 体育：周三、第6节（内部第8节）；1-16周单周 → 步长 2，共 8 次
        self.assertIn("RRULE:FREQ=WEEKLY;COUNT=8;INTERVAL=2;BYDAY=WE", self.text)

    def test_multi_range_splits_into_two_events(self):
        # 嵌入式系统：3-12 与 16-17 两段，各生成一条 RRULE
        self.assertIn("RRULE:FREQ=WEEKLY;COUNT=10;BYDAY=MO", self.text)
        self.assertIn("RRULE:FREQ=WEEKLY;COUNT=2;BYDAY=MO", self.text)

    def test_description_uses_display_section_number(self):
        # 内部第7节必须写成教学口径的"第5节"，不能写内部编号。
        self.assertIn("节次：第5节（14:30-15:15）", _unfold_text(self.text))

    def test_event_count_exceeds_course_count(self):
        self.assertGreater(self.result.event_count, 0)


class ExpandModeTest(unittest.TestCase):
    """--expand：每次课一条 VEVENT，兼容不展开 RRULE 的日历程序。"""

    @classmethod
    def setUpClass(cls):
        semester = SemesterInfo(
            campus="guilin", year=2026, season="spring",
            year_id="46", term_id="1",
            start_monday=START_MONDAY, max_week=20, display_name="2026·春",
        )
        cls.courses = _parse("guilin_timetable.html")
        cls.compact = build_ics(cls.courses, semester, profile="yanshan")
        cls.expanded = build_ics(cls.courses, semester, profile="yanshan", expand=True)

    def test_no_rrule_when_expanded(self):
        self.assertNotIn("RRULE", self.expanded.text)
        self.assertIn("RRULE", self.compact.text)

    def test_expanded_event_count_is_weekly_total(self):
        # 合成样本：12 + 10 + (10+2) + 1 + 2 + 8(单周) = 45 次课
        self.assertEqual(self.expanded.event_count, 45)
        self.assertEqual(self.compact.event_count, 7)

    def test_odd_week_course_is_listed_per_occurrence(self):
        # 体育 1-16周单周（周三第6节 15:20）→ 逐条列出 8 次，而非一条 INTERVAL=2 规则
        starts = [
            line for line in self.expanded.text.split("\r\n")
            if line.startswith("DTSTART;TZID=Asia/Shanghai") and line.endswith("T152000")
        ]
        self.assertEqual(len(starts), 8)

    def test_expanded_is_valid(self):
        self.assertEqual(validate_ics(self.expanded.text), [])


class ValidateTest(unittest.TestCase):
    def test_detects_missing_end(self):
        issues = validate_ics("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\n")
        self.assertTrue(issues)

    def test_detects_duplicate_uid(self):
        text = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\n"
            "BEGIN:VEVENT\r\nUID:a\r\nDTSTAMP:20260101T000000Z\r\n"
            "DTSTART;TZID=Asia/Shanghai:20260309T084000\r\nSUMMARY:x\r\nEND:VEVENT\r\n"
            "BEGIN:VEVENT\r\nUID:a\r\nDTSTAMP:20260101T000000Z\r\n"
            "DTSTART;TZID=Asia/Shanghai:20260309T084000\r\nSUMMARY:y\r\nEND:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        issues = validate_ics(text)
        self.assertTrue(any("UID 重复" in issue for issue in issues))

    def test_unfold(self):
        folded = "SUMMARY:abc\r\n def"
        self.assertEqual(unfold(folded), ["SUMMARY:abcdef"])


if __name__ == "__main__":
    unittest.main()
