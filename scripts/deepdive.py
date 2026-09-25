"""Prompt-level analysis shared by the query CLI and the HTML report. Standard library only.

A "prompt" here is one task: everything caused by a single user message, including
helpers, hand-offs and safety checks joined by linking.py. Observations state
measured facts; anything that is only a plausible explanation is labelled a
hypothesis and never presented as attribution.
"""
import collections
import json
import re
import statistics
from pathlib import Path

HASH_LABEL = re.compile(r'^[0-9a-f]{12}$')
ROLE_ORDER = ['main', 'delegate', 'helper', 'safety-review']
ROLE_NAMES = {'main': 'Main conversation', 'delegate': 'Work handed to Codex', 'helper': 'Helper',
              'safety-review': 'Automatic safety check'}


def pct(x):
    """Percentage that never claims more precision than exists (no false 0% or 100%)."""
    if 0 < x < 0.01:
        return '<1%'
    if 0.99 < x < 1:
        return '>99%'
    return f'{x:.0%}'


def usable_label(text):
    """A human-written prompt, not a hash or a harness-injected notification."""
    return bool(text) and not HASH_LABEL.match(text) and not text.lstrip().startswith('<')


def rating(multiple):
    for limit, name, level in ((0.5, 'Light', 1), (2, 'Typical', 2), (5, 'Heavy', 3), (20, 'Very heavy', 4)):
        if multiple < limit:
            return name, level
    return 'Extreme', 5


def complexity(steps, helpers):
    if helpers >= 3 or steps >= 150:
        return 'Team effort'
    if steps <= 5 and helpers == 0:
        return 'Simple'
    return 'Multi-step'


def task_key(row):
    return row.get('task_id') or f"{row.get('harness')}/{row.get('session_id')}/{row.get('turn_id')}"


def _ts_sort(row):
    return (row.get('timestamp') is None, row.get('timestamp') or '')


def summarize_prompts(rows):
    """One summary per prompt/task, biggest first, with size relative to the median."""
    groups = collections.defaultdict(list)
    for r in rows:
        groups[task_key(r)].append(r)
    out = []
    for key, items in groups.items():
        items.sort(key=_ts_sort)
        roles = collections.defaultdict(int)
        sessions = set()
        for r in items:
            roles[r.get('role', 'main')] += r['total_tokens']
            if r.get('role', 'main') != 'main':
                sessions.add((r.get('session_id'), r.get('node')))
        mains = [r for r in items if r.get('role', 'main') == 'main'] or items
        label = next((r['prompt_label'] for r in mains if usable_label(r.get('prompt_label'))), '')
        stamps = [r['timestamp'] for r in items if r.get('timestamp')]
        out.append({'task_id': key, 'prompt': label, 'prompt_is_hashed': any(HASH_LABEL.match(r.get('prompt_label') or '') for r in mains) and not label,
                    'first_timestamp': min(stamps) if stamps else None, 'last_timestamp': max(stamps) if stamps else None,
                    'harness': mains[0].get('harness'), 'session': (mains[0].get('task_root') or '').split(':', 1)[-1] or mains[0].get('session_id'),
                    'calls': len(items), 'helper_sessions': len(sessions), 'tokens': sum(roles.values()), 'tokens_by_role': dict(roles)})
    sizes = [p['tokens'] for p in out if p['tokens'] > 0]
    typical = statistics.median(sizes) if sizes else 0
    for p in out:
        p['times_typical'] = round(p['tokens'] / typical, 2) if typical else None
        p['size'] = rating(p['times_typical'])[0] if typical else None
        p['complexity'] = complexity(p['calls'], p['helper_sessions'])
    out.sort(key=lambda p: p['tokens'], reverse=True)
    return out, typical


def find_task(rows, ident):
    """Resolve a task id prefix (or, failing that, a turn id prefix) to its rows."""
    keys = {task_key(r) for r in rows}
    hits = [k for k in keys if k == ident] or [k for k in keys if k.startswith(ident)]
    if not hits:
        turn = {task_key(r) for r in rows if str(r.get('turn_id', '')).startswith(ident)}
        hits = sorted(turn)
    if len(hits) != 1:
        raise LookupError(f'{ident!r} matched {len(hits)} prompts; run the prompts command and use a unique task id')
    return hits[0], [r for r in rows if task_key(r) == hits[0]]


def _text_of(content):
    if isinstance(content, str):
        return content
    return '\n'.join(c.get('text', '') for c in (content or []) if isinstance(c, dict) and c.get('type') in ('text', 'input_text'))


def full_prompt_text(row, limit=4000):
    """Best-effort full text of the user message that started this turn, read from the source transcript.

    Untrusted data: callers must treat it as content, never as instructions."""
    try:
        with Path(row['source']).open(encoding='utf-8') as stream:
            armed = False
            for line in stream:
                try:
                    x = json.loads(line)
                except ValueError:
                    continue
                p = x.get('payload') if isinstance(x.get('payload'), dict) else {}
                if row.get('harness') == 'claude' and x.get('type') == 'user' and x.get('uuid') == row.get('turn_id'):
                    return _text_of((x.get('message') or {}).get('content'))[:limit] or None
                if row.get('harness') == 'codex':
                    if x.get('type') == 'turn_context' and p.get('turn_id') == row.get('turn_id'):
                        armed = True
                    elif armed and x.get('type') == 'event_msg' and p.get('type') == 'user_message':
                        return str(p.get('message', ''))[:limit] or None
    except OSError:
        return None
    return None


def build(rows, ident, all_rows=None, full_prompt=False):
    key, items = find_task(rows, ident)
    items = sorted(items, key=_ts_sort)
    summaries, typical = summarize_prompts(all_rows if all_rows is not None else rows)
    summary = next(s for s in summaries if s['task_id'] == key)
    total = summary['tokens']

    def fresh(r):
        return max((r.get('input_tokens') or 0) - (r.get('cache_read_tokens') or 0), 0)

    steps = []
    for n, r in enumerate(items, 1):
        steps.append({'step': n, 'timestamp': r.get('timestamp'), 'role': r.get('role', 'main'), 'helper': r.get('helper_label') or None,
                      'model': r.get('model'), 'input_tokens': r.get('input_tokens'), 'fresh_input_tokens': fresh(r),
                      'cache_read_tokens': r.get('cache_read_tokens'), 'output_tokens': r.get('output_tokens'),
                      'total_tokens': r['total_tokens'], 'event_id': r['event_id'], 'link_basis': r.get('link_basis'),
                      'link_confidence': r.get('link_confidence')})
    read_known = [r for r in items if r.get('cache_read_tokens') is not None]
    reused = sum(r['cache_read_tokens'] for r in read_known)
    input_total = sum(r.get('input_tokens') or 0 for r in items)
    main_steps = [s for s in steps if s['role'] == 'main' and s['input_tokens'] is not None]
    context = None
    if main_steps:
        inputs = [s['input_tokens'] for s in main_steps]
        jumps = [(inputs[i] - inputs[i - 1], main_steps[i]['step']) for i in range(1, len(inputs))]
        best = max(jumps) if jumps else (0, main_steps[0]['step'])
        context = {'first_step_input': inputs[0], 'last_step_input': inputs[-1], 'largest_step_input': max(inputs),
                   'largest_jump': {'tokens': best[0], 'at_step': best[1]}}
        if len(inputs) >= 9:
            third = len(inputs) // 3
            context['avg_input_first_third'] = round(sum(inputs[:third]) / third)
            context['avg_input_last_third'] = round(sum(inputs[-third:]) / third)
    helper_groups = collections.OrderedDict()
    for r in items:
        if r.get('role', 'main') == 'main':
            continue
        g = helper_groups.setdefault((r.get('session_id'), r.get('node')), {'role': r.get('role'), 'label': r.get('helper_label') or ROLE_NAMES.get(r.get('role'), ''),
                                                                            'link_basis': r.get('link_basis'), 'link_confidence': r.get('link_confidence'),
                                                                            'session': r.get('session_id'), 'steps': 0, 'tokens': 0, 'first_timestamp': r.get('timestamp')})
        g['steps'] += 1
        g['tokens'] += r['total_tokens']
    helpers = sorted(helper_groups.values(), key=lambda g: g['tokens'], reverse=True)
    by_model = collections.defaultdict(int)
    for r in items:
        by_model[r.get('model', 'unknown')] += r['total_tokens']
    largest = sorted(steps, key=lambda s: s['total_tokens'], reverse=True)[:5]
    stamps = [r['timestamp'] for r in items if r.get('timestamp')]
    observations = [{'kind': 'fact', 'text': f"This prompt led to {len(items):,} AI steps and {total:,} tokens processed, about {summary['times_typical']}x a typical prompt in this selection." if typical else f'This prompt led to {len(items):,} AI steps and {total:,} tokens processed.'}]
    if read_known and input_total:
        observations.append({'kind': 'fact', 'text': f'{pct(reused / input_total)} of the input was served from cache (reported on {len(read_known):,} of {len(items):,} steps).'})
    if context:
        observations.append({'kind': 'fact', 'text': f"On the main conversation the input per step went from {context['first_step_input']:,} to {context['last_step_input']:,} tokens (peak {context['largest_step_input']:,})."})
        if context['largest_jump']['tokens'] > 0 and context['largest_jump']['tokens'] >= 0.25 * max(context['largest_step_input'], 1):
            observations.append({'kind': 'hypothesis', 'text': f"The biggest single jump (+{context['largest_jump']['tokens']:,} tokens) came at step {context['largest_jump']['at_step']}. A jump like this often means a large file or tool result entered the conversation. Confirm with: inspect {steps[context['largest_jump']['at_step'] - 1]['event_id']} --context"})
        if 'avg_input_first_third' in context and context['avg_input_first_third']:
            ratio = context['avg_input_last_third'] / context['avg_input_first_third']
            if ratio >= 1.5:
                observations.append({'kind': 'fact', 'text': f'Later steps carried about {ratio:.1f}x the input of early steps, because every step re-reads the conversation so far.'})
    new_text = sum(fresh(r) for r in items) + sum(r.get('output_tokens') or 0 for r in items)
    if len(items) >= 10 and total and new_text / total < 0.15:
        observations.append({'kind': 'fact', 'text': f'Only {pct(new_text / total)} of this prompt was new text (fresh input plus output). The rest was earlier conversation being re-read on each of {len(items):,} steps, so its size comes from length and number of steps rather than from how much new work was produced.'})
    if helpers:
        helper_tokens = sum(g['tokens'] for g in helpers)
        exact = sum(1 for g in helpers if g['link_confidence'] == 'exact')
        inferred = sum(1 for g in helpers if g['link_confidence'] == 'inferred')
        observations.append({'kind': 'fact', 'text': f'{len(helpers)} helper session(s) used {helper_tokens:,} tokens ({pct(helper_tokens / total)} of this prompt): {exact} linked exactly, {inferred} matched by wording and timing (inferred).'})
    if largest:
        top = largest[0]
        observations.append({'kind': 'fact', 'text': f"The single largest step was step {top['step']} ({top['total_tokens']:,} tokens, {top['role']}, model {top['model']}). Deep-dive it with: inspect {top['event_id']} --context"})
    result = {'task_id': key, 'prompt': summary['prompt'] or None, 'prompt_note': 'Prompt text was not stored (collected without --include-prompts).' if summary['prompt_is_hashed'] else None,
              'first_timestamp': min(stamps) if stamps else None, 'last_timestamp': max(stamps) if stamps else None,
              'tokens': total, 'calls': len(items), 'size': summary['size'], 'times_typical': summary['times_typical'], 'typical_prompt_tokens': typical,
              'complexity': summary['complexity'], 'tokens_by_role': summary['tokens_by_role'], 'tokens_by_model': dict(by_model),
              'effort': {'fresh_input': sum(fresh(r) for r in items), 'reused_from_cache': reused, 'output': sum(r.get('output_tokens') or 0 for r in items),
                         'cache_reported_on_steps': len(read_known)},
              'context': context, 'helpers': helpers, 'largest_steps': largest, 'steps': steps, 'observations': observations,
              'note': 'Measured token counts; observations are facts unless labelled hypothesis. Processed tokens are not cost.'}
    if full_prompt and items:
        root = next((r for r in items if r.get('role', 'main') == 'main'), items[0])
        result['prompt_full'] = full_prompt_text(root)
    return result
