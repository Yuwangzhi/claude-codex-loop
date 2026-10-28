# README 视觉资产

## v0.4.0 概览图

- 文件：[claude-codex-loop-overview-v0.4.png](claude-codex-loop-overview-v0.4.png)
- 更新日期：2026-10-28。
- 方式：使用内置 `image_gen` 编辑原概览图，保留暖白、深蓝、青绿与杏色玻璃卡片风格，改为横向四阶段布局。
- 信息层级：Claude 规划 → Codex 执行 → CCL 独立检查 → Claude 验收；下方是任务契约、交接包、问题台账与累计预算，返修箭头从验收回到执行。
- 范围：概览图，不是完整状态机。自动检查通过不等于接受；返修受次数、时间及可选 token 预算约束，检查/执行异常也会停止自动推进。模式二不调用 Codex。
- 原图保留为历史素材：[2026-10-24 概览图](claude-codex-loop-promo-20261024.png)，不再作为 README 封面。
- 执行与恢复条件见 [README 的 Mermaid 流程图及处置表](../README.md#闭环是怎样实现的)，以运行实现为准。Mermaid 源码仅维护在 README，不复制为第二份可漂移源码。

## 验证与维护

- 两张 Mermaid 图分别说明模式选择和 `run → review` 主路径；显式恢复单列为处置表，避免将重验误画成自动重跑 Codex。
- 从 README 提取源码，以 Mermaid 11.12.0 和 12.2.0 在本地 Edge 中实际解析、渲染，检查浅色与深色主题；不是仅检查代码围栏。
- 新图经实际查看，确认中文标签、四阶段角色、返修箭头和停止说明；1672 × 941 像素，RGB 不透明图片。旧图保持不变。
- 渲染脚本、下载的渲染器和截图仅为本地校验材料，不作为项目运行依赖。后续改流程时同时核对图、相邻文字与实现。
- Mermaid 用法参考[官方渲染文档](https://mermaid.js.org/config/usage.html)和 [GitHub 图表文档](https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/creating-diagrams)。本地测试的渲染器版本不代表 GitHub 当前使用的版本。

## 实际生成提示词

以下为本次内置图像工具使用的完整提示词；输入为上述历史素材。图片复制到仓库后由 README 使用，运行时不依赖图像生成工具。

```text
Use case: infographic-diagram.
Asset type: GitHub README overview banner for Claude–Codex Loop v0.4.0.
Input image 1: the existing project overview image, EDIT TARGET and visual identity reference.
Primary request: redesign this existing image into a polished landscape 16:9 README banner reflecting the implemented v0.4.0 workflow. Preserve its warm ivory background, dark navy typography, soft teal and apricot accents, translucent glass cards, gentle studio light, and restrained premium feel. Simplify decorative scenery so the workflow is dominant. Keep the project name and Claude/Codex role identity; replace outdated layout and text as specified.

Composition: generous margins; clean horizontal hierarchy. Top: project title and discreet version badge, followed by a readable Chinese subtitle. Middle: four equally sized glass cards aligned left-to-right, joined by unambiguous right-facing teal arrows. They read PLAN, EXECUTE, VERIFY, REVIEW conceptually, but use only the exact labels below. First and last cards have apricot accents; middle two teal accents. Use simple abstract plan/code/check/review icons, not corporate logos. A single slender curved return arrow runs ABOVE the cards from the last review card back to the second execution card; place its label clearly without crossing text. Below the four cards, a shared teal-tinted glass foundation contains the four supporting capabilities. Under this, one restrained stop-rule line and one mode-2 line. Avoid extra branches: this is an overview, not the exhaustive state machine.

Text (verbatim, crisp accurate Simplified Chinese, dark navy, large and legible at normal README width):
Title: "Claude–Codex Loop"
Badge: "v0.4.0"
Subtitle: "Claude 统筹 · Codex 执行 · 验收后交付"
Card 1: "Claude" / "规划" / "目标与验收标准"
Card 2: "Codex" / "执行" / "实现与自测"
Card 3: "CCL" / "独立检查" / "检查结果与文件证据"
Card 4: "Claude" / "验收" / "审查产物与问题"
Curved return-arrow label: "有界返修 · 同一 thread"
Foundation: "任务契约  ·  交接包  ·  问题台账  ·  累计预算"
Stop-rule line: "无进展 / 预算耗尽 → 停止自动返修"
Mode-2 line: "也可选择：仅 Claude 模式"

Constraints: image is opaque, no transparency. No browser frame, screenshots, QR code, watermark, performance claims, platform badges, URLs, or additional words. Do not imply unlimited autonomous loops, automatic acceptance, semantic retrieval, or total-account token billing. Typography must be clean and all Chinese characters exact. The curved arrow points from review to execution, never from execution to planning. Four and only four cards. This is a conceptual product overview; do not depict unseen UI features. Render the complete image with no clipped text.
```
