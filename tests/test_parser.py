import unittest
from pathlib import Path

from glut_ics.academic import _decode_body
from glut_ics.parser import GlutScheduleParser, extract_section_times, html_to_lines, looks_like_room

FIXTURES = Path(__file__).parent / "fixtures"


def _find(courses, title, room=None):
    for course in courses:
        if course.title == title and (room is None or course.room == room):
            return course
    raise AssertionError(f"未找到课程：{title} / {room}")


class ParserHelperTest(unittest.TestCase):
    def test_html_to_lines_keeps_escaped_title(self):
        # 先去标签、后解码实体，`&lt;&lt;...&gt;&gt;` 必须还原成 `<<...>>`。
        lines = html_to_lines("&lt;&lt;数字逻辑&gt;&gt;;2<br>06408D<br>卢佩")
        self.assertEqual(lines, ["<<数字逻辑>>;2", "06408D", "卢佩"])

    def test_looks_like_room(self):
        self.assertTrue(looks_like_room("06408D"))
        self.assertTrue(looks_like_room("@06104D"))
        self.assertTrue(looks_like_room("线上教学"))
        self.assertTrue(looks_like_room("实验楼"))
        self.assertFalse(looks_like_room("卢佩"))


class NanningSampleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        html = (FIXTURES / "nanning_timetable.html").read_text(encoding="utf-8")
        cls.parser = GlutScheduleParser()
        cls.courses = cls.parser.parse_personal_schedule(html)

    def test_no_embedded_title_markers(self):
        self.assertTrue(all("<<" not in course.title for course in self.courses))

    def test_first_lesson(self):
        course = _find(self.courses, "地基处理")
        self.assertEqual(course.teacher, "王俊璇")
        self.assertEqual(course.room, "6304D")
        self.assertEqual(course.hour_type, "课程学时")
        occurrence = course.occurrences[0]
        self.assertEqual(occurrence.day_of_week, 1)
        self.assertEqual((occurrence.start_section, occurrence.end_section), (1, 1))
        self.assertEqual(occurrence.week_text, "1-12周")

    def test_no_noon_offset_for_nanning(self):
        # 南宁无中午时段：第5节就是内部第5节，而不是桂林的 7。
        course = _find(self.courses, "岩土工程测试与监测")
        sections = {(o.day_of_week, o.start_section) for o in course.occurrences}
        self.assertEqual(sections, {(2, 5), (3, 5)})

    def test_single_week_range(self):
        course = _find(self.courses, "安全生产管理", "6502D")
        self.assertEqual(course.occurrences[0].week_text, "第14周")


class GuilinSampleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        html = (FIXTURES / "guilin_timetable.html").read_text(encoding="utf-8")
        cls.parser = GlutScheduleParser()
        cls.courses = cls.parser.parse_personal_schedule(html)

    def test_noon_sections_map_to_5_and_6(self):
        self.assertEqual(_find(self.courses, "数字逻辑").occurrences[0].start_section, 1)
        self.assertEqual(_find(self.courses, "大学英语 4").occurrences[0].start_section, 6)

    def test_sections_after_noon_shift_by_two(self):
        embedded = _find(self.courses, "嵌入式系统")
        self.assertEqual(embedded.occurrences[0].start_section, 7)
        self.assertEqual(embedded.occurrences[0].week_text, "3-12,16-17")

    def test_malformed_section_order_is_not_a_week(self):
        # 实验课块里的 "2-1" 是课序而非周次，周次必须取显式的"第11周"。
        course = _find(self.courses, "微机原理与接口技术")
        self.assertEqual(course.occurrences[0].week_text, "第11周")
        self.assertEqual(course.room, "014102S")

    def test_online_room(self):
        course = _find(self.courses, "大学生创新创业教育")
        self.assertEqual(course.room, "线上教学")
        self.assertEqual(course.teacher, "张威")

    def test_odd_week_text(self):
        course = _find(self.courses, "体育")
        self.assertEqual(course.occurrences[0].week_text, "1-16周单周")
        self.assertEqual(course.occurrences[0].start_section, 8)


class GbkEncodedPageTest(unittest.TestCase):
    """回归：真实教务课表页是 GBK 编码，必须先正确解码再解析。"""

    @classmethod
    def setUpClass(cls):
        raw = (FIXTURES / "guilin_gbk_timetable.html").read_bytes()
        cls.html = _decode_body(raw)
        cls.parser = GlutScheduleParser()
        cls.courses = cls.parser.parse_personal_schedule(cls.html)

    def test_chinese_survives_decoding(self):
        self.assertIn("中午1", self.html)
        self.assertIn("第5节", self.html)

    def test_courses_parsed(self):
        titles = {course.title for course in self.courses}
        self.assertEqual(titles, {"人工智能基础与应用", "Python程序设计", "英语语音B"})

    def test_week_text_survives(self):
        course = _find(self.courses, "Python程序设计")
        self.assertEqual(course.occurrences[0].week_text, "6-16双周")

    def test_noon_offset_applied(self):
        self.assertEqual(_find(self.courses, "Python程序设计").occurrences[0].start_section, 7)
        self.assertEqual(_find(self.courses, "英语语音B").occurrences[0].start_section, 10)

    def test_section_times_read_from_page(self):
        times = extract_section_times(self.html)
        self.assertEqual(times[1], ("08:20", "09:05"))
        self.assertEqual(times[5], ("12:30", "13:15"))
        self.assertEqual(times[7], ("14:30", "15:15"))
        self.assertEqual(times[10], ("17:15", "18:00"))


if __name__ == "__main__":
    unittest.main()
