# 开发与验证

← [返回 README](../README.md)

```bash
python3 -X utf8 -m unittest discover -s tests -v
node --test tests/test_desktop_hooks.mjs
# Windows: python -X utf8 -m unittest discover -s tests -v
```

自动测试不需要模型登录，不发出付费模型调用。覆盖返修、检查失败、产物变化、预算、锁与中断、共享记忆、模式隔离、安装保留设置和失败回退。v0.3.0 在 **macOS 与 Windows 各 38/38 通过**，新增跨进程锁、无副作用 PID 查询、超时子进程终止及 PowerShell 模式门控检查；真实模型联调与自动测试的证据边界见 [VALIDATION.md](../VALIDATION.md)。

[GitHub Actions 工作流](../.github/workflows/tests.yml) 配置 macOS/Linux/Windows × Python 3.9/3.12 矩阵，运行 Python/已安装 CLI 集成测试及 Node 22 hook 测试；[示例](../examples/github-actions.yml) 与实际工作流一致。CI 不调用模型、不使用登录凭据，临时文件位于项目 tmp/。配置存在不等于云端运行通过，实际结果以 Actions run 为准。

```text
ccl.py                任务执行、验证、审查、记忆与 hook 入口
modes.py              session 模式状态与选择门控
runtime.py            Windows/POSIX 锁、进程检查和启动兼容层
diagnostics.py        只读安装诊断、进度与活动证据
governance.py         契约、累计预算和用量完整性
contextpack.py        上下文选择、新鲜度与遗漏清单
compatibility.py      离线 CLI 参数契约探测
install.py            备份、增量安装和失败回退
desktop-plugin/       可选 Claude hooks module 到 Python 执行器的桥接
claude-skill/         Claude 统筹 / Codex 执行规则
mode-skill/           两模式选择与收尾规则
examples/             可运行的任务样例
tests/                不调用真实模型的测试
```
