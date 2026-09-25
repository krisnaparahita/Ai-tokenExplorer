"""The tool must work whether the user has Claude, Codex, or both -- and must not assume they hand work to each other."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
scripts = Path(__file__).resolve().parents[1] / 'scripts'
USAGE = {'input_tokens': 10, 'cache_read_input_tokens': 5, 'cache_creation_input_tokens': 0, 'output_tokens': 5}


def run(script, home, *args):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    env.pop('CODEX_HOME', None)
    return subprocess.run([sys.executable, str(scripts / script), *args], capture_output=True, text=True, env=env)


def claude_logs(home):
    d = home / '.claude/projects/p'
    d.mkdir(parents=True)
    (d / 's.jsonl').write_text('\n'.join(json.dumps(x) for x in [
        {'type': 'user', 'sessionId': 'S', 'uuid': 'u1', 'timestamp': '2026-01-01T00:00:00Z', 'message': {'content': 'Plan the launch checklist for next week please'}},
        {'type': 'assistant', 'sessionId': 'S', 'timestamp': '2026-01-01T00:00:05Z', 'message': {'id': 'm1', 'model': 'claude', 'usage': USAGE}}]))


def codex_logs(home):
    d = home / '.codex/sessions/2026/01/01'
    d.mkdir(parents=True)
    (d / 'r.jsonl').write_text('\n'.join(json.dumps(x) for x in [
        {'type': 'session_meta', 'timestamp': '2026-01-01T00:00:00Z', 'payload': {'id': 'X', 'source': 'exec', 'thread_source': 'user'}},
        {'type': 'turn_context', 'timestamp': '2026-01-01T00:00:00Z', 'payload': {'turn_id': 't1', 'model': 'gpt'}},
        {'type': 'token_usage_record', 'timestamp': '2026-01-01T00:00:05Z', 'payload': {'response_id': 'r1', 'usage': {'input_tokens': 100, 'output_tokens': 20}}}]))


class SingleToolTest(unittest.TestCase):
    def collect_with(self, *makers):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name)
        for maker in makers:
            maker(home)
        out = home / 'out'
        result = run('token_audit.py', home, '--out', str(out))
        self.assertEqual(result.returncode, 0, result.stderr)
        coverage = json.loads((out / 'coverage.json').read_text())
        return out, coverage

    def test_claude_only(self):
        out, coverage = self.collect_with(claude_logs)
        self.assertTrue((out / 'report.html').exists())
        self.assertFalse([w for w in coverage['warnings'] if 'does not exist' in w])
        self.assertEqual(coverage['measured_events'], 1)

    def test_codex_only(self):
        out, coverage = self.collect_with(codex_logs)
        self.assertTrue((out / 'report.html').exists())
        self.assertFalse([w for w in coverage['warnings'] if 'does not exist' in w])
        self.assertEqual(coverage['measured_events'], 1)

    def test_both_with_no_hand_offs_shows_no_invented_links(self):
        out, _ = self.collect_with(claude_logs, codex_logs)
        rows = [json.loads(l) for l in (out / 'ledger.jsonl').read_text().splitlines()]
        self.assertEqual({r['harness'] for r in rows}, {'claude', 'codex'})
        self.assertTrue(all(r['link_basis'] is None and r['role'] == 'main' for r in rows))

    def test_installer_targets_one_tool_and_ignores_the_other_being_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertEqual(run('install.py', home, '--target', 'claude').returncode, 0)
            self.assertTrue((home / '.claude/skills/token-audit/SKILL.md').exists())
            self.assertFalse((home / '.codex/skills/token-audit').exists())
            self.assertEqual(run('install.py', home, '--target', 'codex').returncode, 0)   # not blocked by the Claude copy
            self.assertTrue((home / '.codex/skills/token-audit/scripts/query.py').exists())
            self.assertNotEqual(run('install.py', home, '--target', 'claude').returncode, 0)  # but never overwrites


if __name__ == '__main__':
    unittest.main()
