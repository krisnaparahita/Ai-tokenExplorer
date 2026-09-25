import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
spec = importlib.util.spec_from_file_location('audit', Path(__file__).resolve().parents[1] / 'scripts/token_audit.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class AuditTest(unittest.TestCase):
    def collect(self, records, kind):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'events.jsonl'
            p.write_text('\n'.join(json.dumps(r) for r in records))
            return audit.collect([(kind, p)])

    def test_codex_dedup_and_breakdowns(self):
        usage = {'input_tokens': 100, 'cached_input_tokens': 80, 'output_tokens': 20, 'reasoning_output_tokens': 10}
        event = {'type': 'token_usage_record', 'payload': {'response_id': 'r1', 'usage': usage}}
        rows, _, _ = self.collect([event, event, {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {'total_token_usage': usage}}}], 'codex')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['total_tokens'], 120)
        self.assertEqual(rows[0]['reasoning_tokens'], 10)

    def test_claude_stream_and_tool_result(self):
        events = [{'type': 'user', 'sessionId': 's', 'uuid': 'prompt', 'message': {'content': 'Find evidence'}}]
        for out in [2, 9]:
            events.append({'type': 'assistant', 'sessionId': 's', 'message': {'id': 'a', 'model': 'claude', 'usage': {'input_tokens': 3, 'cache_read_input_tokens': 40, 'cache_creation_input_tokens': 7, 'output_tokens': out}}})
            events.append({'type': 'user', 'uuid': 'tool', 'message': {'content': [{'type': 'tool_result', 'content': 'data'}]}})
        rows, _, _ = self.collect(events, 'claude')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['turn_id'], 'prompt')
        self.assertEqual(rows[0]['total_tokens'], 59)
        self.assertIsNone(rows[0]['reasoning_tokens'])

    def test_legacy_baseline_duplicates_reset(self):
        records = [{'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {'total_token_usage': {'input_tokens': i, 'output_tokens': o}}}} for i,o in [(100,10),(100,10),(200,30),(10,2),(30,4)]]
        rows,warnings,_ = self.collect(records,'codex')
        self.assertEqual(sum(r['total_tokens'] for r in rows),142)
        self.assertEqual(len(warnings),2)

    def test_import_estimates_not_measured(self):
        records = [{'schema':'token-audit/v1','harness':'other','event_id':str(i),'session_id':'s','usage_kind':'canonical','measurement':m,'usage':{'input_tokens':10,'output_tokens':2}} for i,m in enumerate(['reported','estimated'])]
        rows,warnings,files = self.collect(records,'import')
        with tempfile.TemporaryDirectory() as d:
            total = audit.report(rows,warnings,files,Path(d))
            self.assertEqual(total,12)
            self.assertEqual(len((Path(d)/'ledger.jsonl').read_text().splitlines()),2)

    def test_partial_line_and_idempotent_rescan(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'events.jsonl'
            p.write_text('{"schema":"token-audit/v1","event_id":"x","session_id":"s","usage_kind":"openai","usage":{"input_tokens":10,"output_tokens":5}}\n{"unfinished":')
            first = audit.collect([('import',p)])
            second = audit.collect([('import',p)])
            self.assertEqual(first,second)
            self.assertEqual(len(first[0]),1)
            self.assertEqual(len(first[1]),1)

    def test_missing_usage_is_not_zero(self):
        rows,warnings,_ = self.collect([{'type':'token_usage_record','payload':{'response_id':'r','usage':{'output_tokens':2}}}], 'codex')
        self.assertEqual(rows,[])
        self.assertTrue(warnings)


if __name__ == '__main__':
    unittest.main()
