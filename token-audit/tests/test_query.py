import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
script = Path(__file__).resolve().parents[1]/'scripts/query.py'
spec = importlib.util.spec_from_file_location('query', script)
query = importlib.util.module_from_spec(spec)
spec.loader.exec_module(query)

class QueryTest(unittest.TestCase):
    def test_missing_and_subset_categories(self):
        rows = [{'input_tokens':100,'output_tokens':10,'cache_read_tokens':80}, {'input_tokens':50,'output_tokens':5}]
        cats = {r['category']:r for r in query.categories(rows)}
        self.assertEqual(cats['cache_read_tokens']['known_calls'],1)
        self.assertEqual(cats['cache_read_tokens']['subset_of'],'input_tokens')
        self.assertEqual(cats['reasoning_tokens']['known_calls'],0)

    def test_summary_filters_and_inspect_ambiguity(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = Path(d)/'ledger.jsonl'
            rows = [dict(event_id=e,harness='codex',session_id='s',turn_id='t',model='m',stage='unknown',measurement=m,timestamp='2026-09-25T01:00:00Z',input_tokens=100,output_tokens=5,total_tokens=105) for e,m in [('abc1','reported'),('abc2','estimated')]]
            ledger.write_text('\n'.join(json.dumps(r) for r in rows))
            def run(*args):
                return subprocess.run([sys.executable,str(script),*args,'--ledger',str(ledger)],capture_output=True,text=True)
            data = json.loads(run('summary','--since','2026-09-25').stdout)
            self.assertEqual(data['total_tokens'],105)
            self.assertEqual(data['estimated_calls_excluded'],1)
            self.assertEqual(run('inspect','abc').returncode,2)
            self.assertEqual(json.loads(run('inspect','abc2').stdout)['call']['measurement'],'estimated')
            self.assertEqual(json.loads(run('summary','--since','2026-09-26').stdout)['calls'],0)

    def test_context_counts_characters_not_tokens(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'source.jsonl'
            p.write_text(json.dumps({'type':'response_item','payload':{'type':'function_call_output','output':'x'*250}})+'\n')
            x=query.context_inventory({'source':str(p),'source_line':1,'harness':'codex'})
            self.assertEqual(x['characters_by_category']['tool_result'],250)
            self.assertNotIn('tokens',x['largest_visible_blocks'][0])

if __name__ == '__main__':
    unittest.main()
