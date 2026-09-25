#!/usr/bin/env python3
"""Plain-language, self-contained HTML report from a token-audit ledger.

Standard library only; no network requests; the page loads nothing external.
Written for people who do not know what a token is: every number is paired
with an everyday comparison, and every chart has a table view.

    python3 html_report.py --ledger ~/.local/share/token-audit/ledger.jsonl --out report.html
"""
import argparse
import collections
import datetime
import html
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deepdive import complexity, rating, usable_label  # noqa: E402
import deepdive  # noqa: E402
import periods  # noqa: E402

TOKENS_PER_PAGE = 667        # ~500 words per page at ~0.75 words per token (rule of thumb)
TOKENS_PER_BOOK = 200_000    # ~300 pages
HASH_LABEL = re.compile(r'^[0-9a-f]{12}$')

HARNESS_NAMES = {'claude': 'Claude', 'codex': 'Codex'}
ROLE_ORDER = ['main', 'delegate', 'helper', 'safety-review']
ROLE_NAMES = {'main': 'Your conversations', 'delegate': 'Work handed to Codex', 'helper': 'Helpers the AI started',
              'safety-review': 'Automatic safety checks'}
ROLE_BLURB = {'main': 'The AI you talk to directly.',
              'delegate': 'Bigger jobs one AI tool passed to another (for example Claude to Codex, or Codex to Claude). Matched by wording and timing, so treat as a strong estimate.',
              'helper': 'Smaller sub-tasks an AI started to work in parallel.',
              'safety-review': 'A second AI that double-checks risky actions before they run.'}
ROLE_VAR = {'main': '--c1', 'delegate': '--c2', 'helper': '--c3', 'safety-review': '--c4'}


def configure_labels(model):
    """Name hand-offs after the tool that received them; neutral when several did."""
    targets = model.get('delegate_targets') or set()
    ROLE_NAMES['delegate'] = ('Work handed to ' + HARNESS_NAMES.get(next(iter(targets)), next(iter(targets)).title())) if len(targets) == 1 else 'Work handed to another AI tool'


def esc(x):
    return html.escape(str(x), quote=True)


def fmt_tokens(n):
    for size, word in ((1e9, 'billion'), (1e6, 'million'), (1e3, 'thousand')):
        if n >= size:
            value = n / size
            return f'{value:.1f} {word}' if value < 100 else f'{value:.0f} {word}'
    return f'{int(n):,}'


def fmt_short(n):
    for size, suffix in ((1e9, 'B'), (1e6, 'M'), (1e3, 'K')):
        if n >= size:
            value = n / size
            return f'{value:.0f}{suffix}' if value >= 10 or value == int(value) else f'{value:.1f}{suffix}'
    return f'{int(n)}'


def pct(x):
    return '<1%' if 0 < x < 0.01 else f'{x:.0%}'


def fmt_int(n):
    return f'{int(round(n)):,}'


def fmt_pages(tokens):
    pages = tokens / TOKENS_PER_PAGE
    if pages >= 2000:
        return f'{pages / 300:,.0f} books'
    if pages >= 1:
        return f'{pages:,.0f} pages'
    return 'less than a page'


def fmt_money(amount, currency):
    symbol = {'USD': '$', 'EUR': '€', 'GBP': '£'}.get(currency, '')
    suffix = '' if symbol else f' {currency}'
    return f'{symbol}{amount:,.2f}{suffix}' if amount < 1000 else f'{symbol}{amount:,.0f}{suffix}'


def parse_ts(ts):
    if not isinstance(ts, str):
        return None
    try:
        return datetime.datetime.fromisoformat(ts.replace('Z', '+00:00')).astimezone()
    except ValueError:
        return None


def load_ledger(path):
    rows = []
    with Path(path).expanduser().open(encoding='utf-8') as stream:
        for line in stream:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def load_prices(path):
    data = json.loads(Path(path).expanduser().read_text(encoding='utf-8'))
    for entry in data.get('models', []):
        entry['match'] = str(entry.get('match', '')).lower()
    return data


def price_for(model, prices):
    lowered = str(model).lower()
    best = None
    for entry in prices.get('models', []):
        if entry['match'] and entry['match'] in lowered and (best is None or len(entry['match']) > len(best['match'])):
            best = entry
    return best


def row_cost(row, rate):
    """Cost of one call from a user-supplied price entry. None if it cannot be computed."""
    per = 1_000_000
    inp, out = row.get('input_tokens'), row.get('output_tokens')
    if inp is None or out is None or rate.get('input_per_million') is None or rate.get('output_per_million') is None:
        return None
    read, write = row.get('cache_read_tokens') or 0, row.get('cache_write_tokens') or 0
    fresh = max(inp - read - write, 0)
    read_rate = rate.get('cache_read_per_million', rate['input_per_million'])
    write_rate = rate.get('cache_write_per_million', rate['input_per_million'])
    return (fresh * rate['input_per_million'] + read * read_rate + write * write_rate + out * rate['output_per_million']) / per


def build_model(rows, coverage=None, prices=None, since=None, now=None, period_label=None, period=None):
    now = now or datetime.datetime.now().astimezone()
    measured = [r for r in rows if r.get('measurement') != 'estimated' and r.get('total_tokens') is not None]
    if since:
        cutoff = parse_ts(since if 'T' in since else since + 'T00:00:00+00:00')
        measured = [r for r in measured if (parse_ts(r.get('timestamp')) or now) >= cutoff] if cutoff else measured
    model = {'generated': now, 'since': since, 'period_label': (period['label'] if period else period_label), 'period_kind': period.get('kind') if period else None,
             'period_days': ((period['start'].astimezone().date(), (period['end'] - datetime.timedelta(microseconds=1)).astimezone().date()) if period else None), 'currency': (prices or {}).get('currency'), 'priced': bool(prices)}
    total = sum(r['total_tokens'] for r in measured)
    fresh = reused = writing = 0
    unknown_cache = 0
    for r in measured:
        inp, out, read = r.get('input_tokens') or 0, r.get('output_tokens') or 0, r.get('cache_read_tokens')
        if read is None:
            unknown_cache += 1
            read = 0
        reused += read
        fresh += max(inp - read, 0)
        writing += out
    model.update(total=total, calls=len(measured), fresh=fresh, reused=reused, writing=writing, unknown_cache_calls=unknown_cache)

    cost_total, unpriced_models = 0.0, collections.Counter()
    task_rows = collections.defaultdict(list)
    for r in measured:
        task_rows[r.get('task_id') or r['turn_id']].append(r)
        if prices:
            rate = price_for(r.get('model'), prices)
            cost = row_cost(r, rate) if rate else None
            r['_cost'] = cost
            if cost is None:
                unpriced_models[r.get('model', 'unknown')] += r['total_tokens']
            else:
                cost_total += cost
    model.update(cost=cost_total if prices else None, unpriced=unpriced_models.most_common(5))

    session_title = {}
    for r in sorted(measured, key=lambda r: r.get('timestamp') or ''):
        key = (r.get('harness'), r.get('session_id'))
        if key not in session_title and usable_label(r.get('prompt_label')) and len(r['prompt_label']) >= 30:
            session_title[key] = r['prompt_label']
    tasks = []
    for task_id, items in task_rows.items():
        by_role = collections.defaultdict(int)
        parts = collections.defaultdict(lambda: [0, set()])
        for r in items:
            role = r.get('role', 'main')
            by_role[role] += r['total_tokens']
            if role != 'main':
                parts[role][0] += r['total_tokens']
                parts[role][1].add((r.get('session_id'), r.get('node')))
        stamps = sorted(t for t in (parse_ts(r.get('timestamp')) for r in items) if t)
        root_rows = [r for r in items if r.get('role', 'main') == 'main'] or items
        label = next((r['prompt_label'] for r in root_rows if usable_label(r.get('prompt_label'))), '')
        harness = root_rows[0].get('harness', 'unknown')
        helper_sessions = sum(len(v[1]) for v in parts.values())
        tasks.append({
            'context': '' if len(label) >= 30 else session_title.get((items[0].get('harness'), items[0].get('session_id')), ''),
            'id': task_id, 'total': sum(by_role.values()), 'by_role': dict(by_role), 'steps': len(items),
            'helpers': helper_sessions, 'label': label, 'harness': harness, 'start': stamps[0] if stamps else None,
            'max_context': max((r.get('input_tokens') or 0) for r in items),
            'cost': (sum(r['_cost'] for r in items if r.get('_cost') is not None) if prices else None),
            'parts': {role: (tok, len(ss)) for role, (tok, ss) in parts.items()},
            'models': sorted({r.get('model', 'unknown') for r in items}),
        })
    sizes = [t['total'] for t in tasks if t['total'] > 0]
    typical = statistics.median(sizes) if sizes else 0
    for t in tasks:
        t['multiple'] = t['total'] / typical if typical else 0
        t['rating'], t['rating_level'] = rating(t['multiple'])
        t['complexity'] = complexity(t['steps'], t['helpers'])
    tasks.sort(key=lambda t: t['total'], reverse=True)
    model.update(tasks=tasks, typical=typical, task_count=len(tasks))

    role_totals = collections.defaultdict(int)
    for r in measured:
        role_totals[r.get('role', 'main')] += r['total_tokens']
    model['roles'] = [(role, role_totals[role]) for role in ROLE_ORDER if role_totals.get(role)]
    model['delegate_targets'] = {r.get('harness') for r in measured if r.get('role') == 'delegate'}

    conversations = collections.defaultdict(list)
    for r in measured:
        conversations[r.get('task_root') or f"{r.get('harness')}:{r.get('session_id')}"].append(r)
    sessions = []
    for root, items in conversations.items():
        items.sort(key=lambda r: r.get('timestamp') or '')
        mains = [r for r in items if r.get('role', 'main') == 'main'] or items
        labels = [r['prompt_label'] for r in mains if usable_label(r.get('prompt_label'))]
        name = next((l for l in labels if len(l) >= 25), labels[0] if labels else '')
        stamps = [t for t in (parse_ts(r.get('timestamp')) for r in items) if t]
        harness = root.split(':', 1)[0]
        sessions.append({'id': root.split(':', 1)[-1], 'harness': harness, 'name': name, 'start': stamps[0] if stamps else None, 'end': stamps[-1] if stamps else None,
                         'tokens': sum(r['total_tokens'] for r in items), 'prompts': len({r.get('task_id') or r.get('turn_id') for r in items}),
                         'helpers': len({(r.get('session_id'), r.get('node')) for r in items if r.get('role', 'main') != 'main'})})
    sessions.sort(key=lambda x: x['tokens'], reverse=True)
    model['sessions'] = sessions

    days = collections.defaultdict(int)
    for r in measured:
        stamp = parse_ts(r.get('timestamp'))
        if stamp:
            days[stamp.date()] += r['total_tokens']
    model['days'] = days
    by_tool, by_model = collections.defaultdict(int), collections.defaultdict(int)
    for r in measured:
        by_tool[HARNESS_NAMES.get(r.get('harness'), str(r.get('harness', 'other')).title())] += r['total_tokens']
        by_model[r.get('model', 'unknown')] += r['total_tokens']
    model['tools'] = sorted(by_tool.items(), key=lambda kv: -kv[1])
    ranked = sorted(((k, v) for k, v in by_model.items() if v > 0), key=lambda kv: -kv[1])
    top, rest = ranked[:6], sum(v for _, v in ranked[6:])
    model['models'] = top + ([('All other models', rest)] if rest else [])
    stamps = sorted(t for t in (parse_ts(r.get('timestamp')) for r in measured) if t)
    model['first'], model['last'] = (stamps[0], stamps[-1]) if stamps else (None, None)
    links = collections.Counter()
    seen = set()
    for r in measured:
        if r.get('role', 'main') != 'main':
            key = (r.get('session_id'), r.get('node'))
            if key not in seen:
                seen.add(key)
                links[r.get('link_confidence') or 'unlinked'] += 1
    model['links'] = links
    model['coverage'] = coverage or {}
    return model


DARK_TEXT = {'--e0', '--c3', '--c4'}


def bar(share, css_var, label, tip):
    color = '#0b0b0b;text-shadow:none' if css_var in DARK_TEXT else '#fff'
    shown = label if share >= max(0.06, len(label) * 0.011) else ''
    return (f'<div class="seg" style="flex:{max(share, 0.0001):.5f};background:var({css_var});color:{color}" tabindex="0" '
            f'data-tip="{esc(tip)}" role="img" aria-label="{esc(tip)}"><span>{esc(shown)}</span></div>')


def render_effort(m):
    total = m['fresh'] + m['reused'] + m['writing']
    if not total:
        return '<p class="muted">Nothing to show yet.</p>'
    parts = [('Fresh reading', m['fresh'], '--e1', 'New text the AI had to read this time.'),
             ('Reused from memory', m['reused'], '--e0', 'Text the AI had already seen and kept ready. Usually cheaper and faster.'),
             ('Writing', m['writing'], '--e2', 'What the AI produced: answers, code, plans.')]
    segs = ''.join(bar(v / total, var, f'{name} {pct(v / total)}', f'{name}: {fmt_tokens(v)} tokens ({pct(v / total)}). {blurb}') for name, v, var, blurb in parts if v)
    legend = ''.join(f'<li><span class="dot" style="background:var({var})"></span><b>{esc(name)} · {pct(v / total)}</b> — {esc(blurb)}</li>' for name, v, var, blurb in parts)
    reused_pct = m['reused'] / total
    lead = (f'About <b>{reused_pct:.0%}</b> of what the AI read was material it had already seen — usually cheaper and faster than reading it fresh.'
            if m['reused'] else 'This tool did not report how much was reused from memory.')
    note = ''
    if m['unknown_cache_calls']:
        note = f'<p class="muted small">For {fmt_int(m["unknown_cache_calls"])} steps the tool did not say how much was reused; those are counted as fresh reading.</p>'
    return f'<p class="lead">{lead}</p><div class="stack" aria-label="Effort split">{segs}</div><ul class="legend">{legend}</ul>{note}'


def render_roles(m):
    if not m['roles']:
        return ''
    total = sum(v for _, v in m['roles']) or 1
    segs = ''.join(bar(v / total, ROLE_VAR[role], f'{ROLE_NAMES[role]} {pct(v / total)}', f'{ROLE_NAMES[role]}: {fmt_tokens(v)} tokens ({pct(v / total)}). {ROLE_BLURB[role]}') for role, v in m['roles'])
    legend = ''.join(f'<li><span class="dot" style="background:var({ROLE_VAR[role]})"></span><b>{esc(ROLE_NAMES[role])}</b> — {fmt_tokens(v)} tokens. {esc(ROLE_BLURB[role])}</li>' for role, v in m['roles'])
    hidden = total - dict(m['roles']).get('main', 0)
    lead = (f'<b>{hidden / total:.0%}</b> of the work happened behind the scenes — helpers, hand-offs and safety checks you never typed a message for.'
            if hidden else 'All of the work happened in the conversations you started yourself.')
    return f'<p class="lead">{lead}</p><div class="stack" aria-label="Who did the work">{segs}</div><ul class="legend">{legend}</ul>'


def render_task(rank, t, top_total, currency):
    title = t['label'] or f'{HARNESS_NAMES.get(t["harness"], "AI")} task' + (f' from {t["start"]:%b %-d}' if t['start'] else '')
    width = max(t['total'] / top_total, 0.01) * 100 if top_total else 0
    pieces = [f'{ROLE_NAMES[r]}: {fmt_tokens(v)}' for r, v in t['by_role'].items()]
    facts = [f'{fmt_int(t["steps"])} AI steps', f'{t["helpers"]} helper{"s" if t["helpers"] != 1 else ""} involved',
             f'held up to ~{fmt_pages(t["max_context"])} in mind at once']
    if t['cost'] is not None:
        facts.insert(0, f'≈ {fmt_money(t["cost"], currency)}')
    story = ''.join(f'<li><span class="dot" style="background:var({ROLE_VAR[r]})"></span>{esc(ROLE_NAMES[r])}: <b>{fmt_tokens(t["by_role"][r])}</b>' + (f' across {t["parts"][r][1]} session{"s" if t["parts"][r][1] != 1 else ""}' if r in t['parts'] else '') + '</li>' for r in ROLE_ORDER if r in t['by_role'])
    segs = ''.join(bar(v / t['total'], ROLE_VAR[r], '', f'{ROLE_NAMES[r]}: {fmt_tokens(v)} ({v / t["total"]:.0%})') for r, v in ((r, t['by_role'][r]) for r in ROLE_ORDER if r in t['by_role']) if t['total'])
    when = f'{t["start"]:%b %-d, %Y}' if t['start'] else 'date unknown'
    return (f'<details class="task"><summary><span class="rank">{rank}</span><span class="tmain"><span class="ttitle">{esc(title)}</span>'
            f'<span class="tmeta">{esc(when)} · {esc(HARNESS_NAMES.get(t["harness"], t["harness"]))} · {esc(t["complexity"])}</span>'
            + (f'<span class="tmeta">In a conversation that began: “{esc(t["context"][:90])}”</span>' if t['context'] and t['label'] else '') + '</span>'
            f'<span class="badge lvl{t["rating_level"]}">{esc(t["rating"])}</span><span class="tsize"><b>{fmt_tokens(t["total"])}</b> tokens<br>'
            f'<span class="muted small">≈ {esc(fmt_pages(t["total"]))} · {t["multiple"]:.1f}× typical</span></span>'
            f'<span class="meter" aria-hidden="true"><i style="width:{width:.1f}%"></i></span></summary>'
            f'<div class="tbody"><p>{" · ".join(esc(f) for f in facts)}</p><div class="stack thin" aria-label="Who did this task">{segs}</div>'
            f'<ul class="story">{story}</ul><p class="muted small">Models used: {esc(", ".join(t["models"]))}</p></div></details>')


def render_highlights(m):
    cards = []
    days = m['days']
    if days:
        best = max(days, key=days.get)
        cards.append(('Busiest day', f'{best:%A, %b %-d}', f'{fmt_tokens(days[best])} tokens'))
    if m['tasks']:
        t = m['tasks'][0]
        title = t['label'] or f'{HARNESS_NAMES.get(t["harness"], "AI")} task'
        cards.append(('Heaviest prompt', title[:70] + ('…' if len(title) > 70 else ''), f'{t["rating"]} · {t["multiple"]:.0f}× your typical prompt'))
    if m['sessions']:
        top = m['sessions'][0]
        share = top['tokens'] / m['total'] if m['total'] else 0
        cards.append(('Top conversation', session_name(top)[:70], f'{fmt_tokens(top["tokens"])} tokens · {pct(share)} of the total'))
    if m['models']:
        name, tokens = m['models'][0]
        cards.append(('Most-used model', name, f'{pct(tokens / m["total"] if m["total"] else 0)} of the work'))
    if not cards:
        return ''
    return '<div class="highlights">' + ''.join(f'<div class="hl"><span class="hlk">{esc(k)}</span><b>{esc(v)}</b><span class="hld">{esc(d)}</span></div>' for k, v, d in cards) + '</div>'


def session_name(s):
    if s['name']:
        return s['name']
    return f'{HARNESS_NAMES.get(s["harness"], "AI")} conversation' + (f' from {s["start"]:%b %-d}' if s['start'] else '')


def render_sessions(m):
    top = m['sessions'][:5]
    if not top:
        return '<p class="muted">No conversations found.</p>'
    peak = top[0]['tokens'] or 1
    items = ''
    for s in top:
        when = f'{s["start"]:%b %-d} to {s["end"]:%b %-d}' if s['start'] and s['end'] and s['start'].date() != s['end'].date() else (f'{s["start"]:%b %-d}' if s['start'] else '')
        detail = f'{when} · {s["prompts"]} prompt{"s" if s["prompts"] != 1 else ""} · {s["helpers"]} helper{"s" if s["helpers"] != 1 else ""}'
        items += (f'<li class="conv" data-tip="{esc(session_name(s))}" tabindex="0"><span class="cname">{esc(session_name(s)[:110])}<span class="cmeta">{esc(HARNESS_NAMES.get(s["harness"], s["harness"]))} · {esc(detail)}</span></span>'
                  f'<span class="htrack"><i style="width:{max(s["tokens"] / peak * 100, 1):.1f}%;background:var(--seq)"></i></span><span class="hval">{esc(fmt_tokens(s["tokens"]))}</span></li>')
    rows = ''.join(f'<tr><td>{esc(session_name(s)[:90])}</td><td><code>{esc(s["id"])}</code></td><td>{s["prompts"]}</td><td>{s["helpers"]}</td><td>{fmt_int(s["tokens"])}</td></tr>' for s in m['sessions'][:25])
    return (f'<p class="muted">Each conversation includes the helpers, hand-offs and safety checks it started. The name is how the conversation began.</p><ul class="hbars convs">{items}</ul>'
            f'<details class="tbl"><summary>Show the top 25 with their IDs</summary><table><thead><tr><th>Conversation</th><th>ID</th><th>Prompts</th><th>Helpers</th><th>Tokens</th></tr></thead><tbody>{rows}</tbody></table>'
            f'<p class="muted small">To look at just one, pass its ID to <code>--session</code> on any command.</p></details>')


def render_days(m):
    days = m['days']
    if not days:
        return '<p class="muted">No dates were found in the logs.</p>', ''
    last = max(days)
    start = max(min(days), last - datetime.timedelta(days=89))
    if m.get('period_days'):
        start, last = m['period_days']
        start = max(start, last - datetime.timedelta(days=89))
    span = [start + datetime.timedelta(days=i) for i in range((last - start).days + 1)]
    values = [days.get(d, 0) for d in span]
    peak = max(values) or 1
    step = 10 ** len(str(int(peak))) // 10 or 1
    ceil_ = -(-peak // (step * 2)) * step * 2 or 1
    width, height, left, bottom, top = 760, 230, 52, 28, 12
    plot_w, plot_h = width - left - 8, height - bottom - top
    slot = plot_w / len(span)
    short = len(span) <= 14
    bw = max(min(slot * (0.6 if short else 0.68), 56 if short else 22), 3)
    grid = ''
    for i in range(0, 5):
        y = top + plot_h - plot_h * i / 4
        grid += f'<line x1="{left}" x2="{width - 8}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/><text x="{left - 6}" y="{y + 4:.1f}" class="axis" text-anchor="end">{esc(fmt_short(ceil_ * i / 4) if i else "0")}</text>'
    bars = ''
    best = values.index(max(values))
    for i, (d, v) in enumerate(zip(span, values)):
        h = plot_h * v / ceil_
        x = left + slot * i + (slot - bw) / 2
        y = top + plot_h - h
        cls = 'peak' if i == best and v else 'day'
        tip = f'{d:%A, %b %-d}: {fmt_tokens(v)} tokens (≈ {fmt_pages(v)})' if v else f'{d:%A, %b %-d}: no activity'
        r = min(4, bw / 2, h)
        path = f'M{x:.1f},{y + h:.1f}V{y + r:.1f}Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f}H{x + bw - r:.1f}Q{x + bw:.1f},{y:.1f} {x + bw:.1f},{y + r:.1f}V{y + h:.1f}Z' if h > 0.5 else ''
        bars += f'<path d="{path}" class="{cls}" tabindex="0" data-tip="{esc(tip)}" role="img" aria-label="{esc(tip)}"/>' if path else ''
        hit = f'<rect x="{left + slot * i:.1f}" y="{top}" width="{slot:.1f}" height="{plot_h}" class="hit" data-tip="{esc(tip)}"/>'
        bars += hit
    labels = ''
    every = 1 if short else max(len(span) // 6, 1)
    for i in range(0, len(span), every):
        text = f'{span[i]:%a %-d}' if short else f'{span[i]:%b %-d}'
        labels += f'<text x="{left + slot * i + slot / 2:.1f}" y="{height - 8}" class="axis" text-anchor="middle">{text}</text>'
    ptxt = ''
    if values[best]:
        px = left + slot * best + slot / 2
        anchor = 'end' if px > width - 120 else 'start'
        ptxt = f'<text x="{px:.1f}" y="{top + 2}" class="peaklabel" text-anchor="{anchor}" dx="{-6 if anchor == "end" else 6}">Busiest day · {esc(fmt_tokens(values[best]))}</text>'
    chart = f'<svg viewBox="0 0 {width} {height}" class="daily" role="group" aria-label="Tokens used per day">{grid}{bars}{ptxt}{labels}</svg>'
    table = '<table><thead><tr><th>Day</th><th>Tokens</th><th>Roughly</th></tr></thead><tbody>' + ''.join(
        f'<tr><td>{d:%a %b %-d, %Y}</td><td>{fmt_int(v)}</td><td>{esc(fmt_pages(v))}</td></tr>' for d, v in zip(span, values) if v) + '</tbody></table>'
    return chart, table


def hbars(pairs, var='--seq'):
    if not pairs:
        return ''
    top = pairs[0][1] or 1
    return '<ul class="hbars">' + ''.join(
        f'<li data-tip="{esc(name)}: {esc(fmt_tokens(v))} tokens" tabindex="0"><span class="hname">{esc(name)}</span><span class="htrack"><i style="width:{max(v / top * 100, 0.8):.1f}%;background:var({var})"></i></span><span class="hval">{esc(fmt_tokens(v))}</span></li>'
        for name, v in pairs) + '</ul>'


CSS = """
:root{color-scheme:light;--bg:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#6b6963;--line:#e1e0d9;--axis:#c3c2b7;
--c1:#2a78d6;--c2:#eb6834;--c3:#1baf7a;--c4:#eda100;--e0:#9ec5f4;--e1:#3987e5;--e2:#184f95;--seq:#2a78d6;--bar:#86b6ef;--peak:#1c5cab;--chip:#eef4fc}
:root[data-theme="dark"]{color-scheme:dark;--bg:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--muted:#a19f97;--line:#2c2c2a;--axis:#383835;
--c1:#3987e5;--c2:#d95926;--c3:#199e70;--c4:#c98500;--e0:#86b6ef;--e1:#3987e5;--e2:#184f95;--seq:#3987e5;--bar:#3f6ea8;--peak:#86b6ef;--chip:#20262e}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--muted:#a19f97;--line:#2c2c2a;--axis:#383835;
--c1:#3987e5;--c2:#d95926;--c3:#199e70;--c4:#c98500;--e0:#86b6ef;--e1:#3987e5;--e2:#184f95;--seq:#3987e5;--bar:#3f6ea8;--peak:#86b6ef;--chip:#20262e}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:920px;margin:0 auto;padding:28px 20px 80px}header{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;flex-wrap:wrap}
h1{font-size:30px;line-height:1.2;margin:0 0 4px}h2{font-size:22px;margin:0 0 6px}.sub{color:var(--ink2);margin:0}.muted{color:var(--muted)}.small{font-size:14px}
button.theme{background:var(--surface);color:var(--ink);border:1px solid var(--line);border-radius:999px;padding:8px 14px;font:inherit;font-size:14px;cursor:pointer}
section,.card{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:22px;margin-top:20px}
.hero{padding:28px}.hero .big{font-size:clamp(44px,9vw,72px);line-height:1.05;font-weight:700;letter-spacing:-.02em}.hero .unit{font-size:20px;color:var(--ink2);margin-left:6px}
.hero p{margin:10px 0 0;color:var(--ink2)}.cost{margin-top:14px;padding:12px 14px;border-radius:12px;background:var(--chip)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-top:20px}.kpi{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:16px}
.kpi b{display:block;font-size:30px;line-height:1.15}.kpi span{color:var(--ink2);font-size:14px}.lead{margin:6px 0 14px}
.stack{display:flex;gap:2px;height:46px;border-radius:10px;overflow:hidden}.stack.thin{height:22px;margin:8px 0}.seg{display:flex;align-items:center;justify-content:center;min-width:3px;color:#fff;font-size:13px;font-weight:600;overflow:hidden;white-space:nowrap;text-shadow:0 1px 2px rgba(0,0,0,.35)}
.seg span{padding:0 6px;overflow:hidden;text-overflow:ellipsis}.seg:focus{outline:3px solid var(--ink);outline-offset:-3px}
.legend,.story{list-style:none;margin:14px 0 0;padding:0;display:grid;gap:8px}.legend li{color:var(--ink2);font-size:15px}.dot{display:inline-block;width:12px;height:12px;border-radius:4px;margin-right:8px;vertical-align:-1px}
details.task{border-top:1px solid var(--line)}details.task:first-of-type{border-top:0}summary{list-style:none;cursor:pointer;display:grid;grid-template-columns:34px 1fr auto auto;gap:4px 14px;align-items:center;padding:14px 4px}
summary::-webkit-details-marker{display:none}summary:hover{background:var(--chip)}summary:focus-visible{outline:3px solid var(--seq);outline-offset:2px}
.rank{width:28px;height:28px;border-radius:50%;background:var(--chip);display:grid;place-items:center;font-size:14px;font-weight:700}.tmain{min-width:0}.ttitle{display:block;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tmeta{display:block;font-size:14px;color:var(--muted)}.tsize{text-align:right;font-size:15px;line-height:1.3}.meter{grid-column:2/-1;height:8px;background:var(--line);border-radius:6px;overflow:hidden}.meter i{display:block;height:100%;background:var(--seq);border-radius:6px}
.badge{font-size:13px;font-weight:700;padding:4px 10px;border-radius:999px;border:1px solid var(--line);white-space:nowrap}.lvl1{background:var(--chip)}.lvl2{background:var(--chip)}.lvl3{background:#fff2d6;color:#5a3d00}.lvl4{background:#fde0dc;color:#7a1610}.lvl5{background:#7a1610;color:#fff;border-color:#7a1610}
:root[data-theme="dark"] .lvl3{background:#4a3708;color:#ffdf9e}:root[data-theme="dark"] .lvl4{background:#5a1f1b;color:#ffc9c2}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]) .lvl3{background:#4a3708;color:#ffdf9e}:root:not([data-theme="light"]) .lvl4{background:#5a1f1b;color:#ffc9c2}}
.tbody{padding:4px 8px 16px 48px}.story li{font-size:15px}
svg.daily{width:100%;height:auto;display:block}.grid{stroke:var(--line);stroke-width:1}.axis{fill:var(--muted);font-size:11px}.day{fill:var(--bar)}.peak{fill:var(--peak)}.hit{fill:transparent}
.day:hover,.peak:hover,.day:focus,.peak:focus{filter:brightness(.85);outline:none}.peaklabel{fill:var(--ink);font-size:12px;font-weight:600}
.hbars{list-style:none;margin:8px 0 0;padding:0;display:grid;gap:10px}.hbars li{display:grid;grid-template-columns:minmax(120px,220px) 1fr 90px;gap:12px;align-items:center;font-size:15px}
.htrack{height:16px;background:var(--line);border-radius:6px;overflow:hidden;display:block}.htrack i{display:block;height:100%;border-radius:6px}.hval{text-align:right;color:var(--ink2)}.hname{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
details.tbl{margin-top:14px}details.tbl summary{display:block;padding:6px 0;color:var(--ink2);font-size:14px}table{border-collapse:collapse;width:100%;font-size:14px;margin-top:8px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}th{color:var(--muted);font-weight:600}
dl{margin:8px 0 0}dt{font-weight:700;margin-top:12px}dd{margin:2px 0 0;color:var(--ink2)}ul.plain{margin:8px 0 0;padding-left:20px;color:var(--ink2)}
#tip{position:fixed;z-index:9;pointer-events:none;background:var(--ink);color:var(--surface);padding:8px 11px;border-radius:9px;font-size:13px;max-width:320px;opacity:0;transition:opacity .08s}

.highlights{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-top:20px}.hl{background:var(--surface);border:1px solid var(--line);border-left:4px solid var(--seq);border-radius:14px;padding:14px 16px;display:flex;flex-direction:column;gap:2px}
.hlk{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:700}.hl b{font-size:17px;line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.hld{font-size:14px;color:var(--ink2)}
.hbars.convs li{grid-template-columns:minmax(160px,1.4fr) 1fr 90px;align-items:start}.cname{display:block;font-weight:600;line-height:1.35}.cmeta{display:block;font-weight:400;font-size:13px;color:var(--muted)}.convs .htrack{margin-top:6px}.convs .hval{margin-top:2px}
@media(max-width:640px){summary{grid-template-columns:30px 1fr;}.badge,.tsize{grid-column:2}.tsize{text-align:left}.meter{grid-column:1/-1}.tbody{padding-left:8px}.hbars li,.hbars.convs li{grid-template-columns:1fr 80px}.htrack{grid-column:1/-1;order:3}}
@media print{.theme{display:none}details.task{break-inside:avoid}}
"""

JS = """
(function(){var t=document.getElementById('tip'),root=document.documentElement,b=document.getElementById('themebtn');
function show(e){var n=e.target.closest('[data-tip]');if(!n){t.style.opacity=0;return}t.textContent=n.getAttribute('data-tip');t.style.opacity=1;
var x=(e.clientX||n.getBoundingClientRect().left)+14,y=(e.clientY||n.getBoundingClientRect().top)+16;var w=t.offsetWidth;if(x+w>innerWidth-8)x=innerWidth-w-8;t.style.left=x+'px';t.style.top=y+'px'}
document.addEventListener('mousemove',show);document.addEventListener('focusin',show);document.addEventListener('focusout',function(){t.style.opacity=0});
document.addEventListener('mouseleave',function(){t.style.opacity=0});
var saved=null;try{saved=localStorage.getItem('ta-theme')}catch(e){}if(saved)root.setAttribute('data-theme',saved);
b.addEventListener('click',function(){var cur=root.getAttribute('data-theme')||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');var next=cur==='dark'?'light':'dark';
root.setAttribute('data-theme',next);try{localStorage.setItem('ta-theme',next)}catch(e){}});})();
"""


def render(m):
    generated = m['generated']
    first, last = m['first'], m['last']
    span = f'{first:%b %-d, %Y} to {last:%b %-d, %Y}' if first and last else 'no dated activity'
    if m.get('period_label'):
        span = f"{m['period_label']} ({span})" if first and last else f"{m['period_label']} (no activity)"
    typical_txt = f'{fmt_tokens(m["typical"])} tokens' if m['typical'] else 'n/a'
    reuse_pct = f'{m["reused"] / (m["fresh"] + m["reused"]):.0%}' if (m['fresh'] + m['reused']) and m['reused'] else '—'
    hidden_sessions = sum(v for k, v in m['links'].items() if k != 'unlinked')
    tools = ' and '.join(name for name, _ in m['tools']) or 'no tools'
    configure_labels(m)
    headline = {'day': 'Your AI day in review', 'week': 'Your AI week in review', 'month': 'Your AI month in review', 'range': 'Your AI usage in review'}.get(m.get('period_kind'), 'Your AI usage, in plain English')
    if not m.get('period_label'):
        headline = 'Your AI usage, in plain English'
    highlights = render_highlights(m)
    cost_html = ''
    if m['priced'] and m['cost'] is not None:
        extra = ''
        if m['unpriced']:
            extra = ' Not priced (no matching entry in your price list): ' + esc(', '.join(name for name, _ in m['unpriced'])) + '.'
        cost_html = (f'<div class="cost"><b>Estimated cost: {esc(fmt_money(m["cost"], m["currency"]))}</b> — worked out from the price list you supplied. '
                     f'It is an estimate, not a bill: real charges depend on your plan and any subscription.{extra}</div>')
    elif not m['priced']:
        cost_html = ('<div class="cost muted small">Dollar amounts are off by default because prices change and differ by plan. '
                     'Add a price list you trust (see the README) and this page will show an estimate.</div>')
    top = m['tasks'][:10]
    top_total = top[0]['total'] if top else 0
    task_html = ''.join(render_task(i + 1, t, top_total, m['currency']) for i, t in enumerate(top)) or '<p class="muted">No tasks found.</p>'
    task_table = '<table><thead><tr><th>#</th><th>Task</th><th>Tokens</th><th>Steps</th><th>Helpers</th><th>Size</th></tr></thead><tbody>' + ''.join(
        f'<tr><td>{i + 1}</td><td>{esc(t["label"] or HARNESS_NAMES.get(t["harness"], "AI") + " task")}</td><td>{fmt_int(t["total"])}</td><td>{fmt_int(t["steps"])}</td><td>{t["helpers"]}</td><td>{esc(t["rating"])}</td></tr>'
        for i, t in enumerate(m['tasks'][:25])) + '</tbody></table>'
    chart, day_table = render_days(m)
    cov = m['coverage'] or {}
    warnings = cov.get('warnings', [])
    warn_list = ''.join(f'<li>{esc(w)}</li>' for w in warnings[:30])
    link_bits = []
    if m['links'].get('exact'):
        link_bits.append(f'{m["links"]["exact"]} helper/safety sessions were linked to their parent exactly')
    if m['links'].get('inferred'):
        link_bits.append(f'{m["links"]["inferred"]} hand-offs were matched by wording and timing (a strong estimate, not a certainty)')
    link_txt = '; '.join(link_bits) + '.' if link_bits else 'No parent/helper links were found in these logs.'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(headline)}</title><style>{CSS}</style></head><body><main>
<header><div><h1>{esc(headline)}</h1><p class="sub">{esc(span)} · from {esc(tools)} · everything on this page was worked out on your own computer</p></div>
<button class="theme" id="themebtn" type="button">Switch light / dark</button></header>

<section class="hero card"><div><span class="big">{esc(fmt_tokens(m['total']))}</span><span class="unit">tokens processed</span></div>
<p>That is about <b>{esc(fmt_pages(m['total']))}</b> of text read and written by AI. A <em>token</em> is a small piece of a word — the unit AI tools count work in. More tokens means more work, and usually more cost.</p>{cost_html}</section>

{highlights}
<div class="kpis"><div class="kpi"><b>{fmt_int(m['task_count'])}</b><span>things you asked AI to do</span></div>
<div class="kpi"><b>{fmt_int(m['calls'])}</b><span>individual AI steps taken</span></div>
<div class="kpi"><b>{fmt_int(hidden_sessions)}</b><span>helpers and checks started on your behalf</span></div>
<div class="kpi"><b>{esc(reuse_pct)}</b><span>of reading reused from memory</span></div></div>

<section><h2>Where did the effort go?</h2>{render_effort(m)}</section>
<section><h2>Who did the work?</h2>{render_roles(m)}</section>
<section><h2>Which conversations used the most?</h2>{render_sessions(m)}</section>
<section><h2>Which tasks were the heaviest?</h2><p class="muted">Your typical task was about <b>{esc(typical_txt)}</b>. Tap a row to see what happened inside it.</p>{task_html}
<details class="tbl"><summary>Show the top 25 as a table</summary>{task_table}</details></section>
<section><h2>When were you busiest?</h2><p class="muted small">Tokens per day (M = million, B = billion). Days use your computer's clock.</p>{chart}<details class="tbl"><summary>Show as a table</summary>{day_table}</details></section>
<section><h2>Which AI tools and models?</h2><p class="muted">By tool</p>{hbars(m['tools'])}<p class="muted" style="margin-top:18px">By model</p>{hbars(m['models'])}</section>

<section><h2>What this page cannot see</h2><ul class="plain">
<li>Only work that was written to log files on this computer. Chats in a web browser or on a phone, and work done on someone else's server, are invisible here.</li>
<li>Tokens are not dollars and not your plan's allowance. Different AI providers count differently.</li>
<li>{esc(link_txt)}</li>
<li>Read {fmt_int(cov.get('files', 0))} log files{f'; {len(warnings)} notes about things that could not be read perfectly' if warnings else ''}.</li></ul>
{f'<details class="tbl"><summary>Show the technical notes</summary><ul class="plain small">{warn_list}</ul></details>' if warnings else ''}</section>

<section><h2>Words used on this page</h2><dl>
<dt>Token</dt><dd>A small piece of a word. Roughly 3 tokens is 2 words. About {TOKENS_PER_PAGE} tokens fill a page and {fmt_int(TOKENS_PER_BOOK)} fill a 300-page book (rules of thumb, not exact).</dd>
<dt>Fresh reading / reused from memory</dt><dd>Each step, the AI re-reads the whole conversation so far. Providers remember part of it ("caching"), which is usually cheaper and faster than reading it new.</dd>
<dt>Helper, hand-off, safety check</dt><dd>Modern AI tools often start other AI sessions to do part of the job. Those sessions use tokens too, so they are counted with the task that caused them.</dd>
<dt>Light · Typical · Heavy · Very heavy · Extreme</dt><dd>Size compared with <em>your own</em> typical task (the median): under half is Light, up to 2× Typical, up to 5× Heavy, up to 20× Very heavy, beyond that Extreme.</dd>
<dt>Simple · Multi-step · Team effort</dt><dd>How complicated the job was: Simple is a handful of steps; Team effort means several helpers or 150+ steps.</dd></dl>
<p class="muted small">Generated {generated:%b %-d, %Y %H:%M}. This file may contain snippets of your prompts — check before sharing it.</p></section>
</main><div id="tip" role="tooltip"></div><script>{JS}</script></body></html>"""


def step_timeline(steps):
    """Bars per AI step, coloured by who did the work. Buckets consecutive steps when there are many."""
    if not steps:
        return ''
    limit = 160
    size = -(-len(steps) // limit)
    buckets = [steps[i:i + size] for i in range(0, len(steps), size)]
    totals = [sum(x['total_tokens'] for x in b) for b in buckets]
    peak = max(totals) or 1
    step = 10 ** len(str(int(peak))) // 10 or 1
    ceil_ = -(-peak // (step * 2)) * step * 2 or 1
    width, height, left, bottom, top = 760, 230, 52, 28, 12
    plot_w, plot_h = width - left - 8, height - bottom - top
    slot = plot_w / len(buckets)
    bw = max(min(slot * 0.78, 20), 2)
    grid = ''
    for i in range(5):
        y = top + plot_h - plot_h * i / 4
        grid += f'<line x1="{left}" x2="{width - 8}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/><text x="{left - 6}" y="{y + 4:.1f}" class="axis" text-anchor="end">{esc(fmt_short(ceil_ * i / 4) if i else "0")}</text>'
    bars = ''
    for i, (bucket, tokens) in enumerate(zip(buckets, totals)):
        by_role = collections.defaultdict(int)
        for x in bucket:
            by_role[x['role']] += x['total_tokens']
        dominant = max(by_role, key=by_role.get)
        first, last = bucket[0]['step'], bucket[-1]['step']
        where = f'Step {first}' if first == last else f'Steps {first} to {last}'
        mix = ', '.join(f'{ROLE_NAMES[r]} {fmt_tokens(v)}' for r, v in by_role.items())
        tip = f'{where}: {fmt_tokens(tokens)} tokens ({mix})'
        h = plot_h * tokens / ceil_
        x0 = left + slot * i + (slot - bw) / 2
        bars += f'<rect x="{x0:.1f}" y="{top + plot_h - h:.1f}" width="{bw:.1f}" height="{max(h, 1):.1f}" rx="2" style="fill:var({ROLE_VAR[dominant]})" tabindex="0" data-tip="{esc(tip)}" role="img" aria-label="{esc(tip)}"/>'
    labels = ''
    every = max(len(buckets) // 6, 1)
    for i in range(0, len(buckets), every):
        cx = left + slot * i + slot / 2
        anchor = 'end' if cx > width - 40 else 'middle'
        labels += f'<text x="{cx:.1f}" y="{height - 8}" class="axis" text-anchor="{anchor}">step {buckets[i][0]["step"]}</text>'
    present = [r for r in ROLE_ORDER if any(x['role'] == r for x in steps)]
    legend = '<ul class="legend inline">' + ''.join(f'<li><span class="dot" style="background:var({ROLE_VAR[r]})"></span>{esc(ROLE_NAMES[r])}</li>' for r in present) + '</ul>'
    note = f'<p class="muted small">Each bar is {size} consecutive steps.</p>' if size > 1 else ''
    return f'<svg viewBox="0 0 {width} {height}" class="daily" role="group" aria-label="Tokens per AI step">{grid}{bars}{labels}</svg>{legend}{note}'


def render_prompt_page(dd, model):
    configure_labels(model)
    title = dd['prompt'] or (f"A prompt from {parse_ts(dd['first_timestamp']):%b %-d, %Y}" if parse_ts(dd['first_timestamp']) else 'A prompt')
    first, last = parse_ts(dd['first_timestamp']), parse_ts(dd['last_timestamp'])
    duration = ''
    if first and last:
        secs = int((last - first).total_seconds())
        duration = f'{secs // 3600}h {secs % 3600 // 60}m' if secs >= 3600 else f'{secs // 60}m {secs % 60}s' if secs >= 60 else f'{secs}s'
    lvl = deepdive.rating((dd['times_typical'] or 0))[1] if dd['times_typical'] is not None else 2
    reused = dd['effort']['reused_from_cache']
    inp = dd['effort']['fresh_input'] + reused
    reuse = deepdive.pct(reused / inp) if inp and dd['effort']['cache_reported_on_steps'] else '—'
    obs = ''
    for o in dd['observations']:
        if o['kind'] == 'hypothesis':
            obs += f'<li class="hyp"><b>Possible explanation, not proven:</b> {esc(o["text"])}</li>'
        else:
            obs += f'<li>{esc(o["text"])}</li>'
    helper_rows = ''.join(
        f'<tr><td>{esc(h["label"] or ROLE_NAMES.get(h["role"], ""))}</td><td>{esc(ROLE_NAMES.get(h["role"], h["role"]))}</td>'
        f'<td>{"Exact link" if h["link_confidence"] == "exact" else "Matched by wording and timing (inferred)" if h["link_confidence"] == "inferred" else "Not linked"}</td>'
        f'<td>{fmt_int(h["steps"])}</td><td>{fmt_int(h["tokens"])}</td></tr>' for h in dd['helpers'])
    helpers_html = (f'<table><thead><tr><th>Helper</th><th>Kind</th><th>How we know</th><th>Steps</th><th>Tokens</th></tr></thead><tbody>{helper_rows}</tbody></table>'
                    if helper_rows else '<p class="muted">No helpers, hand-offs or safety checks were linked to this prompt.</p>')
    big_rows = ''.join(f'<tr><td>{x["step"]}</td><td>{esc(ROLE_NAMES.get(x["role"], x["role"]))}</td><td>{esc(x["model"])}</td><td>{fmt_int(x["total_tokens"])}</td><td><code>{esc(x["event_id"])}</code></td></tr>' for x in dd['largest_steps'])
    step_rows = ''.join(f'<tr><td>{x["step"]}</td><td>{esc(ROLE_NAMES.get(x["role"], x["role"]))}</td><td>{fmt_int(x["fresh_input_tokens"])}</td><td>{fmt_int(x["cache_read_tokens"] or 0)}</td><td>{fmt_int(x["output_tokens"] or 0)}</td><td>{fmt_int(x["total_tokens"])}</td></tr>' for x in dd['steps'])
    prompt_full = f'<details class="tbl"><summary>Show the full prompt text</summary><pre class="prompt">{esc(dd["prompt_full"])}</pre></details>' if dd.get('prompt_full') else ''
    note = f'<p class="muted small">{esc(dd["prompt_note"])}</p>' if dd.get('prompt_note') else ''
    chart = step_timeline(dd['steps'])
    body = f"""<header><div><h1>One prompt, taken apart</h1><p class="sub">{esc(title[:200])}</p><p class="sub small">{esc(f"{first:%b %-d, %Y %H:%M}") if first else ""}{" · took " + esc(duration) if duration else ""}</p></div>
<button class="theme" id="themebtn" type="button">Switch light / dark</button></header>
<section class="hero card"><div><span class="big">{esc(fmt_tokens(dd['tokens']))}</span><span class="unit">tokens processed</span></div>
<p>About <b>{esc(fmt_pages(dd['tokens']))}</b> of text. That is <b>{dd['times_typical'] if dd['times_typical'] is not None else '—'}× your typical prompt</b> <span class="badge lvl{lvl}">{esc(dd['size'] or '')}</span> · {esc(dd['complexity'])}.</p></section>
<div class="kpis"><div class="kpi"><b>{fmt_int(dd['calls'])}</b><span>AI steps taken</span></div><div class="kpi"><b>{len(dd['helpers'])}</b><span>helpers and checks involved</span></div>
<div class="kpi"><b>{esc(duration or '—')}</b><span>from first to last step</span></div><div class="kpi"><b>{esc(reuse)}</b><span>of reading reused from memory</span></div></div>
<section><h2>What we can say about it</h2><ul class="plain obs">{obs}</ul>{note}{prompt_full}</section>
<section><h2>How it unfolded, step by step</h2><p class="muted small">Tokens per step (M = million). Taller bar, more work in that step. Hover or tab to a bar for details.</p>{chart}
<details class="tbl"><summary>Show every step as a table</summary><table><thead><tr><th>Step</th><th>Who</th><th>Fresh reading</th><th>From memory</th><th>Writing</th><th>Total</th></tr></thead><tbody>{step_rows}</tbody></table></details></section>
<section><h2>Where did the effort go?</h2>{render_effort(model)}</section>
<section><h2>Who did the work?</h2>{render_roles(model)}</section>
<section><h2>Helpers and checks</h2>{helpers_html}</section>
<section><h2>The five biggest steps</h2><table><thead><tr><th>Step</th><th>Who</th><th>Model</th><th>Tokens</th><th>Call ID</th></tr></thead><tbody>{big_rows}</tbody></table>
<p class="muted small">To look inside one of these, run <code>inspect CALL_ID --context</code> with the ID shown.</p></section>
<section><h2>Good to know</h2><ul class="plain"><li>Tokens are processed work, not dollars.</li><li>Every step re-reads the conversation so far, so long tasks get heavy even when little new text is produced.</li>
<li>A “possible explanation” is a clue worth checking, never proof.</li></ul><p class="muted small">Generated {model['generated']:%b %-d, %Y %H:%M}. This file may contain your prompt text — check before sharing.</p></section>"""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>One prompt, taken apart</title><style>{CSS}.legend.inline{{display:flex;flex-wrap:wrap;gap:6px 18px;margin-top:8px}}.hyp{{background:var(--chip);border-radius:10px;padding:8px 12px;list-style:none;margin-left:-20px}}'
            f'.obs li{{margin-bottom:8px}}pre.prompt{{white-space:pre-wrap;background:var(--chip);padding:12px;border-radius:10px;font-size:14px;overflow:auto;max-height:340px}}code{{font-size:13px}}</style></head>'
            f'<body><main>{body}</main><div id="tip" role="tooltip"></div><script>{JS}</script></body></html>')


def select_rows(rows, period=None, since=None, until=None, session=None, harness=None):
    keep = []
    for r in rows:
        if harness and r.get('harness') != harness:
            continue
        if session:
            root = (r.get('task_root') or '').split(':', 1)[-1]
            sid = r.get('session_id', '')
            if not (session in (sid, root) or (len(session) >= 8 and (sid.startswith(session) or root.startswith(session)))):
                continue
        if period and not periods.contains(period, r.get('timestamp')):
            continue
        stamp = periods.parse_ts(r.get('timestamp'))
        if (since or until) and stamp is None:
            continue
        if since and stamp < since or until and stamp >= until:
            continue
        keep.append(r)
    return keep


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ledger', type=Path, default=Path.home() / '.local/share/token-audit/ledger.jsonl')
    ap.add_argument('--out', type=Path, default=None, help='default: report.html (or prompt-<id>.html with --task) next to the ledger')
    ap.add_argument('--period', help='Named period in local time: ' + periods.VALID)
    ap.add_argument('--since', help='ISO date or timestamp (UTC); only usage on or after it')
    ap.add_argument('--until', help='ISO date or timestamp (UTC); only usage before it')
    ap.add_argument('--session', help='Only this conversation, including helpers it started')
    ap.add_argument('--harness', help='Only this tool, e.g. claude or codex')
    ap.add_argument('--task', help='Build a one-prompt deep-dive page for this task id (from the query tool prompts command)')
    ap.add_argument('--full-prompt', action='store_true', help='With --task: include the full prompt text from the source transcript')
    ap.add_argument('--prices', type=Path, help='optional JSON price list (see prices.example.json)')
    args = ap.parse_args(argv)
    ledger = args.ledger.expanduser()
    coverage_path = ledger.with_name('coverage.json')
    coverage = json.loads(coverage_path.read_text(encoding='utf-8')) if coverage_path.exists() else {}
    prices = load_prices(args.prices) if args.prices else None
    all_rows = load_ledger(ledger)
    if args.task:
        measured = [r for r in all_rows if r.get('measurement') != 'estimated' and r.get('total_tokens') is not None]
        try:
            dd = deepdive.build(measured, args.task, full_prompt=args.full_prompt)
        except LookupError as e:
            ap.error(str(e))
        task_rows = [r for r in measured if deepdive.task_key(r) == dd['task_id']]
        out = (args.out or ledger.with_name(f'prompt-{dd["task_id"][:10]}.html')).expanduser()
        out.write_text(render_prompt_page(dd, build_model(task_rows, coverage, prices)), encoding='utf-8')
        print(out)
        return 0
    period = None
    if args.period:
        if args.since or args.until:
            ap.error('use either --period or --since/--until, not both')
        try:
            period = periods.resolve(args.period)
        except periods.PeriodError as e:
            ap.error(str(e))
    since = periods.parse_ts(args.since if not args.since or 'T' in args.since else args.since + 'T00:00:00+00:00') if args.since else None
    until = periods.parse_ts(args.until if not args.until or 'T' in args.until else args.until + 'T00:00:00+00:00') if args.until else None
    rows = select_rows(all_rows, period, since, until, args.session, args.harness)
    label = period['label'] if period else ('Custom range' if (since or until) else None)
    if args.session:
        label = f'{label + ", " if label else ""}one conversation'
    model = build_model(rows, coverage, prices, period_label=label, period=period)
    out = (args.out or ledger.with_name('report.html')).expanduser()
    out.write_text(render(model), encoding='utf-8')
    print(out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
