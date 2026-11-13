# v0.4.3 模式恢复验证（2026-11-13）

## 根因与修复

1. 宿主恢复/分叉后可以更换 session ID，而已有 skill 上下文仍携带旧 ID。旧版 PreToolUse 只读取新 ID 的空状态，不恢复历史偏好，导致对旧 ID 的 `mode sync` 成功也无法解除实际拦截。现在工具入口从当前 session 的真实会话记录恢复选择，找不到显式选择时沿用已有偏好；拒绝信息包含真实 hook session。
2. 原生模块在 `prompt.submit` 的 `next(event)` 返回后才拼接提示。此时 prompt 已经入队，返回值中的新文本无法向模型注入状态。现在在向下传递前追加 `context`，保留用户原文、既有 context 和下游 drop。
3. 裸数字解析匹配整个 prompt，导致 `2，然后执行任务` 被忽略。现在只解析选择分句，仍要求菜单/默认模式的选择上下文，不扫描任务中的普通数字。
4. 模块迁移到原生事件后遗失了 AskUserQuestion 的完成回调。现在在 `tool.call` 的真实执行结果之后记录匹配的模式问题；被拒绝或失败的调用不写选择。
5. Python 运行时与插件 manifest 的版本分别停在 0.4.0 和 0.4.2，doctor 报版本漂移。统一为 0.4.3，安装测试直接比较二者，避免再出现硬编码测试版本不同步。

## 验证命令与结果

```bash
python3 -m unittest discover -s tests -v
node --test tests/test_desktop_hooks.mjs
claude plugin validate desktop-plugin
git diff --check
```

macOS / Python 3.9 / Claude Code 2.1.323：

- Python：131 个测试，130 通过，1 个 Windows npm shim 专用测试跳过。
- Node：11 个测试通过，包括真实 Python 子进程桥接的 session 重绑定恢复。
- 插件 manifest 与原生模块验证通过；未提供插件目录上架图标的提示不影响本地加载。
- 差异空白检查通过。

测试没有调用真实 Claude/Codex 模型，不以本机结果声称 Windows、Linux 或远端 CI 已通过。原生适配测试使用真实 hook Python 进程，但宿主事件由测试驱动，不能替代新开客户端会话的端到端验证。

## 关键边界

- SessionStart 只提示，不凭空启动任务。
- 完全没有选择历史的首次工具执行仍被拦截。
- 显式会话选择优先于全局偏好；其他会话改变偏好不会切换正在执行的任务。
- 已完成的模式任务，必须有 `finished_at` 之后的真实用户输入才能在工具入口恢复；后台通知、旧输入及 meta 消息均不能重开。
- 模式二仍拒绝 Codex 派发；pending 模式交接不被自动恢复覆盖。
- 桥接失败、非法或截断 JSON 仍阻止工具执行；下游工具/权限拒绝保持原样。
- 并发工具共用一次初始化；注册间及不同 session 的初始化状态分离。

## 本机安装策略

已有运行目录可能包含仓库外的本地扩展。此次部署对 `ccl.py` 定点应用版本、UserPromptSubmit 来源检查及 PreToolUse 恢复修改，替换与仓库一致的 `modes.py` 和模块文件，并同步 work-mode 的诊断说明；先备份，保留其他扩展和设置。不在公开仓库提交本机状态、会话记录、私有配置或凭据。

部署后 `ccl --version` 为 0.4.3；模式 hook 的 AST 与通过测试的源码一致，模块文件逐字一致。本机额外的表达规范回归 13 项通过，扩展文件哈希不变。doctor 的运行版本、模块文件和 hook 观测检查通过；它仍报告一个预先存在的不完整历史任务记录，未删除、伪造修复或将整体 healthy=false 描述成通过。当前会话没有重启，因此模块下次宿主加载的端到端行为仍需与上述离线验证区分。
