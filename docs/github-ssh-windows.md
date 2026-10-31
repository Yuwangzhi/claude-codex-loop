# GitHub、SSH 与 Windows

← [返回 README](../README.md)

CCL 使用调用进程已有的 PATH、网络和登录环境，不附带个人凭据，不复制登录，也不自动配置远程机器。每台执行机器应独立验证：

```bash
gh auth status
gh api user --jq .login
gh auth setup-git --hostname github.com
git ls-remote https://github.com/OWNER/REPOSITORY.git HEAD
ssh -o BatchMode=yes YOUR_HOST whoami
```

`gh auth setup-git` 会将 GitHub HTTPS 的 Git 凭据助手配置为 GitHub CLI。其他托管服务需各自认证。Mac 可访问不代表 Windows 的 token 或 SSH 会话有效；在**实际执行任务的账号与 shell**里验证，不把 token 填进任务、remote URL 或日志。

Windows 原生现在可以安装完整 CCL；推荐由 Claude 使用 `ccl new/run` 获得同样的状态、独立检查和共享记忆。下面是额外的直接 CLI 调用方式，这种手动通路本身不包含 CCL 闭环：

```bash
# Claude 调用 Codex 的基本形式
codex exec -c 'approval_policy="never"' -c 'sandbox_mode="danger-full-access"' '明确的任务与验收条件'

# 独立 Codex 会话经用户要求后调用 Claude 的基本形式
claude -p --permission-mode bypassPermissions '明确的任务；不要再次反向委派'
```

CCL worker 不允许再反向调用 Claude，避免无限互调。不同客户端的工具集、浏览器能力和插件可能不同；权限开放不等于能力齐全。
