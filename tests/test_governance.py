import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import ccl
import compatibility
import contextpack
import governance
import install
import modes
import runtime
import test_loop


class GovernanceTests(unittest.TestCase):
    def setUp(self):
        self.f = test_loop.LoopTests()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.project = self.f.project

    def test_modified_spec_cannot_run_or_accept(self):
        task = self.f.create()
        task['spec']['goal'] = 'Changed after approval'
        ccl.save_task(ccl.task_path(task['id']), task)
        result = self.f.run_task(task)
        self.assertEqual(result['state'], 'blocked')
        self.assertEqual(result['attempts'], 0)
        self.assertFalse((self.project / 'calls.jsonl').exists())
        with self.assertRaisesRegex(ValueError, 'spec changed'):
            ccl.review(task['id'], 'accept', 'Try to accept')

    def test_protected_verification_file_change_refused(self):
        checker = self.project / 'check.py'
        checker.write_text('assert True')
        self.f.spec['verification_files'] = ['check.py']
        task = self.f.run_task(self.f.create())
        checker.write_text('pass')
        with self.assertRaisesRegex(ValueError, 'Protected verification'):
            ccl.reverify(task['id'], 'Changed verifier cannot redefine success')
        self.assertFalse(ccl.handoff_payload(ccl.read_task(ccl.task_path(task['id']) / 'task.json'))['evidence_current'])

    def test_missing_protected_file_and_unsafe_context_rejected(self):
        self.f.spec['verification_files'] = ['missing.py']
        with self.assertRaisesRegex(ValueError, 'missing'):
            self.f.create()
        self.f.spec['verification_files'] = []
        self.f.spec['context_files'] = ['../outside.md']
        with self.assertRaises(ValueError):
            self.f.create()

    def test_context_omission_is_explicit_and_required_text_is_never_cut(self):
        ccl.initialize(self.project)
        memory = self.project / '.ai-collab/MEMORY.md'
        memory.write_text('# Unrelated\n' + 'z' * 3000 + '\n# Export\nExport CSV handling', encoding='utf-8')
        spec = dict(self.f.spec, goal='Export CSV', context_chars=1000)
        pack = contextpack.build(self.project, ccl.CONFIG, spec, 1000)
        self.assertTrue(any(x['reason'] == 'character_budget' for x in pack['omitted']))
        self.assertTrue(any('CSV handling' in x['content'] for x in pack['selected']))
        spec['context_files'] = ['.ai-collab/MEMORY.md']
        with self.assertRaisesRegex(ValueError, 'Required context exceeds'):
            contextpack.build(self.project, ccl.CONFIG, spec, 1000)

    def test_stale_indexed_memory_is_omitted_and_missing_index_fails_closed(self):
        ccl.initialize(self.project)
        source = self.project / 'source.py'
        source.write_text('before')
        note = self.project / 'export.md'
        note.write_text('# Export\nVerified CSV guidance')
        index = self.project / '.ai-collab/context.json'
        index.write_text(json.dumps({'version': 1, 'entries': [{'path': 'export.md', 'read_when': 'CSV',
                         'source_hashes': {'source.py': governance.file_digest(source)}}]}))
        spec = dict(self.f.spec, goal='Export CSV')
        fresh = contextpack.build(self.project, ccl.CONFIG, spec)
        self.assertTrue(any(x['path'] == str(note) for x in fresh['selected']))
        source.write_text('after')
        stale = contextpack.build(self.project, ccl.CONFIG, spec)
        self.assertTrue(any(x['path'] == str(note) and x['reason'] == 'stale' for x in stale['omitted']))
        self.assertFalse(any(x['path'] == str(note) for x in stale['selected']))
        index.write_text('{}')
        task = self.f.run_task(self.f.create())
        self.assertEqual(task['state'], 'blocked')
        self.assertEqual(task['attempts'], 0)

    def test_prompt_preserves_full_goal_and_dispatch_records_manifest(self):
        self.f.spec['goal'] = '完整任务' * 6000
        self.f.spec['required_capabilities'] = ['browser visual inspection']
        task = self.f.run_task(self.f.create())
        folder = ccl.task_path(task['id']) / 'attempt-01'
        self.assertIn(self.f.spec['goal'], (folder / 'prompt.txt').read_text(encoding='utf-8'))
        handoff = ccl.read_json(folder / 'handoff.json')
        self.assertEqual(handoff['contract'], task['contract'])
        self.assertEqual(handoff['required_capabilities'][0]['status'], 'unknown')
        self.assertTrue((folder / 'context.json').is_file())

    def test_handoff_is_read_only_and_detects_changed_candidate(self):
        task = self.f.run_task(self.f.create())
        record = ccl.task_path(task['id']) / 'task.json'
        before = record.read_bytes()
        self.assertTrue(ccl.handoff_payload(task)['evidence_current'])
        (self.project / 'answer.txt').write_text('changed')
        self.assertFalse(ccl.handoff_payload(task)['evidence_current'])
        self.assertEqual(record.read_bytes(), before)

    def test_finding_duplicate_id_is_idempotent_and_open_issue_blocks_accept(self):
        task = self.f.run_task(self.f.create())
        for _ in range(2):
            ccl.finding_update(task['id'], 'add', 'F-1', 'answer.txt', 'high', 'Wrong content', 'Observed case')
        saved = ccl.read_task(ccl.task_path(task['id']) / 'task.json')
        self.assertEqual(len(saved['findings']), 1)
        self.assertEqual(len(saved['findings'][0]['history']), 1)
        with self.assertRaisesRegex(ValueError, 'Unresolved'):
            ccl.review(task['id'], 'accept', 'Cannot close an unverified issue')
        with self.assertRaisesRegex(ValueError, 'different issue'):
            ccl.finding_update(task['id'], 'add', 'F-1', 'answer.txt', 'high', 'Other problem', 'Observed')

    def test_fixed_finding_requires_current_check_receipt_and_can_be_reopened(self):
        task = self.f.create()
        ccl.finding_update(task['id'], 'add', 'F-1', 'answer.txt', 'high', 'Need output', 'File missing')
        with self.assertRaisesRegex(ValueError, 'verified candidate'):
            ccl.finding_update(task['id'], 'resolve', 'F-1', status='fixed_verified', note='Not yet checked')
        task = self.f.run_task(task)
        ccl.finding_update(task['id'], 'resolve', 'F-1', status='fixed_verified', note='Read file and actual passing check')
        task = ccl.read_task(ccl.task_path(task['id']) / 'task.json')
        self.assertEqual(ccl.effective_findings(task)[0]['effective_status'], 'fixed_verified')
        (self.project / 'answer.txt').write_text('changed')
        self.assertEqual(ccl.effective_findings(task)[0]['effective_status'], 'accepted')
        self.assertIn('"effective_status": "accepted"', ccl.prompt_for(task))

    def test_deferred_and_rejected_require_reason_and_stale_decision_reopens(self):
        task = self.f.run_task(self.f.create())
        ccl.finding_update(task['id'], 'add', 'F-1', 'answer.txt', 'low', 'Style', 'Observed style')
        with self.assertRaisesRegex(ValueError, 'note'):
            ccl.finding_update(task['id'], 'resolve', 'F-1', status='deferred')
        ccl.finding_update(task['id'], 'resolve', 'F-1', status='rejected', note='Actual requirement permits this format')
        task = ccl.read_task(ccl.task_path(task['id']) / 'task.json')
        self.assertEqual(ccl.effective_findings(task)[0]['effective_status'], 'rejected')
        (self.project / 'answer.txt').write_text('changed')
        self.assertTrue(ccl.effective_findings(task)[0]['stale'])

    def test_token_budget_allows_finishing_checks_but_refuses_next_worker(self):
        task = self.f.run_task(self.f.create(max_tokens=10))  # Fake worker reports 15.
        self.assertEqual(task['state'], 'awaiting_review')
        self.assertEqual(governance.view(task)['observed_tokens'], 15)
        with self.assertRaisesRegex(governance.BudgetExceeded, 'token budget'):
            ccl.review(task['id'], 'revise', 'Another worker is over budget')
        fixed = ccl.reverify(task['id'], 'Recheck without a new model call')
        self.assertEqual(governance.view(fixed)['observed_tokens'], 15)
        self.assertEqual(governance.view(fixed)['worker_calls'], 1)

    def test_failed_call_has_unknown_usage_and_cannot_reset_token_budget(self):
        os.environ['FAKE_MODE'] = 'crash'
        task = self.f.run_task(self.f.create(max_tokens=100))
        self.assertFalse(governance.view(task)['usage_complete'])
        with self.assertRaisesRegex(governance.BudgetExceeded, 'unknown'):
            ccl.review(task['id'], 'revise', 'No invisible free retry')

    def test_total_time_caps_worker_process_and_does_not_replay(self):
        original = ccl.process
        def slow(argv, cwd, stdin, stdout, stderr, timeout, **kw):
            if '--json' in argv:
                argv = [sys.executable, '-c', 'import time; time.sleep(20)']
            return original(argv, cwd, stdin, stdout, stderr, timeout, **kw)
        with patch.object(ccl, 'process', side_effect=slow):
            task = self.f.run_task(self.f.create(max_seconds=1))
        self.assertEqual(task['state'], 'budget_exhausted')
        self.assertEqual(task['attempts'], 1)
        self.assertGreaterEqual(governance.view(task)['spent_seconds'], 1)
        self.assertFalse((self.project / 'calls.jsonl').exists())

    def test_total_time_includes_verification_and_reverify_cannot_reset(self):
        self.f.spec['checks'] = [{'name': 'slow', 'argv': [sys.executable, '-c', 'import time; time.sleep(20)'], 'timeout': 15}]
        task = self.f.run_task(self.f.create(max_seconds=2))
        self.assertEqual(task['state'], 'budget_exhausted')
        self.assertEqual([x['kind'] for x in task['budget']['operations']], ['worker', 'check'])
        with self.assertRaises(governance.BudgetExceeded):
            ccl.reverify(task['id'], 'No fresh time budget')

    def test_hard_crash_reconciliation_charges_once(self):
        task = self.f.create(max_seconds=5, max_tokens=100)
        task.update(state='running', attempts=1)
        task['budget']['operations'].append({'id': 'crash', 'kind': 'worker', 'attempt': 1, 'status': 'running',
            'started': time.time() - 20, 'limit_seconds': 5, 'seconds': 0,
            'input_tokens': 0, 'output_tokens': 0, 'cached_input_tokens': 0, 'usage_complete': False})
        ccl.save_task(ccl.task_path(task['id']), task)
        task = self.f.run_task(task)
        self.assertEqual(task['state'], 'interrupted')
        self.assertEqual(governance.view(task)['spent_seconds'], 5)
        governance.reconcile(task, ccl.task_path(task['id']))
        self.assertEqual(governance.view(task)['spent_seconds'], 5)
        with self.assertRaises(governance.BudgetExceeded):
            ccl.review(task['id'], 'revise', 'Inspection cannot erase budget')

    def test_usage_sums_turns_and_does_not_deduct_cached_input_twice(self):
        path = self.f.root / 'events.jsonl'
        event = {'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 5, 'cached_input_tokens': 4}}
        path.write_text(json.dumps(event) + '\n' + json.dumps(event) + '\n')
        value = governance.usage(path)
        self.assertEqual((value['input_tokens'], value['output_tokens'], value['cached_input_tokens']), (20, 10, 8))
        self.assertTrue(value['usage_complete'])
        path.write_text('{"type":"turn.completed","usage":null}\n')
        self.assertFalse(governance.usage(path)['usage_complete'])

    def test_malformed_thread_event_is_not_a_valid_execution_contract(self):
        path = self.f.root / 'events.jsonl'
        path.write_text('{"type":"thread.started","thread_id":{}}\n{"type":"turn.completed"}\n')
        self.assertTrue(ccl.parse_events(path)['failed'])

    def test_initial_completion_without_thread_cannot_become_a_candidate(self):
        self.f.fake.write_text(test_loop.FAKE.replace(
            "print(json.dumps({'type':'thread.started','thread_id':'test-thread-123'}))", ''))
        task = self.f.run_task(self.f.create())
        self.assertEqual(task['state'], 'execution_failed')
        self.assertIsNone(task['thread_id'])

    def test_identical_failure_stops_early_without_exhausting_all_attempts(self):
        os.environ['FAKE_MODE'] = 'always_bad'
        task = self.f.run_task(self.f.create(attempts=3))
        self.assertEqual(task['state'], 'no_progress')
        self.assertEqual(task['attempts'], 2)

    def test_damaged_new_fields_cannot_be_downgraded_to_legacy(self):
        task = self.f.create()
        for field in ('contract', 'budget', 'findings'):
            bad = dict(task)
            bad.pop(field)
            with self.assertRaises(ValueError):
                governance.validate(bad)
        task['budget']['operations'] = [{'id': 'broken'}]
        with self.assertRaises(ValueError):
            governance.validate(task)

    def test_missing_version_cannot_drop_exhausted_budget(self):
        task = self.f.run_task(self.f.create(max_tokens=10))
        task.pop('record_version')
        task.pop('budget')
        ccl.save_task(ccl.task_path(task['id']), task)
        with self.assertRaisesRegex(ValueError, 'legacy downgrade'):
            ccl.review(task['id'], 'revise', 'Cannot erase budget')

    def test_no_progress_requires_review_and_success_breaks_failure_streak(self):
        os.environ['FAKE_MODE'] = 'always_bad'
        task = self.f.run_task(self.f.create(attempts=5))
        with self.assertRaisesRegex(ValueError, 'review revise'):
            self.f.run_task(task)
        os.environ['FAKE_MODE'] = 'ok'
        ccl.review(task['id'], 'revise', 'Inspected root cause')
        task = self.f.run_task(task)
        self.assertEqual(task['state'], 'awaiting_review')
        self.assertEqual(task['failure_signatures'], [])
        ccl.review(task['id'], 'revise', 'New change after successful fix')
        os.environ['FAKE_MODE'] = 'always_bad'
        task = self.f.run_task(task)
        self.assertEqual(task['attempts'], 5)  # Not stopped at the first post-success failure.

    def test_unknown_usage_recovery_does_not_suggest_forbidden_retry(self):
        os.environ['FAKE_MODE'] = 'crash'
        task = self.f.run_task(self.f.create(max_tokens=100))
        advice = ccl.recovery(task['id'])['recovery']
        self.assertEqual(advice['action'], 'revise_plan')
        self.assertIn('unknown', advice['advice'])

    def test_blocked_context_can_still_be_handed_back(self):
        self.f.spec['context_files'] = ['missing.md']
        task = self.f.run_task(self.f.create())
        packet = ccl.handoff_payload(task)
        self.assertEqual(packet['state'], 'blocked')
        self.assertIn('Required context missing', packet['context']['error'])
        self.assertEqual(packet['budget']['worker_calls'], 0)

    def test_stale_memory_cannot_reenter_via_shared_or_explicit_alias(self):
        ccl.initialize(self.project)
        source = self.project / 'source.py'
        source.write_text('new')
        memory = self.project / '.ai-collab/MEMORY.md'
        memory.write_text('# Old guidance\nSTALE-MARKER')
        (self.project / '.ai-collab/context.json').write_text(json.dumps({'version': 1, 'entries': [
            {'path': '.ai-collab/MEMORY.md', 'read_when': 'CSV', 'source_hashes': {'source.py': '0' * 64}}]}))
        pack = contextpack.build(self.project, ccl.CONFIG, self.f.spec)
        self.assertNotIn('STALE-MARKER', contextpack.render(pack))
        self.assertEqual(sum(x['reason'] == 'stale' for x in pack['omitted']), 1)
        self.f.spec['context_files'] = ['.ai-collab/MEMORY.md']
        with self.assertRaisesRegex(ValueError, 'Required context is stale'):
            contextpack.build(self.project, ccl.CONFIG, self.f.spec)

    def test_required_context_duplicate_is_injected_only_once(self):
        ccl.initialize(self.project)
        self.f.spec['context_files'] = ['.ai-collab/MEMORY.md', '.ai-collab/../.ai-collab/MEMORY.md']
        pack = contextpack.build(self.project, ccl.CONFIG, self.f.spec)
        path = str((self.project / '.ai-collab/MEMORY.md').resolve())
        self.assertEqual(sum(x['path'] == path for x in pack['selected']), 1)

    def test_required_indexed_context_is_not_filtered_by_relevance(self):
        ccl.initialize(self.project)
        source = self.project / 'source.py'
        source.write_text('current')
        note = self.project / 'required.md'
        note.write_text('UNIQUE-MANDATORY-TEXT')
        (self.project / '.ai-collab/context.json').write_text(json.dumps({'version': 1, 'entries': [
            {'path': 'required.md', 'read_when': 'unrelated', 'source_hashes': {'source.py': governance.file_digest(source)}}]}))
        self.f.spec['context_files'] = ['required.md']
        pack = contextpack.build(self.project, ccl.CONFIG, self.f.spec)
        self.assertIn('UNIQUE-MANDATORY-TEXT', contextpack.render(pack))

    def test_protected_file_coverage_cannot_be_deleted(self):
        (self.project / 'checker.py').write_text('assert False')
        self.f.spec['verification_files'] = ['checker.py']
        task = self.f.create()
        task['contract']['verification_files'] = {}
        (self.project / 'checker.py').write_text('pass')
        ccl.save_task(ccl.task_path(task['id']), task)
        task = self.f.run_task(task)
        self.assertEqual(task['state'], 'blocked')
        self.assertEqual(task['attempts'], 0)

    def test_incomplete_artifact_receipt_is_rejected(self):
        task = self.f.run_task(self.f.create())
        task['verification']['artifact_hashes'] = {}
        ccl.save_task(ccl.task_path(task['id']), task)
        with self.assertRaisesRegex(ValueError, 'Incomplete successful'):
            ccl.review(task['id'], 'accept', 'Cannot hide artifact corruption')

    def test_ignored_source_finding_must_match_verification_not_only_resolution_time(self):
        source = self.project / 'source.py'
        source.write_text('correct')
        (self.project / '.gitignore').write_text('source.py\n')
        task = self.f.create()
        ccl.finding_update(task['id'], 'add', 'F-1', 'source.py', 'high', 'Verify source', 'Reproduction')
        task = self.f.run_task(task)
        source.write_text('broken')
        with self.assertRaisesRegex(ValueError, 'Finding path was not verified'):
            ccl.finding_update(task['id'], 'resolve', 'F-1', status='fixed_verified', note='Old checks cannot prove new content')
        self.assertFalse(ccl.handoff_payload(task)['evidence_current'])

    def test_legacy_ledger_write_is_rejected_without_corrupting_record(self):
        task = self.f.create()
        for key in ('record_version', 'contract', 'budget', 'findings'):
            task.pop(key)
        folder = ccl.task_path(task['id'])
        ccl.save_task(folder, task)
        before = (folder / 'task.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'Legacy'):
            ccl.finding_update(task['id'], 'add', 'F-1', 'answer.txt', 'low', 'Issue', 'Evidence')
        self.assertEqual((folder / 'task.json').read_bytes(), before)

    def test_legacy_candidate_requires_explicit_reverify_and_preserves_attempts(self):
        task = self.f.run_task(self.f.create())
        for field in ('record_version', 'contract', 'budget', 'findings'):
            task.pop(field)
        task['verification'].pop('contract')
        ccl.save_task(ccl.task_path(task['id']), task)
        with self.assertRaisesRegex(ValueError, 'Legacy'):
            ccl.review(task['id'], 'accept', 'Must bind contract first')
        task = ccl.reverify(task['id'], 'Explicitly reviewed the legacy spec')
        self.assertEqual(task['attempts'], 1)
        self.assertEqual(governance.view(task)['scope'], 'legacy')
        self.assertEqual(ccl.review(task['id'], 'accept', 'Actual evidence checked')['state'], 'accepted')

    def test_installed_cli_full_new_run_finding_review_handoff_path(self):
        home = self.f.root / 'installed 中文 space'
        home.mkdir()
        prefix = home / '.local/share/claude-codex-loop'
        binary = home / '.local/bin/ccl'
        with patch.object(install, 'HOME', home), patch.object(install, 'PREFIX', prefix), patch.object(install, 'BIN', binary), \
                patch.object(install, 'configure_windows_path'), contextlib.redirect_stdout(io.StringIO()):
            install.main('module')
        env = dict(os.environ, CCL_STATE_DIR=str(ccl.STATE), CCL_CONFIG_DIR=str(ccl.CONFIG))
        def cli(*args, expected=0):
            result = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(prefix / 'ccl.py'), *args],
                                    capture_output=True, encoding='utf-8', env=env, timeout=30)
            self.assertEqual(result.returncode, expected, result.stderr + result.stdout)
            return result.stdout
        spec = self.f.root / 'spec.json'
        spec.write_text(json.dumps(self.f.spec), encoding='utf-8')
        task = json.loads(cli('new', '--project', str(self.project), '--spec', str(spec), '--max-tokens', '100'))['id']
        cli('run', task)
        cli('finding', task, 'add', '--id', 'F-1', '--path', 'answer.txt', '--problem', 'Check result', '--evidence', 'Review output')
        cli('review', task, 'accept', '--note', 'Not yet resolved', expected=2)
        cli('finding', task, 'resolve', '--id', 'F-1', '--status', 'fixed_verified', '--note', 'Read artifact and check receipt')
        packet = json.loads(cli('handoff', task))
        self.assertTrue(packet['evidence_current'])
        self.assertEqual(packet['budget']['observed_tokens'], 15)
        self.assertEqual(json.loads(cli('review', task, 'accept', '--note', 'All evidence reviewed'))['state'], 'accepted')


class CompatibilityTests(unittest.TestCase):
    def test_probe_timeout_cleans_up_pipe_inheriting_child(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            pidfile = Path(folder) / 'child-pid'
            script = ('import subprocess,sys,time; from pathlib import Path; '
                      'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(20)"]); '
                      'Path(' + repr(str(pidfile)) + ').write_text(str(p.pid));time.sleep(30)')
            start = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired):
                runtime.capture_output([sys.executable, '-c', script], timeout=1.5)
            self.assertLess(time.monotonic() - start, 8)
            self.assertTrue(pidfile.is_file())
            pid = int(pidfile.read_text())
            if os.name == 'nt':
                self.assertFalse(runtime.pid_alive(pid))

    def test_actual_required_flags_are_checked_per_subcommand(self):
        def run(argv, **kwargs):
            command = 'exec resume' if 'resume' in argv else 'exec'
            help_text = '\n'.join('    ' + flag + ' <ARG>' for flag in compatibility.REQUIRED[command])
            return subprocess.CompletedProcess(argv, 0, help_text, '')
        with patch.object(compatibility.runtime, 'capture_output', side_effect=run) as mock:
            report = compatibility.probe()
        self.assertEqual(report['status'], 'ok')
        self.assertEqual(report['event_contract'], 'unknown')
        self.assertEqual(mock.call_count, 2)
        self.assertTrue(all(call.args[0][-1] == '--help' for call in mock.call_args_list))

    def test_missing_flags_error_and_probe_failure_unknown(self):
        with patch.object(compatibility.runtime, 'capture_output', return_value=subprocess.CompletedProcess([], 0, '    --json\n', 'secret')):
            report = compatibility.probe()
            self.assertEqual(report['status'], 'error')
            self.assertNotIn('secret', json.dumps(report))
        with patch.object(compatibility.runtime, 'capture_output', side_effect=FileNotFoundError):
            self.assertEqual(compatibility.probe()['status'], 'unknown')

    def test_readonly_gate_allows_compat_but_not_ledger_mutation(self):
        self.assertTrue(modes.diagnostic_command('ccl doctor --compat --session abc'))
        self.assertTrue(modes.diagnostic_command('ccl handoff abcdef123456'))
        self.assertFalse(modes.diagnostic_command('ccl doctor --compat; echo bad'))
        self.assertFalse(modes.diagnostic_command('ccl finding abcdef123456 resolve'))


if __name__ == '__main__':
    unittest.main()
