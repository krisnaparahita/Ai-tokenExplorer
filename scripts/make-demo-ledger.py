#!/usr/bin/env python3
"""Write a fabricated ledger for demos and the website. Nothing here comes from a real conversation.

    python3 scripts/make-demo-ledger.py --out /tmp/demo/ledger.jsonl

The output is deterministic (fixed seed). It covers one week, 8 to 14 September 2026, with a mix of
Claude and Codex conversations, helpers, hand-offs and safety reviews, so a report built from it
shows every section.
"""
import argparse
import datetime
import hashlib
import json
import random
from pathlib import Path

SEED = 20260925
WEEK_START = datetime.datetime(2026, 9, 8, tzinfo=datetime.timezone.utc)

# (session id, tool, first prompt, [(day, hour, prompt, steps, first input, last input, hand-offs, helpers, safety reviews)])
CONVERSATIONS = [
    ('demo-c1', 'claude', 'Plan the launch checklist for the new mobile app release', [
        (0, 9, 'Plan the launch checklist for the new mobile app release', 14, 38_000, 92_000, 1, 1, 2),
        (1, 10, 'yes please, go ahead with all of it', 58, 210_000, 335_000, 2, 1, 4),
        (1, 15, 'add the rollout risks and owners', 22, 340_000, 382_000, 0, 0, 1)]),
    ('demo-c2', 'claude', 'Draft the quarterly board update from these notes', [
        (2, 8, 'Draft the quarterly board update from these notes', 16, 30_000, 71_000, 0, 0, 0),
        (2, 13, 'shorter, and lead with the numbers', 12, 75_000, 101_000, 0, 0, 0)]),
    ('demo-x1', 'codex', 'Fix the flaky login test and explain the root cause', [
        (2, 10, 'Fix the flaky login test and explain the root cause', 30, 52_000, 164_000, 0, 0, 6)]),
    ('demo-c3', 'claude', 'Compare three vendors for the analytics migration', [
        (3, 9, 'Compare three vendors for the analytics migration', 30, 60_000, 204_000, 0, 3, 0),
        (4, 14, 'which one would you pick and why', 8, 210_000, 231_000, 0, 0, 0)]),
    ('demo-c4', 'claude', "Summarise last week's customer support tickets", [
        (0, 7, "Summarise last week's customer support tickets", 18, 25_000, 62_000, 0, 0, 0)]),
    ('demo-x2', 'codex', 'Rename the internal config keys across the repo', [
        (5, 11, 'Rename the internal config keys across the repo', 12, 40_000, 77_000, 0, 0, 2)]),
    ('demo-c5', 'claude', 'Translate the onboarding email into Bahasa Indonesia', [
        (6, 12, 'Translate the onboarding email into Bahasa Indonesia', 6, 12_000, 19_000, 0, 0, 0)]),
]
MODELS = {'claude': 'claude-sonnet-5', 'codex': 'gpt-5.6-sol'}
HELPER_NAMES = ['Research sub-task', 'Review sub-task', 'Fact-check sub-task']


def stamp(base, seconds):
    return (base + datetime.timedelta(seconds=seconds)).strftime('%Y-%m-%dT%H:%M:%SZ')


def steps(rng, n, low, high, model, harness, base, ident, fields):
    """Rows for one run of n steps: the context grows from low to high and is mostly served from cache."""
    rows, clock = [], 0.0
    for i in range(n):
        context = int(low + (high - low) * (i / max(n - 1, 1)) * rng.uniform(0.96, 1.04))
        cached = 0 if i == 0 else int(context * rng.uniform(0.90, 0.98))
        out = rng.randint(200, 1800)
        clock += rng.uniform(18, 75)
        rows.append(dict(harness=harness, event_id=f'{ident}-{i:03d}', model=model, timestamp=stamp(base, clock), stage='unattributed',
                         measurement='reported', total_tokens=context + out, source='(fabricated demo data)', source_line=i + 1,
                         input_tokens=context, output_tokens=out, cache_read_tokens=cached, cache_write_tokens=(int(context * 0.05) if i == 0 else 0),
                         reasoning_tokens=None, **fields))
    return rows


def build():
    rng = random.Random(SEED)
    rows = []
    for sid, tool, _title, prompts in CONVERSATIONS:
        root = f'{tool}:{sid}'
        for n, (day, hour, text, count, low, high, handoffs, helpers, reviews) in enumerate(prompts, 1):
            base = WEEK_START + datetime.timedelta(days=day, hours=hour, minutes=rng.randint(0, 40))
            turn = f'{sid}-turn{n}'
            task = hashlib.sha1(f'{root}|{turn}'.encode()).hexdigest()[:10]
            common = dict(task_id=task, task_root=root, task_turn=turn)
            rows += steps(rng, count, low, high, MODELS[tool], tool, base, f'{sid}-t{n}',
                          dict(session_id=sid, turn_id=turn, prompt_label=text, node=sid, role='main', helper_label='', parent_node=None,
                               link_basis=None, link_confidence=None, **common))
            for k in range(handoffs):
                other = 'codex' if tool == 'claude' else 'claude'
                child = f'{sid}-h{n}{k}'
                rows += steps(rng, rng.randint(9, 18), 24_000, 110_000, MODELS[other], other, base + datetime.timedelta(minutes=3 + 5 * k), child,
                              dict(session_id=child, turn_id=f'{child}-t', prompt_label='', node=child, role='delegate', helper_label=f'Work handed to {other.title()}',
                                   parent_node=sid, link_basis='prompt+time', link_confidence='inferred', **common))
            for k in range(helpers):
                rows += steps(rng, rng.randint(4, 9), 30_000, 70_000, MODELS[tool], tool, base + datetime.timedelta(minutes=2 + 4 * k), f'{sid}-s{n}{k}',
                              dict(session_id=sid, turn_id=f'{sid}-turn{n}', prompt_label='', node=f'{sid}#h{n}{k}', role='helper',
                                   helper_label=HELPER_NAMES[k % len(HELPER_NAMES)], parent_node=sid, link_basis='agent-id', link_confidence='exact', **common))
            for k in range(reviews):
                child = f'{sid}-g{n}{k}'
                rows += steps(rng, rng.randint(2, 4), 18_000, 36_000, 'codex-auto-review', 'codex', base + datetime.timedelta(minutes=1 + 3 * k), child,
                              dict(session_id=child, turn_id=f'{child}-t', prompt_label='', node=child, role='safety-review', helper_label='Automatic safety review',
                                   parent_node=sid, link_basis='parent-thread-id', link_confidence='exact', **common))
    return sorted(rows, key=lambda r: r['timestamp'])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    rows = build()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')
    print(f'{len(rows)} fabricated calls, {sum(r["total_tokens"] for r in rows):,} tokens -> {args.out}')


if __name__ == '__main__':
    main()
