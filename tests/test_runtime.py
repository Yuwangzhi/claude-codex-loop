import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import ccl
import runtime


class RuntimeTests(unittest.TestCase):
    def test_pid_probe_never_terminates_process(self):
        process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            self.assertTrue(runtime.pid_alive(process.pid))
            self.assertIsNone(process.poll())
        finally:
            process.terminate()
            process.wait(timeout=10)
        self.assertFalse(runtime.pid_alive(process.pid))

    def test_lock_excludes_other_process_and_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'lock file'
            code = ('import runtime,sys\n'
                    'try:\n'
                    ' with runtime.file_lock(sys.argv[1], blocking=False): pass\n'
                    'except BlockingIOError: sys.exit(7)\n')
            with runtime.file_lock(path):
                result = subprocess.run([sys.executable, '-c', code, str(path)])
                self.assertEqual(result.returncode, 7)
            result = subprocess.run([sys.executable, '-c', code, str(path)])
            self.assertEqual(result.returncode, 0)

    def test_timeout_stops_descendant_before_it_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root/'unexpected.txt'
            child = 'import time,pathlib; time.sleep(2); pathlib.Path(' + repr(str(marker)) + ').write_text("alive")'
            code = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",' + repr(child) + ']); time.sleep(30)'
            with self.assertRaises(subprocess.TimeoutExpired):
                ccl.process([sys.executable, '-c', code], root, None, root/'out', root/'err', .7)
            time.sleep(2)
            self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
