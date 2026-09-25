import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
scripts = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(scripts))
import deepdive


def row(i, task='T1', role='main', tokens=1000, inp=900, out=100, cache=800, ts=None, label='Fix the flaky login test please and explain', link=None, sid='S'):
    return dict(harness='claude', event_id=f'e{i}', session_id=sid, turn_id=f'turn-{task}', model='m', prompt_label=label if role == 'main' else '',
                timestamp=ts or f'2026-09-10T10:{i:02d}:00Z', measurement='reported', total_tokens=tokens, input_tokens=inp, output_tokens=out,
                cache_read_tokens=cache, role=role, task_id=task, node=sid if role == 'main' else f'{sid}#h', task_root=f'claude:{sid}',
                link_basis=link, link_confidence={'agent-id': 'exact', 'prompt+time': 'inferred'}.get(link), helper_label='Sub job' if role != 'main' else '')


class DeepDiveTest(unittest.TestCase):
    def rows(self):
        big = [row(i, inp=1900 + i * 10, tokens=2000 + i * 10) for i in range(1, 12)]
        big[5] = row(6, inp=30000, tokens=30100)      # a large jump
        helper = [row(20, role='helper', link='agent-id', tokens=500, inp=450, out=50, cache=0)]
        other = [row(30, task='T2', tokens=100, inp=90, out=10, cache=0, ts='2026-09-11T10:00:00Z', label='Something small and unrelated to the above')]
        return big + helper + other

    def test_prompts_are_ranked_and_sized_against_the_median(self):
        summaries, typical = deepdive.summarize_prompts(self.rows())
        self.assertEqual([s['task_id'] for s in summaries], ['T1', 'T2'])
        self.assertEqual(summaries[0]['helper_sessions'], 1)
        self.assertGreater(summaries[0]['times_typical'], summaries[1]['times_typical'])

    def test_deep_dive_reports_facts_and_flags_hypotheses_separately(self):
        dd = deepdive.build(self.rows(), 'T1')
        self.assertEqual(dd['calls'], 12)
        self.assertEqual(dd['context']['largest_jump']['at_step'], 6)
        kinds = {o['kind'] for o in dd['observations']}
        self.assertIn('hypothesis', kinds)
        self.assertTrue(all(o['kind'] in ('fact', 'hypothesis') for o in dd['observations']))
        self.assertEqual(dd['helpers'][0]['link_confidence'], 'exact')
        self.assertEqual(len(dd['steps']), 12)

    def test_id_prefix_and_turn_id_lookup_and_ambiguity(self):
        self.assertEqual(deepdive.find_task(self.rows(), 'T2')[0], 'T2')
        self.assertEqual(deepdive.find_task(self.rows(), 'turn-T2')[0], 'T2')
        with self.assertRaises(LookupError):
            deepdive.find_task(self.rows(), 'T')      # matches T1 and T2
        with self.assertRaises(LookupError):
            deepdive.find_task(self.rows(), 'nope')

    def test_hashed_prompts_are_noted_not_invented(self):
        rows = [row(1, label='0123456789ab')]
        dd = deepdive.build(rows, 'T1')
        self.assertIsNone(dd['prompt'])
        self.assertIn('not stored', dd['prompt_note'])

    def test_full_prompt_read_from_claude_transcript(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / 's.jsonl'
            src.write_text(json.dumps({'type': 'user', 'uuid': 'turn-T1', 'message': {'content': 'The whole prompt, not a 160 character snippet.'}}) + '\n')
            rows = [dict(row(1), source=str(src))]
            self.assertEqual(deepdive.build(rows, 'T1', full_prompt=True)['prompt_full'], 'The whole prompt, not a 160 character snippet.')
            self.assertNotIn('prompt_full', deepdive.build(rows, 'T1'))


class QueryCliTest(unittest.TestCase):
    def run_cli(self, ledger, *args):
        out = subprocess.run([sys.executable, str(scripts / 'query.py'), *args, '--ledger', str(ledger)], capture_output=True, text=True)
        return out

    def test_period_session_prompts_and_deep_dive_commands(self):
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        recent = (now - datetime.timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
        old = (now - datetime.timedelta(days=30)).strftime('%Y-%m-%dT%H:%M:%SZ')
        rows = [row(1, task='NEW', ts=recent, sid='SNEW1234'), row(2, task='OLD', ts=old, sid='SOLD1234', label='An old prompt about the quarterly report')]
        with tempfile.TemporaryDirectory() as d:
            ledger = Path(d) / 'ledger.jsonl'
            ledger.write_text('\n'.join(json.dumps(r) for r in rows))
            last_day = json.loads(self.run_cli(ledger, 'prompts', '--period', 'last-day').stdout)
            self.assertEqual([p['task_id'] for p in last_day['prompts']], ['NEW'])
            self.assertIn('label', last_day['period'])
            self.assertEqual(json.loads(self.run_cli(ledger, 'prompts', '--period', '90d', '--search', 'quarterly').stdout)['total_prompts'], 1)
            sessions = json.loads(self.run_cli(ledger, 'sessions', '--period', '90d').stdout)
            self.assertEqual(sessions['total_sessions'], 2)
            self.assertEqual(json.loads(self.run_cli(ledger, 'summary', '--session', 'SOLD1234').stdout)['calls'], 1)
            # deep-dive finds a prompt by id even outside any period
            dd = json.loads(self.run_cli(ledger, 'deep-dive', 'OLD').stdout)['deep_dive']
            self.assertEqual(dd['task_id'], 'OLD')
            self.assertEqual(self.run_cli(ledger, 'summary', '--period', 'week').returncode, 2)
            self.assertEqual(self.run_cli(ledger, 'summary', '--period', '7d', '--since', '2026-01-01').returncode, 2)


if __name__ == '__main__':
    unittest.main()
