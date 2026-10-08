"""节次号解析与"中午偏移"。

移植自参考仓库 ``data/model/ScheduleModels.kt`` 的 ``offsetSectionForNoon`` 与
``service/parser/AcademicScheduleParser.kt`` 的 ``parseDisplaySectionRange`` /
``mapDisplaySection``。

桂林本部在两个时段之间夹了"中午1/中午2"：

    教务节次   1 2 3 4   中午1 中午2   5 6 7 ... 12
    内部节次   1 2 3 4      5     6     7 8 9 ... 14

南宁没有中午时段，节次直排 1-11。
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

#: 匹配"中午1" / "中午2" / "第5节" / "5节" / "5"
SECTION_TOKEN_PATTERN = re.compile(r"中午[12]|第?\d{1,2}节?")

#: 从"第3节"这类文本里抓节次数字
PERIOD_NUMBER_PATTERN = re.compile(r"第?\s*(\d{1,2})\s*[节大]")


def offset_section_for_noon(section: int, has_noon: bool) -> int:
    """教务原始节次号 → 内部节次号。"""
    return section + 2 if (has_noon and section >= 5) else section


def parse_display_section_range(value: str, has_noon: bool) -> Optional[Tuple[int, int]]:
    """解析显示节次范围并映射为内部节次区间，失败返回 ``None``。

    门户除"第5、6节"外还会返回"中午"、"中午1-第8节"、"第1节-中午2"。
    """
    text = re.sub(r"\s+", "", value or "")
    if text == "中午":
        return (5, 6) if has_noon else None

    tokens = SECTION_TOKEN_PATTERN.findall(text)
    if not tokens or len(tokens) > 2:
        return None

    def map_endpoint(token: str) -> Optional[int]:
        if token == "中午1":
            return 5 if has_noon else None
        if token == "中午2":
            return 6 if has_noon else None
        match = re.search(r"\d{1,2}", token)
        if not match:
            return None
        number = int(match.group())
        if number <= 0:
            return None
        return offset_section_for_noon(number, has_noon)

    start = map_endpoint(tokens[0])
    if start is None:
        return None
    end = map_endpoint(tokens[1]) if len(tokens) > 1 else start
    if end is None:
        return None
    return (start, end) if end >= start else None


def map_display_section(period_text: str, has_noon: bool = True) -> Optional[int]:
    """把行首的节次文本（如"第1节"、"中午2"）映射为内部节次号。"""
    if "中午1" in period_text:
        return 5
    if "中午2" in period_text:
        return 6
    match = PERIOD_NUMBER_PATTERN.search(period_text or "")
    if not match:
        return None
    return offset_section_for_noon(int(match.group(1)), has_noon)


def display_section_label(section: int, has_noon: bool = True) -> str:
    """内部节次号 → 教学口径的显示名称（移植自仓库 ``displaySectionLabel``）。

    桂林：5→中午1，6→中午2，7-14→第5-12节；南宁：直排第N节。
    """
    if has_noon and section == 5:
        return "中午1"
    if has_noon and section == 6:
        return "中午2"
    if has_noon and section > 6:
        return f"第{section - 2}节"
    return f"第{section}节"


def display_section_range_label(start: int, end: int, has_noon: bool = True) -> str:
    """节次区间的显示名称，如 ``第7-8节`` / ``中午1``。"""
    if start == end:
        return display_section_label(start, has_noon)
    if not has_noon:
        return f"第{start}-{end}节"
    if start > 6 and end > 6:
        return f"第{start - 2}-{end - 2}节"
    return f"{display_section_label(start, has_noon)}-{display_section_label(end, has_noon)}"
