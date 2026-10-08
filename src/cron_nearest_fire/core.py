"""Core cron expression parser and next-fire-time finder.

A cron expression here is the classic five-field form:

    minute hour day-of-month month day-of-week

Field ranges:
    minute:         0-59
    hour:           0-23
    day-of-month:   1-31
    month:          1-12
    day-of-week:    0-7   (0 and 7 both mean Sunday)

Operators supported in each field:
    *       wildcard
    a,b,c   list of values
    a-b     inclusive range
    a/b     every b-th value starting at a (a defaulting to the field minimum)
    a-b/c   every c-th value inside the range a-b

The day-of-month and day-of-week fields are AND-ed when both are restricted
(both non-wildcard). This is the System V cron semantic, chosen because it is
the older and more surprising behaviour and therefore the one most worth
being explicit about. When only one of the two is restricted, that one
controls the day and the other is ignored.

Find_next_fire walks forward minute-by-minute from a supplied starting point.
It is simple and allocation-light. For the scales cron-next is used at (a
handful of lookups per process) this is fine; a sieve/table approach would be
faster on hot paths but adds bookkeeping that is easy to get wrong.
"""

from __future__ import annotations

import datetime as _dt
from typing import Callable, Optional, Set, Tuple


_Clock = Callable[[], _dt.datetime]

# (min, max, size) for each of the five fields, in canonical order.
# Day-of-week allows 0 and 7 for Sunday; we normalise 7 -> 0 internally and
# keep the set typed as 0-6.
_FIELD_BOUNDS = [
    (0, 59, 60),
    (0, 23, 24),
    (1, 31, 31),
    (1, 12, 12),
    (0, 7, 8),
]


class CronError(ValueError):
    """Raised when a cron expression is malformed."""


def _parse_field(expr: str, lo: int, hi: int) -> Tuple[Set[int], bool]:
    """Parse a single cron field.

    Returns (values, is_wildcard). is_wildcard is True only when the raw
    expression is a literal '*' — we need that distinction to decide whether
    day-of-month and day-of-week should be AND-ed.
    """
    expr = expr.strip()
    if not expr:
        raise CronError("empty field")
    if expr == "*":
        # Special case: * for the day-of-week field covers 0-7, which after
        # normalisation collapses to 0-6. Pre-populate so lookups succeed.
        if hi == 7:
            return set(range(7)), True
        return set(range(lo, hi + 1)), True

    values: Set[int] = set()
    for part in expr.split(","):
        part = part.strip()
        if not part:
            raise CronError("empty list element")

        step = 1
        if "/" in part:
            base, step_s = part.split("/", 1)
            part = base
            try:
                step = int(step_s)
            except ValueError:
                raise CronError(f"bad step: {step_s!r}")
            if step <= 0:
                raise CronError(f"non-positive step: {step}")

        if part in ("", "*"):
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            try:
                start, end = int(a), int(b)
            except ValueError:
                raise CronError(f"bad range: {part!r}")
            if start > end:
                raise CronError(f"descending range: {part!r}")
        else:
            try:
                start = int(part)
            except ValueError:
                raise CronError(f"bad value: {part!r}")
            end = hi if "/" in expr else start

        if start < lo or start > hi or end < lo or end > hi:
            raise CronError(f"value out of bounds [{lo},{hi}]: {part!r}")

        v = start
        while v <= end:
            # Normalise day-of-week 7 down to 0 (Sunday). The bounds tuple
            # for that field has hi==7 only for the dow field, so this is
            # a safe discriminator.
            if hi == 7 and v == 7:
                values.add(0)
            else:
                values.add(v)
            v += step

    if not values:
        raise CronError(f"field produced no values: {expr!r}")
    return values, False


def parse_cron(expr: str) -> Tuple[Set[int], Set[int], Set[int], Set[int], Set[int], bool, bool]:
    """Parse a five-field cron expression.

    Returns (minutes, hours, doms, months, dows, dom_restricted, dow_restricted).
    The two booleans are the non-wildcard flags for the day fields, used to
    decide whether to AND them.
    """
    if not isinstance(expr, str):
        raise CronError("expression must be a string")
    fields = expr.split()
    if len(fields) != 5:
        raise CronError(f"expected 5 fields, got {len(fields)}")

    parsed = []
    for i, f in enumerate(fields):
        lo, hi, _ = _FIELD_BOUNDS[i]
        try:
            values, is_wild = _parse_field(f, lo, hi)
        except CronError as e:
            raise CronError(f"field {i+1}: {e}") from None
        parsed.append((values, is_wild))

    return (
        parsed[0][0],
        parsed[1][0],
        parsed[2][0],
        parsed[3][0],
        parsed[4][0],
        not parsed[2][1],
        not parsed[4][1],
    )


def find_next_fire(
    expr: str,
    from_time: Optional[_dt.datetime] = None,
    clock: Optional[_Clock] = None,
) -> _dt.datetime:
    """Return the next datetime at which *expr* will fire.

    If *from_time* is given it is used as the reference instant; otherwise the
    current time is read from *clock* if supplied, else from the system clock.
    The returned datetime is strictly later than the reference — the reference
    minute itself is never a match, even if the expression matches it.

    A timezone-aware *from_time* yields a timezone-aware result; the same
    applies to a *clock* that returns aware datetimes. Naive in, naive out.
    """
    minutes, hours, doms, months, dows, dom_r, dow_r = parse_cron(expr)

    if from_time is not None:
        ref = from_time
    elif clock is not None:
        ref = clock()
    else:
        ref = _dt.datetime.now(tz=_dt.timezone.utc)

    # Truncate to the minute. Seconds and microseconds never participate.
    ref = ref.replace(second=0, microsecond=0)

    # AND semantics: when BOTH day fields are restricted, both must match.
    if dom_r and dow_r:
        day_ok = lambda d, w: d in doms and w in dows
    elif dom_r:
        day_ok = lambda d, w: d in doms
    elif dow_r:
        day_ok = lambda d, w: w in dows
    else:
        day_ok = lambda d, w: True

    # Start at the minute after the reference. This guarantees strictness even
    # when the reference itself would match, and bounds the walk at one full
    # year (see guard below).
    cur = ref + _dt.timedelta(minutes=1)

    # Guard against pathological expressions that can never match (e.g.
    # Feb 30). 366 days of minutes is the upper bound; one extra day gives
    # slack for a leap-year wrap-around.
    guard = ref + _dt.timedelta(days=367)

    while cur <= guard:
        if cur.minute in minutes and cur.hour in hours and cur.month in months:
            dom = cur.day
            # Python weekday(): Monday=0..Sunday=6. Cron dow: Sunday=0..Saturday=6.
            dow = (cur.weekday() + 1) % 7
            if day_ok(dom, dow):
                return cur
        cur += _dt.timedelta(minutes=1)

    raise CronError("no fire time within 367 days; expression may be unsatisfiable")
