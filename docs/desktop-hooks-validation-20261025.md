# Claude Code 桌面模式修复验证（2026-10-25）

CCL 0.3.1 已安装到本机，使用 `ccl-work-modes` 函数 hook 插件。原有 CCL settings command hooks 已迁移，其他 settings、AI Pulse hooks 和插件配置经过结构化比对保持一致。更新安装会保留已选通路。

## 实际验证

- Python 测试：63/63；Node 插件桥接测试：8/8。
- 桌面内置 Claude Code 2.1.308 插件严格校验通过；终端 CLI 2.1.310 校验通过。
- 使用桌面内置 CLI 的隔离实测：明确选择模式二、无模式新会话继承模式二均成功。
- Claude Desktop 3.6556.2 实际界面：第一轮输入“模式二吧”读取自建 fixture，模式解析与读取成功；下一项任务未选择模式，直接沿用模式二，读取成功并正常关闭。
- 桌面收尾元工具与模式菜单简写的兼容问题已根据第一轮实测修复；第二轮无 CCL 拒绝或重复模式询问。模型曾误用 macOS 不支持的 `cat -A`，随即改为 `cat -e` 成功，与 hook 无关。
- 实际状态来自模块：`hook_transport=module`，`selection_source=default_last_mode`，结束时 `mode=null`、`last_mode=2`、`phase=awaiting_choice`，保留历史供下一项任务沿用。
- 原问题历史会话恢复验证通过：UserPromptSubmit、PreToolUse、Stop 均记录为 module；未选模式时 default_last_mode=2，begin/finish 正常，任务已关闭。Claude 桌面界面已显示验证通过。
- fixture 内容与原始内容一致，没有修改用户项目文件。

## 行为

接受 1、2、一、二、模式1、模式2、模式一、模式二，以及“二吧”“就用1”等选择。普通任务中的数字和同时提到两种模式不被武断识别为选择。没有明确选择时优先沿用本会话上次模式，否则使用最近明确选择的偏好；从无历史时才等待选择。同一任务不反复问，仍执行安全的执行者交接。

旧客户端或 hook 异常时保留 `ccl mode sync --session SESSION`，仅从真实用户记录恢复。模式约束不是安全沙箱。

## 根因与回退

根因已确认：原会话的真实工作目录 `/private/tmp/claude-switch-loop-20261024` 中，`.claude/settings.local.json` 设置了 `disableAllHooks: true`。该项目本地开关同时禁用命令 hooks 和函数模块，因此只看用户 settings.json、项目 settings.json 或重启客户端无法修复。已备份为首次迁移备份目录中的 `probe-project-settings.local.json`，移除该键后 `/reload-plugins` 加载插件数由 20 变为 21，原会话立即开始记录 module 来源的 UserPromptSubmit、PreToolUse 事件，并在未选择模式时沿用模式二。

未更改 Remote Control、模型、权限模式或 Claude 应用二进制。函数 hook 通路已保留并实测有效；插件 API 为 early access，未来客户端升级后应重新验证。再次遇到 hooks 无记录时，应先按实际工作目录检查用户、项目及项目本地三层 settings 中的 disableAllHooks，而非反复改启动参数。

首次迁移完整备份：`~/.local/state/claude-codex-loop/backups/20261025-115428-436604/`。其 manifest 记录了安装前文件，包括旧安装版补丁。原临时探针目录的两个实验 command hooks 已移除，配置另存为该备份目录的 `probe-project-settings.json`。

切回命令 hook：`python3 install.py --hook-transport command`。新版重新安装：`python3 install.py`，会保留模块通路。安装/更新插件后重开会话。原会话清除项目本地禁用开关后已通过 `/reload-plugins` 加载；重启本身不能解除 disableAllHooks。
