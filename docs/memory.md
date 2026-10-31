# 共享记忆

← [返回 README](../README.md)

双方读取**同一组明确的文件**，不会自动合并私有聊天历史或模型内部记忆。

| 内容 | 默认位置 | 写入方式 |
| --- | --- | --- |
| 项目事实、决策与验证依据 | `PROJECT/.ai-collab/MEMORY.md` | 模式一验收后 `ccl remember`；模式二检查后 `ccl note` |
| 通用协作规则 | `~/.config/claude-codex-loop/SHARED_MEMORY.md` | 安装器维护标记区块，保留其他内容 |
| 可选本机访问说明 | `~/.config/claude-codex-loop/ACCESS_HANDOFF.md` | 用户自行提供；不存在时跳过 |
| 当前模式与任务状态 | `~/.local/state/claude-codex-loop/sessions/` | 按 session 隔离，保留该 session 的上次模式 |
| 默认模式偏好 | `~/.local/state/claude-codex-loop/mode-preference.json` | 保存用户最近明确选择，仅作参考提示；不再决定新任务的模式，也不写入项目事实记忆 |
| 执行与审查记录 | `~/.local/state/claude-codex-loop/projects/` | 保存任务、尝试、检查和审查记录 |

Codex 的 `memory_proposals` 只是候选知识，由 Claude 验收后决定是否记录。不要放入 token、密码、完整日志或未经验证的猜测。本仓库忽略 `.ai-collab/`；其他项目如需保持记忆私有，也应加入其 `.gitignore`。
