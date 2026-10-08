"""教务系统登录与课表抓取。

移植自参考仓库 ``service/academic/``：

  * ``AcademicLoginService.kt`` —— 直接登录（``j_acegi_security_check``）
    与 OA 统一认证兜底（``ca.glut.edu.cn/zfca``）；
  * ``AcademicUrlPolicy.kt`` —— 桂林 ``jw.glut.edu.cn``、南宁 ``jw.glutnn.cn`` 白名单；
  * ``AcademicSemesterImportService.kt`` —— 依次请求
    ``currcourse.jsdo``（取内部学号）、``showTimetable.do``（唯一课表数据源）、
    ``studentWeeklyTimetable.do``（仅取学期总周数）。

登录失败常见于需要验证码/统一身份认证，此时请改用 ``--html`` 离线解析已保存的课表页面。
"""

from __future__ import annotations

import http.cookiejar
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from .models import MAX_ACADEMIC_WEEK, MIN_ACADEMIC_WEEK

GUILIN_BASE_URL = "https://jw.glut.edu.cn"
NANNING_BASE_URL = "http://jw.glutnn.cn"
OA_BASE_URL = "http://ca.glut.edu.cn:8888"
USER_AGENT = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36"

LOGIN_PAGE_PATH = "/academic/affairLogin.do"
LOGIN_SUBMIT_PATH = "/academic/j_acegi_security_check"
FRAME_PAGE_PATH = "/academic/personal/framePage.do"
CURRCOURSE_PATH = "/academic/student/currcourse/currcourse.jsdo"
TIMETABLE_PATH = "/academic/manager/coursearrange/showTimetable.do"
WEEKLY_LANDING_PATH = "/academic/manager/coursearrange/studentWeeklyTimetable.do"

MAX_BODY_BYTES = 6 * 1024 * 1024
SEASONS = ("spring", "autumn")


class AcademicError(RuntimeError):
    """教务系统交互异常。"""


class LoginError(AcademicError):
    """登录失败（凭证错误 / 需要验证码 / 网络异常）。"""


@dataclass
class _Response:
    status: int
    body: str
    headers: object = None


@dataclass
class AcademicSession:
    """一次成功抓取后的结果快照。"""

    campus: str
    year: int
    season: str
    year_id: str
    term_id: str
    student_id: str
    timetable_html: str
    currcourse_body: str = ""
    frame_body: str = ""
    start_monday: Optional[date] = None
    end_date: Optional[date] = None
    current_week: Optional[int] = None
    weekly_max_week: Optional[int] = None
    display_name: str = ""


# --------------------------------------------------------------------------- #
# 客户端
# --------------------------------------------------------------------------- #
class AcademicClient:
    def __init__(self, campus: str = "guilin", base_url: Optional[str] = None, timeout: int = 15):
        if campus not in ("guilin", "nanning"):
            raise ValueError("campus 只能是 'guilin' 或 'nanning'")
        self.campus = campus
        self.base_url = (
            base_url or (GUILIN_BASE_URL if campus == "guilin" else NANNING_BASE_URL)
        ).rstrip("/")
        self.timeout = timeout
        self.cookie_jar = http.cookiejar.CookieJar()
        context = ssl.create_default_context()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookie_jar),
            urllib.request.HTTPSHandler(context=context),
        )
        self.opener.addheaders = [("User-Agent", USER_AGENT)]

    # ------------------------------------------------------------------ #
    # 底层请求
    # ------------------------------------------------------------------ #
    def _open(
        self,
        url: str,
        data: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
        method: Optional[str] = None,
    ) -> _Response:
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("User-Agent", USER_AGENT)
        request.add_header("Accept", "text/html,application/xhtml+xml,application/json,*/*")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                body = _decode_body(response.read(MAX_BODY_BYTES), response.headers)
                return _Response(response.status, body, response.headers)
        except urllib.error.HTTPError as error:
            body = _decode_body(error.read(MAX_BODY_BYTES), error.headers)
            return _Response(error.code, body, error.headers)
        except urllib.error.URLError as error:
            raise AcademicError(f"网络请求失败：{error.reason}") from error

    # ------------------------------------------------------------------ #
    # 登录
    # ------------------------------------------------------------------ #
    def login(self, username: str, password: str) -> None:
        """登录教务系统。

        与参考仓库 ``AcademicLoginService.silentLogin`` 一致：先走直接登录，
        仅在"需要验证码/统一身份认证"或网络异常时，才用 OA 统一认证兜底；
        学号密码错误则直接失败，不额外浪费 OA 尝试。
        """
        if not username or not password:
            raise LoginError("缺少学号或密码")

        status = self._login_direct(username, password)
        if status == "ok":
            return
        if status == "invalid":
            raise LoginError("学号或密码错误")

        try:
            self.login_via_oa(username, password)
        except (LoginError, AcademicError, urllib.error.URLError) as oa_error:
            raise LoginError(
                f"教务要求验证码/统一身份认证，自动登录失败（{oa_error}）；"
                "可改用 --html 离线解析已保存的课表页面"
            )

    def _login_direct(self, username: str, password: str) -> str:
        """直接登录，返回 ``"ok"`` / ``"invalid"`` / ``"captcha"``。"""
        login_page = self._open(self.base_url + LOGIN_PAGE_PATH)
        session_id = self._session_id(login_page.body)
        path = LOGIN_SUBMIT_PATH + (f";jsessionid={session_id}" if session_id else "")
        query = urllib.parse.urlencode(
            {"j_username": username, "j_password": password, "j_captcha": ""}
        )
        login_url = f"{self.base_url}{path}?{query}"

        response = self._open(login_url, headers={"Referer": self.base_url + LOGIN_PAGE_PATH})
        if response.status == 401 or ("用户名" in response.body and "密码错误" in response.body):
            return "invalid"
        if _looks_like_login_page(response.body):
            return "captcha"

        verify = self._post(self.base_url + FRAME_PAGE_PATH, referer=login_url)
        if verify.status in (401, 403):
            return "invalid"
        if _looks_like_login_page(verify.body):
            return "captcha"
        if not 200 <= verify.status < 300:
            raise AcademicError(f"教务系统返回 HTTP {verify.status}")
        return "ok"

    def login_via_oa(self, username: str, password: str) -> None:
        """OA 统一认证登录，成功后把 JSESSIONID 播种回主 CookieJar。

        流程（对应仓库 ``AcademicOALoginClient``）：
        1. GET  ``/zfca/login`` 取 lt 令牌；
        2. POST ``/zfca/login`` 换 CASTGC；
        3. GET  ``/zfca/tojw`` 手动跟随跳转链，直到拿到 JSESSIONID。

        第 2、3 步必须手动跟随跳转（读取 Location），因此使用禁用自动重定向的 opener。
        """
        opener = urllib.request.build_opener(
            _NoRedirectHandler(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        )
        opener.addheaders = [("User-Agent", USER_AGENT)]

        page = _read(opener, OA_BASE_URL + "/zfca/login", self.timeout)
        lt_match = re.search(r"""name=["']lt["']\s+value=["']([^"']+)["']""", page)
        if not lt_match:
            raise LoginError("OA 登录页缺少 lt 令牌")
        lt = lt_match.group(1)

        form = urllib.parse.urlencode({
            "_eventId": "submit",
            "j_captcha_response": "",
            "lt": lt,
            "password": password,
            "useValidateCode": "1",
            "username": username,
        }).encode("utf-8")
        login_request = urllib.request.Request(
            OA_BASE_URL + "/zfca/login", data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        _, headers, _ = _open_manual(opener, login_request, self.timeout)
        cookies = _merge_cookie_headers("", _set_cookie_header(headers))
        if not cookies or "login" in (headers.get("Location") or "").lower():
            raise LoginError("OA 登录被拒绝（凭证错误或仍需验证码）")

        current_url = OA_BASE_URL + "/zfca/tojw"
        for _ in range(5):
            hop = urllib.request.Request(current_url, headers={"Cookie": cookies})
            _, headers, _ = _open_manual(opener, hop, self.timeout)
            cookies = _merge_cookie_headers(cookies, _set_cookie_header(headers))
            location = headers.get("Location")
            if not location:
                break
            current_url = urllib.parse.urljoin(current_url, location)

        jsession = _extract_cookie_value(cookies, "JSESSIONID")
        if not jsession:
            raise LoginError("OA 登录未取得教务 JSESSIONID")
        self._seed_cookie("JSESSIONID", jsession, urllib.parse.urlparse(self.base_url).hostname)

    # ------------------------------------------------------------------ #
    # 抓取
    # ------------------------------------------------------------------ #
    def fetch_frame(self) -> str:
        return self._post(self.base_url + FRAME_PAGE_PATH).body

    def fetch_currcourse(self, year_id: str, term_id: str) -> str:
        query = urllib.parse.urlencode({"year": year_id, "term": term_id})
        return self._open(f"{self.base_url}{CURRCOURSE_PATH}?{query}").body

    def fetch_timetable(self, student_id: str, year_id: str, term_id: str) -> str:
        query = urllib.parse.urlencode({
            "id": student_id,
            "yearid": year_id,
            "termid": term_id,
            "timetableType": "STUDENT",
            "sectionType": "BASE",
        })
        return self._open(f"{self.base_url}{TIMETABLE_PATH}?{query}").body

    def fetch_weekly_landing(self, year_id: str, term_id: str) -> str:
        query = urllib.parse.urlencode({"yearid": year_id, "termid": term_id})
        return self._open(f"{self.base_url}{WEEKLY_LANDING_PATH}?{query}").body

    # ------------------------------------------------------------------ #
    # 高层编排
    # ------------------------------------------------------------------ #
    def fetch_semester(
        self,
        year: Optional[int] = None,
        season: Optional[str] = None,
        student_id: Optional[str] = None,
        start_monday: Optional[date] = None,
    ) -> AcademicSession:
        """抓取学期课表；学期缺省时从门户页面推断。"""
        frame_body = self.fetch_frame()

        alias = parse_calendar_alias(frame_body)
        if alias is not None:
            year = year if year is not None else alias[0]
            season = season if season is not None else alias[1]
        if year is None or season is None:
            raise AcademicError("无法确定学期，请用 --year/--season 显式指定")
        if season not in SEASONS:
            raise ValueError(f"season 只能是 {SEASONS}")

        year_id, term_id = term_info(year, season, self.campus)

        if not student_id:
            currcourse_body = self.fetch_currcourse(year_id, term_id)
            student_id = extract_student_id(currcourse_body)
            if not student_id:
                student_id = extract_student_id(frame_body)
        else:
            currcourse_body = ""
        if not student_id:
            raise AcademicError("无法从教务页面解析内部学号，请用 --student-id 指定")

        timetable_html = self.fetch_timetable(student_id, year_id, term_id)
        if _looks_like_login_page(timetable_html):
            raise AcademicError("登录状态已失效，请重新登录")

        session = AcademicSession(
            campus=self.campus,
            year=year,
            season=season,
            year_id=year_id,
            term_id=term_id,
            student_id=student_id,
            timetable_html=timetable_html,
            currcourse_body=currcourse_body,
            frame_body=frame_body,
            start_monday=start_monday,
            end_date=parse_calendar_end(frame_body),
            current_week=parse_current_week(frame_body),
            display_name=f"{year}·{'春' if season == 'spring' else '秋'}",
        )

        calendar_start = parse_calendar_start(frame_body)
        if session.start_monday is None:
            if calendar_start is not None:
                session.start_monday = normalize_to_monday(calendar_start)
            elif session.current_week and session.current_week > 0:
                today = date.today()
                session.start_monday = normalize_to_monday(today) - timedelta(
                    weeks=session.current_week - 1
                )

        try:
            landing = self.fetch_weekly_landing(year_id, term_id)
            session.weekly_max_week = parse_weekly_max_week(landing)
        except AcademicError:
            session.weekly_max_week = None

        return session

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    def _post(self, url: str, referer: str = "") -> _Response:
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if referer:
            headers["Referer"] = referer
        return self._open(url, data=b"", headers=headers, method="POST")

    def _session_id(self, login_body: str) -> str:
        for cookie in self.cookie_jar:
            if cookie.name.upper() == "JSESSIONID":
                return cookie.value
        match = re.search(r""";jsessionid=([^"'?;>\s]+)""", login_body, re.IGNORECASE)
        return match.group(1) if match else ""

    def _seed_cookie(self, name: str, value: str, host: Optional[str]) -> None:
        if not host:
            return
        cookie = http.cookiejar.Cookie(
            version=0, name=name, value=value, port=None, port_specified=False,
            domain=host, domain_specified=True, domain_initial_dot=False,
            path="/", path_specified=True, secure=False, expires=None,
            discard=False, comment=None, comment_url=None, rest={}, rfc2109=False,
        )
        self.cookie_jar.set_cookie(cookie)


# --------------------------------------------------------------------------- #
# 纯函数工具
# --------------------------------------------------------------------------- #
def term_info(year: int, season: str, campus: str) -> "tuple[str, str]":
    """门户年份 + 季节 → 教务 (yearid, termid)。"""
    year_id = str(year - 1980)
    if season == "spring":
        term_id = "1"
    else:
        term_id = "2" if campus == "guilin" else "3"
    return year_id, term_id


def normalize_to_monday(value: date) -> date:
    return value - timedelta(days=value.weekday())


def academic_week_for_date(value: date, start_monday: date) -> int:
    days = (value - normalize_to_monday(start_monday)).days
    return max(MIN_ACADEMIC_WEEK, days // 7 + 1)


def extract_student_id(html: str) -> str:
    match = re.search(r"showTimetable\.do\?id=(\d+)", html or "")
    return match.group(1) if match else ""


def parse_calendar_alias(body: str):
    match = re.search(r'"schoolCalendarAlias"\s*:\s*"(\d{4})([春秋])"', body or "")
    if not match:
        return None
    season = "spring" if match.group(2) == "春" else "autumn"
    return int(match.group(1)), season


def parse_calendar_start(body: str) -> Optional[date]:
    match = re.search(r'"schoolCalendarStartDate"\s*:\s*"(\d{4}-\d{2}-\d{2})"', body or "")
    return date.fromisoformat(match.group(1)) if match else None


def parse_calendar_end(body: str) -> Optional[date]:
    match = re.search(r'"schoolCalendarEndDate"\s*:\s*"(\d{4}-\d{2}-\d{2})"', body or "")
    return date.fromisoformat(match.group(1)) if match else None


def parse_current_week(body: str) -> Optional[int]:
    match = re.search(r'"whichweek"\s*:\s*(\d{1,2})', body or "")
    return int(match.group(1)) if match else None


def parse_weekly_max_week(html: str) -> Optional[int]:
    """从周次课表落地页 ``select[name=whichWeek]`` 读取最大周次。"""
    select = re.search(
        r"""(?is)<select\b[^>]*\bname\s*=\s*["']?whichWeek["']?[^>]*>(.*?)</select>""",
        html or "",
    )
    if not select:
        return None
    values = [int(v) for v in re.findall(r"""<option\b[^>]*\bvalue\s*=\s*["']?(\d+)""", select.group(1))]
    return max(values) if values else None


def _looks_like_login_page(body: str) -> bool:
    compact = re.sub(r"\s+", "", body or "")
    return any(
        marker in compact
        for marker in ("欢迎登录", "请输入密码", "账号登录", "统一身份认证", "验证码")
    )


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """禁用自动重定向，把 3xx 交由调用方读取 Location 手动跳转。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _decode_body(raw: bytes, headers=None) -> str:
    """按响应声明解码页面。

    桂林教务的课表页是 **GBK** 编码（``<Meta ... Charset=gbk>``），
    如果固定按 UTF-8 解码，"中午/节/周/星期"等中文会全变乱码，解析器将一无所获。
    解码顺序：HTTP 头 charset → 页面 ``<meta>`` 声明 → UTF-8 → GB18030 → 兜底。
    """
    charset = None
    if headers is not None:
        content_type = headers.get("Content-Type") or ""
        match = re.search(r"charset\s*=\s*[\"']?([\w\-]+)", content_type, re.IGNORECASE)
        if match:
            charset = match.group(1)

    if not charset:
        head = raw[:4096].decode("ascii", errors="ignore")
        match = re.search(r"charset\s*=\s*[\"']?([\w\-]+)", head, re.IGNORECASE)
        if match:
            charset = match.group(1)

    candidates = []
    for name in (charset, "utf-8", "gbk", "gb18030", "latin-1"):
        if name and name.lower() not in [c.lower() for c in candidates]:
            candidates.append(name)
    for name in candidates:
        try:
            return raw.decode(name)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def _open_manual(opener, request, timeout):
    """发起请求并把 3xx 当作普通响应返回，便于读取 Location 与 Set-Cookie。"""
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        return error.code, error.headers, ""
    with response:
        body = _decode_body(response.read(MAX_BODY_BYTES), response.headers)
        return response.status, response.headers, body


def _read(opener, url: str, timeout: int) -> str:
    return _open_manual(opener, urllib.request.Request(url), timeout)[2]


def _set_cookie_header(headers) -> str:
    if headers is None:
        return ""
    values = headers.get_all("Set-Cookie") or []
    return "; ".join(value.split(";")[0].strip() for value in values if value)


def _merge_cookie_headers(existing: str, incoming: str) -> str:
    store: Dict[str, str] = {}
    for chunk in (existing, incoming):
        for segment in (chunk or "").split(";"):
            segment = segment.strip()
            if "=" not in segment:
                continue
            name, _, value = segment.partition("=")
            store[name.strip()] = value.strip()
    return "; ".join(f"{name}={value}" for name, value in store.items())


def _extract_cookie_value(header: str, name: str) -> str:
    for segment in (header or "").split(";"):
        key, _, value = segment.strip().partition("=")
        if key.strip().upper() == name.upper():
            return value.strip()
    return ""
