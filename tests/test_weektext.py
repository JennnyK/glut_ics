import unittest

from glut_ics.weektext import (
    academic_weeks_for_text,
    compact_week_numbers,
    is_all_weeks_text,
    week_text_without_week,
)


class WeekTextTest(unittest.TestCase):
    def test_range_with_suffix(self):
        self.assertEqual(academic_weeks_for_text("1-12周"), list(range(1, 13)))

    def test_multi_range_without_suffix(self):
        self.assertEqual(
            academic_weeks_for_text("3-12,16-17"),
            list(range(3, 13)) + [16, 17],
        )

    def test_single_week_forms(self):
        self.assertEqual(academic_weeks_for_text("第14周"), [14])
        self.assertEqual(academic_weeks_for_text("13周"), [13])
        self.assertEqual(academic_weeks_for_text("13"), [13])

    def test_odd_even(self):
        self.assertEqual(academic_weeks_for_text("单周", max_week=6), [1, 3, 5])
        self.assertEqual(academic_weeks_for_text("1-16周单周", max_week=8), [1, 3, 5, 7])
        self.assertEqual(academic_weeks_for_text("1-16周双周", max_week=8), [2, 4, 6, 8])

    def test_all_weeks(self):
        self.assertEqual(academic_weeks_for_text("全周", max_week=3), [1, 2, 3])
        self.assertEqual(academic_weeks_for_text("", max_week=2), [1, 2])
        self.assertTrue(is_all_weeks_text("全周"))
        self.assertTrue(is_all_weeks_text(" "))

    def test_unparsed_returns_empty(self):
        # "2-1" 起止颠倒，是实验课里常见的课序字段，绝不能兜底成全周。
        self.assertEqual(academic_weeks_for_text("2-1"), [])
        self.assertEqual(academic_weeks_for_text("不是周次"), [])

    def test_compact_week_numbers(self):
        self.assertEqual(compact_week_numbers([1, 2, 3, 6, 7]), ["1-3周", "6-7周"])
        self.assertEqual(compact_week_numbers([5]), ["第5周"])
        self.assertEqual(compact_week_numbers([]), [])

    def test_week_text_without_week(self):
        self.assertEqual(
            week_text_without_week("1-16周", 5),
            ["1-4周", "6-16周"],
        )
        self.assertEqual(week_text_without_week("第3周", 3), [])


if __name__ == "__main__":
    unittest.main()
