"""Task-scoped, session-isolated choices. No model calls and no implicit default."""
import contextlib
import datetime as dt
import json
import os
from pathlib import Path
import re
import shlex
import tempfile
import runtime

MENU = "请选择本次执行模式：模式一（1）Claude 统筹、Codex 执行；模式二（2）全部由 Claude 完成。回复 1 或 2，也可以把模式和任务一起说。未选择前不执行。"
LABELS = {"1": "模式一：Claude 统筹、Codex 执行", "2": "模式二：全部由 Claude 完成"}


def parse_choice(prompt, awaiting=True):
    text = prompt.strip()
    if awaiting and text in ("1", "一", "2", "二"):
        return "1" if text in ("1", "一") else "2"
    match = re.match(r"^(?:(?:我(?:选择|选)|选择|选|切换(?:到|为)?|改用|使用|用)\s*)?模式\s*([12一二])(?=$|[\s，,。.:：；;！!])", text)
    if match:
        return "1" if match.group(1) in ("1", "一") else "2"
    if re.match(r"^/codex-loop(?:\s|$)", text):
        return "1"  # An explicit user command, not an agent invoking the Skill tool.
    return None


def control_command(command):
    """Allow only a single standalone mode-management command before selection."""
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if parts and parts[0] == '&':
        parts = parts[1:]
        command = command.lstrip()[1:]
    if len(parts) < 3 or Path(parts[0]).name not in ("ccl", "ccl.cmd") or parts[1] != "mode":
        return False
    if parts[2] not in ("status", "activate", "wait", "finish"):
        return False
    # Shell expansion must not turn a status lookup into unrelated execution.
    return not any(x in command for x in (";", "\n", "&", "|", "`", "$(", ">", "<"))


def dispatches_codex(tool, args):
    if "codex" in tool.lower() and tool.startswith("mcp"):
        return True
    if tool not in ("Bash", "PowerShell"):
        return False
    command = args.get("command", "")
    return bool(re.search(r"(?:^|[\s/'\"])codex\s+(?:exec|e|resume|mcp-server)\b", command) or
                re.search(r"\bccl(?:\.cmd)?[\"']?\s+(?:new|run)\b", command))


class ModeStore:
    def __init__(self, root):
        self.root = Path(root) / "sessions"

    def path(self, session):
        if not isinstance(session, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,160}", session):
            raise ValueError("A valid Claude session id is required")
        return self.root / (session + ".json")

    @contextlib.contextmanager
    def edit(self, session):
        path = self.path(session)
        self.root.mkdir(parents=True, exist_ok=True)
        with runtime.file_lock(path.with_suffix(".lock")):
            state = self.get(session)
            yield state
            state["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            fd, tmp = tempfile.mkstemp(prefix=".mode-", dir=str(self.root))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(state, stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)

    def get(self, session):
        path = self.path(session)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {"session": session, "mode": None, "phase": "awaiting_choice", "pending_mode": None,
                "last_mode": None, "history": [], "turn": 0}

    def submitted(self, session, prompt, open_tasks):
        with self.edit(session) as state:
            state["turn"] += 1
            choice = parse_choice(prompt, state["phase"] in ("awaiting_choice", "ready"))
            if choice:
                if open_tasks and choice != state["mode"]:
                    state["pending_mode"] = choice
                    state["phase"] = "switch_pending"
                else:
                    state["mode"] = choice
                    state["pending_mode"] = None
                    # Switching mode 2 -> 1 preserves an in-progress native task.
                    state["phase"] = "working" if state["phase"] in ("working", "waiting_input", "switch_pending") else "ready"
                    state["selection_source"] = "explicit_user_prompt"
            elif state["phase"] == "waiting_input":
                state["phase"] = "working"
            state.pop("reminder_sent", None)
        return self.get(session)

    def activate(self, session, open_tasks):
        with self.edit(session) as state:
            if not state.get("pending_mode"):
                raise ValueError("No user-requested mode switch is pending")
            if open_tasks:
                raise ValueError("Stop/resolve the old CCL task before activating the requested mode")
            state["mode"] = state.pop("pending_mode")
            state["pending_mode"] = None
            state["phase"] = "working"
        return self.get(session)

    def require(self, session, mode=None):
        state = self.get(session)
        if state.get("pending_mode") or not state["mode"]:
            raise ValueError("User mode selection/transition is required before execution")
        if mode and state["mode"] != mode:
            raise ValueError("Current choice is " + LABELS[state["mode"]] + "; Codex dispatch is disabled")
        return state

    def begin(self, session, project, summary):
        self.require(session)
        with self.edit(session) as state:
            state.update(phase="working", project=str(project), summary=summary[:1000])
        return self.get(session)

    def wait(self, session, reason):
        self.require(session)
        with self.edit(session) as state:
            state.update(phase="waiting_input", waiting_reason=reason)
        return self.get(session)

    def finish(self, session, note, open_tasks):
        self.require(session)
        if not note.strip() or open_tasks:
            raise ValueError("Completion needs evidence/summary and all CCL tasks must be closed")
        with self.edit(session) as state:
            state["history"].append({"mode": state["mode"], "project": state.get("project"), "note": note[:2000]})
            state["history"] = state["history"][-50:]
            state["last_mode"] = state["mode"]
            state.update(mode=None, phase="awaiting_choice", pending_mode=None)
        return self.get(session)

    def guidance(self, state):
        if state.get("pending_mode"):
            return ("用户已要求切换到" + LABELS[state["pending_mode"]] +
                    "。先停止并等待现有 Codex 后台任务退出，检查现场，保留产物，关闭旧 CCL 任务（取消原因写明交接），再运行 ccl mode activate --session SESSION。不要同时启动两个执行者。")
        if not state["mode"]:
            return MENU + " 先用中文向用户给出这两个选项并等待真实选择；不要自行调用模式选择命令，也不要执行任务。"
        text = "已由用户选择" + LABELS[state["mode"]] + "。"
        if state["mode"] == "1":
            text += "使用 codex-loop / ccl new/run 非交互交接具体工作；默认 approval_policy=never、sandbox_mode=danger-full-access。不要另开交互式 codex 终端，不要加 on-request、--full-auto 或把默认权限改回 workspace-write；除非用户本次明确要求收紧权限。已授权任务不逐条要求用户点击 Yes/No。"
        else:
            text += "由 Claude 自己规划、执行、自测和总结，不调用 Codex 或 codex-loop 执行器。"
        return text + "同一任务持续沿用，不反复询问。执行前 mode begin；任务完成、记录共享记忆后 mode finish；最后提醒下一项任务选模式一或模式二。需要澄清时 mode wait，保留当前模式。"

    def pretool(self, session, tool, args):
        state = self.get(session)
        reason = None
        if not state["mode"]:
            allowed = tool in ("AskUserQuestion", "Skill") or (tool in ("Bash", "PowerShell") and control_command(args.get("command", "")))
            if not allowed:
                reason = "用户要求先选模式再执行。" + MENU
        elif state.get("pending_mode"):
            if dispatches_codex(tool, args) or tool in ("Write", "Edit", "NotebookEdit", "Agent", "Task"):
                reason = "正在交接模式；先停止并关闭旧执行任务，保留成果，再激活新模式。"
        elif state["mode"] == "2" and dispatches_codex(tool, args):
            reason = "用户选了模式二：全部由 Claude 完成，不能派发给 Codex。"
        if reason:
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}
        if state["phase"] == "ready" and tool not in ("Skill", "AskUserQuestion") and not (
                tool in ("Bash", "PowerShell") and control_command(args.get("command", ""))):
            with self.edit(session) as current:
                current["phase"] = "working"
        return {}

    def stopping(self, session, payload, open_tasks):
        state = self.get(session)
        if payload.get("stop_hook_active"):
            return {}
        if state["phase"] == "working" and not open_tasks:
            return {"decision": "block", "reason": "结束前请关闭模式任务：若已完成，先验证结果、记录有用共享记忆，再 ccl mode finish --session SESSION --note '完成与验证依据'；若需要用户补充，用 mode wait。随后在回复末尾提示下一项任务选择模式一或模式二。SESSION 用当前 session id。"}
        if state["phase"] == "awaiting_choice":
            message = payload.get("last_assistant_message", "")
            has_menu = (("模式一" in message and "模式二" in message) or
                        ("模式 1" in message and "模式 2" in message) or
                        ("模式1" in message and "模式2" in message))
            if not has_menu and not state.get("reminder_sent"):
                with self.edit(session) as value:
                    value["reminder_sent"] = True
                return {"decision": "block", "reason": "请在回复末尾补充模式选择提示并等待用户，不执行新任务：" + MENU}
        return {}
