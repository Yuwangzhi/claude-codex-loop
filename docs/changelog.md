# 更新记录

← [返回 README](../README.md)

## 0.4.4：自动选择与模式恢复合并

| 问题 | 现在 |
| --- | --- |
| 会话恢复/分叉/清空后 session ID 改变，漏收输入 hook，工具入口拿不到模式 | PreToolUse 按当前真实 session 从会话记录或上次模式恢复；不借用其他 session 的任务，不重开已结束任务 |
| 通知、插件消息、工具结果被误当成用户选择 | 只有 `origin.kind=human` 且非 meta 的输入会更新模式 |
| 恢复命令对不同 session ID 反复同步仍报未选择 | 恢复后的上下文明确写出当前 hook session；技能要求先用当前真实 ID 同步 |
| 自动选择、显式选择、上次模式混用 | 显式选择优先；未指定时按任务难度自选；上次模式仅作参考，不阻塞工具 |

实现保留 0.4.0+ 的自动选模式、启动重试、验收脚本预编译和 Windows 路径提示；回归见 [模式恢复验证记录](mode-recovery-validation-20261113.md)。

## 0.4.0 之后的改动：自动选模式与更少的失败

| 问题 | 现在 |
| --- | --- |
| 新 session 没有模式记录时，门禁拒绝几乎所有工具，连 `ccl mode sync \| head` 这类带管道的恢复命令也被拒 | 未选模式不再拦截；Claude 用 `ccl mode begin --mode 1\|2` 自选，直接 `ccl new` 视为选模式一。只有用户**明确**选了模式二时才拒绝派发 Codex |
| 只认中文“模式二”，`moshi 2`、`mode 2` 不识别 | 识别拼音与英文写法；疑问句、否定句、普通数字仍不算选择 |
| Stop hook 每轮强制菜单提示和 `mode finish` | 不再强制；回合结束自动把任务置为 ready，后续补充沿用模式 |
| Claude 自选后用户回复 `1`/`2` 无法覆盖 | 自选模式接受用户随后的单独 `1`/`2`，以用户为准 |
| 验收脚本有语法错时白烧一次 Codex（任务 blocked 且无法 `--reverify`） | `ccl new` 对 `verification_files` 与 `checks` 里的 `.py` 预编译，不过就拒绝创建 |
| heredoc 内联 spec 的 Windows 路径触发 `Invalid \escape` | 报错附带正确做法：用文件传 `--spec`，路径用 `/`；`claude-skill/references/spec-template.json` 提供固定模板 |
| Windows 上 Codex 进程偶发以 `0xC0000142` 退出且无任何事件 | 自动重试启动（最多 2 次），不占 attempt，仍计入累计预算 |

`codex-loop` skill 新增失败速查表（现象 → 原因 → 处理）。Mac 与 Windows 用同一份源码部署；两个 skill 里的 `@CCL@` 由部署时替换成本机 ccl 路径。
