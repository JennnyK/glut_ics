import contextlib
import io
import os
import subprocess
import sys
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


class CliConsoleEncodingTest(unittest.TestCase):
    def test_help_does_not_crash_on_non_utf8_console(self):
        # 回归：Windows 英文环境（cp1252）下 argparse 打印含中文的 --help 会
        # 抛 UnicodeEncodeError 并以退出码 1 结束。cp1252 在各大平台均可用，
        # 因此本用例可跨平台复现该问题。
        env = dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONUTF8="0")
        proc = subprocess.run(
            [sys.executable, "-m", "glut_ics", "--help"],
            capture_output=True, env=env,
        )
        self.assertEqual(
            proc.returncode, 0, proc.stderr.decode("utf-8", "replace")
        )


if __name__ == "__main__":
    unittest.main()
