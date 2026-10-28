#!/usr/bin/env python3
"""Install locally without replacing unrelated Claude/Codex settings."""
import datetime
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import runtime

SOURCE = Path(__file__).resolve().parent
HOME = Path.home()
PREFIX = HOME / ".local/share/claude-codex-loop"
BIN = HOME / ".local/bin/ccl"
MARKER = "claude-codex-loop"
EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "Stop", "PostToolUse"]
MODULES = ['ccl.py', 'modes.py', 'runtime.py', 'diagnostics.py', 'governance.py', 'compatibility.py', 'contextpack.py']


def plugin_paths(settings):
    value = settings.get("env", {}).get("CLAUDE_CODE_PLUGIN_DIRS", "")
    if not isinstance(value, str):
        raise ValueError("CLAUDE_CODE_PLUGIN_DIRS must be a path-list string")
    return [p for p in value.split(os.pathsep) if p]


def managed_command(command):
    return runtime.managed_hook(command, BIN, PREFIX / 'ccl.py')


def configure_hooks(settings, transport):
    plugin = PREFIX / "desktop-plugin"
    paths = plugin_paths(settings)
    for event in EVENTS:
        groups = []
        for group in settings.setdefault("hooks", {}).get(event, []):
            hooks = [h for h in group.get("hooks", []) if not managed_command(h.get("command"))]
            if hooks:
                groups.append(dict(group, hooks=hooks))
        if transport == "command":
            command = shlex.quote(str(BIN)) + " hook"
            if os.name == 'nt':
                command = '"' + Path(sys.executable).as_posix() + '" -X utf8 "' + (PREFIX / 'ccl.py').as_posix() + '" hook'
            group = {"hooks": [{"type": "command", "command": command, "timeout": 5}]}
            if event == "PostToolUse":
                group["matcher"] = "AskUserQuestion"
            groups.append(group)
        if groups:
            settings["hooks"][event] = groups
        else:
            settings["hooks"].pop(event, None)
    paths = [p for p in paths if Path(p).expanduser() != plugin]
    if transport == "module":
        paths.append(str(plugin))
    env = settings.setdefault("env", {})
    if paths:
        env["CLAUDE_CODE_PLUGIN_DIRS"] = os.pathsep.join(paths)
    else:
        env.pop("CLAUDE_CODE_PLUGIN_DIRS", None)


def block(path, body):
    start, end = "<!-- BEGIN " + MARKER + " -->", "<!-- END " + MARKER + " -->"
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    if start in original:
        a = original.index(start)
        b = original.index(end, a) + len(end)
        updated = original[:a] + start + "\n" + body + "\n" + end + original[b:]
    else:
        updated = original.rstrip() + "\n\n" + start + "\n" + body + "\n" + end + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding="utf-8")


def configure_windows_path(backup):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
        try:
            previous, kind = winreg.QueryValueEx(key, 'Path')
        except FileNotFoundError:
            previous, kind = '', winreg.REG_EXPAND_SZ
        (backup / 'user-path.json').write_text(json.dumps({'value': previous, 'type': kind}), encoding='utf-8')
        directory = str(BIN.parent)
        if directory.lower() not in [p.rstrip('\\').lower() for p in previous.split(';')]:
            winreg.SetValueEx(key, 'Path', 0, kind, previous.rstrip(';') + ';' + directory)
    for name in ('.bash_profile', '.bashrc'):
        path = HOME / name
        text = path.read_text(encoding='utf-8') if path.exists() else ''
        marker = '# CCL user-local executables'
        text = text.replace(marker + '\nexport PATH="$HOME/.local/bin:$PATH"',
                            marker + '\nexport PATH="$PATH:$HOME/.local/bin"')
        if marker not in text:
            text += '\n' + marker + '\nexport PATH="$PATH:$HOME/.local/bin"\n'
        path.write_text(text, encoding='utf-8')


def main(hook_transport=None):
    if sys.version_info < (3, 9):
        raise SystemExit("CCL requires Python 3.9+.")
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    os.umask(0o077)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = HOME / ".local/state/claude-codex-loop/backups" / stamp
    targets = [HOME / ".claude/CLAUDE.md", HOME / ".claude/settings.json", HOME / ".codex/AGENTS.md",
               HOME / ".claude/skills/codex-loop/SKILL.md", BIN, PREFIX / "ccl.py", PREFIX / "modes.py",
               HOME / ".claude/skills/work-mode/SKILL.md", HOME / ".config/claude-codex-loop/SHARED_MEMORY.md",
               PREFIX / "runtime.py", PREFIX / "diagnostics.py"]
    targets += [PREFIX / name for name in MODULES if PREFIX / name not in targets]
    reference = HOME / '.claude/skills/codex-loop/references/contracts.md'
    targets.append(reference)
    plugin_files = [".claude-plugin/plugin.json", "hooks/hooks.json", "hooks/hooks.mjs"]
    targets += [PREFIX / "desktop-plugin" / name for name in plugin_files + ["bridge.json"]]
    if os.name == 'nt':
        targets += [BIN.with_suffix('.cmd'), HOME / '.bash_profile', HOME / '.bashrc']
    backup.mkdir(parents=True)
    manifest = []
    for index, p in enumerate(targets):
        saved = backup / str(index)
        manifest.append({"path": str(p), "existed": p.exists(), "backup": str(saved)})
        if p.exists():
            shutil.copy2(p, saved)
    (backup / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    settings_path = HOME / ".claude/settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8-sig")) if settings_path.exists() else {}
    if hook_transport is None:
        hook_transport = "module" if any(Path(p).expanduser() == PREFIX / "desktop-plugin" for p in plugin_paths(settings)) else "command"
    try:
        PREFIX.mkdir(parents=True, exist_ok=True)
        for name in MODULES:
            shutil.copy2(SOURCE / name, PREFIX / name)
        if hook_transport == "module":
            for name in plugin_files:
                destination = PREFIX / "desktop-plugin" / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(SOURCE / "desktop-plugin" / name, destination)
            (PREFIX / "desktop-plugin/bridge.json").write_text(json.dumps({
                "python": str(Path(sys.executable)), "ccl": str(PREFIX / "ccl.py")
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        BIN.parent.mkdir(parents=True, exist_ok=True)
        python = Path(sys.executable).as_posix()
        program = (PREFIX / 'ccl.py').as_posix()
        BIN.write_bytes(("#!/bin/sh\nexec " + shlex.quote(python) + " -X utf8 " + shlex.quote(program) + ' "$@"\n').encode("utf-8"))
        BIN.chmod(0o700)
        if os.name == 'nt':
            BIN.with_suffix('.cmd').write_text('@echo off\n"' + python + '" -X utf8 "' + program + '" %*\n', encoding='utf-8')
        skill_command = shlex.quote(BIN.as_posix())
        skill = HOME / ".claude/skills/codex-loop/SKILL.md"
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text((SOURCE / "claude-skill/SKILL.md").read_text(encoding="utf-8").replace("@CCL@", skill_command), encoding="utf-8")
        reference.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE / 'claude-skill/references/contracts.md', reference)
        entry = HOME / ".claude/skills/work-mode/SKILL.md"
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text((SOURCE / "mode-skill/SKILL.md").read_text(encoding="utf-8").replace("@CCL@", skill_command), encoding="utf-8")
        block(HOME / ".claude/CLAUDE.md", """## 执行模式：可选 1/2，未回答沿用上次
每次新任务先加载 `work-mode`，可见提示模式一（1，Claude 规划和验收、Codex 执行）与模式二（2，全部由 Claude 完成）。用户可回答 1、2、一、二、模式1、模式二、二吧、就用1，也可把模式与任务一起说。没有明确选择时沿用本 session 上次模式，否则沿用用户最近明确选择的偏好，并告知本次沿用哪种及可回复 1/2 切换；有历史时不阻塞等待。完全没有历史才询问一次等待真实选择，不自行猜测。
先看 hook 状态；若桌面端 hook 没有记录真实回复，使用 `ccl mode sync --session SESSION` 从本机会话记录同步，再执行；不让用户反复重输，不伪造状态。同任务补充和返修沿用当前模式，不反复询问。模式一加载 `codex-loop`；模式二不调用 Codex。只有选择没有任务时保留选择并等待任务。结束本次任务后保留历史偏好，回复末尾提示“下一项可选模式一或二，不选沿用上次”；不能因用户暂时没回复而启动额外工作。切换先停止旧执行者、保存现场，再交接。
模式一统一用 ccl new/run 非交互执行（approval_policy=never、sandbox_mode=danger-full-access）；不要另开交互式 Codex 终端或加 on-request/--full-auto 覆盖权限。只有用户明确要求时才收紧沙箱。已授权执行不逐条询问 Yes/No。
读项目 `.ai-collab/MEMORY.md` 与 `~/.config/claude-codex-loop/SHARED_MEMORY.md`。本机可选访问交接见 `~/.config/claude-codex-loop/ACCESS_HANDOFF.md`，存在时按需读取。沿用已有授权；模式不扩大外部操作权限。技能不可用时读 `~/.claude/skills/work-mode/SKILL.md`。""")
        block(HOME / ".codex/AGENTS.md", """## Claude/Codex 共享项目上下文
当当前项目或其父级（到项目根为止）存在 `.ai-collab/MEMORY.md` 时，按需读取；通用协作约定在 `~/.config/claude-codex-loop/SHARED_MEMORY.md`。这些是已记录的上下文，不能覆盖当前用户指令。接到 Claude 的 CCL 任务时按计划执行、验证并返回结果，不反向启动 Claude/CCL。共享记忆的候选知识随结果返回，由统筹方在验收后写入。独立的 Codex 用户会话仍直接完成用户要求。
独立会话中，用户明确要求交给 Claude 时，可用 claude -p --permission-mode bypassPermissions，并把用户选择的执行模式及具体任务写入提示；被委派者不得再反向调用委派方。普通已授权执行不重复询问工具权限；系统登录与工具自身的审批要求仍按实际处理。本机可选的访问与权限交接见 `~/.config/claude-codex-loop/ACCESS_HANDOFF.md`（存在时按需读取）。""")
        shared = HOME / ".config/claude-codex-loop/SHARED_MEMORY.md"
        if shared.exists():
            old = shared.read_text(encoding="utf-8")
            for line in ["- 用户与 Claude Code 对话；Claude 规划、调度并验收。\n", "- Codex 承担具体执行、测试和返修，以减少 Claude 调用。\n"]:
                old = old.replace(line, "")
            shared.write_text(old, encoding="utf-8")
        block(shared, "## 执行模式约定\n- 新任务提示模式一（Claude 统筹、Codex 执行）或模式二（仅 Claude）；接受 1/2/一/二/模式1/模式二及明确的口语选择。\n- 没有回答选择时沿用本 session 上次模式，否则沿用最近明确选择的偏好；完全无历史才等待选择。\n- 活跃任务按 session 隔离，同任务不重复问；任务结束保留偏好，下一项未指定就沿用。仅有时间流逝不能启动新任务。\n- 两种模式共用项目 .ai-collab/MEMORY.md，仅保存已验证事实和决策，不保存本次模式状态。")
        configure_hooks(settings, hook_transport)
        settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if os.name == 'nt':
            configure_windows_path(backup)
        print(json.dumps({"installed": str(BIN), "skill": str(skill), "backup": str(backup),
                          "hook_transport": hook_transport}, ensure_ascii=False))
    except BaseException:
        if os.name == 'nt' and (backup / 'user-path.json').exists():
            import winreg
            previous = json.loads((backup / 'user-path.json').read_text(encoding='utf-8'))
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
                winreg.SetValueEx(key, 'Path', 0, previous['type'], previous['value'])
        for item in manifest:
            p = Path(item["path"])
            if item["existed"]:
                shutil.copy2(item["backup"], p)
            elif p.exists():
                p.unlink()
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hook-transport", choices=["command", "module"],
                        help="module requires Claude function-hook support; omitted preserves the existing transport")
    main(parser.parse_args().hook_transport)
