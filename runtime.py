"""Small standard-library platform layer for native Windows and POSIX."""
import contextlib
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time


def hook_argv(command, binary, program):
    """Recognize only standalone CCL launchers, including a former Python path.

    This is a matcher, never a shell evaluator. Preserve unrelated commands and
    compound commands rather than deleting hooks based on a substring.
    """
    if not isinstance(command, str):
        return None
    # Exact generated launchers also cover POSIX paths with quotes or literal $.
    for launcher in (str(binary), str(binary) + '.cmd'):
        if command in {launcher + ' hook', shlex.quote(launcher) + ' hook', '"' + launcher + '" hook'}:
            return [launcher, 'hook']
    if any(c in command for c in ('\n', '\r', '`', '$')):
        return None
    tokens = re.findall(r'''"[^"]*"|'[^']*'|[^\s"']+''', command)
    if ' '.join(tokens).split() != command.split():
        return None
    parts = [s[1:-1] if s[:1] in ('"', "'") else s for s in tokens]

    def same(left, right):
        return os.path.normcase(os.path.normpath(left)) == os.path.normcase(os.path.normpath(str(right)))

    if len(parts) == 2 and parts[1] == 'hook':
        return parts if same(parts[0], binary) or same(parts[0], str(binary) + '.cmd') else None
    if len(parts) != 5 or parts[1:3] != ['-X', 'utf8'] or parts[4] != 'hook':
        return None
    executable = parts[0].replace('\\', '/').rsplit('/', 1)[-1]
    return parts if re.fullmatch(r'python(?:\d+(?:\.\d+)*)?(?:\.exe)?', executable, re.I) and same(parts[3], program) else None


def managed_hook(command, binary, program):
    return hook_argv(command, binary, program) is not None


@contextlib.contextmanager
def file_lock(path, blocking=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            if os.fstat(stream.fileno()).st_size == 0:
                stream.write(b'\0')
                stream.flush()
            while True:
                stream.seek(0)
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as error:
                    if error.errno not in (13, 36):
                        raise
                    if not blocking:
                        raise BlockingIOError('File is locked') from error
                    time.sleep(.02)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            fcntl.flock(stream, flags)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


def pid_alive(pid):
    if os.name == 'nt':
        # os.kill(pid, 0) can terminate a process on Windows. Query it instead.
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.OpenProcess(0x1000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:  # invalid PID
                return False
            return True  # denied/unknown: fail closed rather than replay
        try:
            code = wintypes.DWORD()
            return not api.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def executable_argv(argv):
    argv = list(argv)
    first = Path(argv[0])
    if first.suffix.lower() == '.py':
        return [sys.executable, '-X', 'utf8'] + argv
    if os.name == 'nt':
        resolved = Path(shutil.which(argv[0]) or argv[0])
        if argv[0].lower() in ('codex', 'claude') and resolved.suffix.lower() not in ('.exe', '.cmd', '.bat'):
            resolved = Path(shutil.which(argv[0] + '.cmd') or resolved)
        if resolved.name.lower() in ('codex.cmd', 'codex.bat'):
            # Invoke the npm entry point without cmd.exe re-parsing JSON arguments.
            entry = resolved.parent / 'node_modules/@openai/codex/bin/codex.js'
            node = shutil.which('node')
            if entry.is_file() and node:
                return [node, str(entry)] + argv[1:]
            raise FileNotFoundError('Cannot resolve Codex npm entry point; set CCL_CODEX_BIN to codex.exe')
        if resolved.name.lower() in ('claude.cmd', 'claude.bat'):
            package = resolved.parent / 'node_modules/@anthropic-ai/claude-code'
            native = package / 'bin/claude.exe'
            if native.is_file():
                return [str(native)] + argv[1:]
            entry, node = package / 'cli.js', shutil.which('node')
            if entry.is_file() and node:
                return [node, str(entry)] + argv[1:]
            raise FileNotFoundError('Cannot resolve Claude npm entry point; install a working native Claude CLI')
    return argv


def process_options():
    return {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}


def capture_output(argv, timeout=8):
    """Read-only CLI probe with process-tree cleanup, unlike subprocess.run timeout."""
    p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         encoding='utf-8', errors='replace', **process_options())
    try:
        out, err = p.communicate(timeout=timeout)
        return subprocess.CompletedProcess(argv, p.returncode, out, err)
    except BaseException:
        terminate_tree(p)
        try:
            p.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            pass  # Do not wait forever on a descendant's inherited pipe.
        raise
    finally:
        for name, thread_name in (('stdout', 'stdout_thread'), ('stderr', 'stderr_thread')):
            thread = getattr(p, thread_name, None)
            # Closing a stream while Windows' reader owns its lock can block.
            if thread is None or not thread.is_alive():
                getattr(p, name).close()
        if os.name == 'nt' and p.poll() is not None:
            p._handle.Close()


def terminate_tree(process):
    if os.name == 'nt':
        if process.poll() is None:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
