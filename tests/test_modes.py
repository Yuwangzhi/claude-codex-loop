import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ccl
import modes


class ModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name).resolve()
        self.root = self.project/'state'
        self.store = modes.ModeStore(self.root)
        self.patches = [patch.object(ccl, 'STATE', self.root), patch.object(ccl, 'CONFIG', self.project/'config')]
        for p in self.patches:
            p.start()
        self.session = 'session-a'

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def hook(self, event, **kwargs):
        return ccl.hook(dict(cwd=str(self.project),session_id=self.session,hook_event_name=event,**kwargs))

    def test_no_default_and_unselected_cannot_execute(self):
        self.hook('SessionStart')
        self.hook('UserPromptSubmit',prompt='帮我修改文件')
        self.assertIsNone(self.store.get(self.session)['mode'])
        for tool in ['Read','Write','Bash','Agent','mcp__browser__run']:
            response=self.hook('PreToolUse',tool_name=tool,tool_input={'command':'touch test'})
            self.assertEqual(response['hookSpecificOutput']['permissionDecision'],'deny')
        self.assertEqual(self.hook('PreToolUse',tool_name='Skill',tool_input={'skill':'work-mode'}),{})

    def test_choice_with_task_and_persistence_during_clarification(self):
        self.hook('UserPromptSubmit',prompt='模式二：修改文件')
        self.store.begin(self.session,self.project,'edit')
        self.store.wait(self.session,'文件名')
        self.hook('UserPromptSubmit',prompt='文件是 example.txt')
        self.assertEqual(self.store.get(self.session)['mode'],'2')
        self.assertEqual(self.store.get(self.session)['phase'],'working')
        self.hook('SessionStart',source='compact')
        self.assertEqual(self.store.get(self.session)['mode'],'2')

    def test_numeric_choice_not_accepted_as_task_content(self):
        self.hook('UserPromptSubmit',prompt='2')
        self.store.begin(self.session,self.project,'task')
        self.hook('UserPromptSubmit',prompt='1')
        self.assertEqual(self.store.get(self.session)['mode'],'2')
        self.assertIsNone(modes.parse_choice('分析一下模式一和模式二有什么差别'))
        self.assertIsNone(modes.parse_choice('不要用模式一'))

    def test_mode_two_denies_codex_dispatch(self):
        self.hook('UserPromptSubmit',prompt='模式二')
        for tool,args in [('Bash',{'command':'/Users/user/.local/bin/ccl run abcd'}),
                          ('Bash',{'command':'codex exec "do work"'}),('mcp__codex__codex',{})]:
            self.assertEqual(self.hook('PreToolUse',tool_name=tool,tool_input=args)['hookSpecificOutput']['permissionDecision'],'deny')
        self.assertEqual(self.hook('PreToolUse',tool_name='Write',tool_input={}),{})
        self.assertEqual(self.store.get(self.session)['phase'],'working')
        with self.assertRaises(ValueError):
            self.store.require(self.session,'1')

    def test_finish_clears_mode_and_future_task_waits_again(self):
        self.hook('UserPromptSubmit',prompt='模式二')
        self.store.begin(self.session,self.project,'task')
        self.store.finish(self.session,'verified file',[])
        self.hook('UserPromptSubmit',prompt='再帮我做另一个任务')
        state=self.store.get(self.session)
        self.assertIsNone(state['mode'])
        self.assertEqual(state['last_mode'],'2')
        reminder=self.hook('Stop',last_assistant_message='完成')
        self.assertEqual(reminder['decision'],'block')
        self.assertEqual(self.hook('Stop',last_assistant_message='完成',stop_hook_active=True),{})
        self.assertEqual(self.hook('Stop',last_assistant_message='模式一还是模式二？'),{})

    def test_just_selecting_mode_does_not_consume_it(self):
        self.hook('UserPromptSubmit',prompt='2')
        self.assertEqual(self.hook('Stop',last_assistant_message='已选择模式二，请说任务'),{})
        self.assertEqual(self.store.get(self.session)['mode'],'2')

    def test_sessions_are_isolated(self):
        self.store.submitted('session-a','模式一',[])
        self.store.submitted('session-b','模式二',[])
        self.store.finish('session-a','done',[])
        self.assertEqual(self.store.get('session-b')['mode'],'2')
        self.assertIsNone(self.store.get('session-c')['mode'])

    def test_switch_waits_for_old_worker_handoff(self):
        self.store.submitted(self.session,'模式一',[])
        self.store.begin(self.session,self.project,'old task')
        self.store.submitted(self.session,'切换到模式二',[{'id':'old','state':'running'}])
        state=self.store.get(self.session)
        self.assertEqual(state['mode'],'1')
        self.assertEqual(state['pending_mode'],'2')
        with self.assertRaises(ValueError):
            self.store.require(self.session,'1')
        with self.assertRaises(ValueError):
            self.store.activate(self.session,[{'id':'old'}])
        self.store.activate(self.session,[])
        self.assertEqual(self.store.get(self.session)['mode'],'2')

    def test_incomplete_ccl_task_cannot_finish_mode(self):
        self.store.submitted(self.session,'模式一',[])
        with self.assertRaises(ValueError):
            self.store.finish(self.session,'done',[{'id':'unresolved'}])

    def test_native_mode_memory_shared_without_fake_codex_task(self):
        self.store.submitted(self.session,'模式二',[])
        ccl.native_memory(self.project,self.session,'Verified by Claude','artifact/check')
        self.assertIn('Verified by Claude',(self.project/'.ai-collab/MEMORY.md').read_text())
        self.assertFalse(list((self.root/'projects').glob('*/tasks/*/task.json')))

    def test_choice_from_native_question_is_recorded(self):
        self.hook('PostToolUse',tool_name='AskUserQuestion',
                  tool_input={'questions':[{'question':'本次模式一还是模式二？'}]},
                  tool_response={'answers':{'本次模式一还是模式二？':'模式二：仅 Claude'}})
        self.assertEqual(self.store.get(self.session)['mode'],'2')

    def test_shell_metacharacters_cannot_smuggle_work_before_choice(self):
        for cmd in ['ccl mode status --session x; touch x','ccl mode status --session $(touch x)',
                    'ccl mode status --session x > file','ccl mode status --session x\ntouch x']:
            self.assertFalse(modes.control_command(cmd))

    def test_powershell_mode_control_and_dispatch(self):
        command = '& "C:/Users/example/.local/bin/ccl.cmd" mode status --session session-a'
        self.assertTrue(modes.control_command(command))
        self.assertEqual(self.hook('PreToolUse', tool_name='PowerShell', tool_input={'command':command}), {})
        self.hook('UserPromptSubmit', prompt='模式二')
        for command in ['& "C:/Users/example/.local/bin/ccl.cmd" run abc', 'codex exec "work"']:
            response = self.hook('PreToolUse', tool_name='PowerShell', tool_input={'command':command})
            self.assertEqual(response['hookSpecificOutput']['permissionDecision'], 'deny')


if __name__=='__main__':
    unittest.main()
