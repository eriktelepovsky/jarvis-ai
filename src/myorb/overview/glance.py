"""The day's timeline at the top of the report: events laid out between morning and evening."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from myorb.plugins import TimelineItem

DAY_START = 7
DAY_END = 20
# Free time is counted within the working day.
WORK_START = 9
WORK_END = 18
# The shortest free stretch worth deep work.
FOCUS_MINUTES = 60


@dataclass(frozen=True)
class Placed:
    item: TimelineItem
    left: float  # percent of the timeline's width
    width: float
    above: bool  # label above or below the bar, alternating so neighbours do not overlap
    past: bool


@dataclass(frozen=True)
class Glance:
    items: list[Placed]
    hours: list[tuple[int, float]]  # hour and its position in percent
    now: float | None  # position of "now", or None outside the window
    free_minutes: int
    count: int


def free_blocks(
    items: list[TimelineItem], start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    """The stretches between `start` and `end` that no item takes."""
    busy = sorted(
        (max(i.start, start), min(i.end, end)) for i in items if i.end > start and i.start < end
    )
    blocks, cursor = [], start
    for begin, stop in busy:
        if begin > cursor:
            blocks.append((cursor, begin))
        cursor = max(cursor, stop)
    if end > cursor:
        blocks.append((cursor, end))
    return blocks


def free_minutes(items: list[TimelineItem], now: datetime) -> int:
    """Minutes from now until the end of the working day that no event takes."""
    end = now.replace(hour=WORK_END, minute=0, second=0, microsecond=0)
    if now >= end:
        return 0
    return int(
        sum((stop - begin).total_seconds() for begin, stop in free_blocks(items, now, end)) // 60
    )


def focus_blocks(items: list[TimelineItem], now: datetime) -> list[tuple[datetime, datetime]]:
    """Free stretches of an hour or more in the rest of today's working hours, for deep work.
    They start on a five-minute mark, so not at 10:47."""
    start = max(now, now.replace(hour=WORK_START, minute=0, second=0, microsecond=0))
    start = start.replace(second=0, microsecond=0) + timedelta(minutes=-start.minute % 5)
    end = now.replace(hour=WORK_END, minute=0, second=0, microsecond=0)
    if start >= end:
        return []
    return [
        (begin, stop)
        for begin, stop in free_blocks(items, start, end)
        if stop - begin >= timedelta(minutes=FOCUS_MINUTES)
    ]


def glance(items: list[TimelineItem], now: datetime, day_over: bool = False) -> Glance:
    """The day of `now`; with `day_over`, a past day as it ended (no "now", nothing free)."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today = [i for i in items if i.end > midnight and i.start < midnight + timedelta(days=1)]
    first = min([DAY_START] + [i.start.hour for i in today])
    last = max([DAY_END] + [min(24, i.end.hour + (1 if i.end.minute else 0)) for i in today])
    start, span = midnight + timedelta(hours=first), (last - first) * 3600

    def position(moment: datetime) -> float:
        return max(0.0, min(100.0, (moment - start).total_seconds() / span * 100))

    placed = [
        Placed(
            item,
            position(item.start),
            max(0.8, position(item.end) - position(item.start)),
            above=index % 2 == 0,
            past=day_over or item.end <= now,
        )
        for index, item in enumerate(sorted(today, key=lambda i: i.start))
    ]
    hours = [
        (hour, position(midnight + timedelta(hours=hour))) for hour in range(first, last + 1, 2)
    ]
    inside = start <= now <= start + timedelta(seconds=span)
    return Glance(
        items=placed,
        hours=hours,
        now=position(now) if inside and not day_over else None,
        free_minutes=0 if day_over else free_minutes(today, now),
        count=len(today),
    )


def duration(minutes: int) -> str:
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"{hours} h {rest} m"
    return f"{hours} h" if hours else f"{rest} m"
