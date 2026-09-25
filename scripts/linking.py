"""Parent/child linking between logged sessions. Standard library only.

Every link records HOW it was established, so a report never presents a guess
as a fact:

  agent-id          exact: a Claude sub-agent transcript joined to the Agent/Task
                    tool call that started it (toolUseResult.agentId).
  parent-thread-id  exact: Codex sub-agents and automatic safety reviews record
                    the parent session id themselves.
  prompt+time       inferred: a tool or shell call that starts another AI tool
                    (Claude calling Codex, or Codex calling Claude, through an MCP
                    tool or a command such as ``codex exec`` / ``claude -p``) matched
                    to the session of the other tool whose first message is that
                    call's prompt and which began while the call was running.

Unlinked sessions stay their own root; nothing is guessed beyond the rules above.
"""
import collections
import hashlib
import re

DELEGATE_TOOL_MARKERS = ('ask_codex', 'codex-delegate')
CODEX_CLI = re.compile(r'\bcodex\s+(?:exec|e)\b')
CLAUDE_PRINT = re.compile(r'\s(?:-p|--print)\b')
HELPER_TOOLS = ('Agent', 'Task')
# Harness-injected context blocks that precede the real prompt; never part of a match.
WRAPPER_PREFIXES = ('<environment_context>', '<recommended_plugins>', '<system-reminder>', '# AGENTS.md', '<permissions', '<collaboration_mode>')
LATE_START_TOLERANCE_S = 90
NO_RESULT_WINDOW_S = 20 * 60


def new_meta():
    return {'nodes': {}, 'turn_starts': collections.defaultdict(list), 'tool_calls': [],
            'tool_results': {}, 'agent_tool': {}}


def _norm(text):
    return ' '.join((text or '').split())


def _epoch(ts):
    """ISO-8601 -> seconds, or None. Avoids a datetime import cost on hot paths."""
    if not isinstance(ts, str):
        return None
    import datetime
    try:
        return datetime.datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def handoff_targets(harness, name, text):
    """Which other AI tool would this call start? Empty set if it is not a hand-off."""
    if harness == 'claude':
        if any(m in name for m in DELEGATE_TOOL_MARKERS) or (name == 'Bash' and CODEX_CLI.search(text)):
            return {'codex'}
    elif harness == 'codex':
        if 'claude' in text and CLAUDE_PRINT.search(text):
            return {'claude'}
    return set()


def note_handoff(meta, harness, node, turn, ts, call_id, name, text):
    targets = handoff_targets(harness, name, text)
    if targets and text:
        meta['tool_calls'].append(dict(id=call_id, harness=harness, node=node, turn=turn, name=name, ts=ts, kind='delegate',
                                       targets=targets, prompt=_norm(text), system='', label=''))


def note_result(meta, call_id, ts):
    if call_id:
        meta['tool_results'][call_id] = ts


def note_codex_call(meta, sid, turn, ts, call_id, name, text):
    note_handoff(meta, 'codex', sid, turn, ts, call_id, name, text)


def note_claude_user(meta, node_id, message):
    if message.lstrip().startswith(WRAPPER_PREFIXES):
        return
    node = meta['nodes'].setdefault(('claude', node_id), {})
    have = node.get('first_user', '')
    if len(have) < 8000 and message:
        node['first_user'] = (have + '\n' + message)[:8000]


def note_turn(meta, harness, node, ts, turn):
    if ts and turn:
        meta['turn_starts'][(harness, node)].append((ts, turn))


def note_codex_meta(meta, sid, payload, ts):
    source = payload.get('source')
    spawn = source.get('subagent', {}).get('thread_spawn', {}) if isinstance(source, dict) and isinstance(source.get('subagent'), dict) else {}
    thread_source = payload.get('thread_source')
    parent = payload.get('parent_thread_id') or spawn.get('parent_thread_id')
    if thread_source == 'guardian_review':
        role, label = 'safety-review', 'Automatic safety review'
    elif parent:
        role, label = 'helper', payload.get('agent_nickname') or spawn.get('agent_nickname') or 'Codex helper'
    else:
        role, label = 'main', ''
    node = meta['nodes'].setdefault(('codex', sid), {})
    node.update(role=role, label=label, parent_thread=parent, first_ts=node.get('first_ts') or payload.get('timestamp') or ts, agent_id=None)


def note_codex_user(meta, sid, message):
    """Accumulate the opening user text (capped) used to match delegated prompts."""
    if message.lstrip().startswith(WRAPPER_PREFIXES):
        return
    node = meta['nodes'].setdefault(('codex', sid), {})
    have = node.get('first_user', '')
    if len(have) < 8000 and message:
        node['first_user'] = (have + '\n' + message)[:8000]


def note_claude(meta, node_id, sidechain, agent_id, x, msg, ts, turn):
    """Register a Claude record: the node, tool calls it starts, results that finish them."""
    node = meta['nodes'].setdefault(('claude', node_id), {'role': 'helper' if sidechain else 'main', 'label': '',
                                                          'parent_thread': None, 'first_ts': ts, 'agent_id': agent_id if sidechain else None})
    content = msg.get('content')
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get('type')
        if kind == 'tool_use':
            name, inp = str(block.get('name', '')), block.get('input') if isinstance(block.get('input'), dict) else {}
            if name in HELPER_TOOLS:
                meta['tool_calls'].append(dict(id=block.get('id'), harness='claude', node=node_id, turn=turn, name=name, ts=ts, kind='helper',
                                               targets=set(), prompt=inp.get('prompt') or inp.get('description') or '', system='',
                                               label=inp.get('subagent_type') or inp.get('description') or ''))
            else:
                text = inp.get('command') if name == 'Bash' else (inp.get('prompt') or '')
                note_handoff(meta, 'claude', node_id, turn, ts, block.get('id'), name, str(text or ''))
        elif kind == 'tool_result' and block.get('tool_use_id'):
            meta['tool_results'][block['tool_use_id']] = ts
    result = x.get('toolUseResult')
    if isinstance(result, dict) and result.get('agentId') and isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get('type') == 'tool_result' and block.get('tool_use_id'):
                meta['agent_tool'][result['agentId']] = block['tool_use_id']


def _turn_at(meta, key, ts):
    starts = sorted(meta['turn_starts'].get(key, []))
    chosen = None
    for start_ts, turn in starts:
        if ts is not None and start_ts <= ts:
            chosen = turn
        elif chosen is None and ts is None:
            chosen = turn
    return chosen or (starts[0][1] if starts else None)


def _text_match(first, text):
    """The child's opening message and the parent's call describe the same prompt (either can be the longer)."""
    forward = text[:160]
    if len(forward) >= 30 and forward in first:
        return True
    head = re.split(r'["\\\n]', first[:90])[0].strip()
    return len(head) >= 25 and head in text


def _match_delegates(meta, parents):
    """Infer hand-offs between AI tools (marked inferred; never claimed as exact)."""
    calls = [c for c in meta['tool_calls'] if c['kind'] == 'delegate' and c['prompt']]
    roots = [(k, n) for k, n in meta['nodes'].items() if n.get('role') == 'main' and k not in parents and n.get('first_user')]
    for key, node in roots:
        start, first = _epoch(node.get('first_ts')), _norm(node['first_user'])
        best = None
        for call in calls:
            if key[0] not in call['targets'] or (call['harness'], call['node']) == key:
                continue
            call_ts = _epoch(call['ts'])
            if start is None or call_ts is None or start < call_ts - 1:
                continue
            end = _epoch(meta['tool_results'].get(call['id'])) or call_ts + NO_RESULT_WINDOW_S
            if start > end + LATE_START_TOLERANCE_S:
                continue
            if _text_match(first, call['prompt']):
                gap = start - call_ts
                if best is None or gap < best[0]:
                    best = (gap, call)
        if best:
            call = best[1]
            parents[key] = dict(parent=(call['harness'], call['node']), turn=call['turn'], basis='prompt+time', confidence='inferred')
            node['role'], node['label'] = 'delegate', f'Work handed to {key[0].title()}'


def link(rows, meta):
    """Annotate rows in place with role, parent, link basis and a shared task id."""
    nodes, parents = meta['nodes'], {}
    calls = {c['id']: c for c in meta['tool_calls']}
    for key, node in nodes.items():
        harness = key[0]
        if node.get('agent_id'):
            call = calls.get(meta['agent_tool'].get(node['agent_id']))
            if call:
                parents[key] = dict(parent=(harness, call['node']), turn=call['turn'], basis='agent-id', confidence='exact')
                node['label'] = call['label'] or 'Claude helper'
        elif node.get('parent_thread'):
            parent_key = (harness, node['parent_thread'])
            if parent_key in nodes:
                parents[key] = dict(parent=parent_key, turn=_turn_at(meta, parent_key, node.get('first_ts')), basis='parent-thread-id', confidence='exact')
    _match_delegates(meta, parents)

    def root_of(key):
        turn, seen = None, set()
        while key in parents and key not in seen:
            seen.add(key)
            hop = parents[key]
            key, turn = hop['parent'], hop['turn']
        return key, turn

    for row in rows:
        key = (row['harness'], row.get('node') or row['session_id'])
        node = nodes.get(key, {})
        hop = parents.get(key)
        root, entered_turn = root_of(key)
        turn = (entered_turn or 'unknown') if key in parents else row['turn_id']
        digest = hashlib.sha1(f'{root[0]}|{root[1]}|{turn}'.encode()).hexdigest()[:10]
        row.update(role=node.get('role', 'main'), helper_label=node.get('label', ''),
                   parent_node=hop['parent'][1] if hop else None, link_basis=hop['basis'] if hop else None,
                   link_confidence=hop['confidence'] if hop else None, task_id=digest, task_root=f'{root[0]}:{root[1]}', task_turn=turn)
    return rows
