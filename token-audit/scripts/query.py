#!/usr/bin/env python3
"""Read-only summary and call inspection for token-audit snapshots."""
import argparse
from collections import defaultdict
from pathlib import Path
import json


def categories(rows):
    result = []
    for field in ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'reasoning_tokens'):
        known = [r[field] for r in rows if r.get(field) is not None]
        result.append({'category': field, 'known_tokens': sum(known), 'known_calls': len(known), 'total_calls': len(rows),
                       'subset_of': 'input_tokens' if field.startswith('cache_') else 'output_tokens' if field == 'reasoning_tokens' else None})
    return result


def context_inventory(row):
    """Inventory visible serialized transcript text, NOT reconstructed model input."""
    path = Path(row['source'])
    entries = []
    with path.open(encoding='utf-8') as stream:
        for line_no, line in enumerate(stream, 1):
            if line_no > row['source_line']:
                break
            try:
                x = json.loads(line)
            except ValueError:
                continue
            p = x.get('payload', {})
            parts = []
            if row['harness'] == 'codex':
                if x.get('type') == 'session_meta':
                    base = p.get('base_instructions', {})
                    parts = [('system_instructions', base.get('text', '') if isinstance(base, dict) else str(base))]
                elif x.get('type') == 'response_item':
                    kind = p.get('type')
                    if kind == 'message':
                        parts = [(p.get('role', 'unknown') + '_message', c.get('text', '')) for c in p.get('content', []) if isinstance(c, dict)]
                    elif kind in ('function_call_output', 'custom_tool_call_output'):
                        value = p.get('output', '')
                        parts = [('tool_result', value if isinstance(value, str) else json.dumps(value))]
                    elif kind in ('function_call', 'custom_tool_call'):
                        parts = [('tool_call', str(p.get('arguments', p.get('input', ''))))]
            elif row['harness'] == 'claude' and x.get('type') in ('assistant', 'user'):
                content = x.get('message', {}).get('content', [])
                if isinstance(content, str):
                    content = [{'type': 'text', 'text': content}]
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    kind = c.get('type')
                    if kind in ('text', 'thinking'):
                        parts.append((x['type'] + '_' + kind, c.get('text', c.get('thinking', ''))))
                    elif kind in ('tool_result', 'tool_use'):
                        parts.append((kind, json.dumps(c.get('content', c.get('input', '')), ensure_ascii=False)))
            for kind, text in parts:
                if text:
                    entries.append({'source_line': line_no, 'category': kind, 'characters': len(text)})
    totals = defaultdict(int)
    for e in entries:
        totals[e['category']] += e['characters']
    return {'warning': 'Visible transcript inventory through the usage record, NOT exact request context or token attribution. It can include removed/compacted history, duplicated streaming blocks and the current response; it can omit hidden instructions, images and tool schemas. Character size is only a clue.',
            'characters_by_category': dict(totals), 'largest_visible_blocks': sorted(entries, key=lambda e: e['characters'], reverse=True)[:10]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['summary', 'categories', 'calls', 'inspect'])
    parser.add_argument('call_id', nargs='?')
    parser.add_argument('--ledger', type=Path, default=Path.home()/'.local/share/token-audit/ledger.jsonl')
    parser.add_argument('--harness')
    parser.add_argument('--session')
    parser.add_argument('--since', help='Inclusive ISO-8601 date or timestamp (UTC)')
    parser.add_argument('--group-by', choices=['harness','model','session_id','turn_id','stage'], default='model')
    parser.add_argument('--top', type=int, default=10)
    parser.add_argument('--context', action='store_true', help='Inspect source transcript sizes; not token counts')
    args = parser.parse_args()
    try:
        rows = [json.loads(l) for l in args.ledger.expanduser().read_text().splitlines() if l.strip()]
    except (OSError, ValueError) as e:
        parser.error(f'Cannot read ledger: {e}')
    from datetime import datetime, timezone
    def date(value):
        d = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
    try:
        since = date(args.since) if args.since else None
    except ValueError:
        parser.error('--since must be an ISO-8601 date or timestamp')
    selected = []
    for r in rows:
        if args.harness and r['harness'] != args.harness or args.session and r['session_id'] != args.session:
            continue
        if since:
            try:
                if not r.get('timestamp') or date(r['timestamp']) < since:
                    continue
            except ValueError:
                continue
        selected.append(r)
    rows = selected
    measured = [r for r in rows if r['measurement'] != 'estimated']
    result = {'snapshot_modified': datetime.fromtimestamp(args.ledger.expanduser().stat().st_mtime, timezone.utc).isoformat(),
              'note': 'Processed tokens, not cost or quota. Cache/reasoning categories overlap input/output. Missing timestamps are excluded by --since.',
              'estimated_calls_excluded': len(rows)-len(measured)}
    coverage_path = args.ledger.expanduser().parent/'coverage.json'
    if coverage_path.exists():
        try:
            result['snapshot_coverage_warnings'] = len(json.loads(coverage_path.read_text()).get('warnings', []))
        except ValueError:
            result['snapshot_coverage_warnings'] = 'unreadable'
    if args.command == 'inspect':
        if not args.call_id:
            parser.error('inspect requires a call ID from calls')
        exact = [r for r in rows if r['event_id'] == args.call_id]
        found = exact or [r for r in rows if r['event_id'].startswith(args.call_id)]
        if len(found) != 1:
            parser.error(f'Call ID matched {len(found)} calls; use a unique ID and optionally --harness')
        row = found[0]
        result['call'] = row
        result['categories'] = categories([row])
        if args.context:
            try:
                result['visible_transcript'] = context_inventory(row)
            except (OSError, KeyError) as e:
                result['context_unavailable'] = str(e)
    elif args.command == 'categories':
        result['categories'] = categories(measured)
    elif args.command == 'calls':
        result['calls'] = sorted(measured, key=lambda r:r['total_tokens'], reverse=True)[:max(0,args.top)]
    else:
        groups = defaultdict(list)
        for r in measured:
            key = r.get(args.group_by, 'unknown')
            if args.group_by == 'turn_id':
                key = r['harness'] + '/' + r['session_id'] + '/' + key
            groups[key].append(r)
        result.update(calls=len(measured), total_tokens=sum(r['total_tokens'] for r in measured), categories=categories(measured))
        result['groups'] = sorted([{'group': k, 'calls': len(v), 'tokens': sum(r['total_tokens'] for r in v)} for k,v in groups.items()],key=lambda x:x['tokens'],reverse=True)[:max(0,args.top)]
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
