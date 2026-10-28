"""Versioned contracts and cumulative execution budgets (no model calls)."""
import hashlib
import json
import math
from pathlib import Path
import time


class BudgetExceeded(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def file_digest(path):
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def local_file(project, name):
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        raise ValueError('Expected a project-relative file path')
    path = (project / name).resolve()
    if not path.is_relative_to(project.resolve()):
        raise ValueError('Path escapes project: ' + name)
    return path


def contract(spec, project):
    protected = {}
    for name in spec.get('verification_files', []):
        value = file_digest(local_file(project, name))
        if value is None:
            raise ValueError('Verification file missing: ' + name)
        protected[name] = value
    return {'version': 1, 'spec_sha256': digest(spec), 'verification_files': protected}


def check_contract(task):
    saved = task.get('contract')
    if not saved:
        raise ValueError('Legacy task has no bound contract; explicitly reverify it first')
    if saved['spec_sha256'] != digest(task['spec']):
        raise ValueError('Task spec changed after contract binding; create a reviewed new plan')
    if set(saved['verification_files']) != set(task['spec'].get('verification_files', [])):
        raise ValueError('Protected verification file coverage is incomplete')
    project = Path(task['project'])
    for name, expected in saved['verification_files'].items():
        if file_digest(local_file(project, name)) != expected:
            raise ValueError('Protected verification file changed: ' + name)


def new_budget(max_seconds, max_tokens=None):
    if type(max_seconds) is not int or not 1 <= max_seconds <= 604800:
        raise ValueError('Total execution time must be 1..604800 seconds')
    if max_tokens is not None and (type(max_tokens) is not int or max_tokens < 1):
        raise ValueError('Token budget must be a positive integer')
    return {'max_seconds': max_seconds, 'max_tokens': max_tokens, 'operations': []}


def validate(task):
    """Fail closed on damaged new fields; legacy records remain readable."""
    if task.get('record_version') == 2 and not {'contract', 'budget', 'findings'} <= set(task):
        raise ValueError('Incomplete versioned task record')
    if task.get('record_version', 2) != 2:
        raise ValueError('Unsupported task record version')
    if 'record_version' not in task and any(k in task for k in ('contract', 'budget', 'findings')):
        if task.get('legacy_contract_bound') is not True or 'budget' in task:
            raise ValueError('Version missing from a governed task; refusing legacy downgrade')
    if 'contract' in task:
        c = task['contract']
        if (not isinstance(c, dict) or c.get('version') != 1 or
                not isinstance(c.get('spec_sha256'), str) or
                not isinstance(c.get('verification_files'), dict) or
                not all(isinstance(k, str) and isinstance(v, str) for k, v in c['verification_files'].items())):
            raise ValueError('Invalid contract record')
    if 'budget' in task:
        b = task['budget']
        if not isinstance(b, dict):
            raise ValueError('Invalid budget record')
        new_budget(b.get('max_seconds'), b.get('max_tokens'))
        if not isinstance(b.get('operations'), list):
            raise ValueError('Invalid budget operations')
        ids = set()
        for op in b['operations']:
            if (not isinstance(op, dict) or not isinstance(op.get('id'), str) or op['id'] in ids or
                    type(op.get('attempt')) is not int or op['attempt'] < 0 or
                    op.get('kind') not in ('worker', 'check') or
                    op.get('status') not in ('running', 'settled') or
                    not all(type(op.get(k)) in (int, float) and math.isfinite(op[k]) and op[k] >= 0
                            for k in ('started', 'limit_seconds', 'seconds')) or
                    type(op.get('usage_complete')) is not bool or
                    any(type(op.get(k)) is not int or op[k] < 0 for k in ('input_tokens', 'output_tokens', 'cached_input_tokens'))):
                raise ValueError('Invalid budget operation')
            ids.add(op['id'])
    findings = task.get('findings', [])
    if not isinstance(findings, list):
        raise ValueError('Invalid finding ledger')
    ids = set()
    for f in findings:
        if (not isinstance(f, dict) or not isinstance(f.get('id'), str) or f['id'] in ids or
                f.get('status') not in ('accepted', 'rejected', 'deferred', 'fixed_verified') or
                f.get('severity') not in ('low', 'medium', 'high') or
                not all(isinstance(f.get(k), str) and f[k].strip() for k in ('path', 'problem', 'evidence')) or
                not isinstance(f.get('history'), list) or not isinstance(f.get('snapshot'), dict)):
            raise ValueError('Invalid finding record')
        ids.add(f['id'])


def usage(events):
    """Count every completed turn; input includes cached input, never subtract twice."""
    counts = {'input_tokens': 0, 'output_tokens': 0, 'cached_input_tokens': 0}
    complete, seen, failed = True, False, False
    if events.is_file():
        with events.open(encoding='utf-8', errors='replace') as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get('type') in ('turn.failed', 'error'):
                    failed = True
                if event.get('type') != 'turn.completed':
                    continue
                seen = True
                value = event.get('usage', {})
                if not isinstance(value, dict):
                    complete = False
                    continue
                for key in counts:
                    number = value.get(key, 0 if key == 'cached_input_tokens' else None)
                    if type(number) is not int or number < 0:
                        complete = False
                    else:
                        counts[key] += number
    return dict(counts, usage_complete=seen and complete and not failed)


def view(task):
    b = task.get('budget')
    if not b:
        return {'scope': 'legacy', 'usage_complete': False,
                'note': 'Legacy attempts retained; cumulative time before upgrade is unknown.'}
    seconds = 0.0
    tokens = 0
    complete = True
    workers = 0
    for op in b['operations']:
        seconds += (max(op['seconds'], min(op['limit_seconds'], max(0, time.time() - op['started'])))
                    if op['status'] == 'running' else op['seconds'])
        if op['kind'] == 'worker':
            workers += 1
            tokens += op['input_tokens'] + op['output_tokens']
            complete = complete and op['usage_complete'] and op['status'] == 'settled'
    return {'scope': 'ccl_owned_subprocesses', 'max_seconds': b['max_seconds'],
            'spent_seconds': round(seconds, 3), 'remaining_seconds': max(0, b['max_seconds'] - seconds),
            'max_tokens': b['max_tokens'], 'observed_tokens': tokens,
            'remaining_tokens': None if b['max_tokens'] is None else max(0, b['max_tokens'] - tokens),
            'usage_complete': complete, 'worker_calls': workers,
            'note': 'Execution/check time only; no human wait or external Claude usage. Token admission between calls, not a mid-call cap.'}


def admit(task, worker=False):
    b = view(task)
    if b['scope'] == 'legacy':
        return None
    if b['remaining_seconds'] <= 0:
        raise BudgetExceeded('Cumulative execution/check time exhausted')
    if worker and b['max_tokens'] is not None:
        if not b['usage_complete']:
            raise BudgetExceeded('Prior worker token usage unknown; refusing another budgeted model call')
        if b['remaining_tokens'] <= 0:
            raise BudgetExceeded('Cumulative token budget exhausted (next-call admission)')
    return b['remaining_seconds']


def settle(op, events=None, elapsed=None):
    op['seconds'] = max(op['seconds'], min(op['limit_seconds'], max(0, time.time() - op['started']))
                        if elapsed is None else max(0, elapsed))
    if op['kind'] == 'worker' and events is not None:
        op.update(usage(events))
    op['status'] = 'settled'


def reconcile(task, path):
    """Caller holds project lock and has ruled out live workers. Idempotent."""
    for op in task.get('budget', {}).get('operations', []):
        if op['status'] == 'running':
            events = path / ('attempt-%02d' % op.get('attempt', 0)) / 'events.jsonl'
            settle(op, events)
