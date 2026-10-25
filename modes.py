"""Session-isolated execution modes with an explicit last-choice preference."""
import contextlib
import datetime as dt
import json
import os
from pathlib import Path
import re
import shlex
import tempfile
import unicodedata
import runtime

MENU = "请选择本次执行模式：模式一（1）Claude 统筹、Codex 执行；模式二（2）全部由 Claude 完成。回复 1 / 2 / 一 / 二 / 模式1 / 模式二 都行，也可以把模式和任务一起说；未明确选择时沿用上次模式，首次没有历史时需选择一次。"
LABELS = {"1": "模式一：Claude 统筹、Codex 执行", "2": "模式二：全部由 Claude 完成"}
DIGITS = {"1": "1", "一": "1", "2": "2", "二": "2"}
DESKTOP_METADATA_TOOLS = frozenset({"mcp__ccd_turn__wrap_up", "mcp__ccd_turn__update_plan"})


def has_menu(text):
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[*_`]", "", text)
    if re.search(r"模式\s*[1一]", text) and re.search(r"模式\s*[2二]", text):
        return True
    if re.search(r"模式\s*[1一]\s*(?:或|还是|/|、|和)\s*(?:模式\s*)?[2二]", text):
        return True
    # Desktop final recaps often label the two executors instead of repeating
    # the word 模式. Require the selection cue and both executor descriptions.
    return bool(re.search(
        r"(?:可选|选择|选用|回复)\s*1\s*\([^()\n]{0,80}Claude[^()\n]{0,80}Codex[^()\n]{0,80}\)"
        r"\s*(?:或|还是|/|、|和|,)\s*2\s*\([^()\n]{0,80}Claude[^()\n]{0,80}\)",
        text, re.I))


def parse_choice(prompt, awaiting=True):
    """Accept informal choices without treating task numbers or quoted options as a choice."""
    text = unicodedata.normalize("NFKC", prompt).strip()
    if re.match(r"^/codex-loop(?:\s|$)", text):
        return "1"
    # Judge the selection clause separately from an accompanying task.
    clause = re.split(r"[,，。:：;；\n]", text, maxsplit=1)[0]
    if re.search(r"(?:不要|别|不用|不选|不想|不能|不使用|不采用|不确定|不是|不一定)", clause):
        return None
    if re.search(r"(?:什么|区别|差别|解释|介绍|说明|比较|哪个好|可以吗|行吗|能否|是否)", clause):
        return None
    named = {DIGITS[d] for d in re.findall(r"模式\s*([12一二])(?![0-9一二])", clause)}
    if len(named) > 1 or re.search(r"模式\s*(?:12|一二|[1一]\s*(?:/|或|和|、|or)\s*[2二])", clause, re.I):
        return None
    # A named mode must begin a selection clause, not occur in quoted task content.
    prefix = r"(?:(?:那就|那|就|还是|要不|这次|本次|我觉得|我想|我|请|麻烦|先|暂时)\s*)*"
    verb = r"(?:(?:选择|选|切换到|切换为|切换|改用|采用|使用|用|按)\s*)?"
    match = re.match(r"^" + prefix + verb + r"模式\s*([12一二])(?![0-9一二])", text)
    if match:
        suffix = text[match.end():]
        if not suffix or re.match(r"^(?:吧|啊|呀|呢|好了|就行|即可|开始|继续|执行|来|做|，|,|。|\.|!|！|:|：|;|；|\s)", suffix):
            return DIGITS[match.group(1)]
    if not awaiting:
        return None
    # Bare answers are accepted only while a menu is open. Do not scan arbitrary
    # task content for digits (e.g. "修复 2 个错误" / "看一下" / "运行 1 次").
    match = re.fullmatch(prefix + verb + r"([12一二])(?:\s*(?:吧|啊|呀|呢|好了|就行|即可|都行|可以|模式|的))*[\s，,。.!！~～]*", text)
    if match:
        return DIGITS[match.group(1)]
    return None


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def transcript_choices(path, since=None, defaulted=False):
    """Recover choices from human prompts and actual mode-question answers only."""
    found = []
    menu_open = True  # A fresh session starts at its first mode choice.
    with open(path, encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            stamp = record.get("timestamp") or ""
            message = record.get("message") or {}
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if record.get("type") == "assistant":
                texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"] if isinstance(content, list) else [content] if isinstance(content, str) else []
                if texts:
                    menu_open = has_menu("".join(texts))
                continue
            if record.get("type") != "user" or record.get("isMeta") or (since and stamp <= since):
                continue
            answer = record.get("toolUseResult")
            if isinstance(answer, dict) and isinstance(answer.get("answers"), dict):
                asked = [q.get("question", "") for q in answer.get("questions", []) if isinstance(q, dict)]
                for question, value in answer["answers"].items():
                    if has_menu(question) and question in asked:
                        choice = parse_choice(str(value))
                        if choice:
                            found.append({"choice": choice, "timestamp": stamp, "uuid": record.get("uuid"), "source": "ask_user_question"})
                            defaulted = False
                menu_open = False
                continue
            if (record.get("origin") or {}).get("kind") == "human" and isinstance(content, str):
                choice = parse_choice(content, menu_open or defaulted)
                menu_open = False
                if choice:
                    found.append({"choice": choice, "timestamp": stamp, "uuid": record.get("uuid"), "source": "user_prompt"})
                    defaulted = False
    return found


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
    if parts[2] not in ("status", "activate", "wait", "finish", "sync"):
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
        self.preference = Path(root) / "mode-preference.json"

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

    def fallback_mode(self, state):
        if state.get("last_mode") in LABELS:
            return state["last_mode"]
        try:
            preference = json.loads(self.preference.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # Upgrade existing installations without making users re-enter a
            # choice already recorded by the old per-session implementation.
            previous = []
            for path in self.root.glob("*.json"):
                try:
                    saved = json.loads(path.read_text(encoding="utf-8"))
                    if saved.get("selection_source") not in (
                            "explicit_user_prompt", "transcript_sync", "default_last_mode"):
                        continue
                    mode = saved.get("mode") or saved.get("last_mode")
                    if mode in LABELS:
                        evidence = saved.get("selection_evidence") or {}
                        stamp = saved.get("selection_at") or evidence.get("timestamp") or saved.get("finished_at") or saved.get("updated_at", "")
                        previous.append((stamp, mode))
                except (OSError, ValueError, AttributeError):
                    continue
            return max(previous)[1] if previous else None
        return preference.get("mode") if isinstance(preference, dict) and preference.get("mode") in LABELS else None

    def remember_choice(self, state):
        if state.get("mode") not in LABELS or state.get("pending_mode"):
            return
        self.preference.parent.mkdir(parents=True, exist_ok=True)
        with runtime.file_lock(self.preference.with_suffix(".lock")):
            fd, tmp = tempfile.mkstemp(prefix=".preference-", dir=str(self.preference.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump({"mode": state["mode"], "updated_at": now()}, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp, self.preference)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)

    @staticmethod
    def apply_choice(state, choice, open_tasks, source):
        if open_tasks and choice != state["mode"]:
            state["pending_mode"] = choice
            state["phase"] = "switch_pending"
        else:
            state["mode"] = choice
            state["pending_mode"] = None
            state["phase"] = "working" if state["phase"] in ("working", "waiting_input", "switch_pending") else "ready"
            state["selection_source"] = source

    def hook_seen(self, session):
        with self.edit(session) as state:
            state["hook_seen_at"] = now()

    def submitted(self, session, prompt, open_tasks, *, choice_context=False):
        with self.edit(session) as state:
            state["turn"] += 1
            state["hook_seen_at"] = now()
            choice = parse_choice(prompt, choice_context or state["phase"] in ("awaiting_choice", "ready") or
                                  state.get("selection_source") == "default_last_mode")
            if choice:
                self.apply_choice(state, choice, open_tasks, "explicit_user_prompt")
                state["selection_at"] = now()
                self.remember_choice(state)
            elif state["phase"] == "awaiting_choice" and self.fallback_mode(state):
                self.apply_choice(state, self.fallback_mode(state), open_tasks, "default_last_mode")
            elif state["phase"] == "waiting_input":
                state["phase"] = "working"
            state.pop("reminder_sent", None)
        return self.get(session)

    def sync(self, session, transcript, open_tasks):
        """Recover a real choice when a host has omitted settings hooks."""
        with self.edit(session) as state:
            since = max(filter(None, [state.get("finished_at"), state.get("selection_at"), state.get("synced_at")]), default=None)
            defaulted = state.get("selection_source") == "default_last_mode" or (
                not state["mode"] and bool(self.fallback_mode(state)))
            found = transcript_choices(transcript, since, defaulted=defaulted)
            if found:
                self.apply_choice(state, found[-1]["choice"], open_tasks, "transcript_sync")
                state["selection_evidence"] = dict(found[-1], transcript=str(transcript))
                self.remember_choice(state)
            elif not state["mode"]:
                fallback = self.fallback_mode(state)
                if not fallback:
                    raise ValueError("会话记录里没有找到用户的模式选择，也没有上次模式可沿用；请回复 1 或 2")
                self.apply_choice(state, fallback, open_tasks, "default_last_mode")
            state["synced_at"] = now()
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
            state["selection_source"] = "explicit_user_prompt"
            self.remember_choice(state)
        return self.get(session)

    def require(self, session, mode=None):
        state = self.get(session)
        if state.get("pending_mode") or not state["mode"]:
            raise ValueError("User mode selection/transition is required before execution; use ccl mode sync if the user already answered")
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
            state.update(mode=None, phase="awaiting_choice", pending_mode=None, finished_at=now())
            state.pop("selection_evidence", None)
        return self.get(session)

    def guidance(self, state):
        if state.get("pending_mode"):
            return ("用户已要求切换到" + LABELS[state["pending_mode"]] +
                    "。先停止并等待现有 Codex 后台任务退出，检查现场，保留产物，关闭旧 CCL 任务（取消原因写明交接），再运行 ccl mode activate --session SESSION。不要同时启动两个执行者。")
        if not state["mode"]:
            fallback = self.fallback_mode(state)
            if fallback:
                return MENU + " 上次为" + LABELS[fallback] + "。新任务未明确选择时，运行 ccl mode sync --session SESSION 沿用上次并继续；用一句话告知用户即可，不必等待确认。"
            return MENU + " 这是首次选择，没有历史可沿用；先给出两个选项并等待真实选择。已回答但 hook 未记录时运行 ccl mode sync --session SESSION。"
        text = ("本次未明确选择，按用户约定沿用上次的" if state.get("selection_source") == "default_last_mode" else "已由用户选择") + LABELS[state["mode"]] + "。"
        if state.get("selection_source") == "default_last_mode":
            text += "开头简短告知本次沿用该模式，并提示回复模式1或模式2可切换；无需等待确认。"
        if state["mode"] == "1":
            text += "使用 codex-loop / ccl new/run 非交互交接具体工作；默认 approval_policy=never、sandbox_mode=danger-full-access。不要另开交互式 codex 终端，不要加 on-request、--full-auto 或把默认权限改回 workspace-write；除非用户本次明确要求收紧权限。已授权任务不逐条要求用户点击 Yes/No。"
        else:
            text += "由 Claude 自己规划、执行、自测和总结，不调用 Codex 或 codex-loop 执行器。"
        return text + "同一任务持续沿用，不反复询问。执行前 mode begin；任务完成、记录共享记忆后 mode finish；最后提示下一项任务可选模式一或模式二，未明确选择沿用本次模式。需要澄清时 mode wait，保留当前模式。"

    def pretool(self, session, tool, args):
        state = self.get(session)
        # These exact desktop tools only maintain the plan or final recap.
        # They must remain usable after finish, without opening another task.
        if tool in DESKTOP_METADATA_TOOLS:
            return {}
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
            menu_shown = has_menu(message)
            if not menu_shown and not state.get("reminder_sent"):
                with self.edit(session) as value:
                    value["reminder_sent"] = True
                return {"decision": "block", "reason": "请在回复末尾补充模式选择提示；有历史时下次未明确选择沿用上次：" + MENU}
        return {}
