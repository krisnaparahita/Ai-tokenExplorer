"""The package files (skill metadata, plugin manifests, versions, docs) must stay consistent."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PackageTest(unittest.TestCase):
    def test_validator_passes(self):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/validate-package.py')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_manifests_point_at_the_root_skill(self):
        plugin = json.loads((ROOT / '.claude-plugin/plugin.json').read_text())
        self.assertEqual(plugin['skills'], ['./'])
        self.assertTrue((ROOT / 'SKILL.md').is_file())

    def test_installed_copy_has_what_runs_and_leaves_out_maintainer_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {'HOME': tmp, 'USERPROFILE': tmp, 'PATH': ''}
            done = subprocess.run([sys.executable, str(ROOT / 'scripts/install.py'), '--target', 'claude'], capture_output=True, text=True, env=env)
            self.assertEqual(done.returncode, 0, done.stderr)
            installed = Path(tmp) / '.claude/skills/token-audit'
            for needed in ('SKILL.md', 'scripts/query.py', 'scripts/html_report.py', 'references/adapters.md', 'agents/openai.yaml'):
                self.assertTrue((installed / needed).exists(), needed)
            for left_out in ('tests', 'docs', 'AGENTS.md', '.git'):
                self.assertFalse((installed / left_out).exists(), left_out)
            # the installed copy must run on its own
            run = subprocess.run([sys.executable, str(installed / 'scripts/query.py'), '--help'], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)


class ValidatorCatchesProblemsTest(unittest.TestCase):
    """Copy the package, break one thing, and confirm the validator says so."""

    def broken(self, edit):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / 'pkg'
            shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns('.git', '__pycache__'))
            edit(copy)
            return subprocess.run([sys.executable, str(copy / 'scripts/validate-package.py')], capture_output=True, text=True, cwd=copy)

    def test_version_drift_is_caught(self):
        def edit(p):
            f = p / '.claude-plugin/plugin.json'
            f.write_text(f.read_text().replace('"0.1.0"', '"9.9.9"'))
        out = self.broken(edit)
        self.assertIn('one package version', out.stderr + out.stdout)

    def test_real_prices_are_refused(self):
        def edit(p):
            f = p / 'prices.example.json'
            data = json.loads(f.read_text())
            data['models'][0]['input_per_million'] = 3
            f.write_text(json.dumps(data))
        self.assertNotEqual(self.broken(edit).returncode, 0)

    def test_personal_paths_are_refused(self):
        def edit(p):
            (p / 'docs/development.md').write_text((p / 'docs/development.md').read_text() + '\nsee ' + '/ho' + 'me/someone/private/notes\n')
        out = self.broken(edit)
        self.assertIn('personal data', out.stderr + out.stdout)

    def test_stale_nested_paths_in_docs_are_caught(self):
        def edit(p):
            f = p / 'README.md'
            f.write_text(f.read_text() + '\nRun python3 token-audit/scripts/query.py summary\n')
        out = self.broken(edit)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn('repo-root paths', out.stderr + out.stdout)

    def test_external_loads_on_the_website_are_caught(self):
        def edit(p):
            f = p / 'site/index.html'
            f.write_text(f.read_text().replace('</head>', '<script src="https://cdn.example.com/x.js"></script></head>'))
        out = self.broken(edit)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn('other sites', out.stderr + out.stdout)

    def test_publishing_on_push_is_refused(self):
        def edit(p):
            f = p / '.github/workflows/pages.yml'
            f.write_text(f.read_text().replace('  workflow_dispatch:', '  workflow_dispatch:\n  push:\n    branches: [main]'))
        out = self.broken(edit)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn('manual run', out.stderr + out.stdout)

    def test_an_unlabelled_demo_page_is_refused(self):
        def edit(p):
            f = p / 'site/demo/index.html'
            f.write_text(f.read_text().replace('fabricated data', 'sample data'))
        self.assertNotEqual(self.broken(edit).returncode, 0)

    def test_undocumented_command_is_caught(self):
        def edit(p):
            f = p / 'SKILL.md'
            f.write_text(f.read_text().replace('`deep-dive', '`deepdive'))
        self.assertNotEqual(self.broken(edit).returncode, 0)


if __name__ == '__main__':
    unittest.main()
