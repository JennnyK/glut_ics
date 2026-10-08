"""周次文本解析。

逐条移植自参考仓库 ``data/model/ScheduleModels.kt`` 与 ``data/model/WeekText.kt``。

支持形态：
  - 区间 + 周：``1-12周``、``5-14周``
  - 多区间省略"周"：``3-12,16-17``
  - 单周：``第11周``、``13周``、``13``
  - 单双周：``1-12周单周``、``单周``、``双周``
  - 整学期：``全周`` / 空文本
"""

from __future__ import annotations

import re
from typing import List

from .models import MAX_ACADEMIC_WEEK

#: 一段周次区间（如 ``8-16``）或单个数字（如 ``9``）
WEEK_SPAN_PATTERN = re.compile(r"\d{1,2}(?:[-－—]\d{1,2})?")


def normalize_week_text(week_text: str) -> str:
    """去掉"第"与空白，并把全角列表分隔符统一成半角。"""
    return (
        (week_text or "")
        .replace("第", "")
        .replace(" ", "")
        .replace("，", ",")
        .replace("、", ",")
        .replace("；", ",")
        .replace(";", ",")
        .strip()
    )


def is_all_weeks_text(week_text: str) -> bool:
    """只有空文本与"全周"才算真正的全周。

    其余任何无法识别的文本都必须与它区分开，否则"解析失败"会被当成"每周都有课"。
    """
    normalized = normalize_week_text(week_text)
    return normalized == "" or normalized == "全周"


def _segment_weeks(segment: str, max_week: int) -> List[int]:
    requires_odd = "单" in segment
    requires_even = "双" in segment

    spans: List[range] = []
    for match in WEEK_SPAN_PATTERN.finditer(segment):
        token = match.group().replace("－", "-").replace("—", "-")
        if "-" in token:
            head, _, tail = token.partition("-")
            start = _to_int(head)
            end = _to_int(tail)
            if start is not None and end is not None and start <= end:
                spans.append(range(start, end + 1))
        else:
            value = _to_int(token)
            if value is not None:
                spans.append(range(value, value + 1))

    if not spans and (requires_odd or requires_even):
        base = list(range(1, max_week + 1))
    else:
        base = [week for span in spans for week in span]

    result = []
    for week in base:
        if not 1 <= week <= max_week:
            continue
        if requires_odd and week % 2 == 0:
            continue
        if requires_even and week % 2 == 1:
            continue
        result.append(week)
    return result


def academic_weeks_for_text(week_text: str, max_week: int = MAX_ACADEMIC_WEEK) -> List[int]:
    """把周次文本展开成升序去重的周次列表。

    解析失败时返回**空列表**（而不是兜底成全周），调用方据此如实上报，
    避免"教务字段格式一变，整门课就在每一周都显示"。
    """
    if max_week < 1:
        return []
    normalized = normalize_week_text(week_text)
    if normalized == "" or normalized == "全周":
        return list(range(1, max_week + 1))

    weeks: List[int] = []
    for segment in normalized.split(","):
        weeks.extend(_segment_weeks(segment, max_week))
    return sorted(set(weeks))


def is_week_text_active(week_text: str, week_number: int, max_week: int = MAX_ACADEMIC_WEEK) -> bool:
    """判断某个课次在第 ``week_number`` 周是否上课。"""
    if week_number < 1:
        return False
    return week_number in academic_weeks_for_text(week_text, max_week)


def compact_week_numbers(weeks: List[int]) -> List[str]:
    """把周次编号列表压成 ``第5周`` / ``1-16周`` 这样的文本，连续编号合并成区间。"""
    if not weeks:
        return []
    ordered = sorted(set(weeks))
    ranges = []
    start = previous = ordered[0]
    for week in ordered[1:]:
        if week == previous + 1:
            previous = week
        else:
            ranges.append((start, previous))
            start = previous = week
    ranges.append((start, previous))
    return [
        f"第{first}周" if first == last else f"{first}-{last}周"
        for first, last in ranges
    ]


def week_text_without_week(week_text: str, removed_week: int) -> List[str]:
    """把周次文本去掉指定的一周，返回**可能是多段**的剩余文本。

    ``1-16周`` 去掉第 5 周后无法用一段文本表示，必须拆成 ``["第1-4周", "第6-16周"]``。
    所有周次都被去掉时返回空列表。
    """
    remaining = [w for w in academic_weeks_for_text(week_text) if w != removed_week]
    return compact_week_numbers(remaining)


def derived_academic_max_week(week_texts) -> int:
    """从一组周次文本反推学期最大周次；一个数字都没有时返回 0。"""
    best = 0
    for text in week_texts:
        for match in re.findall(r"\d{1,2}", text or ""):
            best = max(best, int(match))
    return best


def _to_int(value: str):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
