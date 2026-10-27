"""Tasks that repeat: the rule, recognized in plain language (Slovak or English), and the next
due date.

A rule is kept as a short string: "day:N" (every N days), "weekdays" (Monday to Friday),
"week:N" (every N weeks), "month:N:D" (every N months on day D) or "year". The next date
counts from the due date, not from when the task was done: a task due on Mondays and done on
a Wednesday is next due on Monday.
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# How a weekday follows "every" / "každý, každú": plain (no diacritics), as the parser matches.
WEEKDAY_WORDS = {
    0: ["monday", "mondays", "pondelok"],
    1: ["tuesday", "tuesdays", "utorok"],
    2: ["wednesday", "wednesdays", "stredu"],
    3: ["thursday", "thursdays", "stvrtok"],
    4: ["friday", "fridays", "piatok"],
    5: ["saturday", "saturdays", "sobotu"],
    6: ["sunday", "sundays", "nedelu"],
}
UNITS = {
    "day": "day",
    "days": "day",
    "den": "day",
    "dni": "day",
    "dna": "day",
    "week": "week",
    "weeks": "week",
    "tyzden": "week",
    "tyzdne": "week",
    "tyzdnov": "week",
    "month": "month",
    "months": "month",
    "mesiac": "month",
    "mesiace": "month",
    "mesiacov": "month",
    "year": "year",
    "years": "year",
    "rok": "year",
    "roky": "year",
    "rokov": "year",
}
EVERY = r"\b(?:every|kazdy|kazdu|kazda|kazde)\s+"
# "every month on the 5th", "každý mesiac 5."
DAY_OF_MONTH = r"(?:\s+(?:on\s+the\s+)?(?P<dom>[0-3]?\d)(?:st|nd|rd|th|\.))?"

PATTERNS = [
    # every weekday, každý pracovný deň, cez pracovné dni, weekdays
    (
        "weekdays",
        re.compile(
            EVERY
            + r"(?:weekday|pracovny\s+den)\b"
            + r"|\bcez\s+pracovne\s+dni\b|\bweekdays\b|\bpracovne\s+dni\b"
        ),
    ),
    # every Monday, každý pondelok, každú stredu
    (
        "weekday",
        re.compile(
            EVERY
            + r"(?P<weekday>"
            + "|".join(w for words in WEEKDAY_WORDS.values() for w in words)
            + r")\b"
        ),
    ),
    # every 2 weeks, každé 3 mesiace, every day, každý mesiac 5.
    (
        "unit",
        re.compile(
            EVERY + r"(?:(?P<n>\d{1,3})\s+)?(?P<unit>" + "|".join(UNITS) + r")\b" + DAY_OF_MONTH
        ),
    ),
    # daily, weekly, monthly, yearly; denne, týždenne, mesačne, ročne
    (
        "word",
        re.compile(
            r"\b(?P<word>daily|weekly|monthly|yearly|annually|denne|tyzdenne|mesacne|rocne)\b"
        ),
    ),
]
WORDS = {
    "daily": "day",
    "denne": "day",
    "weekly": "week",
    "tyzdenne": "week",
    "monthly": "month",
    "mesacne": "month",
    "yearly": "year",
    "annually": "year",
    "rocne": "year",
}


@dataclass(frozen=True)
class Repeat:
    """A rule found in a task's text: the rule without its day of the month, which the first
    due date gives, and the first due date when the rule names one (a weekday, a day of the
    month)."""

    unit: str  # day, weekdays, week, month, year
    every: int
    weekday: int | None = None
    day_of_month: int | None = None


def find(plain: str) -> tuple[re.Match, Repeat] | None:
    """The first repeat phrase in a task's plain text (lower case, no diacritics)."""
    for kind, pattern in PATTERNS:
        match = pattern.search(plain)
        if not match:
            continue
        if kind == "weekdays":
            return match, Repeat("weekdays", 1)
        if kind == "weekday":
            weekday = next(d for d, words in WEEKDAY_WORDS.items() if match["weekday"] in words)
            return match, Repeat("week", 1, weekday=weekday)
        if kind == "word":
            return match, Repeat(WORDS[match["word"]], 1)
        unit = UNITS[match["unit"]]
        every = max(1, int(match["n"] or 1))
        dom = int(match["dom"]) if match["dom"] and unit == "month" else None
        if dom is not None and not 1 <= dom <= 31:
            dom = None
        return match, Repeat(unit, every, day_of_month=dom)
    return None


def first_due(repeat: Repeat, due: date | None, today: date) -> date:
    """When a repeating task is first due: the day its rule names, else the date given in the
    text, else today."""
    if repeat.weekday is not None:
        return today + timedelta(days=(repeat.weekday - today.weekday()) % 7)
    if repeat.day_of_month is not None:
        candidate = _on_day(today.year, today.month, repeat.day_of_month)
        return candidate if candidate >= today else _add_months(candidate, 1, repeat.day_of_month)
    start = due or today
    if repeat.unit == "weekdays" and start.weekday() >= 5:
        return start + timedelta(days=7 - start.weekday())  # the Monday after a weekend
    return start


def rule(repeat: Repeat, due: date) -> str:
    """The rule as kept with the task."""
    if repeat.unit == "weekdays":
        return "weekdays"
    if repeat.unit == "month":
        return f"month:{repeat.every}:{repeat.day_of_month or due.day}"
    if repeat.unit == "year":
        return "year"
    return f"{repeat.unit}:{repeat.every}"


def _on_day(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _add_months(start: date, months: int, day: int) -> date:
    index = start.year * 12 + start.month - 1 + months
    return _on_day(index // 12, index % 12 + 1, day)


def _advance(rule: str, due: date) -> date:
    unit, _, rest = rule.partition(":")
    if unit == "weekdays":
        step = due + timedelta(days=1)
        while step.weekday() >= 5:
            step += timedelta(days=1)
        return step
    if unit == "year":
        return _on_day(due.year + 1, due.month, due.day)
    every, _, day = rest.partition(":")
    n = max(1, int(every or 1))
    if unit == "day":
        return due + timedelta(days=n)
    if unit == "week":
        return due + timedelta(weeks=n)
    if unit == "month":
        return _add_months(due, n, int(day or due.day))
    raise ValueError(f"Unknown repeat rule: {rule}")


def next_due(rule: str, due: date, today: date) -> date:
    """The next due date after `due`, and after today: an occurrence missed is not one to do."""
    following = _advance(rule, due)
    while following <= today:
        following = _advance(rule, following)
    return following


def is_rule(value: str) -> bool:
    try:
        _advance(value, date(2026, 1, 31))
    except (ValueError, TypeError):
        return False
    return True


def describe(rule: str, due: date | None = None) -> str:
    """The rule in words: "every Monday", "every 2 weeks", "every month on the 5th"."""
    unit, _, rest = rule.partition(":")
    every, _, day = rest.partition(":")
    n = int(every or 1)
    if unit == "weekdays":
        return "every weekday"
    if unit == "year":
        return "every year"
    if unit == "day":
        return "every day" if n == 1 else f"every {n} days"
    if unit == "week":
        on = f" on {WEEKDAY_NAMES[due.weekday()]}" if due else ""
        if n == 1:
            return f"every {WEEKDAY_NAMES[due.weekday()]}" if due else "every week"
        return f"every {n} weeks{on}"
    if unit == "month":
        d = int(day or (due.day if due else 1))
        suffix = "th" if 10 <= d % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th")
        what = "every month" if n == 1 else f"every {n} months"
        return f"{what} on the {d}{suffix}"
    return rule


def label(task) -> str:
    """How a task repeats, in words; "" for one that does not."""
    return describe(task.repeat, task.due_date) if task.repeat else ""
