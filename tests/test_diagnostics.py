import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import ccl
import diagnostics
import modes
import test_loop


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_loop.LoopTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root

    def missing_check(self):
        helper = self.root / 'missing checker.py'
        self.fixture.spec['checks'] = [{'name': 'checker', 'argv': [str(self.root / 'missing-checker.exe')]}]
        return helper

    def test_missing_check_executable_stops_after_one_worker(self):
        self.missing_check()
        marker = self.root / 'must-not-run'
        self.fixture.spec['checks'].append({'name': 'later', 'argv': [sys.executable, '-c',
            'from pathlib import Path; Path(' + repr(str(marker)) + ').touch()']})
        task = self.fixture.run_task(self.fixture.create())
        self.assertEqual(task['state'], 'blocked')
        self.assertEqual(task['attempts'], 1)
        self.assertTrue(task['verification']['blocking_failure'])
        self.assertEqual(task['verification']['checks'][0]['failure_kind'], 'environment')
        self.assertFalse(marker.exists())

    def test_check_timeout_is_not_an_automatic_worker_retry(self):
        self.fixture.spec['checks'] = [{'name': 'hang', 'argv': [sys.executable, '-c', 'import time; time.sleep(10)'], 'timeout': 1}]
        task = self.fixture.run_task(self.fixture.create())
        self.assertEqual(task['state'], 'blocked')
        self.assertEqual(task['attempts'], 1)
        self.assertEqual(task['verification']['checks'][0]['failure_kind'], 'timeout')

    def test_reverify_reuses_candidate_without_spending_attempts_or_accepting(self):
        helper = self.root / 'checker.py'
        self.fixture.spec['checks'] = [{'name': 'checker', 'argv': [sys.executable, str(helper)]}]
        # Permission/missing executable errors, rather than a normal nonzero test,
        # represent the installation failure being repaired in this fixture.
        original = ccl.process
        def process(argv, *args, **kwargs):
            if argv == self.fixture.spec['checks'][0]['argv']:
                raise PermissionError('checker unavailable')
            return original(argv, *args, **kwargs)
        with patch.object(ccl, 'process', side_effect=process):
            task = self.fixture.run_task(self.fixture.create(attempts=1))
        helper.write_text("from pathlib import Path; assert Path('answer.txt').read_text() == 'correct'", encoding='utf-8')
        calls = (self.fixture.project / 'calls.jsonl').read_bytes()
        advice = ccl.recovery(task['id'])
        self.assertEqual(advice['recovery']['action'], 'inspect_then_reverify')
        fixed = ccl.reverify(task['id'], 'Checker restored; inspected prior result and side effects')
        self.assertEqual(fixed['attempts'], 1)
        self.assertEqual(fixed['state'], 'awaiting_review')
        self.assertEqual((self.fixture.project / 'calls.jsonl').read_bytes(), calls)
        self.assertEqual(len(fixed['rechecks']), 1)
        self.assertTrue(fixed['verification']['passed'])
        ccl.review(task['id'], 'accept', 'Read answer and check output')
        with self.assertRaisesRegex(ValueError, 'closed'):
            ccl.reverify(task['id'], 'Do not reopen accepted task')

    def test_recovery_advice_and_status_are_read_only(self):
        task = self.fixture.run_task(self.fixture.create())
        def snapshot():
            return {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        before = snapshot()
        result = ccl.recovery(task['id'])
        self.assertEqual(result['recovery']['action'], 'review_candidate')
        self.assertTrue(result['recovery']['read_only'])
        self.assertEqual(result['progress']['stage'], 'awaiting_review')
        self.assertGreaterEqual(result['progress']['elapsed_seconds'], 0)
        self.assertIn('last_activity_at', result['progress'])
        self.assertEqual(snapshot(), before)

    def test_reverify_requires_note_and_current_completed_result(self):
        task = self.fixture.create()
        with self.assertRaisesRegex(ValueError, 'note'):
            ccl.reverify(task['id'], '')
        with self.assertRaisesRegex(ValueError, 'No completed'):
            ccl.reverify(task['id'], 'No candidate yet')
        task = self.fixture.run_task(task)
        ccl.review(task['id'], 'revise', 'New attempt needed')
        os.environ['FAKE_MODE'] = 'crash'
        failed = self.fixture.run_task(task)
        self.assertNotIn('result', failed)
        self.assertFalse(ccl.recovery(task['id'])['recovery']['candidate_available'])
        with self.assertRaisesRegex(ValueError, 'No completed'):
            ccl.reverify(task['id'], 'Cannot recheck stale previous result')

    def test_reverify_respects_mode_transition(self):
        task = self.fixture.run_task(self.fixture.create(owner='owner'))
        modes.ModeStore(ccl.STATE).submitted('owner', '模式二', [task])
        with self.assertRaises(ValueError):
            ccl.reverify(task['id'], 'Mode change not settled')

    def test_recovery_live_pid_does_not_replay(self):
        task = self.fixture.create()
        task.update(state='running', worker_pid=os.getpid())
        ccl.save_task(ccl.task_path(task['id']), task)
        self.assertEqual(ccl.recovery(task['id'])['recovery']['action'], 'inspect_live_process')
        with self.assertRaisesRegex(ValueError, 'worker may still'):
            ccl.reverify(task['id'], 'Still running')

    def test_subproject_untracked_file_edit_invalidates_fingerprint_and_review(self):
        repo = self.fixture.project
        subprocess.run(['git', 'init', '-q', str(repo)], check=True)
        self.fixture.project = repo / 'nested'
        self.fixture.project.mkdir()
        extra = self.fixture.project / 'not-listed-as-artifact.txt'
        extra.write_text('before')
        task = self.fixture.run_task(self.fixture.create())
        before = ccl.git_snapshot(self.fixture.project)
        extra.write_text('after')
        self.assertNotEqual(before['fingerprint'], ccl.git_snapshot(self.fixture.project)['fingerprint'])
        with self.assertRaisesRegex(ValueError, 'Git workspace changed'):
            ccl.review(task['id'], 'accept', 'Earlier snapshot is no longer valid')

    def test_unrelated_corrupt_record_does_not_deny_other_projects(self):
        store = modes.ModeStore(ccl.STATE)
        store.submitted('good-session', '模式二', [])
        bad = ccl.STATE / 'projects/unrelated/tasks/abcdef123456/task.json'
        bad.parent.mkdir(parents=True)
        for text in ('{incomplete', 'null', '[]', '{"state": "running"}'):
            bad.write_text(text, encoding='utf-8')
            self.assertEqual(ccl.hook({'cwd': str(self.fixture.project), 'session_id': 'good-session',
                'hook_event_name': 'PreToolUse', 'tool_name': 'Read', 'tool_input': {}}), {})
            self.assertEqual(len(ccl.scan_tasks()[1]), 1)
            self.assertEqual(bad.read_text(), text)

    def test_damaged_active_project_blocks_replay_but_allows_diagnostics(self):
        task = self.fixture.create(owner='owner')
        record = ccl.task_path(task['id']) / 'task.json'
        record.write_text('{broken', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Damaged task'):
            self.fixture.create()
        # Even if cwd changes, the session's saved active project is protected.
        with self.assertRaisesRegex(ValueError, 'Damaged task'):
            ccl.owned_tasks('owner', self.root)
        payload = {'cwd': str(self.fixture.project), 'session_id': 'owner',
                   'hook_event_name': 'PreToolUse', 'tool_name': 'Bash', 'tool_input': {'command': 'ccl doctor'}}
        self.assertEqual(ccl.hook(payload), {})
        self.assertEqual(record.read_text(), '{broken')

    def test_diagnostic_gate_never_allows_reverify_or_compound_commands(self):
        for command in ('ccl doctor', 'ccl doctor --session abc', 'ccl recover abcdef123456',
                        '& "C:/Tools/ccl.cmd" doctor --project "C:/project with spaces"'):
            self.assertTrue(modes.diagnostic_command(command), command)
        for command in ('ccl recover abcdef123456 --reverify', 'ccl doctor; echo bad',
                        'ccl doctor > x', 'ccl doctor --session $(echo bad)', 'ccl run abcdef123456'):
            self.assertFalse(modes.diagnostic_command(command), command)

    def test_bom_json_spec_is_accepted(self):
        path = self.root / 'spec.json'
        path.write_text(json.dumps(self.fixture.spec), encoding='utf-8-sig')
        self.assertEqual(ccl.read_json(path), self.fixture.spec)

    def test_corrupt_optional_task_fields_are_reported_not_crashed(self):
        task = self.fixture.run_task(self.fixture.create())
        path = ccl.task_path(task['id']) / 'task.json'
        for key, value in [('verification_records', {'bad': 'shape'}), ('history', [17])]:
            bad = dict(task, **{key: value})
            path.write_text(json.dumps(bad), encoding='utf-8')
            self.assertEqual(len(ccl.scan_tasks()[1]), 1)
            with self.assertRaisesRegex(ValueError, 'Invalid task record'):
                ccl.recovery(task['id'])
        ccl.save_task(path.parent, task)

    def test_nonobject_event_lines_do_not_crash_recovery(self):
        task = self.fixture.run_task(self.fixture.create())
        events = ccl.task_path(task['id']) / 'attempt-01/events.jsonl'
        with events.open('a', encoding='utf-8') as stream:
            stream.write('null\n[]\n{"type":"item.completed","item":null}\n')
        self.assertTrue(ccl.recovery(task['id'])['recovery']['candidate_available'])

    def test_corrupt_git_index_cannot_produce_valid_fingerprint(self):
        project = self.fixture.project
        subprocess.run(['git', 'init', '-q', str(project)], check=True)
        self.assertTrue(ccl.git_snapshot(project)['available'])  # Unborn HEAD is supported.
        (project / '.git/index').write_bytes(b'not a git index')
        with self.assertRaisesRegex(ValueError, 'Git snapshot failed'):
            ccl.git_snapshot(project)


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.prefix = self.home / '.local/share/claude-codex-loop'
        self.prefix.mkdir(parents=True)
        (self.prefix / 'ccl.py').write_text('VERSION = "0.3.2"\n')
        self.settings = self.home / '.claude/settings.json'
        self.settings.parent.mkdir()
        self.program = '"C:/Old Python/python.exe" -X utf8 "' + (self.prefix / 'ccl.py').as_posix() + '" hook'
        self.settings.write_text(json.dumps({'hooks': {event: [{'hooks': [{'command': self.program}]}]
            for event in ('SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'Stop')}}))
        self.probe = patch.object(diagnostics.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'fixture-version\n', ''))
        self.probe.start()
        self.addCleanup(self.probe.stop)

    def doctor(self, session='', issues=None):
        return diagnostics.doctor('0.3.2', self.home / 'state', self.home, issues or [], session, self.home)

    def test_doctor_is_read_only_and_does_not_claim_client_loaded(self):
        before = {str(p): p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        report = self.doctor('unseen-session')
        self.assertTrue(report['read_only'])
        self.assertEqual(next(c for c in report['checks'] if c['name'] == 'hook_observation')['status'], 'unknown')
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.home.rglob('*') if p.is_file()})
        self.assertFalse((self.home / 'state').exists())

    def test_duplicates_disabled_hooks_and_corrupt_records_reported(self):
        settings = json.loads(self.settings.read_text())
        settings['disableAllHooks'] = True
        settings['hooks']['PreToolUse'] *= 2
        self.settings.write_text(json.dumps(settings))
        report = self.doctor(issues=[{'path': 'bad-task', 'error': 'invalid JSON'}])
        failures = {c['name'] for c in report['checks'] if c['status'] == 'error'}
        self.assertTrue({'hook_transport', 'hooks_enabled', 'task_records'} <= failures)
        self.assertFalse(report['healthy'])

    def test_doctor_does_not_expose_other_configuration_or_auth(self):
        settings = json.loads(self.settings.read_text())
        settings['env'] = {'UNRELATED_SECRET': 'do-not-print-this-secret'}
        self.settings.write_text(json.dumps(settings))
        self.assertNotIn('do-not-print-this-secret', json.dumps(self.doctor()))

    def test_version_probe_failure_and_bad_settings_are_reported(self):
        self.settings.write_text('[]')
        with patch.object(diagnostics.subprocess, 'run', side_effect=FileNotFoundError('not installed')):
            report = self.doctor()
        names = {c['name'] for c in report['checks'] if c['status'] == 'error'}
        self.assertTrue({'git', 'codex', 'claude', 'settings'} <= names)

    def test_malformed_session_and_missing_hook_interpreter_are_diagnosed(self):
        path = self.home / 'state/sessions/bad-session.json'
        path.parent.mkdir(parents=True)
        for value in ([], None, {'mode': 'bad'}):
            path.write_text(json.dumps(value), encoding='utf-8')
            report = self.doctor('bad-session')
            failures = {c['name'] for c in report['checks'] if c['status'] == 'error'}
            self.assertIn('hook_observation', failures)
            self.assertIn('command_files', failures)
            self.assertFalse(report['healthy'])


if __name__ == '__main__':
    unittest.main()
