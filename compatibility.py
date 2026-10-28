"""Offline CLI surface probe; never starts a model or inspects credentials."""
import os
import re
import subprocess
import runtime

REQUIRED = {
    'exec': {'--config', '--cd', '--json', '--output-schema', '--output-last-message', '--skip-git-repo-check'},
    'exec resume': {'--config', '--json', '--output-schema', '--output-last-message', '--skip-git-repo-check'},
}


def flags(help_text):
    return set(re.findall(r'^\s+(?:-[A-Za-z],\s+)?(--[a-z][a-z0-9-]*)\b', help_text, re.M))


def probe():
    checks = []
    for command, required in REQUIRED.items():
        try:
            argv = runtime.executable_argv([os.environ.get('CCL_CODEX_BIN', 'codex'), *command.split(), '--help'])
            result = runtime.capture_output(argv, timeout=8)
            if result.returncode:
                checks.append({'command': command, 'status': 'unknown', 'missing_flags': [], 'detail': 'Help probe failed'})
                continue
            missing = sorted(required - flags(result.stdout))
            checks.append({'command': command, 'status': 'error' if missing else 'ok', 'missing_flags': missing})
        except (OSError, subprocess.TimeoutExpired):
            checks.append({'command': command, 'status': 'unknown', 'missing_flags': [], 'detail': 'Help unavailable or timed out'})
    return {'read_only': True, 'network': False, 'model_started': False,
            'status': 'error' if any(x['status'] == 'error' for x in checks) else
                      'unknown' if any(x['status'] == 'unknown' for x in checks) else 'ok',
            'checks': checks, 'event_contract': 'unknown', 'authentication': 'unknown',
            'note': 'Help surface only. Runtime JSONL is validated during execution; help does not prove event schema, login or tool access.'}
