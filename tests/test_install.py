import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
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
                        patch.object(install, 'BIN', self.bin)]
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
        result = subprocess.run([str(self.bin), '--version'], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), '0.2.2')
        self.assertNotIn('@CCL@', (folder / 'skills/codex-loop/SKILL.md').read_text())

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


if __name__ == '__main__':
    unittest.main()
