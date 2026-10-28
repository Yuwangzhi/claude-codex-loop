---
name: work-mode
description: 在 Claude Code 开始任务、选择或切换模式、结束任务时管理执行方式。模式一由 Claude 统筹 Codex 执行，模式二全部由 Claude 执行；未指定时沿用上次模式，从无选择记录时询问用户。
---

# 两种执行模式

当前 session：`${CLAUDE_SESSION_ID}`。管理命令：`@CCL@ mode ... --session '${CLAUDE_SESSION_ID}'`。
Windows 中这些 shell 示例可用 Git Bash；PowerShell 使用同目录的 `ccl.cmd`（路径含空格时用 `& '完整路径/ccl.cmd'`）。无需 WSL。
UserPromptSubmit hook 识别 `1`、`2`、`一`、`二`、`模式1`、`模式二`，也接受“二吧”“就用1”等日常选择表达。普通任务里的数字、“看一下”等词，以及同时提到两个模式但未确定选择的文字，不应被猜成模式一或模式二。原生 AskUserQuestion 的模式选择回答也由 PostToolUse hook 记录。显式选择来自真实用户输入；未指定时由程序按用户约定读取上次模式。不要自行写状态或伪造 hook。

## 确认模式后执行

用户同条消息指定模式时直接采用，不重复询问。新任务未指定时，优先沿用当前 session 的上次模式；当前 session 没有记录时采用用户最近一次明确选择的模式。用一句可见提示说明默认值并继续执行，例如：

> 本次沿用模式二（Claude 执行）；回复 1 可切到 Claude＋Codex，回复 2 保持当前模式。

有历史模式时，不用阻塞式 AskUserQuestion 等待一次可省略的选择。只有从无选择记录时，展示选项并结束这一轮等待：

> 本次用哪种模式？
> 1. 模式一：Claude 定方案和验收，Codex 执行，节省 Claude 执行额度。
> 2. 模式二：全部由 Claude 规划、执行和检查。
> 回复 1、2、一、二、模式1、模式二都行，也可以把模式和任务一起说。

首次没有可用模式时，不查项目、不改文件、不派发任务。可以先讨论目标、回答简短概念问题。若用户只选了模式而没给任务，确认当前模式并请其描述任务，保留选择，不立刻关闭。没有新任务输入时不后台制造工作，也不因等待时间变长自动选择或关闭任务。

若使用 AskUserQuestion：问题文本要同时写“模式一”和“模式二”，选项标签分别以“模式一：”和“模式二：”开头。不要把未经用户提交的默认选项说成用户的显式选择。

用户已经选择但 hook 未记录，或桌面端没有传入模式状态时，先运行 `@CCL@ mode sync --session '${CLAUDE_SESSION_ID}'`。它从真实会话记录恢复选择，没有新选择时按规则沿用历史模式；不要用模型编写的文本替代真实输入。同步后按返回状态执行。只有确实没有可恢复的选择或历史模式时才询问，无法读取会话记录时报告具体错误。

如果是安装/任务记录错误，而非缺少模式选择，用 `@CCL@ doctor --project /absolute/project --session '${CLAUDE_SESSION_ID}'` 只读诊断。它区分配置与历史加载证据，不自动改状态、清理记录或调用模型。`@CCL@ recover TASK_ID` 只读提供恢复建议；带 `--reverify` 则会执行原 checks，需先检查副作用，并受模式一和项目锁约束。

## 两条执行路径

先确定真实项目，运行：

```bash
@CCL@ mode begin --session '${CLAUDE_SESSION_ID}' --project /absolute/project --note '用户当前任务的简短目标'
@CCL@ context --project /absolute/project
```

- **模式一**：加载 `codex-loop` skill。Claude 规划、交接、验收；Codex 执行、自测、返修。每个 CCL 任务绑定当前 session。续接旧 session 的任务时先检查现场，然后 `@CCL@ adopt TASK_ID --session '${CLAUDE_SESSION_ID}'`，不要复制创建相同任务。
- **模式二**：由 Claude 使用自己的工具和适用技能完成规划、执行、检查与总结。可以使用 Claude 自己的子代理，但不调用 Codex CLI、Codex MCP 或 CCL 的 new/run。遵守用户现有授权与项目约束，不为模式二制造假的 Codex 执行记录。

同一任务的补充、返修、进度询问沿用本次选择；不要在每个工具调用或内部步骤重复问模式。需要用户补充信息时 `mode wait --session ... --note '缺少的信息'`，然后提问；下一条补充仍沿用原模式。任务结束与新目标不是一回事，未完成不能用“结束”冒充完成，也不能按时间间隔把补充消息当成新任务。

## 切换与中断

用户明确切换时按 hook 的 pending_mode 状态交接。若 Codex 仍在运行，先通过对应 Bash 后台任务的 TaskStop 停止并等待退出，检查已改内容并保留；使用 `ccl review TASK_ID cancel --note '用户切换到模式二，保留成果并交接'` 关闭旧记录，再 `ccl mode activate --session ...` 激活用户已经选好的模式。不要并发启动 Claude 和 Codex 修改同一工作区。进程未停或任务未关闭时程序拒绝激活。

模式二转模式一时，把当前完成内容、未完成项和检查结果交给 Codex，保留现有成果。窗口重开/压缩不会自动重跑；恢复同一 session 时可沿用其未完成任务。新 session 可按历史偏好确定默认模式，接续旧任务仍须检查现场并使用 adopt。

## 共享记忆与收尾

两种模式都读取 `.ai-collab/MEMORY.md`；执行状态按 session 隔离，最近明确选择作为默认偏好单独保存。其他窗口的新选择不会切换当前未完成任务。模式状态和偏好不写进项目事实库。

- 模式一：按 codex-loop 验收后使用 `ccl remember`。
- 模式二：检查产物/测试后，有值得保留的事实时使用 `@CCL@ note --session '${CLAUDE_SESSION_ID}' --project /absolute/project --text '已验证事实' --evidence '具体文件/检查结果'`。这是 Claude 自己的验证，不声称有 Codex 复核。

完成当前任务、记录需要的记忆后：

```bash
@CCL@ mode finish --session '${CLAUDE_SESSION_ID}' --note '完成内容、实际验证结果；或用户取消及当前现场'
```

程序要求 CCL 任务都已关闭，之后关闭当前任务状态并保留上次模式。最终回复先交付结果并说明本次使用的模式，末尾用一句话提示：

> 下一项任务可选 1（Claude＋Codex）或 2（仅 Claude）；不选就沿用本次模式。

等待用户给出下一项任务；用户没有指定新模式时沿用历史模式继续。不为模式提示启动额外模型，没有后台任务时不创建定时提醒。打开空白界面时 SessionStart 可以提供上下文，但 hooks 本身不生成主动对话；收到任务后给出可见的模式提示。
