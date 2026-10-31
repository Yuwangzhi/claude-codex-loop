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

MENU = "未指定模式时由 Claude 按任务难度自动选择，不会询问；想指定可回复 1（模式一：Claude 统筹、Codex 执行）或 2（模式二：全部由 Claude 完成），例如“模式2”“moshi 2”。"
MODE = r"(?:模式|moshi|mode)"
EXPLICIT_SOURCES = ("explicit_user_prompt", "transcript_sync")
AUTO_SOURCES = ("default_last_mode", "auto_by_claude", "auto_by_dispatch")
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
    named = {DIGITS[d] for d in re.findall(MODE + r"\s*([12一二])(?![0-9一二])", clause, re.I)}
    if len(named) > 1 or re.search(MODE + r"\s*(?:12|一二|[1一]\s*(?:/|或|和|、|or)\s*[2二])", clause, re.I):
        return None
    # A named mode must begin a selection clause, not occur in quoted task content.
    prefix = r"(?:(?:那就|那|就|还是|要不|这次|本次|我觉得|我想|我|请|麻烦|先|暂时)\s*)*"
    verb = r"(?:(?:选择|选|切换到|切换为|切换|改用|采用|使用|用|按)\s*)?"
    match = re.match(r"^" + prefix + verb + MODE + r"\s*([12一二])(?![0-9一二])", text, re.I)
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


def diagnostic_command(command):
    """Standalone read-only diagnosis is available even during state repair."""
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if parts and parts[0] == '&':
        parts = parts[1:]
        command = command.lstrip()[1:]
    if len(parts) < 2 or Path(parts[0]).name not in ('ccl', 'ccl.cmd'):
        return False
    if any(x in command for x in (';', '\n', '\r', '&', '|', '`', '$', '>', '<')):
        return False
    if parts[1] == 'doctor':
        rest = parts[2:]
        while rest:
            if rest[0] == '--compat':
                rest = rest[1:]
            elif rest[0] in ('--project', '--session') and len(rest) >= 2 and not rest[1].startswith('--'):
                rest = rest[2:]
            else:
                return False
        return True
    return len(parts) == 3 and parts[1] in ('recover', 'handoff') and bool(re.fullmatch(r'[0-9a-f]{12}', parts[2]))


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
            state = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(state, dict) or state.get("mode") not in (None, "1", "2") or
                    state.get("pending_mode") not in (None, "1", "2") or
                    state.get("phase") not in ("awaiting_choice", "ready", "working", "waiting_input", "switch_pending") or
                    not isinstance(state.get("history", []), list) or type(state.get("turn", 0)) is not int):
                raise ValueError("Invalid mode state structure: " + str(path))
            return state
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

    @staticmethod
    def explicit(state):
        return state.get("selection_source") in EXPLICIT_SOURCES

    def hook_seen(self, session):
        with self.edit(session) as state:
            state["hook_seen_at"] = now()

    def submitted(self, session, prompt, open_tasks, *, choice_context=False):
        with self.edit(session) as state:
            state["turn"] += 1
            state["hook_seen_at"] = now()
            choice = parse_choice(prompt, choice_context or state["phase"] in ("awaiting_choice", "ready") or
                                  state.get("selection_source") in AUTO_SOURCES)
            if choice:
                self.apply_choice(state, choice, open_tasks, "explicit_user_prompt")
                state["selection_at"] = now()
                self.remember_choice(state)
            elif state["phase"] == "waiting_input":
                state["phase"] = "working"
            state.pop("reminder_sent", None)
        return self.get(session)

    def sync(self, session, transcript, open_tasks):
        """Recover a real choice when a host has omitted settings hooks."""
        with self.edit(session) as state:
            since = max(filter(None, [state.get("finished_at"), state.get("selection_at"), state.get("synced_at")]), default=None)
            defaulted = state.get("selection_source") in AUTO_SOURCES or (
                not state["mode"] and bool(self.fallback_mode(state)))
            found = transcript_choices(transcript, since, defaulted=defaulted)
            if found:
                self.apply_choice(state, found[-1]["choice"], open_tasks, "transcript_sync")
                state["selection_evidence"] = dict(found[-1], transcript=str(transcript))
                self.remember_choice(state)
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
        """Check (and, when the user has not decided otherwise, set) the executor for a dispatch."""
        state = self.get(session)
        if state.get("pending_mode"):
            raise ValueError("A user-requested mode switch is pending; stop the old executor, then ccl mode activate")
        if mode and state["mode"] != mode:
            if state["mode"] and (self.explicit(state) or mode == "2"):
                raise ValueError("Current choice is " + LABELS[state["mode"]] + "; this action belongs to " + LABELS[mode])
            # No mode yet, or Claude's own earlier pick: following the dispatch is the decision.
            with self.edit(session) as current:
                current.update(mode=mode, selection_source="auto_by_dispatch", selection_at=now())
                if current["phase"] not in ("working", "waiting_input"):
                    current["phase"] = "ready"
            state = self.get(session)
        return state

    def begin(self, session, project, summary, mode=None, open_tasks=()):
        with self.edit(session) as state:
            if state.get("pending_mode"):
                raise ValueError("A user-requested mode switch is pending; stop the old executor, then ccl mode activate")
            if not state["mode"]:
                state.update(mode=mode or self.fallback_mode(state) or "2", selection_source="auto_by_claude", selection_at=now())
            elif mode and mode != state["mode"] and not self.explicit(state) and not open_tasks:
                state.update(mode=mode, selection_source="auto_by_claude", selection_at=now())
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
            if state["mode"]:
                state["history"].append({"mode": state["mode"], "project": state.get("project"), "note": note[:2000]})
                state["history"] = state["history"][-50:]
                state["last_mode"] = state["mode"]
            state.update(mode=None, phase="awaiting_choice", pending_mode=None, finished_at=now())
            state.pop("selection_evidence", None)
            state.pop("selection_source", None)
        return self.get(session)

    def guidance(self, state):
        if state.get("pending_mode"):
            return ("用户已要求切换到" + LABELS[state["pending_mode"]] +
                    "。先停止并等待现有 Codex 后台任务退出，检查现场，保留产物，关闭旧 CCL 任务（取消原因写明交接），再运行 ccl mode activate --session SESSION。不要同时启动两个执行者。")
        rule = ("规则：用户明确指定模式就严格照办；没指定时由你按任务难度自己选，不要问用户、不要等确认、不要因门禁报错而停下。"
                "判断：问答、调研、单文件或小改动、配置/部署、几步内能做完 → 模式二（自己做）；多文件改动、长耗时、批量处理、需要独立执行与独立验收 → 模式一（ccl new/run 交给 Codex）。"
                "同一任务的补充和返修沿用已选模式；新任务难度明显不同时，用 ccl mode begin --mode N 重选（用户明确选过的除外）。")
        if not state["mode"]:
            hint = "上次为" + LABELS[state["last_mode"]] + "，仅供参考。" if state.get("last_mode") in LABELS else ""
            return ("本次用户未指定执行模式。" + rule + hint +
                    "开工前运行 ccl mode begin --session SESSION --project 项目 --mode 1或2 --note 目标；直接 ccl new 视为选模式一。"
                    "开头用一句话告知所选模式及理由，并说明回复 1/2 可改。")
        who = "已由用户明确选择" if self.explicit(state) else "已由 Claude 按任务难度选定"
        text = who + LABELS[state["mode"]] + "。" + rule
        if state["mode"] == "1":
            text += "使用 codex-loop / ccl new/run 非交互交接具体工作；默认 approval_policy=never、sandbox_mode=danger-full-access。不要另开交互式 codex 终端，不要加 on-request、--full-auto 或把默认权限改回 workspace-write；除非用户本次明确要求收紧权限。已授权任务不逐条要求用户点击 Yes/No。"
        else:
            text += "由 Claude 自己规划、执行、自测和总结，不调用 Codex 或 codex-loop 执行器。"
        return text + "任务真正完成并记录共享记忆后运行 mode finish；需要用户补充信息时 mode wait。最终回复不必再附模式选择提示。"

    def pretool(self, session, tool, args):
        state = self.get(session)
        # These exact desktop tools only maintain the plan or final recap.
        # They must remain usable after finish, without opening another task.
        if tool in DESKTOP_METADATA_TOOLS:
            return {}
        reason = None
        if state.get("pending_mode"):
            if dispatches_codex(tool, args) or tool in ("Write", "Edit", "NotebookEdit", "Agent", "Task"):
                reason = "正在交接模式；先停止并关闭旧执行任务，保留成果，再激活新模式。"
        elif state["mode"] == "2" and self.explicit(state) and dispatches_codex(tool, args):
            reason = "用户明确选了模式二：全部由 Claude 完成，不能派发给 Codex。"
        if reason:
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}
        if state["phase"] == "ready" and tool not in ("Skill", "AskUserQuestion") and not (
                tool in ("Bash", "PowerShell") and control_command(args.get("command", ""))):
            with self.edit(session) as current:
                current["phase"] = "working"
        return {}

    def stopping(self, session, payload, open_tasks):
        """End of turn: never force a menu or a finish call; park the task as ready for follow-ups."""
        state = self.get(session)
        if payload.get("stop_hook_active"):
            return {}
        if state["phase"] == "working" and not open_tasks and state["mode"]:
            with self.edit(session) as current:
                current.update(phase="ready", auto_closed_at=now())
        return {}
