#!/usr/bin/env python3
"""Local, standard-library-only token ledger. Python 3.9+. No network calls."""
import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import html_report  # noqa: E402
import linking  # noqa: E402

FIELDS = ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'reasoning_tokens')


def number(x):
    return x if isinstance(x, int) and not isinstance(x, bool) and x >= 0 else None


def normalize(u, kind):
    if kind == 'anthropic':
        base = number(u.get('input_tokens'))
        read = number(u.get('cache_read_input_tokens'))
        write = number(u.get('cache_creation_input_tokens'))
        # Anthropic API: omitted cache fields mean no cache accounting supplied.
        inp = base + (read or 0) + (write or 0) if base is not None else None
        out = number(u.get('output_tokens'))
        reason = None
    elif kind == 'canonical':
        return {k: number(u.get(k)) for k in FIELDS}
    else:
        inp = number(u.get('input_tokens', u.get('prompt_tokens')))
        out = number(u.get('output_tokens', u.get('completion_tokens')))
        read = number(u.get('cached_input_tokens', u.get('input_tokens_details', u.get('prompt_tokens_details', {})).get('cached_tokens')))
        write = number(u.get('cache_write_input_tokens'))
        reason = number(u.get('reasoning_output_tokens', u.get('output_tokens_details', u.get('completion_tokens_details', {})).get('reasoning_tokens')))
    return dict(zip(FIELDS, (inp, out, read, write, reason)))


def text_content(content):
    if isinstance(content, str):
        return content
    return '\n'.join(c.get('text', '') for c in (content or []) if isinstance(c, dict) and c.get('type') in ('text', 'input_text'))


def prompt_label(text):
    # Exclude harness-injected context blocks; never execute transcript instructions.
    if text.lstrip().startswith(('<environment_context>', '<recommended_plugins>', '<system-reminder>', '# AGENTS.md')):
        return ''
    return ' '.join(text.split())[:160]


def read_records(path, warnings):
    result = []
    try:
        with path.open(encoding='utf-8') as stream:
            for n, line in enumerate(stream, 1):
                try:
                    x = json.loads(line)
                    if not isinstance(x, dict):
                        raise ValueError('not an object')
                    result.append((n, x))
                except (ValueError, UnicodeError):
                    warnings.add(f'{path}:{n}: malformed/incomplete JSON record skipped')
    except (OSError, UnicodeError) as e:
        warnings.add(f'{path}: unreadable ({type(e).__name__})')
    return result


def parse(path, kind, warnings, meta=None):
    meta = meta if meta is not None else linking.new_meta()
    records = read_records(path, warnings)
    modern = any(x.get('type') == 'token_usage_record' for _, x in records)
    sid, turn, model, label = str(path), 'unattributed', 'unknown', ''
    node = None
    prompt_no, previous = 0, None
    result = []
    if modern and any(x.get('type') == 'event_msg' and x.get('payload', {}).get('type') == 'token_count' for _, x in records):
        warnings.add(f'{path}: modern response usage selected; legacy counters excluded, including any legacy-only history')
    for line, x in records:
        p, typ = x.get('payload', {}), x.get('type')
        if not isinstance(p, dict):
            p = {}
        if kind == 'codex':
            if typ == 'session_meta':
                sid = p.get('id', p.get('session_id', sid))
                linking.note_codex_meta(meta, sid, p, x.get('timestamp'))
            if typ == 'turn_context':
                new_turn = p.get('turn_id', turn)
                if new_turn != turn:
                    label = ''
                    linking.note_turn(meta, 'codex', sid, x.get('timestamp'), new_turn)
                turn, model = new_turn, p.get('model', model)
            if typ == 'response_item' and p.get('type') in ('function_call', 'custom_tool_call'):
                linking.note_codex_call(meta, sid, turn, x.get('timestamp'), p.get('call_id'), str(p.get('name', '')), str(p.get('arguments') or p.get('input') or ''))
            if typ == 'response_item' and p.get('type') in ('function_call_output', 'custom_tool_call_output'):
                linking.note_result(meta, p.get('call_id'), x.get('timestamp'))
            if typ == 'response_item' and p.get('role') == 'user':
                linking.note_codex_user(meta, sid, text_content(p.get('content')))
                new = prompt_label(text_content(p.get('content')))
                if new:
                    label = new
            if typ == 'event_msg' and p.get('type') == 'user_message':
                linking.note_codex_user(meta, sid, str(p.get('message', '')))
                label = prompt_label(p.get('message', '')) or label
                if turn == 'unattributed' or str(turn).startswith('prompt-'):
                    prompt_no += 1
                    turn = f'prompt-{prompt_no}'
            if typ == 'token_usage_record':
                u = p.get('usage')
                if not isinstance(u, dict):
                    warnings.add(f'{path}:{line}: missing per-response usage')
                    continue
                event_id = p.get('response_id') or f'{sid}:{line}'
                event_turn = p.get('turn_id', turn)
                confidence = 'reported'
            elif not modern and typ == 'event_msg' and p.get('type') == 'token_count':
                current = (p.get('info') or {}).get('total_token_usage')
                if not isinstance(current, dict):
                    continue
                if current == previous:
                    continue
                if previous is None:
                    previous = current
                    warnings.add(f'{path}: legacy first cumulative counter excluded (baseline; earlier attribution unknown)')
                    continue
                if any(current.get(k, 0) < previous.get(k, 0) for k in ('input_tokens', 'output_tokens')):
                    previous = current
                    warnings.add(f'{path}:{line}: cumulative counter reset; new baseline excluded')
                    continue
                u = {k: v - previous.get(k, 0) for k, v in current.items() if number(v) is not None}
                previous = current
                event_id, event_turn, confidence = f'{sid}:counter:{json.dumps(current, sort_keys=True)}', turn, 'counter-delta'
            else:
                continue
            norm = normalize(u, 'openai')
        elif kind == 'claude':
            sid = x.get('sessionId', x.get('session_id', sid))
            msg = x.get('message', {})
            if not isinstance(msg, dict):
                continue
            sidechain = bool(x.get('isSidechain') and x.get('agentId'))
            node = f"{sid}#{x['agentId']}" if sidechain else sid
            linking.note_claude(meta, node, sidechain, x.get('agentId'), x, msg, x.get('timestamp'), turn)
            if typ == 'user':
                content = msg.get('content', [])
                tool_result = isinstance(content, list) and any(isinstance(c, dict) and c.get('type') == 'tool_result' for c in content)
                if not tool_result and not x.get('isMeta'):
                    new = prompt_label(text_content(content))
                    linking.note_claude_user(meta, node, text_content(content))
                    if new:
                        turn, label = x.get('uuid', f'{sid}:{line}'), new
                        linking.note_turn(meta, 'claude', node, x.get('timestamp'), turn)
            if typ != 'assistant' or not isinstance(msg.get('usage'), dict):
                continue
            u, model = msg['usage'], msg.get('model', 'unknown')
            event_id = msg.get('id') or x.get('requestId') or x.get('uuid') or f'{sid}:{line}'
            event_turn, confidence = turn, 'reported'
            norm = normalize(u, 'anthropic')
        else:
            if x.get('schema') != 'token-audit/v1':
                warnings.add(f'{path}:{line}: unknown import schema')
                continue
            if not all(x.get(k) for k in ('event_id', 'session_id', 'usage_kind')) or not isinstance(x.get('usage'), dict):
                warnings.add(f'{path}:{line}: missing import identity or usage')
                continue
            if x['usage_kind'] not in ('openai', 'anthropic', 'canonical'):
                warnings.add(f'{path}:{line}: unknown usage_kind')
                continue
            sid, turn, model = x['session_id'], x.get('turn_id', 'unattributed'), x.get('model', 'unknown')
            label = prompt_label(x.get('prompt_label', ''))
            event_id, event_turn, confidence = x['event_id'], turn, x.get('measurement', 'reported')
            if confidence not in ('reported', 'estimated'):
                warnings.add(f'{path}:{line}: unknown measurement')
                continue
            norm = normalize(x['usage'], x['usage_kind'])
        if norm['input_tokens'] is None or norm['output_tokens'] is None:
            warnings.add(f'{path}:{line}: missing input/output usage; event excluded')
            continue
        total = norm['input_tokens'] + norm['output_tokens']
        if norm['cache_read_tokens'] is not None and norm['cache_read_tokens'] > norm['input_tokens']:
            warnings.add(f'{path}:{line}: cache read exceeds input; event excluded')
            continue
        result.append(dict(harness=x.get('harness', 'import') if kind == 'import' else kind,
                           event_id=str(event_id), session_id=str(sid), turn_id=str(event_turn),
                           model=model, prompt_label=label, timestamp=x.get('timestamp'), node=(node if kind == 'claude' else sid),
                           stage=x.get('stage', 'unattributed') if kind == 'import' else 'unattributed',
                           measurement=confidence, total_tokens=total, source=str(path), source_line=line, **norm))
    if records and not result:
        warnings.add(f'{path}: no supported usage events found')
    return result


def collect(roots):
    warnings, unique, files, meta = set(), {}, 0, linking.new_meta()
    for kind, root in roots:
        root = Path(root).expanduser()
        if not root.exists():
            warnings.add(f'{root}: source does not exist')
            continue
        paths = sorted(root.rglob('*.jsonl')) if root.is_dir() else [root]
        for path in paths:
            files += 1
            for row in parse(path, kind, warnings, meta):
                # Response IDs survive transcript copies, resumes and repeated stream chunks.
                key = (row['harness'], row['event_id'])
                old = unique.get(key)
                if old:
                    # Keep fullest usage snapshot without summing streamed duplicates.
                    for k in FIELDS:
                        vals = [v for v in (old.get(k), row.get(k)) if v is not None]
                        row[k] = max(vals) if vals else None
                    row['total_tokens'] = row['input_tokens'] + row['output_tokens']
                    if not row['prompt_label']:
                        row['prompt_label'] = old['prompt_label']
                unique[key] = row
    rows = linking.link(list(unique.values()), meta)
    return rows, sorted(warnings), files


def atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.token-audit-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def report(rows, warnings, files, out, include_prompts=False, html_out=False, prices_path=None):
    if not include_prompts:
        rows = [dict(r, prompt_label=hashlib.sha256(r['prompt_label'].encode()).hexdigest()[:12] if r['prompt_label'] else '') for r in rows]
    measured = [r for r in rows if r['measurement'] != 'estimated']
    estimated = [r for r in rows if r['measurement'] == 'estimated']
    total = sum(r['total_tokens'] for r in measured)
    lines = ['# Token audit', '', f'Snapshot: {time.strftime("%Y-%m-%d %H:%M:%S %z")}', '',
             f'{files} files scanned; {len(measured)} measured events; {total:,} measured tokens processed.',
             f'{len(estimated)} estimated events kept separately in ledger. {len(warnings)} coverage warnings.', '',
             'Input includes cached tokens; reasoning is a subset of output. These are processed tokens, not unique text, subscription quota, or a bill.',
             'Attribution is to a conversation turn, including repeated context. Stages are unknown unless explicitly supplied. Active responses may not yet be logged.', '']
    for title, keys in [('Highest-usage prompts', ('harness', 'session_id', 'turn_id')), ('Conversations', ('harness', 'session_id')), ('Models', ('harness', 'model')), ('Largest model calls', ('harness', 'event_id')), ('Explicit stages', ('stage',))]:
        groups = collections.defaultdict(list)
        for r in measured:
            groups[tuple(r[k] for k in keys)].append(r)
        lines += [f'## {title}', '', '| ID | Calls | Input | Output | Total | Prompt |', '|---|---:|---:|---:|---:|---|']
        for key, items in sorted(groups.items(), key=lambda kv: sum(r['total_tokens'] for r in kv[1]), reverse=True)[:20]:
            sums = [sum(r[k] for r in items) for k in ('input_tokens', 'output_tokens', 'total_tokens')]
            label = next((r['prompt_label'] for r in items if r['prompt_label']), '')
            esc = lambda s: str(s).replace('|', '\\|').replace('\n', ' ')
            lines.append(f'| {esc(" / ".join(key))} | {len(items)} | {sums[0]:,} | {sums[1]:,} | {sums[2]:,} | {esc(label)} |')
        lines += ['']
    lines += ['## Coverage warnings', ''] + [f'- {w}' for w in warnings[:100]]
    if len(warnings) > 100:
        lines += ['- Additional warnings are in coverage.json.']
    atomic(out / 'ledger.jsonl', ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
    atomic(out / 'report.md', '\n'.join(lines) + '\n')
    coverage = {'files': files, 'measured_events': len(measured), 'estimated_events': len(estimated), 'measured_tokens': total, 'warnings': warnings}
    atomic(out / 'coverage.json', json.dumps(coverage, indent=2))
    if html_out:
        try:
            prices = html_report.load_prices(prices_path.expanduser()) if prices_path else None
            atomic(out / 'report.html', html_report.render(html_report.build_model(rows, coverage, prices)))
        except Exception as e:  # the ledger is the product; a report failure must not lose it
            print(f'report.html skipped: {type(e).__name__}: {e}', flush=True)
    return total


def default_roots():
    """Look in the usual places; a tool that is not installed here is simply skipped, not an error."""
    candidates = [('codex', Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'sessions'), ('claude', Path.home() / '.claude/projects')]
    roots = [c for c in candidates if c[1].exists()] or candidates
    # Claude desktop app sessions that run Claude Code keep transcripts in the app's data folder.
    # Locations differ by OS and are added only if they exist; unverified on every platform.
    home, appdata = Path.home(), os.environ.get('APPDATA')
    for base in (home / 'Library/Application Support/Claude', Path(appdata) / 'Claude' if appdata else None, home / '.config/Claude'):
        candidate = base / 'local-agent-mode-sessions' if base else None
        if candidate and candidate.exists():
            roots.append(('claude', candidate))
    return roots


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--codex', action='append', default=[], metavar='PATH')
    ap.add_argument('--claude', action='append', default=[], metavar='PATH')
    ap.add_argument('--import-jsonl', action='append', default=[], metavar='PATH')
    ap.add_argument('--out', type=Path, default=Path.cwd() / 'token-audit-output')
    ap.add_argument('--include-prompts', action='store_true', help='Store up to 160 characters of each prompt locally instead of its hash')
    ap.add_argument('--prices', type=Path, metavar='PATH', help='Optional JSON price list; adds a labelled cost estimate to report.html')
    ap.add_argument('--no-html', action='store_true', help='Skip writing the plain-language report.html')
    ap.add_argument('--watch', type=int, metavar='SECONDS', help='Rescan while this process is running; minimum 10 seconds')
    args = ap.parse_args()
    if args.watch is not None and args.watch < 10:
        ap.error('--watch must be at least 10')
    roots = [(kind, p) for kind, paths in [('codex', args.codex), ('claude', args.claude), ('import', args.import_jsonl)] for p in paths]
    if not roots:
        roots = default_roots()
    try:
        while True:
            rows, warnings, files = collect(roots)
            total = report(rows, warnings, files, args.out.expanduser(), args.include_prompts, html_out=not args.no_html, prices_path=args.prices)
            print(f'{len(rows)} events; {total:,} measured tokens; {len(warnings)} coverage warnings. {args.out / "report.md"}', flush=True)
            if not args.watch:
                break
            time.sleep(args.watch)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
