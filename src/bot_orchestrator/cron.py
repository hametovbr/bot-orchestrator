"""A small five-field cron parser: minute hour day-of-month month day-of-week.

Supports ``*``, lists (``1,15``), ranges (``1-5``), steps (``*/10``, ``8-18/2``), month and
weekday names (``jan``, ``mon``), and the shortcuts ``@hourly``, ``@daily``, ``@weekly``,
``@monthly``, ``@yearly``. As in classic cron, when both day-of-month and day-of-week are
restricted, a day matches if either matches.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

SHORTCUTS = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
DAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
FIELDS = (
    ("minute", 0, 59, None),
    ("hour", 0, 23, None),
    ("day of month", 1, 31, None),
    ("month", 1, 12, MONTHS),
    ("day of week", 0, 7, DAYS),
)


class CronError(ValueError):
    pass


def _value(text: str, low: int, names: list[str] | None, name: str) -> int:
    text = text.lower()
    if names and text in names:
        return names.index(text) + (1 if names is MONTHS else 0)
    if not text.isdigit():
        raise CronError(f"{name}: '{text}' is not a number")
    return int(text)


def _parse_field(text: str, name: str, low: int, high: int, names: list[str] | None) -> set[int]:
    values: set[int] = set()
    for part in text.split(","):
        if not part:
            raise CronError(f"{name}: empty item")
        base, _, step_text = part.partition("/")
        step = 1
        if step_text:
            if not step_text.isdigit() or int(step_text) == 0:
                raise CronError(f"{name}: bad step '{step_text}'")
            step = int(step_text)
        if base == "*":
            start, end = low, high
        elif "-" in base:
            a, b = base.split("-", 1)
            start, end = _value(a, low, names, name), _value(b, low, names, name)
        else:
            start = _value(base, low, names, name)
            end = high if step_text else start
        if not (low <= start <= high and low <= end <= high) or start > end:
            raise CronError(f"{name}: '{part}' is outside {low}-{high}")
        values.update(range(start, end + 1, step))
    return values


@dataclass(frozen=True)
class Cron:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # 0 = Sunday
    days_restricted: bool
    weekdays_restricted: bool

    def _day_matches(self, day: dt.date) -> bool:
        dom = day.day in self.days
        dow = (day.isoweekday() % 7) in self.weekdays
        if self.days_restricted and self.weekdays_restricted:
            return dom or dow
        if self.days_restricted:
            return dom
        if self.weekdays_restricted:
            return dow
        return True

    def next_after(self, moment: dt.datetime) -> dt.datetime:
        """The first matching minute strictly after ``moment`` (timezone-aware)."""
        current = moment.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
        limit = current + dt.timedelta(days=366 * 5)
        while current < limit:
            if current.month not in self.months:
                year = current.year + (current.month == 12)
                month = current.month % 12 + 1
                current = current.replace(year=year, month=month, day=1, hour=0, minute=0)
                continue
            if not self._day_matches(current.date()):
                current = (current + dt.timedelta(days=1)).replace(hour=0, minute=0)
                continue
            if current.hour not in self.hours:
                current = (current + dt.timedelta(hours=1)).replace(minute=0)
                continue
            if current.minute not in self.minutes:
                current += dt.timedelta(minutes=1)
                continue
            # Wall-clock arithmetic: the expression is read in the datetime's own timezone.
            return current
        raise CronError("the expression never matches")


def parse(expression: str) -> Cron:
    text = expression.strip().lower()
    text = SHORTCUTS.get(text, text)
    parts = text.split()
    if len(parts) != 5:
        raise CronError("use five fields: minute hour day-of-month month day-of-week")
    sets = [
        _parse_field(part, name, low, high, names)
        for part, (name, low, high, names) in zip(parts, FIELDS, strict=True)
    ]
    weekdays = {0 if d == 7 else d for d in sets[4]}
    cron = Cron(
        minutes=frozenset(sets[0]),
        hours=frozenset(sets[1]),
        days=frozenset(sets[2]),
        months=frozenset(sets[3]),
        weekdays=frozenset(weekdays),
        days_restricted=parts[2] != "*",
        weekdays_restricted=parts[4] != "*",
    )
    # Reject expressions that can never fire, such as 30 February.
    cron.next_after(dt.datetime(2000, 1, 1, tzinfo=dt.UTC))
    return cron


def next_run(expression: str, after: dt.datetime) -> dt.datetime:
    return parse(expression).next_after(after)
