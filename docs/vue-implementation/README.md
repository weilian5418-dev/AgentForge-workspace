# AgentForge 浅紫色工作台：Vue 实施交付规范

版本：1.0 · 2026-11-20。对象：实施 Agent、前端开发、接口开发和验收人员。

这是一份基于当前代码和 12 张设计图核对过的实施方案，不是已完成的 Vue 应用。当前 React 应用保持原样。

## 先看结论

采用 Vue 3、TypeScript、Vite、Tailwind CSS 4、shadcn-vue、Reka UI、Animate UI Vue、motion-v、Vue Router、Pinia、TanStack Vue Query、Vue Flow、Monaco、markdown-it 和 DOMPurify。实时连接直接使用后端已有的 Bearer + fetch SSE。

仅把 PNG 发给另一个 Agent，无法保证实现“一模一样”。这些图片来自生成模型，不同页的侧栏宽度、头像、字体、按钮颜色甚至文案存在小幅差异，也没有提供原始设计字体和矢量资产。本规范将它们收敛为一份可实施的统一契约；在固定环境中用截图、区域差异和人工叠图验收，才能控制还原度。

禁止用整页 PNG 当作网页背景、用图片上的透明热点冒充交互，或为通过视觉检查把参考图直接返回给截图程序。所有主体 UI 必须是真实 DOM、SVG、可操作组件和实际数据绑定。

## 文件阅读顺序

| 文件 | 解决的问题 |
| --- | --- |
| [01-visual-contract.md](01-visual-contract.md) | 唯一视觉基准、坐标、颜色、字号、组件和响应式规则 |
| [02-project-and-code.md](02-project-and-code.md) | 建项目、依赖、文件结构、基础组件与关键 Vue 代码 |
| [03-page-recipes.md](03-page-recipes.md) | 12 页逐页 DOM 分解、尺寸、数据、按钮行为与检查点 |
| [04-backend-contract.md](04-backend-contract.md) | 已核实接口、创建与执行流程、SSE、版本校验、能力缺口 |
| [05-visual-acceptance.md](05-visual-acceptance.md) | 固定测试环境、截图代码、两阶段基线、状态验收与交付 |
| [06-agent-handoff.md](06-agent-handoff.md) | 可直接交给其他 Agent 的完整执行指令与工作包 |
| [page-manifest.json](page-manifest.json) | 12 页 ID、路由、参考图、视图状态与职责分配 |
| [examples/reference-data.ts](examples/reference-data.ts) | 可复制的确定性任务、角色、文件、对话、知识、库存数据 |
| [scripts/compare-reference.mjs](scripts/compare-reference.mjs) | 可运行的原图/实图差异和叠图工具 |
| [verification.md](verification.md) | 本次示例与工具验证的范围、结果和未执行事项 |

## 已核对的材料

- 前端：`D:/AgentFront/package.json`、`vite.config.ts`、`src/api/client.ts`、`src/api/types.ts`、`src/api/endpoints/*.ts`。
- 后端：`D:/AgentForge/src/agentforge/api/` 下的 runs、events、discovery、instances、approvals、delivery、retrieval、documents、providers、tools、git、collaboration 等路由，以及对应 contracts。
- 视觉参考：`D:/AgentFront/output/imagegen/lavender-suite/01-overview.png` 至 `12-settings-system.png`。
- 现有设计集：`D:/AgentFront/output/imagegen/lavender-suite/index.html`。
- 动效原则：<https://animate-ui.com/docs/vue>，包括 Tabs、Collapsible、Sheet、Pinned List、Motion Grid、Accessibility。

## 实施边界

1. 新 Vue 项目放在 `D:/AgentFront/apps/workbench-vue`。这是本规范规定的目标路径，当前尚未创建。第一阶段不替换 React 根入口、不卸载现有依赖、不改 5173 端口上的应用。
2. Vue 开发端口固定 5174，后端仍为 `127.0.0.1:8765`，开发代理 `/api`。切换上线入口需要独立验证和明确的发布动作。
3. 每一页必须有相同组件树驱动的 `reference` 和 `live` 两种数据来源。reference 用于对照原图；live 对接真实接口。禁止在 live API 失败时静默回退成演示成功数据。
4. “图中存在”不代表“后端已实现”。知识库 CRUD、配置名称/权限的任意修改、交互式 PTY、图中演示的任意 Harness 接入等，必须走能力核对。
5. 创建 run 不等于开始执行。计划、分配、确认、执行请求分别处理。用户批准操作不等于原生 Harness 已收到确认。
6. 所有密钥只通过受控连接流程使用。本方案、fixture、截图、日志、`VITE_*` 和浏览器构建产物中都不能出现此前用于生图的真实 Key。

## 里程碑与顺序

| 阶段 | 交付 | 进入下一阶段的条件 |
| --- | --- | --- |
| M0 资产与契约 | 参考图、字体、SVG 资产、页面清单、接口类型快照 | 缺失资产明确记录；不冒充已精确匹配 |
| M1 统一外壳 | Header、两类 Sidebar、Button、Field、Badge、Tabs、Panel | 2048×1152 外壳位置通过叠图评审 |
| M2 核心工作流 | 01–04 页，reference 模式可完整导航 | 创建、执行、差异、交付布局通过评审 |
| M3 管理页面 | 05–12 页，reference 模式 | 12 个路由均有截图；状态与共享组件一致 |
| M4 真实接入 | 身份、查询、SSE、消息、审批、交付、能力降级 | 无假成功；并发/过期/断线均可恢复 |
| M5 联合验收 | 截图、diff、测试报告、功能缺口表 | 视觉与业务分别签收；不得互相替代 |

预估为 12–20 个有效开发日，依赖接口可用性和资产确认；不是承诺工期。先统一外壳再分页面，不能让 12 个 Agent 各写一套导航和按钮。

## 完成标准

- 12 个页面 reference 状态和真实路由齐全；页面可用，非图片展示替代品。
- 已确认的字体/资产本地加载；同一 Header、Sidebar、Badge、Button 被全站复用。
- 2048×1152 原图对照与 1440×900、1280×800、1024×768、390×844 自适应检查完成。
- 建立经人工确认的 HTML 渲染基线后，CI 检查全部 12 页；不能直接以机器第一次截图自动更新基线算通过。
- 业务操作使用真实版本、摘要与幂等键；能力不足的控件准确解释原因。
- 提交实现文件清单、构建与类型检查结果、截图、区域 diff、未完成事项。未经执行的检查不得写“通过”。
