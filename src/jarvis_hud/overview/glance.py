"""The day's timeline at the top of the report: events laid out between morning and evening."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from jarvis_hud.plugins import TimelineItem

DAY_START = 7
DAY_END = 20
# Free time is counted until the end of the working day.
WORK_END = 18


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


def free_minutes(items: list[TimelineItem], now: datetime) -> int:
    """Minutes from now until the end of the working day that no event takes."""
    end = now.replace(hour=WORK_END, minute=0, second=0, microsecond=0)
    if now >= end:
        return 0
    busy = sorted(
        (max(i.start, now), min(i.end, end)) for i in items if i.end > now and i.start < end
    )
    free, cursor = 0.0, now
    for start, stop in busy:
        if start > cursor:
            free += (start - cursor).total_seconds()
        cursor = max(cursor, stop)
    if end > cursor:
        free += (end - cursor).total_seconds()
    return int(free // 60)


def glance(items: list[TimelineItem], now: datetime) -> Glance:
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
            past=item.end <= now,
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
        now=position(now) if inside else None,
        free_minutes=free_minutes(today, now),
        count=len(today),
    )


def duration(minutes: int) -> str:
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"{hours} h {rest} m"
    return f"{hours} h" if hours else f"{rest} m"
