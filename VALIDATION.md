# 验证记录

公开版保留验证范围和结论；个人路径、认证信息、私有仓库、任务 ID、会话日志及访问修复记录留在维护者本地，不随仓库发布。

## v0.3.2：稳定性修复与可诊断恢复（2026-10-28）

- Windows 原生 Python 3.12.0：**88/88 Python 自动测试通过**；Node 桌面桥接测试 **8/8 通过**。两个 skill 的结构校验及 `git diff --check` 通过。本次不据此宣称 macOS/Linux 或云端 CI 已验证。
- 验收工具无法启动/无权限或超时：停在 `blocked`，保留原因，不自动消耗剩余 worker 次数；普通断言失败仍按原预算返修。
- Git 子目录中的未跟踪文件内容变化会改变工作区指纹并阻止接受；损坏索引导致采集失败时拒绝生成有效快照；新仓库无 HEAD 仍可用。
- 损坏任务逐项报告且保留原件，无关项目不再被连带阻塞；活动项目仍拒绝重放。非法任务可选字段、非对象事件行和损坏 session 有针对性回归。
- 换 Python 更新会清理旧 CCL 命令 hooks；其他 hooks/compound commands 保留。Windows 正反斜杠的模块路径重装保留原通路；POSIX 特殊引号路径的匹配以纯函数测试覆盖，不等同于原生 POSIX 安装实测。
- `doctor` 检查 CLI、版本、hook 通路与入口文件、损坏记录；发现 error 返回 2，原记录不变。不把配置或历史事件等同于当前客户端加载。
- `status` / 默认 `recover` 的只读性以文件字节快照验证。显式 `recover --reverify` 保留候选、执行预算和原检查，遵守项目锁及模式交接，通过后仍待人工验收，关闭任务不能重开。
- 三个独立提交前审计分别覆盖执行状态与校验、安装兼容与诊断、需求/文档/CLI 交付。审计发现的结构校验、失效解释器诊断、路径归一化和 Git 采集错误问题均已修复并复核。

### 本机安装后的验证

- 使用原 Python 3.12.7 入口备份更新，保留 `module` 通路。实际 `ccl --version` 返回 `0.3.2`，`doctor` 返回 `healthy=true`，识别 Codex CLI `0.165.0` 和 Claude Code `2.1.308`。
- 已安装运行文件、模块文件与两个 skills 共 **9 项与源码一致**（skills 比较时替换安装器路径占位符）；6 个既有配置文件及 18 个任务/会话状态文件的内容哈希保持不变。
- 在隔离 state/config/project 中运行**实际已安装 CLI + 模拟 worker**：缺工具 → `blocked`（1 次 worker）→ 恢复工具 → `--reverify` → `awaiting_review` → 阅读实际结果后 `accepted`。重验没有新增 worker 调用；默认恢复建议未改记录；接受后拒绝重开。
- 本轮没有真实模型调用，也未重启或操作用户的 Claude 桌面会话；安装后的 `hook_observation` 无当前会话证据时保持 `unknown`。这不冒充真实模型或新桌面会话的端到端验证。

## v0.3.0：Windows 原生安装

- macOS Python 3.9 与 Windows 原生 Python 3.12：各 38/38 自动测试通过。不是 WSL 结果。
- 新增 Windows 字节范围文件锁、无副作用 PID 查询、进程树超时终止；测试实际启动子进程验证锁与终止行为。
- 支持原生 `.cmd` 与 Git Bash 启动器；安装器保留原权限、其他 hooks、模型和账号登录，补充 PATH 并备份。
- Windows hooks 直接使用绝对 Python 路径与 UTF-8；npm Codex 通过 Node 入口接收参数，避免 shell 重解析。
- Windows 真实两轮 Claude 对话：首轮未选模式，3 turns 后明确等待选择且目标文件不存在；回复 1 后，10 turns 内完成 CCL 派发、Codex 实际写文件、3 项独立检查、Claude 验收、共享记忆写入和模式关闭。任务最终 accepted，session 回到 awaiting_choice；没有工具权限拒绝。
- 同一 Windows 会话继续选择模式二：6 turns 完成原生 Claude 写文件、读回核验和模式关闭，没有新增 Codex 任务；原模式一产物保持一致。两种模式最终都重新提示下一项选择。
- Windows 只对已验证的运行环境作实测声明；Linux/其他 Python 版本仍由可选测试模板供复现。

## v0.2.2：公开安装包（历史）

- Python 标准库实现，自动测试不调用真实模型。
- 原有 32 个测试，加上两个隔离 HOME 安装测试：重复安装保留原指令、权限和其他 hooks；含空格路径的启动器可执行；安装失败回退原文件。
- 去除了指令中的个人环境假设。可选 ACCESS_HANDOFF.md 仅在本机存在时引用，仓库不包含该文件。
- 安装器在写入前拒绝原生 Windows 与 Python 3.9 以下环境。
- macOS 本地 34/34 测试通过。提供可选 GitHub Actions 矩阵模板，但当前发布凭据无 workflow 写入权限，未安装或运行云端 CI；Linux/Python 矩阵未据此声称通过。

## 已完成的真实模型联调

以下为早期版本的历史实测，不代表所有 CLI 版本、平台或插件组合均已验证。

| 范围 | 实际观察 | 结论边界 |
| --- | --- | --- |
| Claude → CCL → Codex → Claude | Claude 写计划；Codex 生成 JSON；独立检查通过；Claude 验收并写共享记忆 | macOS 上的真实任务闭环 |
| Codex 自动返修 | 首次受控检查失败，第二次复用同一 thread 修复通过 | 主动注入失败以验证返修机制 |
| 两模式选择 | 未选模式不写文件；选 2 后 Claude 自己执行；结束后下一项再次等待选择 | 真实 Claude CLI 三轮对话，未派发 Codex |
| 完整权限 CCL | never / danger-full-access 下执行本机与 SSH 远端 GitHub 查询，独立检查后接受 | 验证当时账号/网络，不代表任意服务可用 |
| Mac Codex → Claude | Claude Bash 查询成功，无工具审批拒绝 | 测试提示过度禁止 CCL 收尾；控制器后来关闭测试模式，不算无人干预收尾通过 |
| Windows Claude → Codex | Bash 调用 codex exec，身份查询和仓库读取成功，退出 0 | 原生 CLI 委派，未在 Windows 原生运行 POSIX CCL |

## 自动测试覆盖

- 子进程模拟、结构化结果校验、同 thread 返修、预算耗尽、工具/认证阻塞停止。
- 文件缺失与内容变化拒绝接受、额外产物指纹、Git 状态变化检查。
- 项目锁、孤儿进程、硬中断恢复、进程组超时、路径与符号链接检查。
- 共享记忆须验收、写入去重、模式二不制造 Codex 审查记录。
- 未选模式门控、模式二常见 Codex 派发拦截、会话隔离、切换、收尾提示和防递归。
- 首轮与返修完整权限参数一致；显式 read-only / workspace-write 保留。
- 安装幂等性、设置保留、启动器路径引用与失败回退。

## 尚未声称的能力

- 跨设备自动共享 memory、所有桌面客户端/插件组合的端到端覆盖。
- 后台自动唤醒 Claude、绕过 OS 登录或工具强制确认。
- 把文件存在、模型自报成功或一次 CI 通过当成所有真实任务都已验收。

复现：`python3 -m unittest discover -s tests -v`。真实模型联调消耗 CLI 账号额度，未纳入公共 CI。
