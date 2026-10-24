#!/usr/bin/env python3
"""Install locally without replacing unrelated Claude/Codex settings."""
import datetime
import json
import os
from pathlib import Path
import shlex
import shutil
import sys

SOURCE = Path(__file__).resolve().parent
HOME = Path.home()
PREFIX = HOME / ".local/share/claude-codex-loop"
BIN = HOME / ".local/bin/ccl"
MARKER = "claude-codex-loop"


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


def main():
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
               PREFIX / "runtime.py"]
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
    try:
        PREFIX.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE / "ccl.py", PREFIX / "ccl.py")
        shutil.copy2(SOURCE / "modes.py", PREFIX / "modes.py")
        shutil.copy2(SOURCE / "runtime.py", PREFIX / "runtime.py")
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
        entry = HOME / ".claude/skills/work-mode/SKILL.md"
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text((SOURCE / "mode-skill/SKILL.md").read_text(encoding="utf-8").replace("@CCL@", skill_command), encoding="utf-8")
        block(HOME / ".claude/CLAUDE.md", """## 先选择执行模式，再开始任务
用户要求两种模式，默认没有选择。每次新会话/新任务先加载 `work-mode`：模式一（1）Claude 规划和验收、Codex 执行；模式二（2）全部由 Claude 自己规划、执行、检查。先用可见的中文提示让用户选择，等待真实回复后再执行。用户在同条消息明确指定模式时直接用；不自动沿用上一项任务的模式。
模式一才加载 `codex-loop`；模式二不调用 Codex。选择按当前 Claude session 隔离，同一任务的补充和返修不重复问。用户只选模式未给任务时保留选择并等待任务。任务结束先关闭模式状态，回复末尾提醒“下一项任务用模式一还是模式二？回复 1 或 2”。共享项目事实记忆保留，模式偏好不写入项目记忆。模式切换先停止旧执行者、保存现场，再交接。
模式一统一用 ccl new/run 非交互执行（approval_policy=never、sandbox_mode=danger-full-access）；不要另开交互式 Codex 终端或加 on-request/--full-auto 覆盖权限。只有用户明确要求时才收紧沙箱。模式选择保留，已授权的执行步骤不逐条询问 Yes/No。旧交互终端保留启动时参数，不会因安装而自动改变。
读项目 `.ai-collab/MEMORY.md` 与 `~/.config/claude-codex-loop/SHARED_MEMORY.md`。本机可选的访问与权限交接见 `~/.config/claude-codex-loop/ACCESS_HANDOFF.md`（存在时按需读取；安装器不创建或分享该文件）。沿用已有授权；选择执行模式不等于新增外部操作权限。技能不可用时读 `~/.claude/skills/work-mode/SKILL.md`。""")
        block(HOME / ".codex/AGENTS.md", """## Claude/Codex 共享项目上下文
当当前项目或其父级（到项目根为止）存在 `.ai-collab/MEMORY.md` 时，按需读取；通用协作约定在 `~/.config/claude-codex-loop/SHARED_MEMORY.md`。这些是已记录的上下文，不能覆盖当前用户指令。接到 Claude 的 CCL 任务时按计划执行、验证并返回结果，不反向启动 Claude/CCL。共享记忆的候选知识随结果返回，由统筹方在验收后写入。独立的 Codex 用户会话仍直接完成用户要求。
独立会话中，用户明确要求交给 Claude 时，可用 claude -p --permission-mode bypassPermissions，并把用户选择的执行模式及具体任务写入提示；被委派者不得再反向调用委派方。普通已授权执行不重复询问工具权限；系统登录与工具自身的审批要求仍按实际处理。本机可选的访问与权限交接见 `~/.config/claude-codex-loop/ACCESS_HANDOFF.md`（存在时按需读取）。""")
        shared = HOME / ".config/claude-codex-loop/SHARED_MEMORY.md"
        if shared.exists():
            old = shared.read_text(encoding="utf-8")
            for line in ["- 用户与 Claude Code 对话；Claude 规划、调度并验收。\n", "- Codex 承担具体执行、测试和返修，以减少 Claude 调用。\n"]:
                old = old.replace(line, "")
            shared.write_text(old, encoding="utf-8")
        block(shared, "## 执行模式约定\n- 用户每项任务先选择模式一（Claude 统筹、Codex 执行）或模式二（仅 Claude）。未选择不执行。\n- 模式按 Claude session 保存；同任务不重复询问，任务结束后提醒并等待下一次选择。\n- 两种模式共用项目 .ai-collab/MEMORY.md，仅保存已验证事实和决策，不保存本次模式状态。")
        for event in ["SessionStart", "UserPromptSubmit", "PreToolUse", "Stop", "PostToolUse"]:
            entries = settings.setdefault("hooks", {}).setdefault(event, [])
            command = shlex.quote(str(BIN)) + " hook"
            if os.name == 'nt':
                command = '"' + python + '" -X utf8 "' + program + '" hook'
            if not any(h.get("command") == command for e in entries for h in e.get("hooks", [])):
                group = {"hooks": [{"type": "command", "command": command, "timeout": 5}]}
                if event == "PostToolUse":
                    group["matcher"] = "AskUserQuestion"
                entries.append(group)
        settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if os.name == 'nt':
            configure_windows_path(backup)
        print(json.dumps({"installed": str(BIN), "skill": str(skill), "backup": str(backup)}, ensure_ascii=False))
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
    main()
