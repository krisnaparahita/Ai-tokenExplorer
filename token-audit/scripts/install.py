#!/usr/bin/env python3
"""Install the token-audit skill for Codex, Claude Code, or both; optionally enable the macOS background collector."""
import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--target', choices=['codex', 'claude', 'both'], default='both',
                   help='Which tool to install the skill for (default: both). The collector reads whichever tool logs exist either way.')
    p.add_argument('--enable-monitor', action='store_true')
    args = p.parse_args()
    source = Path(__file__).resolve().parent.parent
    home = Path.home()
    codex = Path(os.environ.get('CODEX_HOME', home / '.codex')) / 'skills/token-audit'
    claude = home / '.claude/skills/token-audit'
    plist = home / 'Library/LaunchAgents/local.token-audit.collector.plist'
    destinations = [d for name, d in (('codex', codex), ('claude', claude)) if args.target in (name, 'both')]
    if any(d.exists() for d in destinations) or (args.enable_monitor and plist.exists()):
        p.error('Existing installation found; refusing to overwrite. Update deliberately after reviewing local changes.')
    if args.enable_monitor and sys.platform != 'darwin':
        p.error('--enable-monitor requires macOS; use --watch on other platforms')
    os.umask(0o077)
    for d in destinations:
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, d, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        print(f'Installed {d}')
    if args.enable_monitor:
        out = home / '.local/share/token-audit'
        out.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, str(destinations[0] / 'scripts/token_audit.py'), '--out', str(out), '--include-prompts']
        subprocess.run(cmd, check=True)
        plist.parent.mkdir(parents=True, exist_ok=True)
        config = {'Label': 'local.token-audit.collector', 'ProgramArguments': cmd, 'StartInterval': 60, 'RunAtLoad': True,
                  'StandardOutPath': str(out / 'monitor.log'), 'StandardErrorPath': str(out / 'monitor.log'), 'ProcessType': 'Background'}
        with plist.open('wb') as f:
            plistlib.dump(config, f)
        subprocess.run(['launchctl', 'bootstrap', f'gui/{os.getuid()}', str(plist)], check=True)
        print(f'Monitor enabled. Plain-language report: {out / "report.html"}')


if __name__ == '__main__':
    main()
