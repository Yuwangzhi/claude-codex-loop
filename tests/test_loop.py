import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ccl


FAKE = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
result_path = Path(args[args.index('--output-last-message')+1])
attempt = int(result_path.parent.name.split('-')[1])
mode = os.environ.get('FAKE_MODE', 'ok')
prompt = sys.stdin.read()
with (Path.cwd()/'calls.jsonl').open('a') as f:
    f.write(json.dumps({'args':args,'prompt':prompt})+'\n')
print(json.dumps({'type':'thread.started','thread_id':'test-thread-123'}))
if mode == 'crash':
    print(json.dumps({'type':'turn.failed','error':{'message':'test failure'}}))
    sys.exit(1)
if mode != 'missing':
    value = 'broken' if mode == 'always_bad' or (mode == 'repair' and attempt == 1) else 'correct'
    (Path.cwd()/'answer.txt').write_text(value)
print(json.dumps({'type':'item.completed','item':{'type':'command_execution','exit_code':0}}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':5}}))
result = {'outcome':'ready','summary':'Done','artifacts':['answer.txt'],'verification':['self report'],
          'memory_proposals':['fixture fact'],'blockers':[]}
if mode == 'blocked':
    result.update(outcome='blocked',blockers=['Required login is missing'])
if mode == 'malformed':
    result = {'summary':'all done'}
if mode == 'extra':
    (Path.cwd()/'extra.txt').write_text('additional delivered artifact')
    result['artifacts'].append('extra.txt')
result_path.write_text(json.dumps(result))
'''


class LoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / 'project with spaces'
        self.project.mkdir()
        self.fake = self.root / 'fake-codex.py'
        self.fake.write_text(FAKE)
        self.fake.chmod(0o700)
        self.patches = [patch.object(ccl, 'STATE', self.root/'state'),
                        patch.object(ccl, 'CONFIG', self.root/'config'),
                        patch.dict(os.environ, {'CCL_CODEX_BIN':str(self.fake),'FAKE_MODE':'ok'})]
        for p in self.patches:
            p.start()
        self.spec = {'title':'Check work','goal':'Produce correct output','plan':'Write answer.txt',
                     'acceptance':['answer is correct'],'constraints':['Only project files'],
                     'artifacts':['answer.txt'],'checks':[{'name':'content check','argv':[sys.executable,'-c',
                     "from pathlib import Path; assert Path('answer.txt').read_text() == 'correct'"], 'timeout':10}]}

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def create(self, **kw):
        if kw.get('owner'):
            ccl.modes.ModeStore(ccl.STATE).submitted(kw['owner'], '模式一', [])
        return ccl.create(self.project, self.spec, **kw)

    def run_task(self, task):
        with contextlib.redirect_stdout(io.StringIO()):
            return ccl.run_task(task['id'])

    def test_real_process_repair_reuses_thread_and_preserves_plan(self):
        os.environ['FAKE_MODE'] = 'repair'
        task = self.run_task(self.create())
        self.assertEqual(task['state'], 'awaiting_review')
        self.assertEqual(task['attempts'], 2)
        calls = [json.loads(s) for s in (self.project/'calls.jsonl').read_text().splitlines()]
        self.assertNotIn('resume', calls[0]['args'])
        self.assertIn('test-thread-123', calls[1]['args'])
        self.assertIn('Independent acceptance checks failed', calls[1]['prompt'])
        self.assertIn('Produce correct output', calls[1]['prompt'])
        for call in calls:
            self.assertIn('approval_policy="never"', call['args'])
            self.assertIn('sandbox_mode="danger-full-access"', call['args'])
            self.assertNotIn('--full-auto', call['args'])
        self.assertEqual(task['history'][0]['tool_items'], 1)

    def test_explicit_restricted_task_keeps_its_sandbox(self):
        for sandbox in ('read-only', 'workspace-write'):
            task = self.create(sandbox=sandbox)
            args = ccl.codex_argv(task, self.root / 'attempt')
            self.assertIn('sandbox_mode="' + sandbox + '"', args)
            self.assertIn('approval_policy="never"', args)

    def test_check_failure_never_accepted_and_budget_is_bounded(self):
        os.environ['FAKE_MODE'] = 'always_bad'
        task = self.run_task(self.create(attempts=2))
        self.assertEqual(task['state'], 'budget_exhausted')
        self.assertEqual(task['attempts'], 2)
        with self.assertRaises(ValueError):
            ccl.review(task['id'], 'accept', 'It said done')
        with self.assertRaises(ValueError):
            self.run_task(task)

    def test_missing_artifact_rejects_success_claim(self):
        os.environ['FAKE_MODE'] = 'missing'
        self.spec['checks'] = []
        task = self.run_task(self.create(attempts=1))
        self.assertEqual(task['state'], 'budget_exhausted')

    def test_crash_stops_without_burning_all_attempts(self):
        os.environ['FAKE_MODE'] = 'crash'
        task = self.run_task(self.create())
        self.assertEqual(task['state'], 'execution_failed')
        self.assertEqual(task['attempts'], 1)
        self.assertEqual(task['thread_id'], 'test-thread-123')

    def test_malformed_result_not_success(self):
        os.environ['FAKE_MODE'] = 'malformed'
        self.assertEqual(self.run_task(self.create())['state'], 'execution_failed')

    def test_worker_blocker_is_not_automatically_retried(self):
        os.environ['FAKE_MODE'] = 'blocked'
        task = self.run_task(self.create())
        self.assertEqual(task['state'], 'blocked')
        self.assertEqual(task['attempts'], 1)

    def test_changed_artifact_invalidates_acceptance(self):
        task = self.run_task(self.create())
        (self.project/'answer.txt').write_text('changed after check')
        with self.assertRaisesRegex(ValueError, 'Artifact changed'):
            ccl.review(task['id'], 'accept', 'Checked earlier')

    def test_additional_reported_artifact_is_also_fingerprinted(self):
        os.environ['FAKE_MODE'] = 'extra'
        task = self.run_task(self.create())
        self.assertIn('extra.txt',task['verification']['artifact_hashes'])
        (self.project/'extra.txt').write_text('unreviewed change')
        with self.assertRaisesRegex(ValueError, 'Artifact changed'):
            ccl.review(task['id'], 'accept', 'Checked earlier')

    def test_claude_revision_returns_to_same_worker(self):
        task = self.run_task(self.create())
        ccl.review(task['id'], 'revise', 'Check edge cases and preserve correct output')
        task = self.run_task(task)
        self.assertEqual(task['attempts'], 2)
        calls = [json.loads(s) for s in (self.project/'calls.jsonl').read_text().splitlines()]
        self.assertIn('Claude review requires revision', calls[-1]['prompt'])

    def test_memory_requires_acceptance_and_deduplicates(self):
        task = self.run_task(self.create())
        with self.assertRaises(ValueError):
            ccl.remember(task['id'], 'Verified fact', 'test evidence')
        ccl.review(task['id'], 'accept', 'Read answer.txt and actual check log')
        for _ in range(2):
            ccl.remember(task['id'], 'Verified fact', 'test evidence')
        self.assertEqual((self.project/'.ai-collab/MEMORY.md').read_text().count('Verified fact'), 1)
        next_task = self.create()
        self.assertIn('Verified fact', ccl.prompt_for(next_task))

    def test_stop_hook_only_owns_current_session_and_does_not_loop(self):
        task = self.create(owner='claude-session-one')
        payload = {'cwd':str(self.project),'session_id':'claude-session-two','hook_event_name':'Stop'}
        ccl.modes.ModeStore(ccl.STATE).submitted('claude-session-two', '模式二', [])
        self.assertEqual(ccl.hook(payload), {})
        payload['session_id'] = 'claude-session-one'
        self.assertEqual(ccl.hook(payload)['decision'], 'block')
        payload['cwd'] = str(self.root)
        self.assertEqual(ccl.hook(payload)['decision'], 'block')
        payload['stop_hook_active'] = True
        self.assertEqual(ccl.hook(payload), {})
        payload['hook_event_name'] = 'SessionStart'
        payload['cwd'] = str(self.project)
        self.assertIn(task['id'], ccl.hook(payload)['hookSpecificOutput']['additionalContext'])

    def test_project_lock_prevents_duplicate_launch(self):
        with ccl.lock(self.project):
            with self.assertRaisesRegex(ValueError, 'Another CCL'):
                self.create()

    def test_orphan_worker_prevents_parallel_replay(self):
        task = self.create()
        task.update(state='running', worker_pid=os.getpid())
        ccl.save_task(ccl.task_path(task['id']), task)
        with self.assertRaisesRegex(ValueError, 'worker may still'):
            self.run_task(task)
        with self.assertRaisesRegex(ValueError, 'worker may still'):
            self.create()

    def test_hard_interruption_requires_inspection_before_resume(self):
        task = self.create()
        task['state'] = 'running'
        ccl.save_task(ccl.task_path(task['id']), task)
        recovered = self.run_task(task)
        self.assertEqual(recovered['state'], 'interrupted')
        self.assertEqual(recovered['attempts'], 0)
        with self.assertRaisesRegex(ValueError, 'Inspect interrupted'):
            self.run_task(task)
        ccl.review(task['id'], 'revise', 'Inspected prior records; safe to continue')
        self.assertEqual(self.run_task(task)['state'], 'awaiting_review')

    def test_paths_cannot_escape_project_even_with_symlink(self):
        names = ['../escape.txt', str(self.root/'escape.txt')]
        try:
            (self.project/'outside').symlink_to(self.root, target_is_directory=True)
            names.append('outside/escape.txt')
        except OSError:
            if os.name != 'nt':
                raise
        for name in names:
            self.spec['artifacts'] = [name]
            with self.assertRaises(ValueError):
                self.create()

    def test_git_changes_after_check_invalidate_acceptance(self):
        subprocess.run(['git','init','-q',str(self.project)],check=True)
        task = self.run_task(self.create())
        (self.project/'unrelated.txt').write_text('new unreviewed change')
        with self.assertRaisesRegex(ValueError, 'Git workspace changed'):
            ccl.review(task['id'], 'accept', 'Previously reviewed')

    def test_process_timeout_kills_child_group(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            ccl.process([sys.executable,'-c','import time; time.sleep(20)'], self.project, None,
                        self.root/'out', self.root/'err', .05)

    def test_noncode_report_requires_human_review(self):
        self.spec['checks'] = []
        task = self.run_task(self.create())
        self.assertEqual(task['state'], 'awaiting_review')
        self.assertEqual(task['verification']['automated_check_count'], 0)
        self.assertEqual(ccl.review(task['id'], 'accept', 'Read and verified report')['state'], 'accepted')

    def test_selected_mode_one_completes_loop_then_requires_new_choice(self):
        session = 'mode-one-owner'
        task = self.run_task(self.create(owner=session))
        ccl.review(task['id'], 'accept', 'Read artifact and verification output')
        store = ccl.modes.ModeStore(ccl.STATE)
        state = store.finish(session, 'accepted task', ccl.owned_tasks(session))
        self.assertIsNone(state['mode'])
        with self.assertRaises(ValueError):
            ccl.create(self.project, self.spec, owner=session)


if __name__ == '__main__':
    unittest.main()
