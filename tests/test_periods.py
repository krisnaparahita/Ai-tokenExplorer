import datetime
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import periods

TZ = datetime.timezone(datetime.timedelta(hours=8))
NOW = datetime.datetime(2026, 9, 25, 12, 30, tzinfo=TZ)  # a Friday


def span(spec):
    p = periods.resolve(spec, NOW)
    return p['start'].strftime('%m-%d %H:%M'), p['end'].strftime('%m-%d %H:%M')


class PeriodTest(unittest.TestCase):
    def test_rolling_windows_end_now(self):
        self.assertEqual(span('last-day'), ('09-24 12:30', '09-25 12:30'))
        self.assertEqual(span('24h'), ('09-24 12:30', '09-25 12:30'))
        self.assertEqual(span('7d'), ('09-18 12:30', '09-25 12:30'))
        self.assertEqual(span('last 14 days'), ('09-11 12:30', '09-25 12:30'))
        self.assertEqual(span('12h'), ('09-25 00:30', '09-25 12:30'))

    def test_calendar_periods(self):
        self.assertEqual(span('today'), ('09-25 00:00', '09-25 12:30'))
        self.assertEqual(span('yesterday'), ('09-24 00:00', '09-25 00:00'))
        self.assertEqual(span('this-week'), ('09-21 00:00', '09-25 12:30'))
        self.assertEqual(span('last-week'), ('09-14 00:00', '09-21 00:00'))
        self.assertEqual(span('this-month'), ('09-01 00:00', '09-25 12:30'))
        self.assertEqual(span('last-month'), ('08-01 00:00', '09-01 00:00'))

    def test_year_boundary_last_month(self):
        january = datetime.datetime(2026, 1, 15, 9, 0, tzinfo=TZ)
        p = periods.resolve('last-month', january)
        self.assertEqual((p['start'].year, p['start'].month, p['end'].year, p['end'].month), (2025, 12, 2026, 1))

    def test_explicit_dates_are_inclusive_of_the_last_day(self):
        self.assertEqual(span('2026-09-10'), ('09-10 00:00', '09-11 00:00'))
        self.assertEqual(span('2026-09-01..2026-09-07'), ('09-01 00:00', '09-08 00:00'))

    def test_ambiguous_or_invalid_periods_are_rejected(self):
        for bad in ('week', 'month', 'soon', '2026-13-40', '2026-09-07..2026-09-01', ''):
            with self.assertRaises(periods.PeriodError, msg=bad):
                periods.resolve(bad, NOW)

    def test_contains_is_half_open_and_needs_a_timestamp(self):
        p = periods.resolve('2026-09-10', NOW)
        self.assertTrue(periods.contains(p, '2026-09-10T00:00:00+08:00'))
        self.assertFalse(periods.contains(p, '2026-09-11T00:00:00+08:00'))
        self.assertFalse(periods.contains(p, None))
        self.assertFalse(periods.contains(p, 'not a time'))
        # a UTC timestamp is compared as an instant, not as local wall time
        self.assertTrue(periods.contains(p, '2026-09-09T16:00:00Z'))


if __name__ == '__main__':
    unittest.main()
