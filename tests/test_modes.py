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

    def test_unselected_is_never_blocked_and_claude_is_told_to_choose(self):
        self.hook('SessionStart')
        response = self.hook('UserPromptSubmit', prompt='帮我修改文件')
        self.assertIsNone(self.store.get(self.session)['mode'])
        context = response['hookSpecificOutput']['additionalContext']
        self.assertIn('不要问用户', context)
        self.assertIn('--mode', context)
        for tool in ['Read', 'Write', 'Bash', 'Agent', 'mcp__browser__run']:
            self.assertEqual(self.hook('PreToolUse', tool_name=tool, tool_input={'command': 'touch test'}), {})
        self.assertEqual(self.hook('PreToolUse', tool_name='Bash', tool_input={'command': 'ccl mode sync --session x | head'}), {})

    def test_claude_picks_by_difficulty_and_user_choice_wins(self):
        self.hook('UserPromptSubmit', prompt='帮我改一个小文件')
        state = self.store.begin(self.session, self.project, 'small edit', '2')
        self.assertEqual((state['mode'], state['selection_source']), ('2', 'auto_by_claude'))
        # Claude may re-pick for a harder task while no Codex task is open ...
        self.assertEqual(self.store.begin(self.session, self.project, 'big', '1')['mode'], '1')
        # ... but not while one is open, and never against an explicit user choice.
        self.assertEqual(self.store.begin(self.session, self.project, 'x', '2', [{'id': 'open'}])['mode'], '1')
        self.hook('UserPromptSubmit', prompt='moshi 2')
        self.assertEqual(self.store.get(self.session)['selection_source'], 'explicit_user_prompt')
        self.assertEqual(self.store.begin(self.session, self.project, 'y', '1')['mode'], '2')

    def test_dispatch_without_a_mode_selects_mode_one_unless_user_chose_two(self):
        self.assertEqual(self.store.require(self.session, '1')['mode'], '1')
        self.assertEqual(self.store.get(self.session)['selection_source'], 'auto_by_dispatch')
        self.store.finish(self.session, 'done', [])
        self.store.submitted(self.session, '模式二', [])
        with self.assertRaises(ValueError):
            self.store.require(self.session, '1')

    def test_bare_answer_changes_an_auto_pick(self):
        self.store.begin(self.session, self.project, 'task', '2')
        self.store.submitted(self.session, '一吧', [])
        state = self.store.get(self.session)
        self.assertEqual((state['mode'], state['selection_source']), ('1', 'explicit_user_prompt'))

    def test_pinyin_and_english_mode_words(self):
        for prompt, mode in [('moshi 2', '2'), ('Moshi2', '2'), ('mode 1', '1'), ('mode 2 帮我改', '2'), ('用moshi1做', '1')]:
            with self.subTest(prompt=prompt):
                self.assertEqual(modes.parse_choice(prompt), mode)
        for prompt in ['mode 2 是什么意思', 'not mode 1', 'dark mode 2']:
            with self.subTest(prompt=prompt):
                self.assertIsNone(modes.parse_choice(prompt))

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

    def test_finish_clears_mode_and_next_task_is_chosen_again_without_asking(self):
        self.hook('UserPromptSubmit', prompt='模式二')
        self.store.begin(self.session, self.project, 'task')
        self.store.finish(self.session, 'verified file', [])
        self.assertIsNone(self.store.get(self.session)['mode'])
        self.assertEqual(self.hook('Stop', last_assistant_message='完成'), {})
        response = self.hook('UserPromptSubmit', prompt='再帮我做另一个任务')
        state = self.store.get(self.session)
        self.assertIsNone(state['mode'])
        self.assertEqual(state['last_mode'], '2')
        context = response['hookSpecificOutput']['additionalContext']
        self.assertIn('上次为模式二', context)
        self.assertIn('按任务难度', context)
        self.assertEqual(self.hook('PreToolUse', tool_name='Write', tool_input={}), {})

    def test_turn_end_parks_task_without_forcing_finish_or_menu(self):
        self.hook('UserPromptSubmit', prompt='模式一')
        self.store.begin(self.session, self.project, 'task')
        self.assertEqual(self.hook('Stop', last_assistant_message='做完了，没有任何提示'), {})
        state = self.store.get(self.session)
        self.assertEqual((state['mode'], state['phase']), ('1', 'ready'))
        self.hook('UserPromptSubmit', prompt='再补一点')
        self.assertEqual(self.store.get(self.session)['mode'], '1')

    def test_informal_choices(self):
        for prompt in ['1','一','模式1','模式一','模式 １','一吧','用1','就用一吧','那就模式1吧','我觉得模式一吧','模式1：不要删除文件']:
            with self.subTest(prompt=prompt):
                self.assertEqual(modes.parse_choice(prompt),'1')
        for prompt in ['2','二','模式2','模式二','２','二吧','那就2吧','还是二','这次用模式二','模式二：仅 Claude','我想选2']:
            with self.subTest(prompt=prompt):
                self.assertEqual(modes.parse_choice(prompt),'2')

    def test_numbers_mentions_negation_and_ambiguous_answers_are_not_choices(self):
        for prompt in ['看一下','再试一次','有2个错误','跑1次','12','模式12','模式一二','模式1或2',
                       '模式一和模式二','模式1 / 模式2','不要用模式一','不选2','不是模式二',
                       '模式一还是模式二？','解释模式二','模式一可以吗','在文件里写模式二','随便','沿用上次']:
            with self.subTest(prompt=prompt):
                self.assertIsNone(modes.parse_choice(prompt))

    def test_new_session_is_not_forced_and_does_not_change_active_sessions(self):
        self.store.submitted('session-a', '模式一', [])
        self.store.submitted('session-b', '帮我修复文件', [])
        self.assertIsNone(self.store.get('session-b')['mode'])
        self.store.submitted('session-c', '模式二', [])
        self.assertEqual(self.store.get('session-a')['mode'], '1')
        self.assertIsNone(self.store.get('session-b')['mode'])
        self.assertEqual(self.store.begin('session-d', self.project, 'x')['mode'], '2')

    def test_session_last_mode_wins_over_another_session_preference(self):
        self.store.submitted('session-a', '模式一', [])
        self.store.finish('session-a', 'done', [])
        self.store.submitted('session-b', '模式二', [])
        self.store.submitted('session-a', '再来一个任务', [])
        self.assertIsNone(self.store.get('session-a')['mode'])
        self.assertEqual(self.store.begin('session-a', self.project, 'x')['mode'], '1')

    def test_existing_installation_migrates_last_saved_choice(self):
        with self.store.edit('legacy') as state:
            state.update(mode=None, last_mode='2', phase='awaiting_choice', selection_source='transcript_sync')
        self.assertIsNone(self.store.submitted(self.session, '继续执行', [])['mode'])
        state = self.store.begin(self.session, self.project, 'x')
        self.assertEqual((state['mode'], state['selection_source']), ('2', 'auto_by_claude'))

    def test_legacy_state_without_choice_evidence_does_not_set_default(self):
        with self.store.edit('synthetic') as state:
            state.update(mode=None,last_mode='2',phase='awaiting_choice')
        self.assertIsNone(self.store.submitted(self.session,'继续执行',[])['mode'])

    def test_session_start_shows_inherited_preference_without_consuming_task(self):
        self.store.submitted('previous', '模式二', [])
        response = self.hook('SessionStart')
        self.assertIn('不要问用户', response['hookSpecificOutput']['additionalContext'])
        self.assertIsNone(self.store.get(self.session)['mode'])

    def test_unanswered_native_mode_question_leaves_choice_to_claude(self):
        self.store.submitted('previous', '模式二', [])
        response = self.hook('PostToolUse', tool_name='AskUserQuestion',
            tool_input={'questions': [{'question': '模式1还是模式2？'}]}, tool_response={'answers': {}})
        self.assertIsNone(self.store.get(self.session)['mode'])
        self.assertIn('不要问用户', response['hookSpecificOutput']['additionalContext'])

    def test_unanswered_first_mode_question_still_needs_real_choice(self):
        self.hook('PostToolUse',tool_name='AskUserQuestion',
            tool_input={'questions':[{'question':'模式1还是模式2？'}]},tool_response={'answers':{}})
        self.assertIsNone(self.store.get(self.session)['mode'])

    def test_native_numeric_answer_can_change_active_mode(self):
        self.store.submitted(self.session,'模式一',[])
        self.store.begin(self.session,self.project,'work')
        question='模式1还是模式2？'
        self.hook('PostToolUse',tool_name='AskUserQuestion',
            tool_input={'questions':[{'question':question}]},tool_response={'answers':{question:'二吧'}})
        self.assertEqual(self.store.get(self.session)['mode'],'2')

    def test_default_can_be_overridden_before_work_but_numbers_in_work_are_not_choices(self):
        self.store.submitted('old','模式二',[])
        self.store.submitted(self.session,'帮我修复文件',[])
        self.store.submitted(self.session,'1',[])
        self.assertEqual(self.store.get(self.session)['mode'],'1')
        self.store.begin(self.session,self.project,'work')
        self.store.submitted(self.session,'2',[])
        self.assertEqual(self.store.get(self.session)['mode'],'1')

    def test_auto_mode_accepts_bare_switch_during_work(self):
        self.store.submitted(self.session, '继续', [])
        self.store.begin(self.session, self.project, 'work', '2')
        self.store.submitted(self.session, '一吧', [])
        self.assertEqual(self.store.get(self.session)['mode'], '1')

    def transcript(self, records):
        path=self.project/'transcript.jsonl'
        path.write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in records)+'\n',encoding='utf-8')
        return path

    def user_record(self, text, stamp='2026-01-01T00:00:01.000Z', **kwargs):
        return dict(type='user',timestamp=stamp,uuid=stamp,message={'content':text},origin={'kind':'human'},**kwargs)

    def test_sync_recovers_menu_answer_from_real_human(self):
        path=self.transcript([
            {'type':'assistant','message':{'content':[{'type':'text','text':'请选择模式 1 或模式 2'}]}},
            self.user_record('二吧'),
        ])
        state=self.store.sync(self.session,path,[])
        self.assertEqual(state['mode'],'2')
        self.assertEqual(state['selection_source'],'transcript_sync')
        self.assertEqual(state['selection_evidence']['source'],'user_prompt')

    def test_sync_ignores_tools_meta_and_unprompted_bare_numbers(self):
        path = self.transcript([
            self.user_record('帮我检查文件'),
            {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': '要检查几个文件？'}]}},
            self.user_record('1'),
            self.user_record('模式二', isMeta=True),
            {'type': 'user', 'message': {'content': '模式二'}, 'origin': {'kind': 'tool'}},
            {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'content': '模式二'}]}},
        ])
        self.assertIsNone(self.store.sync(self.session, path, [])['mode'])

    def test_sync_accepts_bare_first_choice(self):
        path=self.transcript([self.user_record('二吧')])
        self.assertEqual(self.store.sync(self.session,path,[])['mode'],'2')

    def test_sync_accepts_bare_override_of_auto_mode(self):
        self.store.begin(self.session, self.project, 'work', '2')
        path = self.transcript([
            {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': '本次我选了模式二'}]}},
            self.user_record('1', '2999-01-01T00:00:02.000Z')])
        self.assertEqual(self.store.sync(self.session, path, [])['mode'], '1')

    def test_sync_native_question_uses_numeric_menu_and_matched_answer(self):
        question='本次用模式1还是模式2？'
        path=self.transcript([{'type':'user','timestamp':'2026-01-01T00:00:01.000Z',
            'toolUseResult':{'questions':[{'question':question}], 'answers':{question:'那就2吧'}}}])
        self.assertEqual(self.store.sync(self.session,path,[])['mode'],'2')

    def test_sync_after_finish_does_not_replay_old_selection(self):
        path = self.transcript([self.user_record('模式一')])
        self.store.sync(self.session, path, [])
        self.store.finish(self.session, 'done', [])
        state = self.store.sync(self.session, path, [])
        self.assertIsNone(state['mode'])
        self.assertEqual(state['last_mode'], '1')

    def test_sync_detects_new_explicit_switch_and_preserves_worker_handoff(self):
        with patch.object(modes,'now',return_value='2026-01-01T00:00:01.000Z'):
            self.store.submitted(self.session,'模式一',[])
        path=self.transcript([self.user_record('模式二','2026-01-01T00:00:02.000Z')])
        state=self.store.sync(self.session,path,[{'id':'running'}])
        self.assertEqual(state['mode'],'1')
        self.assertEqual(state['pending_mode'],'2')
        self.assertEqual(self.store.sync(self.session,path,[{'id':'running'}])['pending_mode'],'2')

    def test_session_start_marker_does_not_hide_unrecorded_choice(self):
        self.store.hook_seen(self.session)
        path=self.transcript([self.user_record('模式二')])
        self.assertEqual(self.store.sync(self.session,path,[])['mode'],'2')

    def test_just_selecting_mode_does_not_consume_it(self):
        self.hook('UserPromptSubmit',prompt='2')
        self.assertEqual(self.hook('Stop',last_assistant_message='已选择模式二，请说任务'),{})
        self.assertEqual(self.store.get(self.session)['mode'],'2')

    def test_desktop_metadata_can_close_turn_after_mode_finish(self):
        self.store.submitted(self.session,'模式二',[])
        self.store.begin(self.session,self.project,'work')
        self.store.finish(self.session,'verified',[])
        for tool in ['mcp__ccd_turn__wrap_up','mcp__ccd_turn__update_plan']:
            with self.subTest(tool=tool):
                self.assertEqual(self.hook('PreToolUse',tool_name=tool,tool_input={'recap':'done'}),{})
                state=self.store.get(self.session)
                self.assertIsNone(state['mode'])
                self.assertEqual(state['phase'],'awaiting_choice')
        for tool in ['mcp__ccd_turn__execute','mcp__ccd_turn__wrap_up_extra','mcp__other__wrap_up']:
            with self.subTest(tool=tool):
                self.assertEqual(self.hook('PreToolUse',tool_name=tool,tool_input={}),{})

    def test_desktop_metadata_does_not_begin_ready_task(self):
        self.store.submitted(self.session,'模式二',[])
        for tool in ['mcp__ccd_turn__wrap_up','mcp__ccd_turn__update_plan']:
            self.assertEqual(self.hook('PreToolUse',tool_name=tool,tool_input={}),{})
            self.assertEqual(self.store.get(self.session)['phase'],'ready')

    def test_stop_accepts_desktop_numeric_and_abbreviated_mode_reminders(self):
        self.store.submitted(self.session,'模式二',[])
        self.store.finish(self.session,'verified',[])
        for message in [
            '下一项任务可选 1（Claude＋Codex）或 2（仅 Claude）；不选就沿用本次模式。',
            '下一项任务可选 **1（Claude + Codex）** 或 **2（仅 Claude）**；不选就沿用本次模式。',
            '下一项可选模式一或二，不选就沿用本次模式。',
            '下一项可选模式1或2，不选就沿用本次模式。',
        ]:
            with self.subTest(message=message):
                self.assertTrue(modes.has_menu(message))
                self.assertEqual(self.hook('Stop',last_assistant_message=message),{})
                self.assertNotIn('reminder_sent',self.store.get(self.session))
        for message in ['完成了 1 个 Claude 测试和 2 个 Codex 测试','下一项可选 1 个或 2 个文件']:
            self.assertFalse(modes.has_menu(message))

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
