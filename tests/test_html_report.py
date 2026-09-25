import importlib.util
import json
from pathlib import Path
import unittest
scripts = Path(__file__).resolve().parents[1] / 'scripts'
import sys
sys.path.insert(0, str(scripts))
spec = importlib.util.spec_from_file_location('html_report', scripts / 'html_report.py')
hr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hr)


def row(i, tokens=1000, label='A task', ts='2026-03-01T10:00:00Z', model='m', role='main', cache=None, out=100):
    return dict(harness='claude', event_id=f'e{i}', session_id='s', turn_id=f't{i}', model=model, prompt_label=label, timestamp=ts,
                measurement='reported', total_tokens=tokens, input_tokens=tokens - out, output_tokens=out, cache_read_tokens=cache,
                cache_write_tokens=None, reasoning_tokens=None, role=role, task_id=f'task{i}', node='s')


class HtmlReportTest(unittest.TestCase):
    def test_empty_ledger_renders(self):
        page = hr.render(hr.build_model([]))
        self.assertIn('0', page)
        self.assertIn('<!doctype html>', page)

    def test_untrusted_prompt_text_is_escaped(self):
        page = hr.render(hr.build_model([row(1, label='<script>alert(1)</script> & "quotes"')]))
        self.assertNotIn('<script>alert(1)</script>', page)
        # system-injected notifications are never used as a title
        page = hr.render(hr.build_model([row(1, label='<task-notification>x</task-notification>')]))
        self.assertNotIn('&lt;task-notification', page)

    def test_effort_split_counts_cache_once_and_flags_unknown(self):
        model = hr.build_model([row(1, tokens=1000, cache=600, out=100), row(2, tokens=1000, cache=None, out=100)])
        self.assertEqual(model['reused'], 600)
        self.assertEqual(model['writing'], 200)
        self.assertEqual(model['fresh'], 300 + 900)
        self.assertEqual(model['unknown_cache_calls'], 1)

    def test_estimated_rows_excluded(self):
        estimated = dict(row(1), measurement='estimated')
        self.assertEqual(hr.build_model([estimated])['total'], 0)

    def test_task_rollup_includes_helpers_and_rates_against_median(self):
        rows = [row(i, tokens=1000) for i in range(1, 6)]
        heavy = [dict(row(9, tokens=5000), task_id='big'), dict(row(10, tokens=5000, role='helper'), task_id='big', node='h')]
        model = hr.build_model(rows + heavy)
        big = model['tasks'][0]
        self.assertEqual(big['total'], 10000)
        self.assertEqual(big['helpers'], 1)
        self.assertEqual(big['rating'], 'Heavy' if 10000 / model['typical'] < 5 else 'Very heavy')

    def test_cost_only_with_prices_and_labels_unpriced_models(self):
        prices = {'currency': 'USD', 'models': [{'match': 'known', 'input_per_million': 1.0, 'output_per_million': 2.0}]}
        rows = [row(1, tokens=1_000_000, out=500_000, model='known-1'), row(2, tokens=1000, model='mystery')]
        model = hr.build_model(rows, prices=prices)
        self.assertAlmostEqual(model['cost'], 0.5 * 1.0 + 0.5 * 2.0)
        self.assertEqual(model['unpriced'][0][0], 'mystery')
        self.assertIn('Estimated cost', hr.render(model))
        self.assertIsNone(hr.build_model(rows)['cost'])

    def test_formatting_helpers(self):
        self.assertEqual(hr.fmt_tokens(1_500_000), '1.5 million')
        self.assertEqual(hr.fmt_short(50_000_000), '50M')
        self.assertEqual(hr.pct(0.001), '<1%')

    def test_select_rows_by_period_session_and_harness(self):
        import datetime
        import periods
        tz = datetime.timezone.utc
        now = datetime.datetime(2026, 3, 10, 12, 0, tzinfo=tz)
        rows = [dict(row(1, ts='2026-03-09T13:00:00Z'), session_id='AAAAAAAA1'), dict(row(2, ts='2026-02-01T13:00:00Z'), session_id='BBBBBBBB1', harness='codex')]
        period = periods.resolve('last-day', now)
        self.assertEqual([r['event_id'] for r in hr.select_rows(rows, period=period)], ['e1'])
        self.assertEqual([r['event_id'] for r in hr.select_rows(rows, session='BBBBBBBB')], ['e2'])
        self.assertEqual([r['event_id'] for r in hr.select_rows(rows, harness='codex')], ['e2'])
        self.assertEqual(hr.select_rows([dict(rows[0], timestamp=None)], since=periods.parse_ts('2026-01-01T00:00:00Z')), [])

    def test_period_label_shown_and_prompt_page_renders_and_escapes(self):
        model = hr.build_model([row(1)], period_label='Last 7 days')
        self.assertIn('Last 7 days', hr.render(model))
        import deepdive
        rows = [dict(row(i, ts=f'2026-03-01T10:{i:02d}:00Z', label='<b>bold</b> prompt text that is long enough'), task_id='T', role='main') for i in range(1, 13)]
        dd = deepdive.build(rows, 'T')
        dd['prompt_full'] = '<script>alert(1)</script>'
        page = hr.render_prompt_page(dd, hr.build_model(rows))
        self.assertIn('One prompt, taken apart', page)
        self.assertNotIn('<script>alert(1)</script>', page)
        self.assertNotIn('<b>bold</b>', page)

    def test_headline_follows_the_period_and_defaults_when_there_is_none(self):
        import datetime
        import periods
        now = datetime.datetime(2026, 3, 10, 12, 0, tzinfo=datetime.timezone.utc)
        week = hr.render(hr.build_model([row(1, ts='2026-03-08T10:00:00Z')], period=periods.resolve('7d', now)))
        self.assertIn('Your AI week in review', week)
        self.assertIn('Your AI day in review', hr.render(hr.build_model([row(1)], period=periods.resolve('yesterday', now))))
        self.assertIn('Your AI month in review', hr.render(hr.build_model([row(1)], period=periods.resolve('last-month', now))))
        self.assertIn('Your AI usage, in plain English', hr.render(hr.build_model([row(1)])))

    def test_weekly_chart_shows_every_day_of_the_period_even_when_quiet(self):
        import datetime
        import periods
        now = datetime.datetime(2026, 3, 10, 12, 0, tzinfo=datetime.timezone.utc).astimezone()
        period = periods.resolve('7d', now)
        page = hr.render(hr.build_model([row(1, ts=now.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))], period=period))
        self.assertEqual(page.count('class="axis" text-anchor="middle"'), 8)  # 7 days plus the day the window ends on

    def test_top_conversations_are_named_and_include_their_helpers(self):
        rows = [dict(row(1, tokens=1000, label='Plan the launch checklist for the new product release'), task_root='claude:SESS1', session_id='SESS1'),
                dict(row(2, tokens=4000, role='helper'), task_root='claude:SESS1', session_id='SESS1', node='h'),
                dict(row(3, tokens=500, label='A different and much smaller conversation here'), task_root='claude:SESS2', session_id='SESS2')]
        model = hr.build_model(rows)
        self.assertEqual(model['sessions'][0]['id'], 'SESS1')
        self.assertEqual(model['sessions'][0]['tokens'], 5000)
        self.assertEqual(model['sessions'][0]['helpers'], 1)
        page = hr.render(model)
        self.assertIn('Which conversations used the most?', page)
        self.assertIn('Plan the launch checklist', page)
        self.assertIn('--session', page)

    def test_hand_off_label_names_the_receiving_tool_not_a_fixed_setup(self):
        to_claude = hr.build_model([dict(row(1, role='delegate'), harness='claude')])
        hr.configure_labels(to_claude)
        self.assertEqual(hr.ROLE_NAMES['delegate'], 'Work handed to Claude')
        both = hr.build_model([dict(row(1, role='delegate'), harness='claude'), dict(row(2, role='delegate'), harness='codex')])
        hr.configure_labels(both)
        self.assertEqual(hr.ROLE_NAMES['delegate'], 'Work handed to another AI tool')
        hr.configure_labels(hr.build_model([dict(row(1, role='delegate'), harness='codex')]))
        self.assertEqual(hr.ROLE_NAMES['delegate'], 'Work handed to Codex')


if __name__ == '__main__':
    unittest.main()
