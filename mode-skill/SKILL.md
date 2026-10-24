---
name: work-mode
description: 在 Claude Code 开始任务、用户选择模式一或模式二、切换执行者、结束任务时管理模式。必须先由用户选择：模式一由 Claude 统筹 Codex 执行，模式二全部由 Claude 执行。任务完成后再次提醒下一项任务的模式选择。
---

# 两种执行模式

当前 session：`${CLAUDE_SESSION_ID}`。管理命令：`@CCL@ mode ... --session '${CLAUDE_SESSION_ID}'`。
Windows 中这些 shell 示例可用 Git Bash；PowerShell 使用同目录的 `ccl.cmd`（路径含空格时用 `& '完整路径/ccl.cmd'`）。无需 WSL。
UserPromptSubmit hook 识别用户开头的“模式一”“模式二”“用模式一/二”“我选模式一/二”，等待选择时也识别单独的 `1` 或 `2`。只有真实用户输入能选择；你不能替用户写状态或伪造 hook。原生 AskUserQuestion 的模式选择回答也由 PostToolUse hook 记录。

## 选择在执行之前

新会话未选模式时，用简短中文给用户选项并结束这一轮等待：

> 本次用哪种模式？
> 1. 模式一：Claude 定方案和验收，Codex 执行，节省 Claude 执行额度。
> 2. 模式二：全部由 Claude 规划、执行和检查。
> 回复 1 或 2 即可，也可以把模式和任务一起说。

此时不查项目、不改文件、不派发任务。可以先讨论目标、回答简短概念问题，但回复末尾仍给这两个选项。用户已经同条消息明确选择时不要再问；按 hook 里的状态继续。若用户只选了模式而没给任务，确认当前模式并请其描述任务，保留选择，不立刻关闭。

若使用 AskUserQuestion：问题文本要同时写“模式一”和“模式二”，选项标签分别以“模式一：”和“模式二：”开头。不要把未经用户提交的默认选项视为答案。若输入没有被解析，向用户请求明确回复“模式一”或“模式二”，不要自行用 shell 写入状态。

## 两条执行路径

先确定真实项目，运行：

```bash
@CCL@ mode begin --session '${CLAUDE_SESSION_ID}' --project /absolute/project --note '用户当前任务的简短目标'
@CCL@ context --project /absolute/project
```

- **模式一**：加载 `codex-loop` skill。Claude 规划、交接、验收；Codex 执行、自测、返修。每个 CCL 任务绑定当前 session。续接旧 session 的任务时先检查现场，然后 `@CCL@ adopt TASK_ID --session '${CLAUDE_SESSION_ID}'`，不要复制创建相同任务。
- **模式二**：由 Claude 使用自己的工具和适用技能完成规划、执行、检查与总结。可以使用 Claude 自己的子代理，但不调用 Codex CLI、Codex MCP 或 CCL 的 new/run。遵守用户现有授权与项目约束，不为模式二制造假的 Codex 执行记录。

同一任务的补充、返修、进度询问沿用本次选择；不要在每个工具调用或内部步骤重复问模式。需要用户补充信息时 `mode wait --session ... --note '缺少的信息'`，然后提问；下一条补充仍沿用原模式。任务结束与新目标不是一回事，未完成不能用“结束”冒充完成。

## 切换与中断

用户明确切换时按 hook 的 pending_mode 状态交接。若 Codex 仍在运行，先通过对应 Bash 后台任务的 TaskStop 停止并等待退出，检查已改内容并保留；使用 `ccl review TASK_ID cancel --note '用户切换到模式二，保留成果并交接'` 关闭旧记录，再 `ccl mode activate --session ...` 激活用户已经选好的模式。不要并发启动 Claude 和 Codex 修改同一工作区。进程未停或任务未关闭时程序拒绝激活。

模式二转模式一时，把当前完成内容、未完成项和检查结果交给 Codex，保留现有成果。窗口重开/压缩不会自动重跑；恢复同一 session 时可沿用其未完成任务，新 session 需重新选择。

## 共享记忆与收尾

两种模式都读取 `.ai-collab/MEMORY.md`；模式选择保存在 session 状态，不写进项目事实库，避免一个窗口影响另一个。

- 模式一：按 codex-loop 验收后使用 `ccl remember`。
- 模式二：检查产物/测试后，有值得保留的事实时使用 `@CCL@ note --session '${CLAUDE_SESSION_ID}' --project /absolute/project --text '已验证事实' --evidence '具体文件/检查结果'`。这是 Claude 自己的验证，不声称有 Codex 复核。

完成当前任务、记录需要的记忆后：

```bash
@CCL@ mode finish --session '${CLAUDE_SESSION_ID}' --note '完成内容、实际验证结果；或用户取消及当前现场'
```

程序要求 CCL 任务都已关闭，之后本次选择清空。最终回复先交付结果并说明本次使用的模式，末尾用一句话提示：

> 下一项任务用模式一（Claude＋Codex）还是模式二（仅 Claude）？回复 1 或 2。

不要立即执行下一项任务，不自动沿用上一模式，不为提示启动额外模型。没有后台任务时不创建定时提醒。打开空白界面时 SessionStart 可以提供启动提示，但 hooks 本身不生成一条主动对话；首条用户消息后必须给可见选项。
