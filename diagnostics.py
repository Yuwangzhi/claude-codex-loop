"""Read-only operational diagnostics. Never start a model or change task state."""
import ast
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import modes
import runtime


def timestamp(value):
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc).timestamp()
    except (AttributeError, ValueError, TypeError):
        return None


def progress(task, path):
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    updated = timestamp(task.get('updated_at')) or timestamp(task.get('created_at')) or now
    created = timestamp(task.get('created_at')) or updated
    last = updated
    folders = [path / ('attempt-%02d' % task.get('attempts', 0))]
    # Only inspect record directories underneath this task, never arbitrary paths.
    if task.get('verification_records'):
        candidate = Path(task['verification_records'])
        if candidate.parent == path:
            folders.append(candidate)
    for folder in folders:
        for record in folder.glob('*'):
            try:
                if record.is_file():
                    last = max(last, record.stat().st_mtime)
            except OSError:
                pass
    processes = {}
    for field in ('runner_pid', 'worker_pid'):
        pid = task.get(field)
        if type(pid) is int and pid > 0:
            try:
                processes[field] = runtime.pid_alive(pid)
            except OSError:
                processes[field] = True  # Unknown is not permission to replay.
    active = task.get('state') in ('running', 'verifying')
    end = now if active else max(updated, last)
    return {'stage': task.get('state'), 'elapsed_seconds': round(max(0, end - created), 1),
            'last_activity_at': dt.datetime.fromtimestamp(last, dt.timezone.utc).isoformat(timespec='seconds'),
            'idle_seconds': round(max(0, now - last), 1), 'processes': processes,
            'process_identity_verified': False,
            'activity_note': '文件/状态最后更新，不是模型心跳；PID 存活不证明进程身份或任务仍在推进。',
            'failure_reason': task.get('feedback') if task.get('state') in
            ('blocked', 'execution_failed', 'interrupted', 'budget_exhausted', 'needs_revision') else None}


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def doctor(version, state, project, record_issues, session='', home=None):
    home = Path.home() if home is None else Path(home)
    prefix = home / '.local/share/claude-codex-loop'
    binary = home / '.local/bin/ccl'
    checks = []

    def check(name, status, detail):
        checks.append({'name': name, 'status': status, 'detail': detail})

    check('python', 'ok' if sys.version_info >= (3, 9) else 'error', sys.executable + ' · ' + sys.version.split()[0])
    for tool in ('git', 'codex', 'claude'):
        try:
            argv = runtime.executable_argv([os.environ.get('CCL_CODEX_BIN', tool) if tool == 'codex' else tool, '--version'])
            result = subprocess.run(argv, capture_output=True, timeout=8, encoding='utf-8', errors='replace')
            # Version commands only. Never dump configuration, auth files or stderr.
            line = result.stdout.strip().splitlines()
            check(tool, 'ok' if result.returncode == 0 else 'error',
                  line[0][:160] if result.returncode == 0 and line else 'Version probe failed; inspect executable/PATH.')
        except (OSError, subprocess.TimeoutExpired):
            check(tool, 'error', 'Executable missing, unusable or version probe timed out.')
    try:
        tree = ast.parse((prefix / 'ccl.py').read_text(encoding='utf-8-sig'))
        installed = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                         and any(isinstance(t, ast.Name) and t.id == 'VERSION' for t in node.targets))
        check('installed_version', 'ok' if installed == version else 'warning',
              'running=' + version + ', installed=' + str(installed))
    except (OSError, ValueError, SyntaxError, StopIteration):
        check('installed_version', 'error', 'Installed runtime missing or version unreadable.')

    env, hooks, disabled, commands = {}, {}, False, []
    settings_paths = list(dict.fromkeys([home / '.claude/settings.json', home / '.claude/settings.local.json',
                                        project / '.claude/settings.json', project / '.claude/settings.local.json']))
    for path in settings_paths:
        if not path.exists():
            continue
        try:
            settings = read_json(path)
            if not isinstance(settings, dict) or not isinstance(settings.get('env', {}), dict) or not isinstance(settings.get('hooks', {}), dict):
                raise ValueError('Invalid settings shape')
            if 'disableAllHooks' in settings:
                disabled = settings['disableAllHooks'] is True
            env.update(settings.get('env', {}))
            for event, groups in settings.get('hooks', {}).items():
                for group in groups:
                    for entry in group.get('hooks', []):
                        if runtime.managed_hook(entry.get('command'), binary, prefix / 'ccl.py'):
                            hooks[event] = hooks.get(event, 0) + 1
                            commands.append(runtime.hook_argv(entry['command'], binary, prefix / 'ccl.py'))
        except (OSError, ValueError, TypeError, AttributeError):
            check('settings', 'error', 'Unreadable/invalid settings: ' + str(path))
    check('hooks_enabled', 'error' if disabled else 'ok', 'disableAllHooks=true' if disabled else 'No effective disableAllHooks=true found in inspected files.')
    value = env.get('CLAUDE_CODE_PLUGIN_DIRS', '')
    plugin = prefix / 'desktop-plugin'
    module = False
    if isinstance(value, str):
        module = any(Path(p).expanduser() == plugin for p in value.split(os.pathsep) if p)
    else:
        check('plugin_paths', 'error', 'CLAUDE_CODE_PLUGIN_DIRS must be a string.')
    transport = 'module' if module else 'command' if hooks else 'missing'
    events = ('SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'Stop')
    if module and hooks or any(n > 1 for n in hooks.values()):
        check('hook_transport', 'error', 'Duplicate CCL command hooks or mixed module/command configuration.')
    elif not module and any(hooks.get(event) != 1 for event in events):
        check('hook_transport', 'error', 'No complete five-event CCL hook configuration found.')
    else:
        check('hook_transport', 'ok', transport + ' configured; this alone does not prove client loading.')
    if commands:
        missing = set()
        for argv in commands:
            if not Path(argv[0]).is_file() and not shutil.which(argv[0]):
                missing.add(argv[0])
            if len(argv) == 5 and not Path(argv[3]).is_file():
                missing.add(argv[3])
        check('command_files', 'error' if missing else 'ok',
              'Missing hook entry points: ' + ', '.join(sorted(missing)) if missing else 'Command entry points exist; event delivery still requires client evidence.')
    if module:
        try:
            bridge = read_json(plugin / 'bridge.json')
            manifest = read_json(plugin / '.claude-plugin/plugin.json')
            valid = (isinstance(bridge, dict) and isinstance(manifest, dict)
                     and isinstance(bridge.get('python'), str) and Path(bridge['python']).is_file()
                     and isinstance(bridge.get('ccl'), str) and Path(bridge['ccl']) == prefix / 'ccl.py'
                     and Path(bridge['ccl']).is_file() and manifest.get('version') == version
                     and (plugin / 'hooks/hooks.mjs').is_file() and (plugin / 'hooks/hooks.json').is_file())
            check('module_files', 'ok' if valid else 'error', 'Bridge paths, plugin version and hook files ' + ('valid.' if valid else 'need repair.'))
        except (OSError, ValueError, TypeError):
            check('module_files', 'error', 'Missing or invalid module/bridge files.')
    if session:
        try:
            saved = modes.ModeStore(state).get(session)
            if not isinstance(saved, dict):
                raise ValueError('Invalid session state')
            seen = saved.get('hook_seen_at')
            check('hook_observation', 'ok' if seen else 'unknown',
                  ('Recorded hook activity at ' + str(seen) + '; historical evidence, not a live client probe.') if seen
                  else 'No hook activity recorded for this session. Open/reload the client and check again.')
        except (ValueError, OSError):
            check('hook_observation', 'error', 'Session state unreadable or session id invalid.')
    else:
        check('hook_observation', 'unknown', 'Pass --session SESSION_ID to inspect recorded hook evidence; no live loading claim made.')
    check('task_records', 'error' if record_issues else 'ok', str(len(record_issues)) + ' damaged records; originals left untouched.')
    return {'version': version, 'read_only': True, 'project': str(project), 'hook_transport': transport,
            'healthy': not any(c['status'] == 'error' for c in checks), 'checks': checks, 'record_issues': record_issues}
