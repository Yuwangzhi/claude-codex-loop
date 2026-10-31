# v0.3.2：诊断、状态与恢复

← [返回 README](../README.md)

```bash
ccl doctor --project /absolute/project
ccl doctor --project /absolute/project --session SESSION_ID
ccl status TASK_ID
ccl status --project /absolute/project --full
ccl recover TASK_ID
```

- **doctor 默认只读**：检查本机 Python / Git / Codex / Claude CLI、安装版本、重复或禁用的 hooks、命令入口、模块文件和损坏任务记录。不联网检查新版、不读取或打印凭据、不启动模型、不自动修复。`healthy` 表示所检查项目没有 error，不等于一次真实模型任务已通过；发现 error 时退出码为 2，否则为 0（unknown 不伪装成通过）。
- **配置与加载分开报告**：`hook_transport` 说明配置；`hook_observation` 只报告指定 session 的历史 hook 证据。没有证据就是 `unknown`，不能把“已配置”说成“当前客户端已加载”。重开/重载客户端后，可使用实际 session id 再检查。
- **status 增加 progress**：当前阶段、从任务创建起的耗时（非执行状态冻结到最近活动）、最后日志/状态更新时间、空闲秒数、记录进程的存活情况和失败原因。文件更新不是模型心跳；PID 存活也不证明身份。`status --project` 保留列表格式，`--full` 额外返回 `record_issues`。
- **recover 默认只给建议**：区分正在运行、待验收、已有候选结果、需要返修和预算耗尽；不改状态、不启动第二个执行者。

检查命令无法启动或超时时，任务停在 `blocked`，不再自动消耗 Codex 返修次数。正常运行但断言失败的检查仍进入原有有界返修循环。环境问题解决后，如果最新一次 worker 已完整返回候选结果，可以只重跑原验收：

```bash
ccl recover TASK_ID --reverify --note '已检查上次产物、日志及外部副作用，验收工具已恢复'
```

**`--reverify` 不是只读**：会执行原任务中授权的 checks，检查命令可能修改文件或调用外部系统，先确认可以重跑。它不调用 Codex、不重置执行预算、不修改验收条件；通过后仍是 `awaiting_review`，需要实际审查后 `review accept`。无完整当前候选、任务已关闭、项目有活动 worker 或模式切换未完成时拒绝执行。

损坏记录不自动删除或覆盖：无关项目可以继续；受影响项目仍阻止重放，避免重复副作用。`doctor` 列出具体文件，应先保存原件并检查进程、日志及备份，再恢复记录；不能删除记录来绕过运行中任务或执行预算。只读诊断命令在模式/任务记录异常时仍可调用。

这一版同时修复 Git 子目录中未跟踪文件的内容校验，并在换 Python 重装时替换旧 CCL 命令 hooks（保留其他 hooks）。原有任务记录、模式偏好和授权范围保持不变。

### 桌面 hooks 排查与实测

若 `ccl mode status` 的 `hooks_seen` 为 false，先检查实际工作目录的 `.claude/settings.local.json`、`.claude/settings.json` 和用户 `~/.claude/settings.json` 中是否有 `disableAllHooks: true`。它同时关闭命令 hooks 和函数模块，重启或迁移通路都不能覆盖此设置。确认是误关后备份并移除该键，再 `/reload-plugins` 或重开会话。

本机新建桌面会话、历史会话恢复及未选择模式时沿用历史的验证见 [2026-10-25 实测记录](desktop-hooks-validation-20261025.md)。
