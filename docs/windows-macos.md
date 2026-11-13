# Windows 与 macOS 的部署差异

两个平台运行同一套 Python 代码，但 hooks 通路、启动器和安装步骤不同。把一边的安装产物部分复制到另一边，会出现"只在 Windows 复现"的故障。

## 差异一览

| 项目 | macOS / Linux | Windows 原生 |
| --- | --- | --- |
| 启动器 | `ccl` POSIX shim | `ccl.cmd` 包装器（PowerShell 调用）；Git Bash 里仍用 `ccl` |
| 安装命令 | `python3 install.py` | `python -X utf8 install.py` |
| hooks 通路 | 默认 command hooks，可用 `--hook-transport module` 切换 | 同上；已启用 module 时 `CLAUDE_CODE_PLUGIN_DIRS` 指向 `desktop-plugin`，由 `hooks.mjs` 调用 `ccl.py hook` |
| Python 解释器 | 由 `python3` 解析 | 安装时固定的解释器，写入 `desktop-plugin/bridge.json` |
| 路径与编码 | POSIX 路径 | `.cmd`、Git Bash、UTF-8 状态文件与 Windows 文件锁 |

module 通路的插件清单 `desktop-plugin/.claude-plugin/plugin.json` 中的 `version` 必须等于 `ccl.py` 的 `VERSION`。`ccl doctor` 的 `module_files` 检查直接比较两者，不一致就报 `Bridge paths, plugin version and hook files need repair.`

## 2026-11-13 的 Windows 故障

现象：新会话里任意工具调用都被拒绝，理由是 `CCL 模式检查失败，已暂停本次工具执行；请检查 CCL 安装后重试。`，读取普通文件也一样。

实际原因不是安装缺失，而是安装目录内模块版本不一致：

- `ccl.py` 已更新到 0.4.3；
- `modes.py` 来自旧版本，没有 `ModeStore.recover_for_tool`；
- `plugin.json` 停在 0.4.2。

会话没有记录执行模式时，`PreToolUse` 会进入"从会话记录恢复模式"的分支，调用 `recover_for_tool` 抛出 `AttributeError`，hook 进程以非零码退出。`hooks.mjs` 对任何 hook 进程失败都返回同一句拒绝文案，模块异常因此显示成安装故障。

同一版本的验收在 macOS / Python 3.9 上完成；出问题的 Windows 部署里 `ccl.py` 与 `modes.py` 版本不同步，模块通路又由 `hooks.mjs` 统一包装异常，所以只在 Windows 暴露。

## 升级与修复规则

1. 同一版本整套替换：`ccl.py`、`modes.py`、`runtime.py`、`diagnostics.py`、`governance.py`、`contextpack.py`、`compatibility.py`，以及 `desktop-plugin/` 下的全部文件。不要只替换 `ccl.py`。
2. 替换前备份目标文件。
3. 替换后运行 `ccl doctor --project <项目> --session <会话>`，要求 `healthy: true` 且 `module_files` 为 ok。
4. 用探针验证 hook 能正常返回，不需要真实模型调用：

```bash
printf '%s' '{"hook_event_name":"PreToolUse","session_id":"probe","cwd":"<项目>","tool_name":"Read","tool_input":{"file_path":"x"}}' \
  | <安装时的 Python> -X utf8 ~/.local/share/claude-codex-loop/ccl.py hook
```

退出码 0 且输出 JSON 即为正常；输出 traceback 说明模块仍然不完整。首次探针会写入该 `session_id` 的状态，测完可删除 `~/.local/state/claude-codex-loop/sessions/probe*`。

5. 重开 Claude Code，让新的模块文件被加载。

## 排错顺序

1. 先运行 `ccl doctor --project <项目> --session <会话>`，区分安装检查失败与真正的权限拒绝。
2. 比较版本：`grep '^VERSION' ccl.py` 与 `cat desktop-plugin/.claude-plugin/plugin.json`。
3. 复查接口：`ccl.py` 调用的 `ModeStore` 方法必须在 `modes.py` 中存在；缺失就是模块没有同步。
4. 用上面的探针直接运行 hook。客户端只显示统一文案，Python 的 traceback 才会指出真正缺失的部分。
