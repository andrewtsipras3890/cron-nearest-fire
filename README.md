# Cron Nearest Fire

Finds the next time a five-field cron expression will fire, given a reference instant.

```python
from cron_nearest_fire import find_next_fire, parse_cron
import datetime as dt

# next fire after a fixed instant
nxt = find_next_fire("*/15 * * * *", from_time=dt.datetime(2024, 1, 1, 12, 7))
# -> datetime(2024, 1, 1, 12, 15)

# or read from an injected clock, so nothing touches the wall clock
nxt = find_next_fire("0 0 * * 7", clock=lambda: dt.datetime(2024, 1, 1, 0, 0))
# -> datetime(2024, 1, 7, 0, 0)

# parse_cron returns (minutes, hours, doms, months, dows, dom_restricted, dow_restricted)
minutes, *_ = parse_cron("*/15 * * * *")
```

## Why this exists

The problem is narrow: given a cron expression and a starting instant, what is
the next minute at which it matches? The standard libraries that do this also
do a lot more (job scheduling, subprocess management, signal handling). This
library does only the prediction, so it can be dropped into a process that
already has its own runtime.

The trade-off is speed versus correctness surface. `find_next_fire` walks
forward minute by minute from the reference. That is O(minutes walked), which
in the worst case (an expression that matches once a year) is about half a
million iterations. A sieve/table approach would be faster, but the bookkeeping
for the day-of-month / day-of-week interaction is where cron libraries
historically ship bugs, so the flat walk is kept deliberately.

## The awkward edge

When **both** day-of-month and day-of-week are restricted (neither is `*`),
this library ANDs them: a match requires both to hit. This is the System V cron
semantic. If you are used to the "either one or the other" V7 semantic, that
is the one place your intuition will differ. The choice is fixed; there is no
flag to switch it.

A second edge: an expression like `0 0 30 2 *` (Feb 30) can never match. The
walker will run for 367 days and then raise `CronError` rather than loop
forever.

## Performance

The window keeps a bounded buffer, so `push` is constant time and memory does not
grow with the length of the stream. `peak` and `trough` are linear in the window
size, which is the trade that keeps `push` cheap.

