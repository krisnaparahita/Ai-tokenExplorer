"""The website and its demo data: fabricated, deterministic, private, and honest about being a demo."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import html_report


def generate(out):
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/make-demo-ledger.py'), '--out', str(out)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


class DemoDataTest(unittest.TestCase):
    def test_generator_is_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            generate(Path(d) / 'a.jsonl')
            generate(Path(d) / 'b.jsonl')
            digest = lambda name: hashlib.sha256((Path(d) / name).read_bytes()).hexdigest()
            self.assertEqual(digest('a.jsonl'), digest('b.jsonl'))

    def test_demo_shows_every_kind_of_work_and_is_clearly_fabricated(self):
        with tempfile.TemporaryDirectory() as d:
            generate(Path(d) / 'ledger.jsonl')
            rows = [json.loads(l) for l in (Path(d) / 'ledger.jsonl').read_text().splitlines()]
        self.assertEqual({r['role'] for r in rows}, {'main', 'delegate', 'helper', 'safety-review'})
        self.assertEqual({r['harness'] for r in rows}, {'claude', 'codex'})
        self.assertTrue(all(r['source'] == '(fabricated demo data)' for r in rows))
        model = html_report.build_model(rows)
        self.assertGreater(len(model['sessions']), 5)

    def test_checked_in_demo_pages_are_labelled_and_contain_no_local_paths(self):
        for page in ('site/demo/index.html', 'site/demo/prompt.html'):
            text = (ROOT / page).read_text(encoding='utf-8')
            self.assertIn('fabricated data', text)
            self.assertNotIn('/home/', text)
            self.assertNotIn('/Users/', text)

    def test_banner_is_escaped(self):
        model = html_report.build_model([])
        model['banner'] = '<script>alert(1)</script>'
        self.assertNotIn('<script>alert(1)</script>', html_report.render(model))


class SiteTest(unittest.TestCase):
    def test_site_page_loads_nothing_external_and_links_to_the_demo(self):
        text = (ROOT / 'site/index.html').read_text(encoding='utf-8')
        self.assertIn('href="demo/"', text)
        self.assertIn('assets/report-preview.png', text)
        self.assertNotIn('<script src', text)

    def test_publishing_is_manual_only(self):
        workflow = (ROOT / '.github/workflows/pages.yml').read_text()
        self.assertIn('workflow_dispatch', workflow)
        self.assertNotIn('push:', workflow)


if __name__ == '__main__':
    unittest.main()
