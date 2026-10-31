# 更新记录

← [返回 README](../README.md)

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
