#!/usr/bin/env python3
"""Claude plans/reviews; Codex executes. Local, standard-library-only bridge."""
import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import uuid
import modes
import runtime
import diagnostics

VERSION = "0.3.2"
STATE = Path(os.environ.get("CCL_STATE_DIR", str(Path.home() / ".local/state/claude-codex-loop"))).expanduser()
CONFIG = Path(os.environ.get("CCL_CONFIG_DIR", str(Path.home() / ".config/claude-codex-loop"))).expanduser()
TERMINAL = {"accepted", "cancelled"}
RESULT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "outcome": {"type": "string", "enum": ["ready", "blocked"]},
        "summary": {"type": "string"},
        **{k: {"type": "array", "items": {"type": "string"}} for k in
           ["artifacts", "verification", "memory_proposals", "blockers"]}},
    "required": ["outcome", "summary", "artifacts", "verification", "memory_proposals", "blockers"]}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def dump(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".ccl-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def project_dir(project):
    project = Path(project).expanduser().resolve(strict=True)
    if not project.is_dir():
        raise ValueError("Project must be a directory")
    return project


def bucket(project):
    key = hashlib.sha256(str(project).encode()).hexdigest()[:20]
    return STATE / "projects" / key


def read_task(path):
    task = read_json(path)
    if (not isinstance(task, dict) or task.get("id") != path.parent.name or
            not isinstance(task.get("project"), str) or not Path(task["project"]).is_absolute() or
            not isinstance(task.get("state"), str) or not isinstance(task.get("spec"), dict) or
            not isinstance(task["spec"].get("title"), str) or
            type(task.get("attempts")) is not int or type(task.get("max_attempts")) is not int or
            not isinstance(task.get("updated_at", ""), str) or
            not isinstance(task.get("owner_session", ""), str) or
            not isinstance(task.get("verification_records", ""), str) or
            not isinstance(task.get("history", []), list) or
            not all(isinstance(item, dict) for item in task.get("history", [])) or
            bucket(Path(task["project"])) != path.parents[2]):
        raise ValueError("Invalid task record structure or project identity: " + str(path))
    if "result" in task:
        validate_result(task["result"])
    if "verification" in task:
        value = task["verification"]
        if (not isinstance(value, dict) or not isinstance(value.get("artifact_hashes"), dict) or
                not isinstance(value.get("git"), dict) or not isinstance(value.get("checks"), list)):
            raise ValueError("Invalid verification record: " + str(path))
    return task


def task_path(task_id):
    if not re.fullmatch(r"[0-9a-f]{12}", task_id):
        raise ValueError("Invalid task id")
    matches = list((STATE / "projects").glob("*/tasks/" + task_id))
    if len(matches) != 1:
        raise ValueError("Task not found or ambiguous: " + task_id)
    return matches[0]


def save_task(path, task):
    task["updated_at"] = now()
    atomic(path / "task.json", dump(task) + "\n")


@contextlib.contextmanager
def lock(project):
    root = bucket(project)
    root.mkdir(parents=True, exist_ok=True)
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(runtime.file_lock(root / "project.lock", blocking=False))
        except BlockingIOError:
            raise ValueError("Another CCL operation is active in this project; do not duplicate it")
        yield


def relative_file(project, name):
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        raise ValueError("Artifact paths must be nonempty project-relative paths")
    result = (project / name).resolve()
    try:
        result.relative_to(project)
    except ValueError:
        raise ValueError("Artifact escapes project: " + name)
    return result


def assert_no_orphan(project):
    for task in tasks_for(project):
        pid = task.get("worker_pid")
        if pid and runtime.pid_alive(pid):
            raise ValueError("A recorded worker may still be running (PID %s, task %s). Inspect it before retrying; never duplicate side effects." % (pid, task["id"]))


def file_hash(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def git_snapshot(project):
    base = project
    def git(*args, required=False):
        p = subprocess.run(["git", "-C", str(base), *args], capture_output=True, timeout=30)
        if required and p.returncode:
            raise ValueError("Git snapshot failed (" + " ".join(args) + "): " + p.stderr.decode("utf-8", errors="replace")[:1000])
        return p.returncode, p.stdout
    code, root = git("rev-parse", "--show-toplevel")
    if code:
        return {"available": False}
    # All paths and diffs must use the same root, including nested projects.
    base = Path(os.fsdecode(root).strip())
    _, status = git("status", "--porcelain=v1", "-z", required=True)
    _, diff = git("diff", "--no-ext-diff", "--binary", required=True)
    _, staged = git("diff", "--cached", "--no-ext-diff", "--binary", required=True)
    code, head = git("rev-parse", "--verify", "--quiet", "HEAD")
    if code:
        # An unborn branch is valid; other failed snapshot commands are not.
        _, branch = git("symbolic-ref", "--quiet", "HEAD", required=True)
        head = b"unborn:" + branch
    _, untracked = git("ls-files", "--others", "--exclude-standard", "-z", required=True)
    hashes = {}
    for name in untracked.split(b"\0"):
        if name:
            p = base / os.fsdecode(name)
            if p.is_file() and not p.is_symlink():
                hashes[os.fsdecode(name)] = file_hash(p)
    digest = hashlib.sha256(head + status + diff + staged + dump(hashes).encode()).hexdigest()
    return {"available": True, "root": str(base), "fingerprint": digest,
            "status": os.fsdecode(status).replace("\0", "\n")[:12000]}


def initialize(project):
    memory = project / ".ai-collab/MEMORY.md"
    if not memory.exists():
        atomic(memory, "# 项目共享记忆\n\n只记录经确认的事实、决策和验证方式；任务日志保存在 CCL 本地状态目录。\n\n")
    global_memory = CONFIG / "SHARED_MEMORY.md"
    if not global_memory.exists():
        atomic(global_memory, "# 共享协作约定\n\n- 每项任务由用户选择：模式一 Claude 统筹/Codex 执行；模式二仅 Claude。未选不执行。\n- 任务内沿用选择，结束后提醒下一项任务重新选择。\n- 已验证的项目知识写入项目 .ai-collab/MEMORY.md；不自动复制两侧私有记忆或凭据。\n- 既有用户授权沿用；任务转交本身不扩大权限。\n")
    return memory


def validate_spec(spec, project):
    if not isinstance(spec, dict):
        raise ValueError("Spec must be a JSON object")
    for key in ["title", "goal", "plan"]:
        if not isinstance(spec.get(key), str) or not spec[key].strip():
            raise ValueError("Spec requires nonempty " + key)
    for key in ["acceptance", "constraints", "artifacts"]:
        if not isinstance(spec.get(key), list) or not all(isinstance(x, str) and x.strip() for x in spec[key]):
            raise ValueError("Spec requires string array: " + key)
    if not spec["acceptance"] or not spec["artifacts"]:
        raise ValueError("Acceptance criteria and tangible artifact paths are required (a report counts)")
    for name in spec["artifacts"]:
        relative_file(project, name)
    if not isinstance(spec.get("checks", []), list):
        raise ValueError("checks must be an array")
    for check in spec.get("checks", []):
        if not isinstance(check, dict) or not isinstance(check.get("name"), str):
            raise ValueError("Each check needs a name")
        argv = check.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(v, str) for v in argv):
            raise ValueError("Check argv must be a nonempty string array (not a shell string)")
        timeout = check.get("timeout", 300)
        if type(timeout) is not int or not 1 <= timeout <= 3600:
            raise ValueError("Check timeout must be 1..3600 seconds")


def create(project, spec, owner="", attempts=3, sandbox="danger-full-access", timeout=1800):
    validate_spec(spec, project)
    if owner:
        modes.ModeStore(STATE).require(owner, "1")
    if not 1 <= attempts <= 5 or not 10 <= timeout <= 14400:
        raise ValueError("Attempts must be 1..5 and timeout 10..14400 seconds")
    with lock(project):
        assert_no_orphan(project)
        initialize(project)
        task_id = uuid.uuid4().hex[:12]
        path = bucket(project) / "tasks" / task_id
        task = {"id": task_id, "project": str(project), "spec": spec, "owner_session": owner,
                "state": "planned", "attempts": 0, "max_attempts": attempts, "timeout": timeout,
                "sandbox": sandbox, "thread_id": None, "created_at": now(), "feedback": "",
                "history": [], "baseline": git_snapshot(project)}
        save_task(path, task)
        if owner:
            modes.ModeStore(STATE).begin(owner, project, spec["title"])
        return task


def process(argv, cwd, stdin, stdout, stderr, timeout, env=None, on_start=None):
    with stdout.open("wb") as out, stderr.open("wb") as err:
        p = subprocess.Popen(runtime.executable_argv(argv), cwd=str(cwd), stdin=subprocess.PIPE,
                             stdout=out, stderr=err, env=env, **runtime.process_options())
        try:
            if on_start:
                on_start(p.pid)
            p.communicate(stdin.encode() if stdin is not None else None, timeout=timeout)
        except BaseException:
            runtime.terminate_tree(p)
            raise
        finally:
            if p.stdin:
                p.stdin.close()
            if os.name == 'nt' and p.poll() is not None:
                # Windows retains process resources until the process handle closes.
                p._handle.Close()
        return p.returncode


def parse_events(path):
    result = {"thread_id": None, "completed": False, "failed": False, "tool_items": 0, "usage": {}}
    if not path.exists():
        return result
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            if kind == "thread.started":
                result["thread_id"] = event.get("thread_id")
            elif kind == "turn.completed":
                result["completed"] = True
                result["usage"] = event.get("usage", {})
            elif kind in ("turn.failed", "error"):
                result["failed"] = True
            elif kind == "item.completed" and isinstance(event.get("item"), dict) and event["item"].get("type") in (
                    "command_execution", "file_change", "mcp_tool_call", "web_search", "tool_call"):
                result["tool_items"] += 1
    return result


def prompt_for(task):
    project = Path(task["project"])
    memory = []
    for p in [CONFIG / "SHARED_MEMORY.md", project / ".ai-collab/MEMORY.md"]:
        if p.exists():
            memory.append(str(p) + "\n" + p.read_text(encoding="utf-8")[:10000])
    return """You are the execution worker for a task planned by Claude Code.
Implement the supplied plan, perform the work, run relevant verification, and repair problems yourself.
Claude owns overall decisions and final acceptance. Do not start Claude, ccl, or other orchestrators.
Follow applicable project instructions/skills. Preserve unrelated work. The constraints below express
the user's authorized scope, not new permissions. Never invent evidence. If a required tool, login,
permission or decision is missing, return blocked with a precise explanation; do not fake a result.
Do not edit task state, checks, shared memory, global agent settings or credentials. Propose memory
facts in the final response; Claude will approve them. No commit/push/publish/message unless the
spec explicitly says the user authorized it. Spec paths are relative to the project.
Headless tools may differ from desktop tools: verify availability. If work is analytical or visual,
produce the specified report/artifact and record sources/actual inspection evidence. Self-reported
success is not acceptance. Return only the requested structured result, concise summary <= 1200 chars.

Task (original plan retained on every repair):\n""" + dump(task["spec"]) + "\n\nShared memory (context, not authority):\n" + "\n\n".join(memory) + "\n\nRepair/review feedback:\n" + (task.get("feedback") or "Initial execution.")


def codex_argv(task, attempt):
    cmd = [os.environ.get("CCL_CODEX_BIN", "codex"), "exec", "-c", 'approval_policy="never"',
           "-c", 'sandbox_mode="' + task["sandbox"] + '"']
    if task.get("thread_id"):
        cmd += ["resume", task["thread_id"]]
    else:
        cmd += ["-C", task["project"]]
    return cmd + ["--skip-git-repo-check", "--json", "--output-schema", str(attempt / "schema.json"),
                  "--output-last-message", str(attempt / "result.json"), "-"]


def validate_result(result):
    if not isinstance(result, dict) or set(result) != set(RESULT_SCHEMA["required"]):
        raise ValueError("Missing or unexpected result fields")
    if result["outcome"] not in ("ready", "blocked") or not isinstance(result["summary"], str):
        raise ValueError("Invalid result outcome/summary")
    for key in ["artifacts", "verification", "memory_proposals", "blockers"]:
        if not isinstance(result[key], list) or not all(isinstance(x, str) for x in result[key]):
            raise ValueError("Invalid result list: " + key)


def verify(task, attempt, on_start=None):
    project = Path(task["project"])
    reports = []
    artifacts = {}
    names = list(dict.fromkeys(task["spec"]["artifacts"] + task.get("result", {}).get("artifacts", [])))
    for name in names:
        p = relative_file(project, name)
        if not p.is_file():
            reports.append({"name": "artifact " + name, "passed": False, "detail": "Missing file"})
        else:
            artifacts[name] = file_hash(p)
    for index, check in enumerate(task["spec"].get("checks", [])):
        out, err = attempt / ("check-%d.out" % index), attempt / ("check-%d.err" % index)
        failure_kind = None
        try:
            rc = process(check["argv"], project, None, out, err, check.get("timeout", 300), on_start=on_start)
            detail = out.read_text(encoding="utf-8", errors="replace")[-3000:] + err.read_text(encoding="utf-8", errors="replace")[-3000:]
        except subprocess.TimeoutExpired as e:
            rc, detail, failure_kind = -1, str(e), "timeout"
        except OSError as e:
            rc, detail = -1, str(e)
            failure_kind = "environment"
        reports.append({"name": check["name"], "passed": rc == 0, "exit_code": rc,
                        "detail": detail, "stdout": str(out), "stderr": str(err),
                        "failure_kind": failure_kind or ("check_failed" if rc else None)})
        if failure_kind:
            break  # Do not spend worker quota or execute later checks blindly.
    # Capture hashes AFTER checks, since builds/formatters can alter artifacts.
    for name in list(artifacts):
        p = relative_file(project, name)
        if p.is_file():
            artifacts[name] = file_hash(p)
        else:
            reports.append({"name": "artifact " + name, "passed": False, "detail": "Removed during checks"})
    result = {"passed": all(r["passed"] for r in reports), "checks": reports,
              "blocking_failure": any(r.get("failure_kind") in ("environment", "timeout") for r in reports),
              "artifact_hashes": artifacts, "git": git_snapshot(project),
              "automated_check_count": len(task["spec"].get("checks", [])), "verified_at": now()}
    atomic(attempt / "verification.json", dump(result))
    return result


def run_task(task_id):
    path = task_path(task_id)
    initial = read_task(path / "task.json")
    project = Path(initial["project"])
    if initial.get("owner_session"):
        modes.ModeStore(STATE).require(initial["owner_session"], "1")
    with lock(project):
        assert_no_orphan(project)
        task = read_task(path / "task.json")
        if task.get("recovery_required"):
            raise ValueError("Inspect interrupted task evidence, then use review revise with recovery instructions")
        if task["state"] in ("running", "verifying"):
            task["state"] = "interrupted"
            task["recovery_required"] = True
            task["feedback"] = "Runner ended without a persisted outcome. Inspect existing result/logs/artifacts, then use review revise with explicit recovery instructions before running again."
            task.pop("worker_pid", None)
            if task["attempts"]:
                prior = path / ("attempt-%02d" % task["attempts"]) / "events.jsonl"
                task["thread_id"] = parse_events(prior)["thread_id"] or task.get("thread_id")
            save_task(path, task)
            return task
        if task["state"] in TERMINAL or task["state"] == "awaiting_review":
            raise ValueError("Task is " + task["state"] + "; review it or request a revision")
        if task["attempts"] >= task["max_attempts"]:
            raise ValueError("Repair budget exhausted; Claude must revise the plan, not loop blindly")
        while task["attempts"] < task["max_attempts"]:
            if task.get("owner_session"):
                try:
                    modes.ModeStore(STATE).require(task["owner_session"], "1")
                except ValueError:
                    task["state"] = "interrupted"
                    task["feedback"] = "User requested a mode change; no further Codex attempt was started."
                    save_task(path, task)
                    return task
            task["attempts"] += 1
            # A failed new attempt must not expose the previous candidate as current.
            task.pop("result", None)
            task.pop("verification", None)
            task.pop("verification_records", None)
            attempt = path / ("attempt-%02d" % task["attempts"])
            attempt.mkdir()
            atomic(attempt / "schema.json", dump(RESULT_SCHEMA))
            prompt = prompt_for(task)
            atomic(attempt / "prompt.txt", prompt)
            task["state"] = "running"
            task["runner_pid"] = os.getpid()
            save_task(path, task)
            print(dump({"id": task_id, "state": "running", "attempt": task["attempts"]}), flush=True)
            interrupted = False
            def record_worker(pid):
                task["worker_pid"] = pid
                save_task(path, task)
            try:
                env = dict(os.environ, CCL_WORKER="1")
                rc = process(codex_argv(task, attempt), project, prompt, attempt / "events.jsonl",
                             attempt / "stderr.log", task["timeout"], env, record_worker)
            except (subprocess.TimeoutExpired, OSError, KeyboardInterrupt, SystemExit) as e:
                rc = -1
                task["feedback"] = "Execution interrupted/failed: " + type(e).__name__ + ": " + str(e)
                interrupted = True
            events = parse_events(attempt / "events.jsonl")
            if events["thread_id"]:
                task["thread_id"] = events["thread_id"]
            task["history"].append({"attempt": task["attempts"], "exit_code": rc, **events})
            task.pop("runner_pid", None)
            task.pop("worker_pid", None)
            if rc != 0 or not events["completed"] or events["failed"]:
                task["state"] = "interrupted" if interrupted else "execution_failed"
                task["feedback"] = task.get("feedback") if interrupted else "Codex process/turn failed. Inspect " + str(attempt / "stderr.log")
                save_task(path, task)
                return task  # Never silently spend quota retrying infrastructure/auth failures.
            try:
                result = read_json(attempt / "result.json")
                validate_result(result)
            except (ValueError, OSError) as e:
                task["state"] = "execution_failed"
                task["feedback"] = "Invalid worker result: " + str(e)
                save_task(path, task)
                return task
            task["result"] = result
            task["result_attempt"] = task["attempts"]
            if result["outcome"] == "blocked" or result["blockers"]:
                task["state"] = "blocked"
                task["feedback"] = result["summary"] + "\n" + "\n".join(result["blockers"])
                save_task(path, task)
                return task
            task["state"] = "verifying"
            save_task(path, task)
            try:
                verification = verify(task, attempt, record_worker)
            except (KeyboardInterrupt, SystemExit):
                task["state"] = "interrupted"
                task.pop("worker_pid", None)
                save_task(path, task)
                raise
            except (ValueError, OSError, subprocess.TimeoutExpired) as e:
                task["state"] = "blocked"
                task["feedback"] = "Verification could not complete: " + str(e)
                task.pop("worker_pid", None)
                save_task(path, task)
                return task
            task.pop("worker_pid", None)
            task["verification"] = verification
            if verification["blocking_failure"]:
                task["state"] = "blocked"
                task["feedback"] = "Verification environment/timeout failure; no automatic worker retry. Inspect checks, fix the cause, then use ccl recover TASK --reverify --note 'evidence':\n" + dump(verification["checks"])
                save_task(path, task)
                return task
            if verification["passed"]:
                task["state"] = "awaiting_review"
                task["feedback"] = "Automated checks/artifact existence passed. Claude must inspect deliverables and accept."
                save_task(path, task)
                return task
            task["state"] = "needs_revision"
            task["feedback"] = "Independent acceptance checks failed. Fix implementation without weakening checks:\n" + dump(verification["checks"])
            save_task(path, task)
        task["state"] = "budget_exhausted"
        save_task(path, task)
        return task


def review(task_id, verdict, note):
    if not note.strip():
        raise ValueError("A concrete review note is required")
    path = task_path(task_id)
    project = Path(read_task(path / "task.json")["project"])
    with lock(project):
        assert_no_orphan(project)
        task = read_task(path / "task.json")
        if task["state"] in TERMINAL:
            raise ValueError("Task already closed")
        if verdict == "accept":
            if task["state"] != "awaiting_review" or not task.get("verification", {}).get("passed"):
                raise ValueError("Only a verified candidate can be accepted")
            for name, expected in task["verification"]["artifact_hashes"].items():
                p = relative_file(project, name)
                if not p.is_file() or file_hash(p) != expected:
                    raise ValueError("Artifact changed since checks; request revision/reverification: " + name)
            previous = task["verification"]["git"]
            if previous != git_snapshot(project):
                raise ValueError("Git workspace changed since checks; request revision/reverification")
            task["state"] = "accepted"
            task["feedback"] = "Accepted after orchestrator review: " + note
        elif verdict == "revise":
            if task["attempts"] >= task["max_attempts"]:
                raise ValueError("Budget exhausted; revise the plan and create a new task with prior evidence")
            task["state"] = "needs_revision"
            task.pop("recovery_required", None)
            task["feedback"] = "Claude review requires revision:\n" + note
        else:
            task["state"] = "cancelled"
            task["feedback"] = "Cancelled: " + note
        task.setdefault("reviews", []).append({"verdict": verdict, "note": note, "at": now()})
        save_task(path, task)
        return task


def remember(task_id, text, evidence):
    if not text.strip() or len(text) > 2000 or not evidence.strip():
        raise ValueError("Memory requires <=2000 chars of confirmed knowledge and evidence")
    path = task_path(task_id)
    project = Path(read_task(path / "task.json")["project"])
    with lock(project):
        task = read_task(path / "task.json")
        if task["state"] != "accepted":
            raise ValueError("Only accepted tasks may contribute confirmed memory")
        memory = initialize(project)
        token = hashlib.sha256((task_id + text).encode()).hexdigest()[:16]
        old = memory.read_text(encoding="utf-8")
        if "<!-- " + token + " -->" not in old:
            entry = "\n<!-- " + token + " -->\n### " + now() + " · " + task_id + "\n\n" + text.strip() + "\n\n验证依据：" + evidence.strip() + "\n"
            atomic(memory, old + entry)
        return {"memory": str(memory), "task": task_id}


def scan_tasks(project=None):
    """Read records independently; never delete, rewrite or silently repair them."""
    root = bucket(project) / "tasks" if project is not None else STATE / "projects"
    paths = root.glob("*/task.json" if project is not None else "*/tasks/*/task.json")
    found, issues = [], []
    for path in paths:
        try:
            task = read_task(path)
            found.append(task)
        except (ValueError, OSError) as e:
            issues.append({"path": str(path), "project_bucket": path.parents[2].name,
                           "error": type(e).__name__ + ": " + str(e)})
    return sorted(found, key=lambda t: t.get("updated_at", ""), reverse=True), issues


def tasks_for(project):
    tasks, issues = scan_tasks(project)
    if issues:
        raise ValueError("Damaged task record in this project; inspect ccl doctor before replay: " + issues[0]["path"])
    return tasks


def owned_tasks(session, project=None):
    found, issues = scan_tasks()
    # An unreadable unrelated project must not disable every session. The active
    # project remains fail-closed: its unknown worker must not be duplicated.
    saved = modes.ModeStore(STATE).get(session).get("project")
    protected = {bucket(Path(p)).name for p in (project, saved) if p}
    affected = [issue for issue in issues if issue["project_bucket"] in protected]
    if affected:
        raise ValueError("Damaged task record in this session's project; inspect ccl doctor: " + affected[0]["path"])
    return [task for task in found if task.get("owner_session") == session and task["state"] not in TERMINAL]


def adopt(task_id, session):
    modes.ModeStore(STATE).require(session, "1")
    path = task_path(task_id)
    project = Path(read_task(path / "task.json")["project"])
    with lock(project):
        assert_no_orphan(project)
        task = read_task(path / "task.json")
        if task["state"] in TERMINAL:
            raise ValueError("Cannot adopt a closed task")
        task.setdefault("handoffs", []).append({"from": task.get("owner_session"), "to": session, "at": now()})
        task["owner_session"] = session
        save_task(path, task)
        modes.ModeStore(STATE).begin(session, project, task["spec"]["title"])
    return task


def native_memory(project, session, text, evidence):
    modes.ModeStore(STATE).require(session, "2")
    if not text.strip() or len(text) > 2000 or not evidence.strip():
        raise ValueError("Confirmed memory requires text <=2000 chars and concrete verification evidence")
    with lock(project):
        memory = initialize(project)
        token = hashlib.sha256((session + text).encode()).hexdigest()[:16]
        old = memory.read_text(encoding="utf-8")
        if "<!-- " + token + " -->" not in old:
            atomic(memory, old + "\n<!-- " + token + " -->\n### " + now() + " · Claude 模式二\n\n" + text + "\n\n验证依据：" + evidence + "\n")
    return {"memory": str(memory), "mode": "2", "review": "Claude verified; no Codex review implied"}


def summary(task):
    path = task_path(task["id"])
    return {k: task.get(k) for k in ["id", "project", "state", "attempts", "max_attempts", "thread_id", "feedback", "result", "verification"]} | {
        "records": str(path), "progress": diagnostics.progress(task, path)}


def candidate_available(task, path):
    """Only the latest successfully completed worker output can be rechecked."""
    try:
        attempt = path / ("attempt-%02d" % task["attempts"])
        result = read_json(attempt / "result.json")
        validate_result(result)
        events = parse_events(attempt / "events.jsonl")
        return (result == task.get("result") and result["outcome"] == "ready" and not result["blockers"]
                and events["completed"] and not events["failed"] and bool(task.get("history"))
                and task["history"][-1].get("attempt") == task["attempts"]
                and task["history"][-1].get("exit_code") == 0)
    except (ValueError, OSError, KeyError, TypeError):
        return False


def recovery(task_id):
    """Read-only advice; no mode edits, subprocess work, or automatic replay."""
    path = task_path(task_id)
    task = read_task(path / "task.json")
    output = summary(task)
    progress = output["progress"]
    candidate = candidate_available(task, path)
    if task["state"] in TERMINAL:
        action, advice = "closed", "任务已关闭，不要重复执行。"
    elif any(progress["processes"].values()):
        action, advice = "inspect_live_process", "有记录进程仍存活；先核对对应进程及日志，不启动第二个执行者。"
    elif task["state"] == "awaiting_review":
        action, advice = "review_candidate", "先阅读实际产物与验收记录；合格后 review accept，无需重新调用 Codex。"
    elif candidate:
        action, advice = "inspect_then_reverify", "已有完整候选结果。先检查现场并解决环境问题，可显式 --reverify 重跑原验收，不消耗 Codex 次数，也不自动接受。"
    elif task["attempts"] >= task["max_attempts"]:
        action, advice = "revise_plan", "执行预算已耗尽；保留证据并调整计划，不自动新建同一任务。"
    elif task["state"] == "planned":
        action, advice = "run_planned", "尚未执行；确认原目标、授权与当前模式后运行原任务。"
    else:
        action, advice = "inspect_then_revise", "先检查已有日志、文件及外部副作用，再 review revise 写明恢复依据，继续原任务；不要重复派发。"
    output["recovery"] = {"action": action, "advice": advice, "candidate_available": candidate,
                          "read_only": True, "reverify_command": "ccl recover " + task_id + " --reverify --note '检查与恢复依据'" if candidate else None}
    return output


def reverify(task_id, note):
    if not note.strip():
        raise ValueError("Reverification requires a concrete inspection/recovery note")
    path = task_path(task_id)
    project = Path(read_task(path / "task.json")["project"])
    with lock(project):
        assert_no_orphan(project)
        task = read_task(path / "task.json")
        if task["state"] in TERMINAL:
            raise ValueError("Task already closed")
        if task.get("owner_session"):
            modes.ModeStore(STATE).require(task["owner_session"], "1")
        if not candidate_available(task, path):
            raise ValueError("No completed current candidate to reverify; inspect then request revision")
        attempt = path / ("recheck-" + uuid.uuid4().hex[:12])
        attempt.mkdir()
        task.update(state="verifying", runner_pid=os.getpid(), verification_records=str(attempt))
        task.pop("verification", None)
        save_task(path, task)
        def record_worker(pid):
            task["worker_pid"] = pid
            save_task(path, task)
        try:
            verification = verify(task, attempt, record_worker)
            task["verification"] = verification
            task["state"] = ("awaiting_review" if verification["passed"] else "blocked" if verification["blocking_failure"]
                             else "needs_revision" if task["attempts"] < task["max_attempts"] else "budget_exhausted")
            task["feedback"] = "Reverification: " + note + "\n" + ("Checks passed; human review still required." if verification["passed"] else dump(verification["checks"]))
        except (ValueError, OSError, subprocess.TimeoutExpired) as e:
            task["state"], task["feedback"] = "blocked", "Reverification could not complete: " + str(e)
        except (KeyboardInterrupt, SystemExit):
            task["state"], task["feedback"] = "interrupted", "Reverification interrupted; inspect original candidate and check side effects."
            raise
        finally:
            task.pop("runner_pid", None)
            task.pop("worker_pid", None)
            task.pop("recovery_required", None)
            task.setdefault("rechecks", []).append({"at": now(), "note": note, "records": str(attempt), "state": task["state"]})
            save_task(path, task)
        return task


def find_transcript(session):
    modes.ModeStore(STATE).path(session)
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))).expanduser() / "projects"
    found = sorted(root.glob("*/" + session + ".jsonl"), key=lambda p: p.stat().st_mtime)
    if not found:
        raise ValueError("Transcript not found for session " + session + "; pass --transcript")
    return found[-1]


def hook(payload):
    project = project_dir(payload["cwd"])
    session = payload["session_id"]
    store = modes.ModeStore(STATE)
    event = payload.get("hook_event_name")
    # Keep the read-only repair path usable even if task records are damaged.
    if (event == "PreToolUse" and payload.get("tool_name") in ("Bash", "PowerShell") and
            modes.diagnostic_command(payload.get("tool_input", {}).get("command", ""))):
        return {}
    if payload.get("ccl_transport") == "module":
        # Store health metadata only, never prompts or tool arguments.
        with store.edit(session) as state:
            state["hook_transport"] = "module"
            state["hook_seen_at"] = modes.now()
            state.setdefault("hook_events", {})[event] = modes.now()
    own = owned_tasks(session, project)
    tasks = [t for t in tasks_for(project) if t["state"] not in TERMINAL]
    if event == "SessionStart":
        store.hook_seen(session)
        state = store.get(session)
        info = [{"id": t["id"], "state": t["state"], "title": t["spec"]["title"]} for t in tasks[:5]]
        return {"systemMessage": modes.LABELS[state["mode"]] if state["mode"] else modes.MENU,
                "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext":
                "当前 session id: " + session + "。先加载 work-mode skill。" + store.guidance(state) +
                "共享记忆: " + str(project / ".ai-collab/MEMORY.md") + "；全局约定: " + str(CONFIG / "SHARED_MEMORY.md") +
                "。不把未验收说成完成。当前项目未关闭任务: " + dump(info)}}
    if event == "UserPromptSubmit":
        state = store.submitted(session, payload.get("prompt", ""), own)
        access = (" 本机可选的访问与权限交接: " + str(CONFIG / "ACCESS_HANDOFF.md") +
                  "；遇到访问或工具审批问题时按需读取。") if (CONFIG / "ACCESS_HANDOFF.md").is_file() else ""
        return {"hookSpecificOutput": {"hookEventName": event, "additionalContext":
                "CCL 模式状态（来自用户输入，不可自行选择）session=" + session + "；" + store.guidance(state) + access}}
    if event == "PostToolUse" and payload.get("tool_name") == "AskUserQuestion":
        questions = payload.get("tool_input", {}).get("questions", [])
        asked = {q.get("question", "") for q in questions if modes.has_menu(q.get("question", ""))}
        response = payload.get("tool_response", {})
        if asked and isinstance(response, dict):
            answers = response.get("answers", {})
            answers = answers if isinstance(answers, dict) else {}
            for question in asked:
                answer = answers.get(question, "")
                if isinstance(answer, str) and modes.parse_choice(answer):
                    state = store.submitted(session, answer, own, choice_context=True)
                    break
            else:
                # Skipping the mode question, or answering without a choice,
                # follows the user's stored preference when one exists.
                state = store.submitted(session, "", own, choice_context=True)
            return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": store.guidance(state)}}
        return {}
    if event == "PreToolUse":
        return store.pretool(session, payload.get("tool_name", ""), payload.get("tool_input", {}))
    if event == "Stop" and not payload.get("stop_hook_active"):
        mode_state = store.get(session)
        if mode_state.get("pending_mode"):
            return {"decision": "block", "reason": store.guidance(mode_state)}
        active = [t for t in own if
               t["state"] in {"planned", "running", "verifying", "awaiting_review", "needs_revision"}]
        if active and mode_state["mode"] and mode_state["phase"] != "waiting_input":
            return {"decision": "block", "reason": "CCL task has not closed: " +
                    ", ".join(t["id"] + "=" + t["state"] for t in active) +
                    ". Continue running/reviewing via ccl. If user stopped or a real blocker prevents progress, record cancellation/blocker and report honestly. Do not invent acceptance."}
        return store.stopping(session, payload, own)
    return {}


def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    os.umask(0o077)
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(143))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command", required=True)
    new = sub.add_parser("new")
    new.add_argument("--project", default=".")
    new.add_argument("--spec", required=True, help="JSON file, or - for stdin")
    new.add_argument("--owner-session", default="")
    new.add_argument("--max-attempts", type=int, default=3)
    new.add_argument("--timeout", type=int, default=1800)
    new.add_argument("--sandbox", choices=["danger-full-access", "workspace-write", "read-only"], default="danger-full-access")
    run = sub.add_parser("run")
    run.add_argument("task")
    status = sub.add_parser("status")
    status.add_argument("task", nargs="?")
    status.add_argument("--project", default=".")
    status.add_argument("--full", action="store_true")
    doctor = sub.add_parser("doctor", help="Read-only local installation and hook diagnostics")
    doctor.add_argument("--project", default=".")
    doctor.add_argument("--session", default="", help="Inspect recorded hook evidence for this session")
    recover = sub.add_parser("recover", help="Read-only recovery advice; --reverify explicitly reruns checks only")
    recover.add_argument("task")
    recover.add_argument("--reverify", action="store_true")
    recover.add_argument("--note", default="")
    rev = sub.add_parser("review")
    rev.add_argument("task")
    rev.add_argument("verdict", choices=["accept", "revise", "cancel"])
    rev.add_argument("--note", required=True)
    mem = sub.add_parser("remember")
    mem.add_argument("task")
    mem.add_argument("--text", required=True)
    mem.add_argument("--evidence", required=True)
    ctx = sub.add_parser("context")
    ctx.add_argument("--project", default=".")
    sub.add_parser("hook")
    mode = sub.add_parser("mode")
    mode.add_argument("action", choices=["status", "begin", "wait", "finish", "activate", "sync"])
    mode.add_argument("--session", required=True)
    mode.add_argument("--project", default=".")
    mode.add_argument("--note", default="")
    mode.add_argument("--transcript", default="", help="sync only; default: locate session transcript under Claude projects")
    transfer = sub.add_parser("adopt")
    transfer.add_argument("task")
    transfer.add_argument("--session", required=True)
    note = sub.add_parser("note")
    note.add_argument("--session", required=True)
    note.add_argument("--project", default=".")
    note.add_argument("--text", required=True)
    note.add_argument("--evidence", required=True)
    args = parser.parse_args()
    try:
        if args.command == "doctor":
            _, issues = scan_tasks()
            output = diagnostics.doctor(VERSION, STATE, project_dir(args.project), issues, args.session)
            print(dump(output))
            return 0 if output["healthy"] else 2
        elif args.command == "recover":
            if args.reverify:
                task = reverify(args.task, args.note)
                print(dump(summary(task)))
                return 0 if task["state"] == "awaiting_review" else 2
            output = recovery(args.task)
        elif args.command == "mode":
            store = modes.ModeStore(STATE)
            if args.action == "status":
                output = store.get(args.session)
                output["hooks_seen"] = bool(output.get("hook_seen_at"))
            elif args.action == "sync":
                transcript = Path(args.transcript).expanduser() if args.transcript else find_transcript(args.session)
                output = store.sync(args.session, transcript, owned_tasks(args.session))
            elif args.action == "begin":
                output = store.begin(args.session, project_dir(args.project), args.note)
            elif args.action == "wait":
                output = store.wait(args.session, args.note)
            elif args.action == "activate":
                output = store.activate(args.session, owned_tasks(args.session))
            else:
                output = store.finish(args.session, args.note, owned_tasks(args.session))
            output = dict(output, guidance=store.guidance(output))
        elif args.command == "adopt":
            output = summary(adopt(args.task, args.session))
        elif args.command == "note":
            output = native_memory(project_dir(args.project), args.session, args.text, args.evidence)
        elif args.command == "new":
            spec = json.load(sys.stdin) if args.spec == "-" else read_json(Path(args.spec))
            task = create(project_dir(args.project), spec, args.owner_session, args.max_attempts, args.sandbox, args.timeout)
            output = {"id": task["id"], "state": task["state"], "records": str(task_path(task["id"]))}
        elif args.command == "run":
            task = run_task(args.task)
            print(dump(summary(task)))
            return 0 if task["state"] == "awaiting_review" else 2
        elif args.command == "status":
            if args.task:
                task = read_task(task_path(args.task) / "task.json")
                output = dict(task, progress=diagnostics.progress(task, task_path(task["id"]))) if args.full else summary(task)
            else:
                tasks, issues = scan_tasks(project_dir(args.project))
                listing = [{"id": t["id"], "title": t["spec"]["title"], "state": t["state"], "attempts": t["attempts"],
                            "progress": diagnostics.progress(t, task_path(t["id"]))} for t in tasks[:20]]
                output = {"tasks": listing, "record_issues": issues} if args.full else listing
                if issues and not args.full:
                    print(dump({"record_issues": issues, "advice": "Run ccl doctor; damaged records were not modified."}), file=sys.stderr)
        elif args.command == "review":
            output = summary(review(args.task, args.verdict, args.note))
        elif args.command == "remember":
            output = remember(args.task, args.text, args.evidence)
        elif args.command == "context":
            project = project_dir(args.project)
            output = {"project": str(project), "memory": {str(p): p.read_text(encoding="utf-8")[:12000] for p in
                     [CONFIG / "SHARED_MEMORY.md", project / ".ai-collab/MEMORY.md"] if p.exists()},
                     "pending": [{"id": t["id"], "state": t["state"]} for t in tasks_for(project) if t["state"] not in TERMINAL][:10]}
        else:
            payload = json.load(sys.stdin)
            output = hook(payload)
        print(dump(output))
        return 0
    except (ValueError, OSError, KeyError) as e:
        if args.command == "hook":
            if 'payload' in locals() and payload.get("hook_event_name") == "PreToolUse":
                print(dump({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                      "permissionDecisionReason": "无法读取模式状态，请先恢复模式状态再执行：" + str(e)}}))
            else:
                print("{}")
            return 0
        print(dump({"error": str(e)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
