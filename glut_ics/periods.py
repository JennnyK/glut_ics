"""各校区作息时间表。

数值逐条移植自参考仓库 ``data/model/ScheduleModels.kt``：
  - 雁山校区（桂林默认） ``yanshan_class_periods()``
  - 屏风校区 ``pingfeng_class_periods()``
  - 南宁校区 ``nanning_class_periods()``
"""

from __future__ import annotations

from typing import Dict, List

from .models import ClassPeriod

#: 可供选择的作息档案
PROFILES = ("yanshan", "pingfeng", "nanning")


def yanshan_class_periods() -> List[ClassPeriod]:
    """雁山校区（2026 春起第 6-7 节课间 20 分钟，后续顺延 10 分钟）。"""
    return [
        ClassPeriod(1, "08:30", "09:15"),
        ClassPeriod(2, "09:20", "10:05"),
        ClassPeriod(3, "10:25", "11:10"),
        ClassPeriod(4, "11:15", "12:00"),
        ClassPeriod(5, "12:30", "13:15"),   # 中午1（教务节次=5）
        ClassPeriod(6, "13:20", "14:05"),   # 中午2（教务节次=6）
        ClassPeriod(7, "14:30", "15:15"),   # 第5节
        ClassPeriod(8, "15:20", "16:05"),   # 第6节
        ClassPeriod(9, "16:25", "17:10"),   # 第7节
        ClassPeriod(10, "17:15", "18:00"),  # 第8节
        ClassPeriod(11, "18:30", "19:15"),  # 第9节
        ClassPeriod(12, "19:20", "20:05"),  # 第10节
        ClassPeriod(13, "20:15", "21:00"),  # 第11节
        ClassPeriod(14, "21:05", "21:50"),  # 第12节
    ]


def pingfeng_class_periods() -> List[ClassPeriod]:
    """屏风校区。"""
    return [
        ClassPeriod(1, "08:20", "09:05"),
        ClassPeriod(2, "09:15", "10:00"),
        ClassPeriod(3, "10:20", "11:05"),
        ClassPeriod(4, "11:15", "12:00"),
        ClassPeriod(5, "12:30", "13:15"),
        ClassPeriod(6, "13:20", "14:05"),
        ClassPeriod(7, "14:30", "15:15"),
        ClassPeriod(8, "15:25", "16:10"),
        ClassPeriod(9, "16:20", "17:05"),
        ClassPeriod(10, "17:15", "18:00"),
        ClassPeriod(11, "18:30", "19:15"),
        ClassPeriod(12, "19:25", "20:10"),
        ClassPeriod(13, "20:20", "21:05"),
        ClassPeriod(14, "21:15", "22:00"),
    ]


def nanning_class_periods() -> List[ClassPeriod]:
    """南宁校区（无中午时段，第 1-11 节直排）。"""
    return [
        ClassPeriod(1, "08:40", "09:20"),
        ClassPeriod(2, "09:25", "10:05"),
        ClassPeriod(3, "10:25", "11:05"),
        ClassPeriod(4, "11:10", "11:50"),
        ClassPeriod(5, "14:30", "15:10"),
        ClassPeriod(6, "15:15", "15:55"),
        ClassPeriod(7, "16:05", "16:45"),
        ClassPeriod(8, "16:50", "17:30"),
        ClassPeriod(9, "19:30", "20:10"),
        ClassPeriod(10, "20:15", "20:55"),
        ClassPeriod(11, "21:00", "21:40"),
    ]


_BUILDERS = {
    "yanshan": yanshan_class_periods,
    "pingfeng": pingfeng_class_periods,
    "nanning": nanning_class_periods,
}


def class_periods(profile: str) -> List[ClassPeriod]:
    """按档案名返回作息表。"""
    try:
        return _BUILDERS[profile]()
    except KeyError:
        raise ValueError(
            f"未知作息档案 {profile!r}，可选：{', '.join(PROFILES)}"
        )


def period_map(profile: str) -> Dict[int, ClassPeriod]:
    """按内部节次号索引的作息表。"""
    return {p.section: p for p in class_periods(profile)}


def default_profile(campus: str) -> str:
    """校区默认作息档案。"""
    return "nanning" if campus == "nanning" else "yanshan"
