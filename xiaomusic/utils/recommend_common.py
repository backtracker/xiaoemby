"""今日推荐的共享口径：常量、字符串枚举与纯数据助手（无网络 / DB / 副作用）。

行为画像、候选池与每日编排都用同一套日期解析、曝光去重和镜像条目形状，
避免同一规则在三个模块里各写一份。术语以 GLOSSARY.md 为准。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from enum import StrEnum
from typing import Any

# 口味画像与 MMR 的唯一起点值；config.py 与编排模块都从这里取。
MIN_TASTE_SAMPLES = 15
MMR_LAMBDA = 0.7


class RecommendMode(StrEnum):
    """当日档位；继承 str，可直接比较、写缓存与序列化。"""

    PR = "pr"
    MIXED = "mixed"
    V2 = "v2"


class RecommendSource(StrEnum):
    """歌单来源；继承 str，保持既有缓存 / 测试的字符串语义。"""

    LLM = "llm"
    LOCAL = "local"


MODE_FIELDS: tuple[str, ...] = tuple(mode.value for mode in RecommendMode)


def as_day(value: Any) -> date | None:
    """把 Emby ISO 时间戳 / date / datetime 归一为 date；非法值返回 None。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def age_days(value: Any, today: str | date | None) -> int:
    """时间戳距今天数；缺日期 / 非法值返回 99（视为足够旧）。"""
    day = as_day(value)
    reference = as_day(today)
    if day is None or reference is None:
        return 99
    return max((reference - day).days, 0)


def exposure_is_recent(value: Any, today: date | None, window_days: int) -> bool:
    """推荐曝光只做跨天去重复；无日期可证时保守当作仍在排除窗内。"""
    if value is None or value == "" or value == 0:
        return False
    if isinstance(value, (int, float)):
        return True
    parsed = as_day(value)
    if parsed is None and isinstance(value, (list, tuple)):
        dates = [day for entry in value if (day := as_day(entry)) is not None]
        parsed = max(dates) if dates else None
    if parsed is None or today is None:
        return True
    return (today - parsed).days < window_days


def days_ago(today: Any, day: Any) -> int | None:
    """两个日期字段相差的天数；任一字段非法返回 None。"""
    reference, target = as_day(today), as_day(day)
    if reference is None or target is None:
        return None
    return (reference - target).days


def library_by_id(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """把快照摊成 id → 条目（后出现的覆盖先出现的），不改原行。"""
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("id", ""))
        if key:
            result[key] = dict(row)
    return result


def active_items(snapshot: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """摊成 id → 条目：排除墓碑、按 Id 去重（首次出现为准）。"""
    result: dict[str, dict[str, Any]] = {}
    for row in snapshot:
        if not isinstance(row, Mapping) or row.get("tombstone"):
            continue
        key = str(row.get("id", ""))
        if key and key not in result:
            result[key] = dict(row)
    return result
