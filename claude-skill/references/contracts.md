# v0.4：契约、交接、问题台账与累计预算

定义复杂任务上下文/预算、处理具体 finding 或中断时阅读。命令中的 `ccl` 是安装后的 CLI，Windows 也可用 `ccl.cmd`。

## 兼容性

`ccl doctor --compat` 在默认版本和安装检查之外，额外运行本机 Codex `exec --help`、`exec resume --help`，检查 CCL 需要的参数；不联网、开模型、读凭据或改配置。缺参数为 error；探测不可用为 unknown，不能算兼容通过；无法确认时退出 2。默认 doctor 行为不变。

help 不证明当前 CLI 的事件 schema、登录或工具能力。runner 解析真实事件，离线测试覆盖相应事件样例。`required_capabilities` 在交接中标 unknown，由执行者核实；桌面会话的浏览器/MCP 不自动传给 headless CLI。

## 可选 spec 字段

以下字段添加到原 spec，不是完整任务：

```json
{
  "context_files": ["docs/task-background.md"],
  "context_chars": 20000,
  "verification_files": ["tests/acceptance_export.py"],
  "required_capabilities": ["Python test runner"]
}
```

- 原 spec 以规范化 JSON SHA256 绑定。`verification_files` 额外绑定已存在的验收文件内容；运行前、检查前后、accept 都核对。修改验收文件需重新审定任务；未列出的测试文件不享有这一保护。
- `context_files` 全文必须进入上下文，不存在或超过 context_chars 时在 worker 启动前停止。目标、约束和验收条件不计入可选记忆字符预算，也不截断。
- 共享 Markdown 按标题分块，以任务词项相关性选取完整块；这是确定性词项匹配，不是语义检索。包内列明未装入内容及原因。
- 可选 `.ai-collab/context.json` 格式如下，所有路径相对项目，不允许越界：

```json
{
  "version": 1,
  "entries": [{
    "path": ".ai-collab/export-notes.md",
    "read_when": "CSV export 中文文件名",
    "source_hashes": {"src/export.py": "替换为当前文件的真实SHA256"}
  }]
}
```

源码哈希变化/文件缺失时，条目标 stale 并不注入。由统筹方重新核验笔记并更新哈希，不自动提升未经确认的经验。无索引时兼容现有 MEMORY 文件。

每轮保存 `attempt-NN/context.json`（实际内容）和 `handoff.json`（派发快照）。`ccl handoff TASK` 实时输出只读审查包：原任务、契约、Git 基线/当前指纹、检查与日志路径、产物、问题、上下文来源、预算和能力缺口。`evidence_current` 只说明记录匹配，不证明内容正确或已被接受。

## 问题台账

```bash
ccl finding TASK add --id F-001 --path src/export.py --severity high --problem '空数据无法导出' --evidence '复现输入和实际失败日志位置'
ccl finding TASK list
ccl finding TASK resolve --id F-001 --status fixed_verified --note '修复位置、检查结果和实际复验过程'
```

- 稳定 ID；重复添加相同问题不重置状态，同 ID 不允许改成另一问题。
- `accepted` = 问题成立、待处理，不是任务接受。
- `rejected` 要有反证；`deferred` 要有本次不处理的理由。两者是 reviewer 的明确决定，不伪装成已修复。
- `fixed_verified` 要求当前候选 awaiting_review、契约/产物/Git 指纹匹配、checks 通过，并有 reviewer 的复验说明。程序不能证明说明真实，也不能从通用测试推导某个具体 bug 必已修复。
- 文件、Git 快照或 spec 改变会使已结案项 stale/待处理；fixed_verified 另绑定具体 verification receipt，重新验收后需重新确认。list/handoff 只读，不偷偷改台账。
- 未解决或 stale finding 阻止任务 accept。关闭任务不能改台账；状态变化不自动启动 worker。

## 预算与停止

```bash
ccl new --project /project --spec task.json --max-seconds 5400 --max-tokens 100000
```

- max_attempts 仍默认 3，单次 timeout 默认 1800 秒。默认累计时间为尝试次数 ×（worker 超时 + 每轮 checks 超时之和），最多 7 天；可显式设 1..604800 秒。
- 时间累计 **CCL 启动的 worker 和检查子进程**，包括 reverify；不计人工等待、外层 Claude 对话或 CCL 未启动的 reviewer。启动前持久化操作，超时清理该调用的进程树。
- token 默认不限。设限后累计已观察 input + output（input 已包含缓存，不重复减缓存），在下一次 worker 前拦截，不是单次调用中的实时上限。最后一次调用可能超额。
- 失败/中断调用缺失用量记 unknown，设 token 限额时拒绝下一次 worker，不当成免费调用；已有候选仍可在剩余时间内无模型重验并接受。
- 硬中断后以已记录开始时间和调用上限保守结算，存活 worker 未排除前不重放。采用跨进程 UTC 和单次 monotonic 计时，不是外部计费账单。
- 连续两次失败检查内容和产物哈希相同、仍有次数时进入 no_progress；这仅检测一种确定性停滞，不推断全部语义进展。先查原因，再显式 revise；次数/预算不清零。
- recover、adopt、review、finding 不增加预算。不得删除记录或新建同一任务绕过停机；确有新计划才创建引用旧证据的新任务。

## 旧记录

旧任务保留 attempts/thread/历史，累计时间无法可靠补算，显示 scope=legacy / unknown。旧候选 accept 前须 `recover TASK --reverify --note '已检查并确认旧任务契约'` 绑定当前已审定 spec 并重验，不静默改写旧任务。v0.4 新记录必须包含完整契约/预算/台账，缺字段不能降级为 legacy。
