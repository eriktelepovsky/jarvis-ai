"""Recognizes a due date and time in a task written in plain language (Slovak or English).

"zavolať účtovníčke zajtra 10:00" -> title "zavolať účtovníčke", due tomorrow at 10:00.
Rules only, no AI: what is not recognized stays in the title.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, time, timedelta


@dataclass(frozen=True)
class ParsedTask:
    title: str
    due_date: date | None = None
    due_time: time | None = None


def _plain(text: str) -> str:
    """Lower case without diacritics, so "štvrtok" and "stvrtok" match alike."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


RELATIVE_DAYS = {
    "today": 0,
    "dnes": 0,
    "tonight": 0,
    "dnes vecer": 0,
    "tomorrow": 1,
    "zajtra": 1,
    "day after tomorrow": 2,
    "pozajtra": 2,
}

WEEKDAYS = {
    # Full names only: abbreviations like "sun" or "ut" are too often ordinary words.
    0: ["monday", "pondelok"],
    1: ["tuesday", "utorok"],
    2: ["wednesday", "streda", "stredu"],
    3: ["thursday", "stvrtok"],
    4: ["friday", "piatok"],
    5: ["saturday", "sobota", "sobotu"],
    6: ["sunday", "nedela", "nedelu"],
}

# Words that only introduce a date or time and are dropped together with it.
LEADING = r"(?:\b(?:on|at|by|due|o|do|na|v|vo|ku)\s+)?"
WORD_END = r"(?![\w:.])"

TIME_PATTERNS = [
    # 10:00, 9:30, o 10:00, at 9:30
    re.compile(LEADING + r"(?<![\d.])(?P<h>[01]?\d|2[0-3]):(?P<m>[0-5]\d)" + WORD_END),
    # 10am, 3 pm, at 3pm
    re.compile(LEADING + r"(?<![\d.:])(?P<h>1[0-2]|0?[1-9])\s?(?P<ampm>am|pm)\b"),
    # "o 10", "at 10" (hours only, after a word that makes it a time)
    re.compile(r"(?:\bo|\bat)\s+(?P<h>[01]?\d|2[0-3])(?:\s?h)?" + WORD_END),
]

DATE_PATTERNS = [
    # 2026-10-24
    re.compile(LEADING + r"\b(?P<y>\d{4})-(?P<mo>\d{1,2})-(?P<d>\d{1,2})\b"),
    # 24.10.2026, 24. 10. 2026, 24.10., 24.10, 24/10, 24/10/2026
    re.compile(
        LEADING
        + r"(?<![\d:])(?P<d>[0-3]?\d)\s?[./]\s?(?P<mo>[01]?\d)(?:\s?[./]\s?(?P<y>\d{4}))?\.?"
        + WORD_END
    ),
]


def _take(pattern: re.Pattern, text: str, plain: str) -> tuple[re.Match | None, str, str]:
    """Find the pattern in the plain text and remove the same span from both texts."""
    match = pattern.search(plain)
    if not match:
        return None, text, plain
    return match, *_remove(match, text, plain)


def _remove(match: re.Match, text: str, plain: str) -> tuple[str, str]:
    start, end = match.span()
    return text[:start] + " " + text[end:], plain[:start] + " " + plain[end:]


def _date_from(match: re.Match, today: date) -> date | None:
    day, month = int(match["d"]), int(match["mo"])
    year = int(match["y"]) if match["y"] else today.year
    try:
        due = date(year, month, day)
    except ValueError:
        return None
    if not match["y"] and due < today:
        # "24.1." in October means next January.
        due = due.replace(year=year + 1)
    return due


def _phrase(words: list[str]) -> re.Pattern:
    alternatives = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(LEADING + r"\b(?:" + alternatives + r")\b")


def parse_task(text: str, today: date) -> ParsedTask:
    # Matching runs on a plain copy (no diacritics) of the same length, so spans line up.
    plain = _plain(text)
    if len(plain) != len(text):
        plain = "".join(_plain(c) or c for c in text)
    due_date: date | None = None
    due_time: time | None = None

    for pattern in TIME_PATTERNS:
        match, text, plain = _take(pattern, text, plain)
        if match:
            hour = int(match["h"])
            if match.groupdict().get("ampm") == "pm" and hour < 12:
                hour += 12
            elif match.groupdict().get("ampm") == "am" and hour == 12:
                hour = 0
            minute = int(match.groupdict().get("m") or 0)
            due_time = time(hour, minute)
            break

    for words, offset in sorted(RELATIVE_DAYS.items(), key=lambda item: -len(item[0])):
        match, text, plain = _take(_phrase([words]), text, plain)
        if match:
            due_date = today + timedelta(days=offset)
            break

    if due_date is None:
        for weekday, words in WEEKDAYS.items():
            match, text, plain = _take(_phrase(words), text, plain)
            if match:
                # The next such day, today included.
                due_date = today + timedelta(days=(weekday - today.weekday()) % 7)
                break

    if due_date is None:
        for pattern in DATE_PATTERNS:
            # Only a valid date is taken out of the title: "verzia 2.0" stays as written.
            match = next((m for m in pattern.finditer(plain) if _date_from(m, today)), None)
            if match:
                due_date = _date_from(match, today)
                text, plain = _remove(match, text, plain)
                break

    if due_time is not None and due_date is None:
        # A time alone means today.
        due_date = today

    title = re.sub(r"\s+", " ", text).strip(" ,.-–")
    return ParsedTask(title=title, due_date=due_date, due_time=due_time)
