# 安装与卸载

← [返回 README](../README.md)

### 1. 准备环境

- Python **3.9+**、Git；macOS/Linux 使用 POSIX shell，Windows 的 Claude Bash 工具使用 Git Bash。
- 能独立工作的 **Claude Code CLI** 和 **Codex CLI**，并完成各自登录。
- Claude 客户端需支持 Claude Code 的本地 skills 和 hooks；普通网页聊天不能直接安装本地 hooks。
- 本项目不安装模型、不创建 API key，不修改模型或供应商。调用使用各自 CLI 账号的额度或计费方式。

| 环境 | 支持范围 |
| --- | --- |
| macOS | 完整 CCL 流程，已有真实 CLI 联调 |
| Linux | POSIX 实现；配置了跨平台离线 CI，尚未进行真实模型联调 |
| WSL | 可作为 Linux 环境使用，未单独进行端到端验证 |
| Windows 原生 | **v0.3.0 起支持完整 CCL**：原生 Python、Windows 文件锁、进程树终止、UTF-8 状态及 `.cmd` / Git Bash 启动器，不需要 WSL |

```bash
python3 --version
git --version
claude --version
codex --version
codex login status
```

### 2. 克隆并安装

```bash
git clone https://github.com/Yuwangzhi/claude-codex-loop.git
cd claude-codex-loop
python3 install.py
export PATH="$HOME/.local/bin:$PATH"
ccl --version
```

安装器复制运行文件、安装两个 skills，并在修改前备份。首次默认使用五类 command hooks；更新时保留已选择的 hook 通路。它保留既有权限、插件配置和其他 hooks；**不会自动打开 Claude 的 Bypass 模式，也不会配置 GitHub 登录或 SSH**。

桌面端如果忽略 settings 中的 command hooks，可在支持 hooks module/mod 的 Claude Code 版本中安装模块通路：

```bash
python3 install.py --hook-transport module
```

这会安装本地 `desktop-plugin`，将其路径加入 settings 的 `env.CLAUDE_CODE_PLUGIN_DIRS`，并移除 CCL 自己的五类 command hooks，避免重复处理。其他插件路径、环境变量和 hooks 保留。hooks module/mod 属于 early-access 能力，取决于当前 Claude 版本；安装成功不代表客户端已经加载，需要在新会话验证。后续普通 `python3 install.py` 会保留模块通路；可用 `python3 install.py --hook-transport command` 切回命令通路。

安装后重新打开 Claude Code 会话。桌面客户端若未加载新 hooks，可在当前任务结束后完全退出再打开，无需重启电脑。更新时再次安装：

```bash
git pull --ff-only
python3 install.py
```

Windows 在 PowerShell 中安装：

```powershell
git clone https://github.com/Yuwangzhi/claude-codex-loop.git
cd claude-codex-loop
python -X utf8 install.py
& "$env:USERPROFILE\.local\bin\ccl.cmd" --version
```

安装器会将用户 `.local/bin` 加到 Windows 用户 PATH 和 Git Bash 启动配置末尾，保留已有 CLI 的优先级。旧窗口的 PATH 不会热更新：重新打开终端和 Claude Code，或者先使用上面的绝对路径。Windows hooks 直接调用安装时的 Python，不依赖终端能否找到 `ccl`；skills 中的 shell 示例使用 Git Bash。npm 安装的 Codex 通过其 Node 入口启动，避免 `.cmd` 再次解析 JSON 参数。

## 安装内容与卸载

```text
~/.local/bin/ccl                              启动器
~/.local/bin/ccl.cmd                          Windows 原生命令入口
~/.local/share/claude-codex-loop/              Python 运行文件
~/.local/share/claude-codex-loop/desktop-plugin/ 可选 hooks module 与 Python 桥接配置
~/.claude/skills/work-mode/                   两模式入口
~/.claude/skills/codex-loop/                  Codex 闭环技能
~/.claude/settings.json                      command hooks 或模块目录配置
~/.claude/CLAUDE.md                          带标记的 Claude 指令
~/.codex/AGENTS.md                           带标记的 Codex 指令
~/.local/state/claude-codex-loop/backups/     备份与 manifest
```

五类事件为 SessionStart、UserPromptSubmit、PreToolUse、PostToolUse、Stop；PostToolUse 只处理模式选择的 AskUserQuestion。command 与 module 通路二选一，重复安装不重复注册 CCL 命令或模块路径。安装是用户级的，新会话在客户端实际加载该通路后应用模式规则。

卸载时先停止 CCL 任务，再删除 CLAUDE.md、AGENTS.md、SHARED_MEMORY.md 中 `BEGIN/END claude-codex-loop` 区块；从 settings 中仅移除指向 CCL 启动器的 hooks。使用模块通路时，还要从 `env.CLAUDE_CODE_PLUGIN_DIRS` 的路径列表中仅移除 CCL 的 `desktop-plugin` 路径，保留其他插件。删除两个 skills、启动器和运行文件（包括 `desktop-plugin`）。任务记录与项目记忆可保留。备份 manifest 包含模块文件与桥接配置；回退时逐项恢复，避免整体覆盖安装后新改过的设置。

Windows hooks 的命令直接指向 `ccl.py hook`，卸载时只移除这些项；还可移除 Git Bash 配置中 `# CCL user-local executables` 对应行。`.local/bin` 若还有其他工具，不要从 PATH 整体移除。Windows 安装备份额外包含用户 PATH 的原值。

高级变量：`CCL_STATE_DIR` 更改状态目录，`CCL_CONFIG_DIR` 更改共享配置目录，`CCL_CODEX_BIN` 指定 Codex 可执行文件。这些控制执行器运行时，不改变安装器位置；同一流程需使用一致的值。
