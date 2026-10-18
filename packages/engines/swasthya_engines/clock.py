"""Minute-of-day arithmetic for local wall-clock times that may cross midnight."""

import re
from datetime import time

DAY = 24 * 60

_TIME = re.compile(
    r"^\s*(?P<h>\d{1,2})(?:[:.](?P<m>\d{2}))?\s*(?P<ampm>a\.?m\.?|p\.?m\.?)?\s*$", re.I
)


def parse_time(text: str) -> time:
    """'7' → 07:00, '7.30am' → 07:30, '10 pm' → 22:00, '19:30' → 19:30."""
    m = _TIME.match(text)
    if not m:
        raise ValueError(f"not a time: {text!r}")
    hour, minute = int(m["h"]), int(m["m"] or 0)
    ampm = (m["ampm"] or "").lower().replace(".", "")
    if minute > 59 or hour > 23 or (ampm and not 1 <= hour <= 12):
        raise ValueError(f"not a time: {text!r}")
    if ampm == "pm" and hour != 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    return time(hour, minute)


def minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def to_time(m: int) -> time:
    m %= DAY
    return time(m // 60, m % 60)


def after(base: int, t: int) -> int:
    """``t`` as minutes at or after ``base`` on a clock that wraps at midnight."""
    while t < base:
        t += DAY
    return t


def fmt(t: time) -> str:
    return t.strftime("%H:%M")


def fmt_duration(m: int) -> str:
    h, rest = divmod(m, 60)
    return f"{h}h{rest:02d}" if rest else f"{h}h"
