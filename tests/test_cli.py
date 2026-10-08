import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from glut_ics.cli import main
from glut_ics.ics import validate_ics

FIXTURES = Path(__file__).parent / "fixtures"


class CliOfflineTest(unittest.TestCase):
    def test_offline_conversion_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "schedule.ics"
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                code = main([
                    "--html", str(FIXTURES / "guilin_timetable.html"),
                    "--semester-start", "2026-03-09",
                    "--profile", "yanshan",
                    "--output", str(output),
                ])
            self.assertEqual(code, 0)
            text = output.read_text(encoding="utf-8")
            self.assertIn("BEGIN:VCALENDAR", text)
            self.assertEqual(validate_ics(text), [])
            self.assertIn("校验] 通过", captured.getvalue())

    def test_missing_semester_start_exits(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stdout(io.StringIO()):
                main(["--html", str(FIXTURES / "nanning_timetable.html")])

    def _convert(self, extra):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "s.ics"
            argv = [
                "--html", str(FIXTURES / "guilin_gbk_timetable.html"),
                "--semester-start", "2026-09-07",
                "--output", str(output),
            ] + extra
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(argv)
            self.assertEqual(code, 0)
            return output.read_text(encoding="utf-8")

    def test_guilin_defaults_to_yanshan_0830(self):
        # 用户反馈的回归点：桂林默认必须是雁山 08:30，而不是页面上的屏风 08:20。
        text = self._convert([])
        self.assertIn("20261012T083000", text)
        self.assertNotIn("20261012T082000", text)

    def test_explicit_pingfeng_profile(self):
        # 教务页面恒为屏风时间；屏风学生需显式指定，命中页面同款 08:20。
        text = self._convert(["--profile", "pingfeng"])
        self.assertIn("20261012T082000", text)

    def test_validate_only_reports_problem(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "broken.ics"
            broken.write_text("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["--validate-only", "--output", str(broken)])
            self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
