import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
spec = importlib.util.spec_from_file_location('audit', Path(__file__).resolve().parents[1] / 'scripts/token_audit.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

USAGE = {'input_tokens': 10, 'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0, 'output_tokens': 5}


def collect(files):
    """files: list of (kind, name, records)."""
    with tempfile.TemporaryDirectory() as d:
        roots = []
        for kind, name, records in files:
            path = Path(d) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('\n'.join(json.dumps(r) for r in records))
            roots.append((kind, path))
        return audit.collect(roots)[0]


def claude_parent(tool_name='Agent', tool_input=None, tool_id='tu1', agent_id=None, ts='2026-01-01T00:00:10Z'):
    events = [
        {'type': 'user', 'sessionId': 'S', 'uuid': 'turn1', 'timestamp': '2026-01-01T00:00:00Z', 'message': {'content': 'Please do the big job'}},
        {'type': 'assistant', 'sessionId': 'S', 'timestamp': ts, 'message': {'id': 'm1', 'model': 'claude', 'usage': USAGE,
         'content': [{'type': 'tool_use', 'id': tool_id, 'name': tool_name, 'input': tool_input or {'description': 'sub job'}}]}},
        {'type': 'user', 'sessionId': 'S', 'uuid': 'r1', 'timestamp': '2026-01-01T00:02:00Z',
         'message': {'content': [{'type': 'tool_result', 'tool_use_id': tool_id, 'content': 'done'}]},
         'toolUseResult': {'agentId': agent_id} if agent_id else None},
    ]
    return events


class LinkingTest(unittest.TestCase):
    def test_claude_subagent_links_exactly_to_parent_turn(self):
        child = [{'type': 'assistant', 'sessionId': 'S', 'isSidechain': True, 'agentId': 'A1', 'timestamp': '2026-01-01T00:00:30Z',
                  'message': {'id': 'c1', 'model': 'claude', 'usage': USAGE}}]
        rows = collect([('claude', 'S.jsonl', claude_parent(agent_id='A1')), ('claude', 'S/subagents/agent-A1.jsonl', child)])
        by_id = {r['event_id']: r for r in rows}
        self.assertEqual(by_id['c1']['role'], 'helper')
        self.assertEqual(by_id['c1']['link_basis'], 'agent-id')
        self.assertEqual(by_id['c1']['link_confidence'], 'exact')
        self.assertEqual(by_id['c1']['task_id'], by_id['m1']['task_id'])

    def test_unlinked_helper_stays_its_own_task(self):
        child = [{'type': 'assistant', 'sessionId': 'S', 'isSidechain': True, 'agentId': 'ZZ', 'message': {'id': 'c1', 'model': 'claude', 'usage': USAGE}}]
        rows = collect([('claude', 'S.jsonl', claude_parent(agent_id='A1')), ('claude', 'S/subagents/agent-ZZ.jsonl', child)])
        by_id = {r['event_id']: r for r in rows}
        self.assertIsNone(by_id['c1']['link_basis'])
        self.assertNotEqual(by_id['c1']['task_id'], by_id['m1']['task_id'])

    def codex_records(self, sid, prompt, start, source='exec', parent=None, thread_source='user'):
        meta = {'id': sid, 'timestamp': start, 'source': source, 'thread_source': thread_source}
        if parent:
            meta['parent_thread_id'] = parent
        return [
            {'type': 'session_meta', 'timestamp': start, 'payload': meta},
            {'type': 'turn_context', 'timestamp': start, 'payload': {'turn_id': f't-{sid}', 'model': 'gpt'}},
            {'type': 'response_item', 'payload': {'role': 'user', 'content': [{'type': 'input_text', 'text': '<environment_context>noise</environment_context>'}]}},
            {'type': 'response_item', 'payload': {'role': 'user', 'content': [{'type': 'input_text', 'text': prompt}]}},
            {'type': 'token_usage_record', 'payload': {'response_id': f'resp-{sid}', 'usage': {'input_tokens': 100, 'output_tokens': 20}}},
        ]

    def test_codex_safety_review_links_by_parent_thread_id(self):
        rows = collect([('codex', 'a.jsonl', self.codex_records('P', 'main work', '2026-01-01T00:00:00Z')),
                        ('codex', 'b.jsonl', self.codex_records('G', 'review', '2026-01-01T00:00:05Z', source={'subagent': {'other': 'guardian'}}, parent='P', thread_source='guardian_review'))])
        by_id = {r['event_id']: r for r in rows}
        self.assertEqual(by_id['resp-G']['role'], 'safety-review')
        self.assertEqual(by_id['resp-G']['link_basis'], 'parent-thread-id')
        self.assertEqual(by_id['resp-G']['task_id'], by_id['resp-P']['task_id'])

    def test_missing_parent_leaves_helper_unlinked(self):
        rows = collect([('codex', 'b.jsonl', self.codex_records('G', 'review', '2026-01-01T00:00:05Z', parent='GONE', thread_source='guardian_review'))])
        self.assertIsNone(rows[0]['link_basis'])

    def test_delegated_prompt_matches_by_text_and_time_and_skips_context_wrappers(self):
        prompt = 'Investigate the flaky login test and report which line fails and why, with evidence.'
        call = claude_parent(tool_name='mcp__codex-delegate__ask_codex', tool_input={'prompt': prompt, 'system': 'be careful'})
        rows = collect([('claude', 'S.jsonl', call), ('codex', 'x.jsonl', self.codex_records('X', prompt, '2026-01-01T00:00:20Z'))])
        by_id = {r['event_id']: r for r in rows}
        self.assertEqual(by_id['resp-X']['role'], 'delegate')
        self.assertEqual(by_id['resp-X']['link_basis'], 'prompt+time')
        self.assertEqual(by_id['resp-X']['link_confidence'], 'inferred')
        self.assertEqual(by_id['resp-X']['task_id'], by_id['m1']['task_id'])

    def test_same_prompt_outside_time_window_is_not_linked(self):
        prompt = 'Investigate the flaky login test and report which line fails and why, with evidence.'
        call = claude_parent(tool_name='mcp__codex-delegate__ask_codex', tool_input={'prompt': prompt})
        rows = collect([('claude', 'S.jsonl', call), ('codex', 'x.jsonl', self.codex_records('X', prompt, '2026-01-02T00:00:00Z'))])
        by_id = {r['event_id']: r for r in rows}
        self.assertIsNone(by_id['resp-X']['link_basis'])

    def test_different_prompt_is_not_linked(self):
        call = claude_parent(tool_name='mcp__codex-delegate__ask_codex', tool_input={'prompt': 'Write the quarterly summary for the finance team.'})
        rows = collect([('claude', 'S.jsonl', call), ('codex', 'x.jsonl', self.codex_records('X', 'Something else entirely about weather.', '2026-01-01T00:00:20Z'))])
        by_id = {r['event_id']: r for r in rows}
        self.assertIsNone(by_id['resp-X']['link_basis'])


class HandoffDirectionTest(unittest.TestCase):
    """Hand-offs are not specific to one setup: either tool can start the other, by MCP tool or by shell command."""
    PROMPT = 'Summarise the open incidents from last week and list the three riskiest, with owners.'

    def claude_child(self, start='2026-01-01T00:00:20Z', prompt=None):
        return [{'type': 'user', 'sessionId': 'C2', 'uuid': 'cu1', 'timestamp': start, 'message': {'content': prompt or self.PROMPT}},
                {'type': 'assistant', 'sessionId': 'C2', 'timestamp': start, 'message': {'id': 'cm1', 'model': 'claude', 'usage': USAGE}}]

    def codex_records(self, sid, start, tool_input=None, prompt=None):
        records = [{'type': 'session_meta', 'timestamp': start, 'payload': {'id': sid, 'timestamp': start, 'source': 'exec', 'thread_source': 'user'}},
                   {'type': 'turn_context', 'timestamp': start, 'payload': {'turn_id': f't-{sid}', 'model': 'gpt'}}]
        if prompt:
            records.append({'type': 'response_item', 'payload': {'role': 'user', 'content': [{'type': 'input_text', 'text': prompt}]}})
        if tool_input:
            records.append({'type': 'response_item', 'timestamp': '2026-01-01T00:00:10Z', 'payload': {'type': 'custom_tool_call', 'call_id': 'c1', 'name': 'exec', 'input': tool_input}})
            records.append({'type': 'response_item', 'timestamp': '2026-01-01T00:02:00Z', 'payload': {'type': 'custom_tool_call_output', 'call_id': 'c1'}})
        records.append({'type': 'token_usage_record', 'payload': {'response_id': f'resp-{sid}', 'usage': {'input_tokens': 100, 'output_tokens': 20}}})
        return records

    def test_codex_can_hand_off_to_claude_through_a_shell_command(self):
        command = 'const r = await tools.exec_command({"cmd":"claude -p \\"' + self.PROMPT + '\\""})'
        rows = collect([('codex', 'p.jsonl', self.codex_records('P', '2026-01-01T00:00:00Z', tool_input=command)),
                        ('claude', 'c2.jsonl', self.claude_child())])
        by_id = {r['event_id']: r for r in rows}
        self.assertEqual(by_id['cm1']['role'], 'delegate')
        self.assertEqual(by_id['cm1']['link_confidence'], 'inferred')
        self.assertEqual(by_id['cm1']['task_id'], by_id['resp-P']['task_id'])
        self.assertEqual(by_id['cm1']['helper_label'], 'Work handed to Claude')

    def test_claude_can_hand_off_to_codex_through_a_bash_command(self):
        events = claude_parent(tool_name='Bash', tool_input={'command': f'codex exec --json "{self.PROMPT}"'})
        rows = collect([('claude', 'S.jsonl', events), ('codex', 'x.jsonl', self.codex_records('X', '2026-01-01T00:00:20Z', prompt=self.PROMPT))])
        by_id = {r['event_id']: r for r in rows}
        self.assertEqual(by_id['resp-X']['role'], 'delegate')
        self.assertEqual(by_id['resp-X']['task_id'], by_id['m1']['task_id'])

    def test_unrelated_shell_commands_are_not_hand_offs(self):
        events = claude_parent(tool_name='Bash', tool_input={'command': 'ls -la && echo done'})
        rows = collect([('claude', 'S.jsonl', events), ('codex', 'x.jsonl', self.codex_records('X', '2026-01-01T00:00:20Z', prompt=self.PROMPT))])
        self.assertIsNone({r['event_id']: r for r in rows}['resp-X']['link_basis'])

    def test_a_tool_never_links_to_itself_or_outside_the_time_window(self):
        events = claude_parent(tool_name='Bash', tool_input={'command': f'codex exec "{self.PROMPT}"'})
        rows = collect([('claude', 'S.jsonl', events), ('codex', 'x.jsonl', self.codex_records('X', '2026-01-05T00:00:00Z', prompt=self.PROMPT))])
        self.assertIsNone({r['event_id']: r for r in rows}['resp-X']['link_basis'])

    def test_standalone_sessions_with_no_hand_offs_stay_unlinked(self):
        rows = collect([('claude', 'S.jsonl', claude_parent(tool_name='Read', tool_input={'file_path': '/tmp/x'}))])
        self.assertTrue(all(r['link_basis'] is None and r['role'] == 'main' for r in rows))


if __name__ == '__main__':
    unittest.main()
