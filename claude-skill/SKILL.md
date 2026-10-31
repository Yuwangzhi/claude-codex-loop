---
name: codex-loop
description: 当前执行方式为模式一时，由 Claude Code 规划、调度和验收，本地 Codex 执行任务并返修。用户指定模式一，或任务多文件/长耗时/需要独立验收而你自选模式一时使用；模式二不使用此执行器。
---

# Claude 统筹，Codex 执行

用户只与你对话。你定方向和验收标准，具体工作交给 Codex；不要先让多个 Claude 子代理重复探索。同一任务内沿用已有选择和授权，不重复询问。

- Claude session：`${CLAUDE_SESSION_ID}`；CLI：`@CCL@`（继承 Codex 登录，不需要新 API key）。
- **选模式不用问用户**：用户指定就照办；没指定，多文件/长耗时/批量/需独立验收的任务直接 `ccl new`，系统自动记为模式一。用户明确选了模式二时系统会拒绝派发，你自己做。
- 默认 `approval_policy=never` + `sandbox_mode=danger-full-access`，首轮与返修一致；不另开交互式 Codex，不加 `on-request`/`--full-auto`。用户明确要求只读/限目录才传 `--sandbox`。已授权任务内不逐条问 Yes/No。
- Windows：Bash 工具里用 `ccl`，PowerShell 里用 `ccl.cmd`；Bash heredoc 不能原样放进 PowerShell。无需 WSL。

## 一、开始前

1. 确定真实项目目录（不要用 home 代替）。`@CCL@ context --project /abs/project` 读共享记忆与未关闭任务。续接旧任务用原 ID；旧 session 的任务检查现场后 `@CCL@ adopt TASK_ID --session '${CLAUDE_SESSION_ID}'`，不重复派发。
2. 大规模查找/通读/研究也交给 Codex：先建“调查并输出报告”任务，再按证据定方案。不猜测未知代码位置。
3. 把一个完整可验收单元交给 Codex（目标、范围、计划、约束、产物、验收），让它自己跑测试和返修。小事不拆成多个往返。

## 二、任务模板（固定格式，照填）

Codex 实际收到的提示词由程序固定生成，你不手写：开头一段固定规则（你是执行 worker；Claude 负责决策和验收；缺工具/登录/权限就返回 blocked，不伪造；不改任务状态/检查/共享记忆/凭据；未经授权不 commit/push/发布；最多 1200 字符的结构化摘要），后面依次拼接 **spec → 共享记忆 → 累计预算 → 问题台账 → 能力要求 → 返修意见**（首轮为 `Initial execution.`，返修为你写的 `review revise --note`）。Codex 必须按固定 schema 返回 `outcome / summary / artifacts / verification / memory_proposals / blockers`。所以 spec 写得越具体，Codex 越不会猜。

**spec 用 Write 工具写成 UTF-8 的 .json 文件，再 `--spec 文件` 传入。** 不要用 heredoc 内联：Windows 路径里的反斜杠、引号会让 JSON 报 `Invalid \escape`。路径一律用 `/`。模板见 [references/spec-template.json](references/spec-template.json)，字段规则：

| 字段 | 要求 |
|---|---|
| title / goal / plan | 非空字符串。goal 写用户原始目标和已授权范围；plan 写实施步骤和需要的环境（Python 解释器、工具路径） |
| acceptance | 非空数组，写**可观察**的完成条件 |
| constraints | 数组（可空）：允许修改的路径、保留未提交工作、不得 commit/push、外部写入范围 |
| artifacts | **非空**数组，项目内相对路径；纯分析任务也要有报告文件 |
| checks | `{"name","argv":[...],"timeout"}`，由程序实际执行；argv 是参数数组，不是 shell 串 |
| verification_files | 你写的验收脚本，Codex 不得改；见下 |
| context_files / required_capabilities | 可选，见 references/contracts.md |

```bash
@CCL@ new --project /abs/project --owner-session '${CLAUDE_SESSION_ID}' --spec /abs/path/spec.json
@CCL@ run TASK_ID      # 用 run_in_background 执行，完成会通知你，期间不轮询
```

### 验收脚本先自检再派发

验收脚本是你写的，错了会白烧一次 Codex（任务停在 blocked，且没有完整候选，无法 `recover --reverify`）。派发前必须：

- 用目标解释器跑 `-m py_compile`；程序也会在 `new` 时对 `verification_files` 和 `checks` 里的 `.py` 做编译预检，不过就拒绝创建。
- 不要在脚本里断言你没亲眼见过的输出（如猜 `--version` 含某个词）；先手动跑一次被测命令。
- 脚本里含 Windows 路径的字符串，用 `chr(92)`、原始字符串或 `/`，不要写 `"C:\Users"`。

代码任务加项目的测试/构建 checks；分析/视觉任务可 `checks: []`，但要有真实产物并由你实际阅读/渲染验收，不能把“文件存在”当合格。

默认单项目串行、最多 3 次 Codex 执行，每次最长 30 分钟，并受累计时间约束。不要用 `resume --last`，不要同时操作同一项目。

## 三、验收

`@CCL@ status TASK_ID` 返回状态、检查、sha256、progress；详情读 `status TASK_ID --full` 或 records 文件，不把完整日志塞回对话。

- `awaiting_review`：自动检查通过只是待你验收。看实际 diff/产物/检查日志，核对原始目标与授权；视觉产物必须渲染查看。合格：`@CCL@ review TASK_ID accept --note '看了哪些文件/结果，为何满足哪些条件'`；有具体问题：`@CCL@ review TASK_ID revise --note '位置、证据、修复要求'` 再 `run`，无需让用户再批准。用户取消：`review TASK_ID cancel --note ...`。
- 有价值的经验：`@CCL@ remember TASK_ID --text '已验证事实' --evidence '路径/检查结果'`。不记凭据、猜测、整段日志。
- 完成后 `@CCL@ mode finish --session '${CLAUDE_SESSION_ID}' --note '完成与验证依据'`（不 finish 也会在回合结束自动保持当前模式，不会拦你）。最终回复先交付结果，一句话说明用了哪种模式，不必邀请用户选下一项模式。

## 四、失败速查（先查这里，不要反复试）

| 现象 | 原因 | 处理 |
|---|---|---|
| `Invalid \escape` / JSON 报错 | heredoc 里的反斜杠 | 用 Write 写 .json 文件，路径用 `/` |
| `Acceptance criteria and tangible artifact paths are required` | artifacts 为空 | 至少列一个产物或报告路径 |
| `Acceptance script does not compile` | 你的验收脚本语法错 | 修好再 `new` |
| `execution_failed`，exit `3221225794`(0xC0000142)、stderr 空 | Windows 上 Codex 进程没启动 | 程序已自动重试 2 次（不占 attempt）；仍失败再 `ccl run ID` 一次，还不行看 `ccl doctor --compat` |
| `execution_failed`，stderr 有内容 | 登录失效/CLI 报错 | 读 stderr 如实报告，登录类需用户处理 |
| `blocked` 且 blockers 指向你的检查或脚本 | 验收契约有缺陷 | 修脚本后 `review revise` 让 Codex 同线程复验；若产物已完整，自己独立跑检查并在备注写明后 `cancel`，不能称作 accept |
| `blocked`（验证环境/超时） | 检查工具缺失或超时 | 解决原因后 `recover ID --reverify --note '依据'`（仅当存在完整候选；会执行 checks） |
| `no_progress` / `budget_exhausted` | 连续相同失败 / 预算用尽 | 先改变计划再 `review revise`，不能新建任务绕过 |
| `Current choice is 模式二…` | 用户明确选了模式二 | 不派发，自己做 |
| 每次启动 `codex: CONNECTION_CLOSED` | `~/.mcp.json` 里残留已废弃的 `codex mcp-server` | 删掉该项（备份）；不要重建 |

## 五、环境与恢复

- 先 `@CCL@ doctor --project /abs/project --session '${CLAUDE_SESSION_ID}'`（只读）判断安装/hook 问题；`recover TASK_ID` 只读给恢复建议。
- Codex CLI 的工具不一定与桌面端相同；缺浏览器/插件/登录时让 Codex 如实回报，你可在已有授权内补做仅你可用的部分并说明分工，不虚构成功。
- 关闭 Claude 后不会后台调用 Claude；任务记录仍在，重开后用 `context/status` 接续。硬中断后先检查进程与结果再恢复，避免重复外部副作用。
- 共享记忆：项目 `.ai-collab/MEMORY.md` 与 `~/.config/claude-codex-loop/SHARED_MEMORY.md`；Codex 的 `memory_proposals` 只是建议，由你验收后写入。

细则（可选 spec 字段、finding 台账、累计预算、旧记录）见 [契约、交接与预算](references/contracts.md)。
