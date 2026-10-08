import unittest

from glut_ics.sections import (
    map_display_section,
    offset_section_for_noon,
    parse_display_section_range,
)


class SectionTest(unittest.TestCase):
    def test_noon_offset(self):
        self.assertEqual(offset_section_for_noon(4, True), 4)
        self.assertEqual(offset_section_for_noon(5, True), 7)
        self.assertEqual(offset_section_for_noon(5, False), 5)

    def test_guilin_ranges(self):
        self.assertEqual(parse_display_section_range("第5、6节", True), (7, 8))
        self.assertEqual(parse_display_section_range("第1、2节", True), (1, 2))
        self.assertEqual(parse_display_section_range("中午", True), (5, 6))
        self.assertEqual(parse_display_section_range("中午1-第8节", True), (5, 10))
        self.assertEqual(parse_display_section_range("第1节-中午2", True), (1, 6))

    def test_nanning_no_noon(self):
        self.assertEqual(parse_display_section_range("第5、6节", False), (5, 6))
        self.assertIsNone(parse_display_section_range("中午", False))

    def test_invalid(self):
        self.assertIsNone(parse_display_section_range("第6、5节", True))
        self.assertIsNone(parse_display_section_range("没有任何节次", True))

    def test_map_display_section(self):
        self.assertEqual(map_display_section("第1节 08:30 ┆ 09:15", True), 1)
        self.assertEqual(map_display_section("第5节 14:30", True), 7)
        self.assertEqual(map_display_section("中午1 12:30", True), 5)
        self.assertEqual(map_display_section("中午2 13:20", True), 6)
        self.assertEqual(map_display_section("第5节 14:30", False), 5)


if __name__ == "__main__":
    unittest.main()
