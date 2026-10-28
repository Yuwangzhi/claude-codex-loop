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

import install


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / 'home with spaces'
        self.home.mkdir()
        self.prefix = self.home / '.local/share/claude-codex-loop'
        self.bin = self.home / '.local/bin/ccl'
        self.patches = [patch.object(install, 'HOME', self.home),
                        patch.object(install, 'PREFIX', self.prefix),
                        patch.object(install, 'BIN', self.bin),
                        patch.object(install, 'configure_windows_path')]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_install_twice_preserves_settings_and_existing_hooks(self):
        folder = self.home / '.claude'
        folder.mkdir()
        existing = {'hooks': [{'type': 'command', 'command': 'existing-monitor', 'timeout': 2}]}
        settings = {'permissions': {'defaultMode': 'default'}, 'custom': {'keep': True},
                    'hooks': {'Stop': [existing]}}
        (folder / 'settings.json').write_text(json.dumps(settings))
        (folder / 'CLAUDE.md').write_text('Keep existing guidance.\n')
        with contextlib.redirect_stdout(io.StringIO()):
            install.main()
            install.main()
        actual = json.loads((folder / 'settings.json').read_text())
        self.assertEqual(actual['permissions'], settings['permissions'])
        self.assertEqual(actual['custom'], settings['custom'])
        self.assertIn(existing, actual['hooks']['Stop'])
        for event in ['SessionStart', 'UserPromptSubmit', 'PreToolUse', 'Stop', 'PostToolUse']:
            managed = [h for group in actual['hooks'][event] for h in group['hooks']
                       if h['command'].endswith(' hook')]
            self.assertEqual(len(managed), 1)
        self.assertTrue((folder / 'CLAUDE.md').read_text().startswith('Keep existing guidance.'))
        self.assertEqual((self.prefix / 'ccl.py').read_bytes(), (install.SOURCE / 'ccl.py').read_bytes())
        command = ['cmd', '/d', '/c', str(self.bin.with_suffix('.cmd')), '--version'] if os.name == 'nt' else [str(self.bin), '--version']
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), '0.3.2')
        self.assertNotIn('@CCL@', (folder / 'skills/codex-loop/SKILL.md').read_text())
        self.assertNotIn(b'\r\n', self.bin.read_bytes())

    def test_failed_install_restores_previous_files(self):
        folder = self.home / '.claude'
        folder.mkdir()
        original = '<!-- BEGIN claude-codex-loop -->\nmissing end marker\n'
        (folder / 'CLAUDE.md').write_text(original)
        (folder / 'settings.json').write_text('{}\n')
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
            install.main()
        self.assertEqual((folder / 'CLAUDE.md').read_text(), original)
        self.assertEqual((folder / 'settings.json').read_text(), '{}\n')
        self.assertFalse(self.bin.exists())
        self.assertFalse((self.prefix / 'ccl.py').exists())

    def test_module_migration_reinstall_and_command_restore_preserve_other_settings(self):
        folder = self.home / '.claude'
        folder.mkdir()
        other_plugin = str(self.home / 'other plugin')
        monitor = {'type': 'command', 'command': 'existing-monitor', 'timeout': 2}
        unrelated_group = {'matcher': 'Write', 'hooks': [monitor]}
        settings = {'permissions': {'defaultMode': 'default'},
                    'env': {'KEEP_THIS': 'preserved', 'CLAUDE_CODE_PLUGIN_DIRS': other_plugin},
                    'hooks': {'PreToolUse': [unrelated_group], 'Notification': [unrelated_group]}}
        path = folder / 'settings.json'
        path.write_text(json.dumps(settings), encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()):
            install.main('command')
        command_settings = json.loads(path.read_text())
        # CCL and unrelated hooks can share a matcher group in an existing install.
        managed = command_settings['hooks']['PreToolUse'].pop()
        command_settings['hooks']['PreToolUse'][0]['hooks'].extend(managed['hooks'])
        path.write_text(json.dumps(command_settings), encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()):
            install.main('module')
            install.main()
        actual = json.loads(path.read_text())
        plugin = self.prefix / 'desktop-plugin'
        self.assertEqual(install.plugin_paths(actual), [other_plugin, str(plugin)])
        self.assertEqual(actual['env']['KEEP_THIS'], 'preserved')
        self.assertEqual(actual['permissions'], settings['permissions'])
        self.assertEqual(actual['hooks']['PreToolUse'], [unrelated_group])
        self.assertEqual(actual['hooks']['Notification'], [unrelated_group])
        for event in install.EVENTS:
            hooks = [h for group in actual['hooks'].get(event, []) for h in group['hooks']]
            self.assertFalse(any(install.managed_command(h.get('command')) for h in hooks))
        for name in ['.claude-plugin/plugin.json', 'hooks/hooks.json', 'hooks/hooks.mjs']:
            self.assertEqual((plugin / name).read_bytes(), (install.SOURCE / 'desktop-plugin' / name).read_bytes())
        bridge = json.loads((plugin / 'bridge.json').read_text())
        self.assertEqual(bridge, {'python': str(Path(sys.executable)), 'ccl': str(self.prefix / 'ccl.py')})
        with contextlib.redirect_stdout(io.StringIO()):
            install.main('command')
            install.main()
        restored = json.loads(path.read_text())
        self.assertEqual(install.plugin_paths(restored), [other_plugin])
        self.assertEqual(restored['env']['KEEP_THIS'], 'preserved')
        self.assertEqual(restored['hooks']['Notification'], [unrelated_group])
        self.assertIn(unrelated_group, restored['hooks']['PreToolUse'])
        for event in install.EVENTS:
            hooks = [h for group in restored['hooks'][event] for h in group['hooks']]
            self.assertEqual(sum(install.managed_command(h.get('command')) for h in hooks), 1)

    def test_module_failure_after_settings_write_restores_existing_and_removes_new_files(self):
        folder = self.home / '.claude'
        folder.mkdir()
        plugin = self.prefix / 'desktop-plugin'
        previous_module = plugin / 'hooks/hooks.mjs'
        previous_module.parent.mkdir(parents=True)
        previous_module.write_text('// previous module\n', encoding='utf-8')
        previous_runtime = self.prefix / 'ccl.py'
        previous_runtime.write_text('# previous runtime\n', encoding='utf-8')
        settings_path = folder / 'settings.json'
        original_settings = json.dumps({'env': {'KEEP_THIS': 'preserved'},
                                        'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'monitor'}]}]}})
        settings_path.write_text(original_settings, encoding='utf-8')
        # Printing the final result happens after all configuration writes.
        with patch('builtins.print', side_effect=RuntimeError('simulate late install failure')):
            with self.assertRaisesRegex(RuntimeError, 'late install failure'):
                install.main('module')
        self.assertEqual(settings_path.read_text(), original_settings)
        self.assertEqual(previous_runtime.read_text(), '# previous runtime\n')
        self.assertEqual(previous_module.read_text(), '// previous module\n')
        for name in ['.claude-plugin/plugin.json', 'hooks/hooks.json', 'bridge.json']:
            self.assertFalse((plugin / name).exists())
        self.assertFalse((self.prefix / 'modes.py').exists())
        self.assertFalse((self.prefix / 'runtime.py').exists())
        self.assertFalse((self.prefix / 'diagnostics.py').exists())
        self.assertFalse(self.bin.exists())
        manifests = list((self.home / '.local/state/claude-codex-loop/backups').glob('*/manifest.json'))
        self.assertEqual(len(manifests), 1)
        backed_up = {item['path'] for item in json.loads(manifests[0].read_text())}
        self.assertIn(str(previous_module), backed_up)
        self.assertIn(str(plugin / 'bridge.json'), backed_up)

    def test_changed_python_replaces_old_command_and_preserves_unrelated_hooks(self):
        old = '"C:/Previous Python/python.exe" -X utf8 "' + (self.prefix / 'ccl.py').as_posix() + '" hook'
        other = '"C:/Previous Python/python.exe" -X utf8 "C:/other/ccl.py" hook'
        compound = old + '; echo keep-me'
        for transport in ('command', 'module'):
            settings = {'hooks': {'PreToolUse': [{'hooks': [
                {'type': 'command', 'command': cmd} for cmd in (old, other, compound)]}]}}
            install.configure_hooks(settings, transport)
            commands = [h['command'] for group in settings['hooks']['PreToolUse'] for h in group['hooks']]
            self.assertNotIn(old, commands)
            self.assertIn(other, commands)
            self.assertIn(compound, commands)
            self.assertEqual(sum(install.managed_command(cmd) for cmd in commands), 1 if transport == 'command' else 0)

    def test_new_diagnostics_is_installed_and_backed_up(self):
        with contextlib.redirect_stdout(io.StringIO()):
            install.main('module')
        self.assertEqual((self.prefix / 'diagnostics.py').read_bytes(), (install.SOURCE / 'diagnostics.py').read_bytes())
        manifests = list((self.home / '.local/state/claude-codex-loop/backups').glob('*/manifest.json'))
        self.assertIn(str(self.prefix / 'diagnostics.py'), {item['path'] for item in json.loads(manifests[0].read_text())})

    def test_forward_slash_module_path_is_preserved_on_reinstall(self):
        with contextlib.redirect_stdout(io.StringIO()):
            install.main('module')
        path = self.home / '.claude/settings.json'
        settings = json.loads(path.read_text())
        settings['env']['CLAUDE_CODE_PLUGIN_DIRS'] = (self.prefix / 'desktop-plugin').as_posix()
        path.write_text(json.dumps(settings))
        with contextlib.redirect_stdout(io.StringIO()):
            install.main()
        actual = json.loads(path.read_text())
        self.assertEqual(install.plugin_paths(actual), [str(self.prefix / 'desktop-plugin')])
        self.assertFalse(any(install.managed_command(h.get('command')) for groups in actual['hooks'].values()
                            for group in groups for h in group['hooks']))


if __name__ == '__main__':
    unittest.main()
