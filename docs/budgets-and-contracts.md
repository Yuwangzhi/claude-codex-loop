# v0.4.0：明确交接、问题和累计预算

← [返回 README](../README.md)

保留原两种模式、同 thread 有限返修与最终验收，不引入新模型、daemon 或第三方 Python 依赖。

| 能力 | 入口 | 边界 |
| --- | --- | --- |
| CLI 参数兼容检查 | `ccl doctor --compat` | 额外运行本机 help；不联网/开模型，不据此宣称登录、事件 schema 或工具可用 |
| 任务契约 | spec SHA256 + 可选 `verification_files` | 检查前后/接受前核对，不允许改标准后复用旧证据 |
| 结构化交接包 | `ccl handoff TASK` | 只读原目标、当前快照、检查/产物、问题、预算与上下文索引 |
| 上下文装配 | `context_files/context_chars` + 可选 `.ai-collab/context.json` | 必需文件不截断；可选块按词项相关性选择，明确列出遗漏/过期 |
| 问题台账 | `ccl finding TASK add/list/resolve` | 问题成立、驳回、延期、修复且复验分开；未解/过期项阻止 accept |
| 累计预算 | `new --max-seconds N --max-tokens N` | CCL worker/check 执行时间；token 为调用间准入，不是中途截断或全账号账单 |
| 无进展停止 | `no_progress` | 连续相同失败检查和产物哈希时停止自动返修 |

完整字段、命令和旧任务兼容规则见 [契约、交接与预算](../claude-skill/references/contracts.md)。旧任务历史时间显示 unknown，不伪造补算；旧候选须显式重验后绑定契约。

```bash
ccl doctor --compat
ccl new --project /absolute/project --spec task.json --max-seconds 5400 --max-tokens 100000
ccl run TASK
ccl handoff TASK
ccl finding TASK add --id F-001 --path src/export.py --problem '复现的问题' --evidence '实际失败证据'
# 修复、检查通过并实际审查后：
ccl finding TASK resolve --id F-001 --status fixed_verified --note '复验过程与结果'
ccl review TASK accept --note '实际检查过的结果与验收依据'
```

`fixed_verified` 仍需 reviewer 的真实审查，通用测试通过不自动证明每个问题已修好。所有 budget/recovery/ledger 操作不自动新增模型调用、不清零既有预算。
