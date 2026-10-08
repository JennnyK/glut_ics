import unittest
from datetime import date

from glut_ics.academic import (
    AcademicClient,
    LoginError,
    _decode_body,
    academic_week_for_date,
    parse_calendar_alias,
    parse_calendar_end,
    parse_calendar_start,
    parse_current_week,
    parse_weekly_max_week,
    term_info,
)


class _Response:
    def __init__(self, status, body):
        self.status = status
        self.body = body
        self.headers = None


class TermInfoTest(unittest.TestCase):
    def test_guilin(self):
        self.assertEqual(term_info(2026, "spring", "guilin"), ("46", "1"))
        self.assertEqual(term_info(2026, "autumn", "guilin"), ("46", "2"))

    def test_nanning_autumn_is_three(self):
        self.assertEqual(term_info(2026, "autumn", "nanning"), ("46", "3"))


class ParserHelperTest(unittest.TestCase):
    def test_calendar_alias(self):
        self.assertEqual(
            parse_calendar_alias('{"schoolCalendarAlias":"2026春"}'), (2026, "spring")
        )
        self.assertEqual(
            parse_calendar_alias('{"schoolCalendarAlias":"2026秋"}'), (2026, "autumn")
        )
        self.assertIsNone(parse_calendar_alias("{}"))

    def test_calendar_dates_and_week(self):
        body = (
            '{"schoolCalendarStartDate":"2026-03-09",'
            '"schoolCalendarEndDate":"2026-07-12","whichweek":8}'
        )
        self.assertEqual(parse_calendar_start(body), date(2026, 3, 9))
        self.assertEqual(parse_calendar_end(body), date(2026, 7, 12))
        self.assertEqual(parse_current_week(body), 8)

    def test_weekly_max_week(self):
        html = (
            '<select name="whichWeek">'
            '<option value="1">1</option><option value="20" selected>20</option>'
            "</select>"
        )
        self.assertEqual(parse_weekly_max_week(html), 20)
        self.assertIsNone(parse_weekly_max_week("<html></html>"))

    def test_academic_week_for_date(self):
        start = date(2026, 3, 9)
        self.assertEqual(academic_week_for_date(date(2026, 3, 9), start), 1)
        self.assertEqual(academic_week_for_date(date(2026, 3, 16), start), 2)
        self.assertEqual(academic_week_for_date(date(2026, 4, 6), start), 5)


class LoginFlowTest(unittest.TestCase):
    def setUp(self):
        self.client = AcademicClient(campus="guilin")

    def test_success(self):
        self.client._open = lambda *a, **k: _Response(200, "<html>frame</html>")
        self.client._post = lambda *a, **k: _Response(200, "<html>frame</html>")
        self.client.login("u", "p")  # 不抛异常即通过

    def test_invalid_credentials(self):
        self.client._open = lambda *a, **k: _Response(200, "用户名或密码错误")
        self.client._post = lambda *a, **k: _Response(200, "")
        with self.assertRaises(LoginError) as ctx:
            self.client.login("u", "p")
        self.assertIn("学号或密码错误", str(ctx.exception))

    def test_captcha_falls_back_to_oa(self):
        self.client._open = lambda *a, **k: _Response(200, "请输入密码 验证码")
        self.client._post = lambda *a, **k: _Response(200, "请输入密码 验证码")
        called = {}

        def fake_oa(username, password):
            called["oa"] = True
            raise LoginError("OA 不可用")

        self.client.login_via_oa = fake_oa
        with self.assertRaises(LoginError) as ctx:
            self.client.login("u", "p")
        self.assertTrue(called.get("oa"))
        self.assertIn("验证码", str(ctx.exception))

    def test_captcha_falls_back_and_succeeds(self):
        self.client._open = lambda *a, **k: _Response(200, "欢迎登录 验证码")
        self.client._post = lambda *a, **k: _Response(200, "欢迎登录 验证码")
        self.client.login_via_oa = lambda username, password: None
        self.client.login("u", "p")  # OA 成功则整体成功


class DecodeBodyTest(unittest.TestCase):
    """桂林教务课表页是 GBK 编码；按 UTF-8 硬解会让所有中文变乱码、解析器全军覆没。"""

    def test_gbk_detected_from_meta(self):
        page = '<Meta http-equiv="Content-Type" Content="text/html; Charset=gbk"><p>中午1 第5节 6-11周</p>'
        decoded = _decode_body(page.encode("gbk"))
        self.assertIn("中午1", decoded)
        self.assertIn("第5节", decoded)

    def test_utf8_default(self):
        self.assertIn("周一 第1节", _decode_body("<p>周一 第1节</p>".encode("utf-8")))

    def test_header_charset_wins(self):
        class _Headers:
            def get(self, name):
                return "text/html; charset=gbk" if name == "Content-Type" else None

        self.assertEqual(_decode_body("中午".encode("gbk"), _Headers()), "中午")

    def test_never_raises_on_undecodable(self):
        self.assertIsInstance(_decode_body(b"\xff\xfe\x00garbage"), str)


if __name__ == "__main__":
    unittest.main()
