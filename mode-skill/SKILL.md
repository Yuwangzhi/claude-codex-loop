---
name: work-mode
description: 在 Claude Code 开始任务、切换模式、结束任务时管理执行方式。用户明确指定模式一/二就严格照办；没指定由 Claude 按任务难度自己选，不询问、不等确认。
---

# 两种执行模式：用户说了照办，没说你来选

当前 session：`${CLAUDE_SESSION_ID}`。管理命令：`@CCL@ mode ... --session '${CLAUDE_SESSION_ID}'`。Windows 的 Bash 工具用 `ccl`，PowerShell 用 `ccl.cmd`。

- **模式一**：Claude 规划和验收，Codex 执行（加载 `codex-loop`，`ccl new/run`）。
- **模式二**：全部由 Claude 完成，不调用 Codex。

## 怎么定模式（不要问用户）

1. **用户明确指定** → 严格照办。hook 认 `1`、`2`、`一`、`二`、`模式1`、`模式二`、`mode 2`、`moshi 2`、“二吧”“就用1”“用模式二做”等；同条消息里带任务也行。疑问句（“模式二是什么”）、否定句、普通数字（“修复 2 个错误”）不算选择。
2. **没指定** → 你按任务难度自己选，开工时用一句话告知所选模式及理由，并说一句“回复 1/2 可改”：

   | 倾向模式二（自己做） | 倾向模式一（交给 Codex） |
   |---|---|
   | 问答、解释、调研、简短分析 | 多文件改动、跨模块重构 |
   | 单文件/几处小改、配置、部署、登录、同步 | 长耗时、批量处理、反复跑测试 |
   | 几步内能做完且无需独立验收 | 需要 Codex 独立执行 + 你独立验收 |

3. **同一任务**的补充、返修、进度询问沿用已选模式，不重复判断。新任务难度明显不同，且模式是你自己选的（不是用户明确指定的），用 `mode begin --mode N` 重选；有未关闭的 Codex 任务时不重选。
4. **不要因为没选模式而停下、提问或反复重试**。hook 不再拦截未选模式的工具调用。

## 开工

```bash
@CCL@ mode begin --session '${CLAUDE_SESSION_ID}' --project /abs/project --mode 1或2 --note '用户当前任务的简短目标'
@CCL@ context --project /abs/project
```

- 用户已明确选择时，`--mode` 会被忽略（以用户为准）。
- 模式一也可以直接 `ccl new`，系统自动记为模式一。用户明确选了模式二时派发会被拒绝，这是正确行为，自己做即可。
- hook 没有记录用户的选择（桌面端有时漏记）：`@CCL@ mode sync --session '${CLAUDE_SESSION_ID}'` 从真实会话记录恢复。它是只读恢复，**不要用模型文字伪造选择**。命令里不要加管道或 `;`（旧版门禁会拒绝，新版已不拦）。
- 安装/任务记录异常：`@CCL@ doctor --project /abs/project --session '${CLAUDE_SESSION_ID}'`（只读）。

## 两条路径

- **模式一**：加载 `codex-loop`。每个 CCL 任务绑定当前 session；续接旧 session 的任务先检查现场再 `ccl adopt TASK_ID --session ...`，不要复制创建相同任务。
- **模式二**：用自己的工具和技能完成规划、执行、检查与总结；可以用 Claude 自己的子代理，但不调用 Codex CLI、Codex MCP 或 `ccl new/run`。不为模式二制造假的 Codex 记录。有值得保留的已验证事实时：`@CCL@ note --session ... --project /abs/project --text '事实' --evidence '文件/检查结果'`。

两种模式共用项目 `.ai-collab/MEMORY.md`；执行状态按 session 隔离，其他窗口的新选择不影响当前任务。

## 切换与中断（仅用户明确要求切换时）

按 hook 的 `pending_mode` 交接：先用对应后台任务的 TaskStop 停止 Codex 并等它退出，检查并保留已改内容，`ccl review TASK_ID cancel --note '用户切换到模式二，保留成果并交接'`，再 `ccl mode activate --session ...`。不并发启动 Claude 和 Codex 修改同一工作区；进程未停或任务未关闭时程序拒绝激活。模式二转模式一时，把已完成内容、未完成项和检查结果交给 Codex。窗口重开/压缩不会自动重跑。

## 收尾

任务真正完成并记录需要的记忆后：

```bash
@CCL@ mode finish --session '${CLAUDE_SESSION_ID}' --note '完成内容与实际验证结果；或用户取消及现场'
```

要求 CCL 任务都已关闭；之后清除本次模式，保留“上次模式”仅作参考。不 finish 也不会被拦：回合结束时系统会自动保持当前模式，后续补充沿用。需要用户补充信息时 `mode wait --note '缺什么'` 再提问，下一条补充仍沿用原模式。

最终回复先交付结果，用一句话说明用了哪种模式即可，**不要再附“请选择模式”的提示**。不为模式启动额外模型，不创建定时提醒；没有新任务输入时不后台制造工作。
