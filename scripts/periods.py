"""Time-period selection shared by the query CLI and the HTML report. Standard library only.

A period is resolved to a half-open interval [start, end) in the computer's local
time zone, plus a human label, so an answer can always state exactly what was
covered. Rows without a usable timestamp never match a period.

Accepted forms (case-insensitive; spaces and underscores count as hyphens):

  today | yesterday
  last-day | 24h | Nh | last-N-hours     rolling window ending now
  Nd | last-N-days | N-days              rolling N x 24 hours ending now
  this-week                              Monday 00:00 to now
  last-week                              the previous Monday to Sunday
  this-month | last-month                calendar months
  YYYY-MM-DD                             one calendar day
  YYYY-MM-DD..YYYY-MM-DD                 inclusive range of days

There is deliberately no bare ``week`` or ``month``: people mean different things
by them. Use ``7d`` for a rolling week or ``last-week`` for the previous calendar week.
"""
import datetime
import re

VALID = ('today, yesterday, last-day, 24h, Nh, last-N-hours, Nd, last-N-days, this-week, last-week, '
         'this-month, last-month, YYYY-MM-DD, or YYYY-MM-DD..YYYY-MM-DD')


class PeriodError(ValueError):
    pass


def _day(text, tz):
    try:
        d = datetime.date.fromisoformat(text)
    except ValueError:
        raise PeriodError(f'{text!r} is not a valid date (use YYYY-MM-DD)')
    return datetime.datetime(d.year, d.month, d.day, tzinfo=tz)


def _label(start, end):
    return f'{start:%b %-d, %Y %H:%M} to {end:%b %-d, %Y %H:%M}'


def resolve(spec, now=None):
    now = now or datetime.datetime.now().astimezone()
    tz = now.tzinfo
    text = re.sub(r'[\s_]+', '-', str(spec).strip().lower())
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    one_day = datetime.timedelta(days=1)
    label, kind = None, 'range'
    if text == 'today':
        start, end, label, kind = midnight, now, 'Today', 'day'
    elif text == 'yesterday':
        start, end, label, kind = midnight - one_day, midnight, 'Yesterday', 'day'
    elif text in ('last-day', 'day', '24h'):
        start, end, label, kind = now - datetime.timedelta(hours=24), now, 'Last 24 hours', 'day'
    elif (m := re.fullmatch(r'(?:last-)?(\d+)-?(d|days?)', text)) or (m := re.fullmatch(r'last-(\d+)-(days?)', text)):
        n = int(m.group(1))
        start, end, label = now - datetime.timedelta(days=n), now, f'Last {n} day{"s" if n != 1 else ""}'
        kind = 'day' if n == 1 else 'week' if n == 7 else 'month' if 28 <= n <= 31 else 'range'
    elif (m := re.fullmatch(r'(?:last-)?(\d+)-?(h|hours?)', text)):
        n = int(m.group(1))
        start, end, label = now - datetime.timedelta(hours=n), now, f'Last {n} hour{"s" if n != 1 else ""}'
        kind = 'day' if n <= 24 else 'range'
    elif text == 'this-week':
        start = midnight - datetime.timedelta(days=midnight.weekday())
        end, label, kind = now, 'This week (Monday to now)', 'week'
    elif text == 'last-week':
        this_monday = midnight - datetime.timedelta(days=midnight.weekday())
        start, end, label, kind = this_monday - datetime.timedelta(days=7), this_monday, 'Last week (Monday to Sunday)', 'week'
    elif text == 'this-month':
        start, end, label, kind = midnight.replace(day=1), now, 'This month', 'month'
    elif text == 'last-month':
        first = midnight.replace(day=1)
        start, end, label, kind = (first - one_day).replace(day=1), first, 'Last month', 'month'
    elif '..' in text:
        a, b = text.split('..', 1)
        start, end = _day(a, tz), _day(b, tz) + one_day
        if end <= start:
            raise PeriodError('the end date must not be before the start date')
        label = f'{start:%b %-d, %Y} to {(end - one_day):%b %-d, %Y}'
        kind = 'week' if (end - start).days == 7 else 'day' if (end - start).days == 1 else 'range'
    elif re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
        start = _day(text, tz)
        end, label, kind = start + one_day, f'{start:%A, %b %-d, %Y}', 'day'
    else:
        raise PeriodError(f'unrecognised period {spec!r}. Valid forms: {VALID}')
    return {'start': start, 'end': end, 'label': label, 'kind': kind, 'timezone': str(tz), 'resolved': _label(start, end)}


def parse_ts(ts):
    if not isinstance(ts, str):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.timezone.utc)


def contains(period, ts):
    parsed = parse_ts(ts)
    return parsed is not None and period['start'] <= parsed < period['end']


def describe(period):
    """JSON-safe form for output, so every answer states what it covered."""
    return {'label': period['label'], 'kind': period.get('kind'), 'start': period['start'].isoformat(), 'end': period['end'].isoformat(),
            'timezone': period['timezone']}
