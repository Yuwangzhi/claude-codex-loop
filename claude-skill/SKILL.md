---
name: codex-loop
description: 当前执行方式为模式一时，由 Claude Code 规划、调度和验收，本地 Codex 执行任务并返修。模式未确定时先用 work-mode 读取用户选择或历史偏好；模式二不使用此执行器。
---

# Claude 统筹，Codex 执行

先确认 work-mode 状态为模式一，可以来自用户本次选择，也可以按约定沿用上次模式。状态缺失时按 work-mode 用 `ccl mode sync` 恢复真实选择或历史偏好；从无记录时才展示两种模式等待用户。模式二由 Claude 自己完成。用户只与你对话。模式一由你确定方向和验收标准，具体工作交给 Codex；不要先让多个 Claude 子代理重复探索/执行。同一任务内沿用已有选择和授权，不在每次交接时重新询问。

当前 Claude session id：`${CLAUDE_SESSION_ID}`。
CLI：`@CCL@`。它继承现有 Codex 登录和模型，不需要新 API key。

需要定义上下文/验收文件、设置累计预算、处理具体审查问题或旧任务恢复时，读 [契约、交接与预算](references/contracts.md)。v0.4 支持 `doctor --compat`、只读 `handoff TASK`、reviewer-owned `finding` 台账和 `new --max-seconds/--max-tokens`；不增加额外模型调用。

Windows：以下 heredoc 示例用 Git Bash/Bash 工具执行；如果只使用 PowerShell，请调用同目录的 `ccl.cmd`，用 PowerShell here-string 或 UTF-8 JSON 文件传任务。不要把 Bash heredoc 原样放进 PowerShell。CCL 支持原生 Windows，无需转入 WSL。

用户已要求自动执行：新任务默认 `approval_policy=never` + `sandbox_mode=danger-full-access`，首轮和返修相同。统一使用 ccl new/run，不另开交互式 Codex 终端，不加 `on-request`、`--full-auto` 或默认 `workspace-write` 覆盖该设置。用户明确要求只读/限制目录时才传 `--sandbox read-only|workspace-write`。任务内不逐命令要求 Yes/No。登录失效、操作系统授权、浏览器工具自己的确认要求仍需准确报告。旧任务记录和已运行的交互终端保留原权限，不在运行中更改或重复派发。

## 开始与规划

1. 确定真实项目目录（不能习惯性用 home 代替具体项目）。先运行 `@CCL@ context --project /absolute/project`，读取共享记忆与未关闭任务。继续原任务时用原 ID；旧 session 的任务经检查现场后用 `@CCL@ adopt TASK_ID --session '${CLAUDE_SESSION_ID}'` 接续，不重复派发。
2. 用现有上下文形成简短方案。大规模查找/通读/研究也交给 Codex，可先创建“调查并输出报告”任务，再根据证据决定实现方案。不要猜测未知代码位置。
3. 你可以读必要材料、写任务 JSON、审查产物、运行验收命令和更新确认后的共享记忆；代码/正文/图表等主要产物由 Codex 修改。必要的技术取舍由你决定，不为每个小判断反问用户。
4. 将一个完整可验收单元交给 Codex，包含原始目标、范围、计划、约束、产物、验收标准。请 Codex 自己跑测试和返修，再返回摘要。小事不拆成多个往返。

## 创建与执行

用带引号的 heredoc 输入 JSON，避免任务文字被 shell 展开。`checks[].argv` 是参数数组，只有你明确需要 shell 语法时才显式调用 shell。验收命令由你制定，由本地程序实际执行；它们用当前用户权限，必须在用户授权范围内。模型提出的命令不自动成为检查。

```bash
@CCL@ new --project /absolute/project --owner-session '${CLAUDE_SESSION_ID}' --spec - <<'CCL_SPEC'
{
  "title": "简短任务名",
  "goal": "用户的完整目标与明确授权",
  "plan": "实施方案；必要时引用项目内的详细计划路径",
  "acceptance": ["可观察的完成条件"],
  "constraints": ["允许修改的路径/范围；保留既有未提交工作；外部写入须符合已有授权"],
  "artifacts": ["relative/output.md"],
  "checks": []
}
CCL_SPEC
@CCL@ run TASK_ID
```

示例里的占位目标和路径必须换成真实内容。对于代码任务加入项目适用的测试/构建 `checks`，例如 `{"name":"单元测试","argv":["python3","-m","unittest","discover","-s","tests"],"timeout":300}`。分析/视觉任务可以 `checks: []`，但要有报告/图片/PDF 等真实产物，由你实际阅读/渲染验收，不能把“文件存在”当成质量合格。

默认单项目串行、每个任务最多 3 次 Codex 执行（包含你要求的返修），每次最长 30 分钟，并受累计执行时间约束。程序把失败的验收结果交回同一 Codex thread；相同失败和产物连续出现时停在 no_progress。身份验证、进程错误、工具缺失不会无限重试。不要调用 `resume --last`，不要同时操作同一项目。长任务用 Bash 的 `run_in_background` 和完成通知/TaskOutput 等待，等待期间不频繁向模型查询空状态。

## 闭环验收

`@CCL@ status TASK_ID` 返回状态、简短结果、检查、sha256、progress（阶段、耗时、最后日志/状态活动、进程探测）及工具调用数/usage所在的记录路径。文件更新不是模型心跳，PID 存活不证明进程身份。详情按需读 `status TASK_ID --full` 或 records 下的文件，不把完整事件日志塞回对话。

安装或 hooks 异常时先用 `@CCL@ doctor --project /absolute/project`；有真实 session id 时加 `--session SESSION_ID` 查看历史 hook 证据，不把配置成功等同于当前客户端已加载。`@CCL@ recover TASK_ID` 只读分析恢复步骤，不自动重跑任务。损坏记录保留原件；无关项目不受牵连，受影响项目须检查进程和备份后恢复，不能删除状态绕过预算。

验收工具无法启动或超时会停止自动返修。解决原因且最新 worker 已完整交付后，可用 `@CCL@ recover TASK_ID --reverify --note '检查现场和恢复环境的依据'` 仅重跑原验收，不调用 Codex、不重置预算。此选项会执行 checks，不是只读；先确认检查的文件/外部副作用可重复。通过后仍须人工验收，不能直接称为完成。

- `awaiting_review`：自动检查通过，仍待你验收。查看实际 diff/产物和检查日志，核对原始目标与授权。重要视觉产物需要实际渲染或打开查看；外部任务需要相应外部状态证据。程序会在接受时再次核对产物指纹和 Git 状态。
- 合格：`@CCL@ review TASK_ID accept --note '实际看了哪些文件/结果，为什么满足哪些验收条件'`。有用经验写入记忆后，按 work-mode 的 mode finish 关闭本次任务状态、保留历史模式，再向用户报告结果并提示“下一项任务可选 1 或 2，不选就沿用本次模式”。
- 有具体问题：`@CCL@ review TASK_ID revise --note '问题位置、证据和修复要求'`，再 `@CCL@ run TASK_ID`。无需让用户再次批准已授权的修复。
- `blocked/execution_failed/interrupted/budget_exhausted/no_progress`：看证据并判断能否解决。预算耗尽后先改变计划，确有必要才新建任务并引用旧记录，不能通过新建任务绕过停机条件。用户决定或权限缺失时明确说明具体阻碍。不能把未验收称为已完成。
- 用户取消：`@CCL@ review TASK_ID cancel --note '用户取消/变更目标'`。运行中先终止并等待对应 Bash 后台任务退出；不要盲杀全部 Codex 进程。

## 共享记忆

双方读同一份项目 `.ai-collab/MEMORY.md`；通用约定在 `~/.config/claude-codex-loop/SHARED_MEMORY.md`。Codex 每次派发/返修都会收到相关内容，无需共享私有聊天记录。

Codex 的 `memory_proposals` 是建议，不自动写入事实库。任务已接受且有值得复用的事实时，你用：

```bash
@CCL@ remember TASK_ID --text '经验证的项目事实/决定与适用条件' --evidence '文件路径、检查结果或对应任务证据'
```

不要记录凭据、未验证猜测、整段日志或用户无关信息；不要覆盖两侧原生 memory。此文件服务于项目连续性，不等于模型内部记忆完全互通。

## 能力与恢复

Codex CLI 的工具不一定与 Codex 桌面端相同。需要的浏览器、插件、登录或 UI 能力缺失时，让 Codex 准确回报；你可在已有授权内补做仅你可用的能力并说明分工变化，不许虚构成功。CCL 按用户要求以当前账户完整本机权限执行；外部操作仍须符合任务已有授权。

关闭 Claude 后不会后台自动调用 Claude。Codex 已写出的任务记录仍在；重新打开后通过 `context/status` 接续。运行被硬中断后先检查该项目进程与结果，再恢复原任务，避免重复外部副作用。
