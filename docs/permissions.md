# 权限：为什么不会逐条要求 Yes / No？

← [返回 README](../README.md)

CCL 统一使用非交互的 `codex exec`，新任务默认显式传入：

```text
approval_policy = "never"
sandbox_mode = "danger-full-access"
```

**这是当前用户账户的完整本机访问权限：模型生成的命令不再受 Codex 工作区沙箱限制，也不会逐条请求执行批准。** 只在你愿意授予这种访问的环境使用。它不授予 root/管理员身份，不等于允许任意发布、发送消息或破坏数据；任务范围仍必须明确。

需要限制单个任务时：

```bash
ccl new --project /absolute/project --spec task.json --sandbox workspace-write
ccl new --project /absolute/project --spec task.json --sandbox read-only
```

两种限制仍配合 `approval_policy=never`：超出权限时失败或报告阻塞，不通过弹窗升级权限。首次执行和返修沿用任务记录里的设置；旧任务和已启动进程不会自动改变权限。

Claude 调用 Bash 是**另一层权限**。希望 Claude 也不逐条询问时，可在 Claude Code 中选择 Bypass permissions，或按[官方说明](https://code.claude.com/docs/en/permissions)配置 `permissions.defaultMode: "bypassPermissions"`。安装器保留已有值。系统登录、OTP、操作系统授权和浏览器工具自身的强制确认不由 CLI 开关取消。

不要另开带 `on-request` 或 `--full-auto` 的交互终端覆盖预期权限。GitHub 登录失败也不是提高沙箱权限就能解决的问题。
