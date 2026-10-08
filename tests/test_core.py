import datetime as dt
import unittest

from cron_nearest_fire import find_next_fire, parse_cron
from cron_nearest_fire.core import CronError


class TestParse(unittest.TestCase):
    def test_basic(self):
        m, h, doms, mon, dow, dom_r, dow_r = parse_cron("*/15 * * * *")
        self.assertEqual(m, {0, 15, 30, 45})
        self.assertEqual(h, set(range(24)))
        self.assertEqual(dows := dow, set(range(7)))
        self.assertFalse(dom_r)
        self.assertFalse(dow_r)

    def test_sunday_zero_and_seven(self):
        _, _, _, _, dow, _, _ = parse_cron("* * * * 7")
        self.assertEqual(dow, {0})
        _, _, _, _, dow, _, _ = parse_cron("* * * * 0")
        self.assertEqual(dow, {0})
        _, _, _, _, dow, _, _ = parse_cron("* * * * 0,7")
        self.assertEqual(dow, {0})

    def test_step_with_range(self):
        m, *_ = parse_cron("10-30/5 * * * *")
        self.assertEqual(m, {10, 15, 20, 25, 30})

    def test_step_from_zero(self):
        m, *_ = parse_cron("0/10 * * * *")
        self.assertEqual(m, {0, 10, 20, 30, 40, 50})

    def test_bare_step(self):
        m, *_ = parse_cron("*/10 * * * *")
        self.assertEqual(m, {0, 10, 20, 30, 40, 50})

    def test_list(self):
        m, *_ = parse_cron("1,2,3,57 * * * *")
        self.assertEqual(m, {1, 2, 3, 57})

    def test_wrong_field_count(self):
        with self.assertRaises(CronError):
            parse_cron("* * * *")
        with self.assertRaises(CronError):
            parse_cron("* * * * * *")

    def test_out_of_range(self):
        with self.assertRaises(CronError):
            parse_cron("60 * * * *")
        with self.assertRaises(CronError):
            parse_cron("* 24 * * *")
        with self.assertRaises(CronError):
            parse_cron("* * 32 * *")
        with self.assertRaises(CronError):
            parse_cron("* * * 13 *")

    def test_descending_range(self):
        with self.assertRaises(CronError):
            parse_cron("5-1 * * * *")

    def test_non_positive_step(self):
        with self.assertRaises(CronError):
            parse_cron("*/0 * * * *")


class TestFindNext(unittest.TestCase):
    def test_every_minute(self):
        ref = dt.datetime(2024, 1, 1, 12, 0, 0)
        self.assertEqual(find_next_fire("* * * * *", from_time=ref),
                         dt.datetime(2024, 1, 1, 12, 1, 0))

    def test_strictly_after(self):
        # 12:00 matches * * * * * but must be skipped because from_time is it.
        ref = dt.datetime(2024, 1, 1, 12, 0, 0)
        self.assertEqual(find_next_fire("* * * * *", from_time=ref),
                         dt.datetime(2024, 1, 1, 12, 1, 0))

    def test_hour_rollover(self):
        ref = dt.datetime(2024, 1, 1, 12, 59, 0)
        self.assertEqual(find_next_fire("* * * * *", from_time=ref),
                         dt.datetime(2024, 1, 1, 13, 0, 0))

    def test_every_15_minutes(self):
        ref = dt.datetime(2024, 1, 1, 12, 7, 0)
        self.assertEqual(find_next_fire("*/15 * * * *", from_time=ref),
                         dt.datetime(2024, 1, 1, 12, 15, 0))

    def test_skips_non_matching_hour(self):
        ref = dt.datetime(2024, 1, 1, 11, 30, 0)
        self.assertEqual(find_next_fire("0 12 * * *", from_time=ref),
                         dt.datetime(2024, 1, 1, 12, 0, 0))

    def test_specific_day_of_month(self):
        ref = dt.datetime(2024, 1, 1, 0, 0, 0)
        self.assertEqual(find_next_fire("0 0 15 * *", from_time=ref),
                         dt.datetime(2024, 1, 15, 0, 0, 0))

    def test_february_thirtieth_unsatisfiable(self):
        # Feb 30 never exists; the guard must trip.
        ref = dt.datetime(2024, 1, 1, 0, 0, 0)
        with self.assertRaises(CronError):
            find_next_fire("0 0 30 2 *", from_time=ref)

    def test_day_of_week_saturday(self):
        # 2024-01-01 is a Monday. Next Saturday is 2024-01-06.
        ref = dt.datetime(2024, 1, 1, 0, 0, 0)
        self.assertEqual(find_next_fire("0 0 * * 6", from_time=ref),
                         dt.datetime(2024, 1, 6, 0, 0, 0))

    def test_day_of_week_sunday_as_seven(self):
        # 0 and 7 are both Sunday. 2024-01-07 is a Sunday.
        ref = dt.datetime(2024, 1, 1, 0, 0, 0)
        self.assertEqual(find_next_fire("0 0 * * 7", from_time=ref),
                         dt.datetime(2024, 1, 7, 0, 0, 0))

    def test_dom_and_dow_anded(self):
        # System V semantics: both restricted => both must match.
        # The 15th of any month AND a Friday. 2024-03-15 is a Friday.
        ref = dt.datetime(2024, 1, 1, 0, 0, 0)
        self.assertEqual(find_next_fire("0 0 15 * 5", from_time=ref),
                         dt.datetime(2024, 3, 15, 0, 0, 0))

    def test_dom_only_when_dow_wildcard(self):
        # When dow is wildcard, only dom matters, regardless of the weekday.
        ref = dt.datetime(2024, 1, 1, 0, 0, 0)
        self.assertEqual(find_next_fire("0 0 15 * *", from_time=ref),
                         dt.datetime(2024, 1, 15, 0, 0, 0))

    def test_month_rollover(self):
        ref = dt.datetime(2024, 1, 1, 0, 0, 0)
        self.assertEqual(find_next_fire("0 0 1 3 *", from_time=ref),
                         dt.datetime(2024, 3, 1, 0, 0, 0))

    def test_year_rollover(self):
        ref = dt.datetime(2024, 6, 1, 0, 0, 0)
        self.assertEqual(find_next_fire("0 0 1 1 *", from_time=ref),
                         dt.datetime(2025, 1, 1, 0, 0, 0))

    def test_aware_datetime_preserved(self):
        tz = dt.timezone(dt.timedelta(hours=2))
        ref = dt.datetime(2024, 1, 1, 12, 7, 0, tzinfo=tz)
        out = find_next_fire("*/15 * * * *", from_time=ref)
        self.assertEqual(out, dt.datetime(2024, 1, 1, 12, 15, 0, tzinfo=tz))
        self.assertIs(out.tzinfo, tz)

    def test_clock_function_used(self):
        # The clock callable is the dependency-injection seam for callers that
        # cannot rely on the wall clock.
        fixed = dt.datetime(2024, 1, 1, 12, 7, 0)
        out = find_next_fire("*/15 * * * *", clock=lambda: fixed)
        self.assertEqual(out, dt.datetime(2024, 1, 1, 12, 15, 0))

    def test_seconds_truncated(self):
        ref = dt.datetime(2024, 1, 1, 12, 7, 45, 123456)
        out = find_next_fire("*/15 * * * *", from_time=ref)
        self.assertEqual(out, dt.datetime(2024, 1, 1, 12, 15, 0))


if __name__ == "__main__":
    unittest.main()
