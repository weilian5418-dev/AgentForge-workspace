# 06 · 可直接交给实施Agent的指令

## A. 总实施任务提示词

复制下列整段给负责落地的Agent即可；无需重复说明历史聊天：

```text
请在 D:/AgentFront/apps/workbench-vue 实现 AgentForge 浅紫色多 Agent 工作台，使用 Vue 3 + TypeScript + Vite + Tailwind CSS 4 + shadcn-vue + Reka UI + Animate UI Vue + motion-v + Vue Router + Pinia + TanStack Vue Query + Vue Flow + Monaco。

先完整阅读 D:/AgentFront/docs/vue-implementation/README.md 和其中01至06全部文件，以及page-manifest.json。参考原图位于 D:/AgentFront/output/imagegen/lavender-suite，共12张2048x1152 PNG。对话与执行画布图03是共享外壳的主基准。其他页面图控制各自内容布局；跨页矛盾以01视觉契约为准。

目标是可交互DOM页面在固定环境下高精度还原，不是展示PNG、重新设计、制作一个类似主题或用静态图片热区冒充网页。禁止把整页设计图作为背景。禁止生成新的视觉方案覆盖现有浅紫色方案。

保留根目录React应用和现有5173服务，在新子项目用5174。先打通依赖smoke build、字体和品牌资产，再统一Header/Sidebar/Button/Badge/Field/Tabs，外壳完成截图评审后才扩展12页。不要12页复制12套外壳。锁定版本与lockfile；统一使用docs中的tokens和尺寸。

每页必须支持同一组件树的reference与live数据源。reference用于像素对照，文案、状态、分栏、时间固定；不得在live失败时返回reference示例。正式构建禁用reference开关。仅reference构建响应reference查询参数。

后端在D:/AgentForge。按04接口契约实施，优先复用现有src/api纯TS模块并对照后端contracts修正。后端关闭在线OpenAPI，离线导出schema后生成类型。实时使用Bearer fetch SSE，先snapshot后afterSequence。控制帧没有业务sequence；断线/409重新同步；切任务清理旧连接。创建run不是自动执行，计划分配和确认必须保留。审批绑定用户看过的actionDigest，交付验收绑定manifest和status版本。202不是业务完成。

知识库完整CRUD、profile任意编辑、PTY终端等尚未核实支持，live保持有解释的readonly/不可用状态，并明确列出后端工作项。不要捏造API和真实Harness接入，不把provider模型配置与Harness模型继承混为一谈。

按M0→M5顺序完成，任务未完成时继续推进，不以计划/占位结束。每完成一个页面保存浏览器截图，对照原图修正；公共组件变更重截所有页面。实际运行vue-tsc、build、必要Vitest/Playwright、截图与diff检查。首次机器截图不能自动成为已批准baseline。不能声称100%像素一致或未测通过。

最终交付12页路由、reference预览入口、真实接口接入清单、全部截图/diff、功能测试结果、未支持能力和未执行检查。不得提交真实Key，不自动git commit/创建branch、不替用户部署发布。
```

## B. 工作包切分

以下是人或Agent分工建议，不是本方案已经启动了多个Agent。若用户允许并行开发，必须先完成共享M1再分派。每一工作包都要求读取上述总提示词与完整契约。

| 包 | 负责文件/页面 | 输入 | 不得改动 | 交付 |
| --- | --- | --- | --- | --- |
| P0 基础架构 | app、styles、layouts、components/af、ui、构建 | 01/02契约 | 根React入口/后端业务逻辑 | 统一外壳与组件展示、2048截图 |
| P1 核心工作流 | pages01–04、conversation/execution/artifacts | P0组件API、固定fixture | 全局token/Header/Sidebar | 4页reference+交互+截图 |
| P2 任务审批 | pages05–06、tasks/approvals | P0 API和状态VM | 审批摘要/版本语义 | 列表/审批多状态、截图 |
| P3 执行者扩展 | pages07–08/11 | P0、profile/tool/provider契约 | 通用Button、虚构profile API | 管理页、能力只读边界 |
| P4 知识系统 | pages09–10/12 | retrieval/source能力、P0 | API鉴权和全局外壳 | 资料、文档、设置、partial状态 |
| P5 业务接入 | data、queries、realtime、presenters | 后端schema与现有TS | 页面几何 | typed API、SSE、业务测试 |
| P6 联合验收 | e2e、visual、report | 所有页面 | 擅自改baseline | 对照报告、回归证据、缺口清单 |

共享文件只由P0负责人修改。页面Agent需要新variant时提交组件需求，不在本页偷偷追加一个颜色和圆角。P5提供稳定VM，页面Agent不能自己从Record任意拼业务状态。

## C. 单页Agent任务模板

```text
只负责页面：[page-manifest中的ID与标题]
参考图片：[绝对路径]
目标路由：[manifest.route]
共享外壳：[manifest.shell]
具体布局：阅读03-page-recipes.md对应章节，几何与字体遵循01。
负责代码：pages/[页面].vue、对应features目录、fixtures/[页面].ts、该页测试。
禁止改：styles/tokens.css、GlobalHeader、共享Sidebar、基础Button、根项目package.json。
必须交付：reference主状态；loading/empty/error/partial/permission适用状态；所有按钮行为；2048截图；目标响应式截图；与原图的差异清单；真实后端已接通/待接通说明。
接受标准：不要自行发挥；不以相似色调替代结构还原；图片不能替代真实DOM。所有不确定的跨页视觉问题按契约解决，无法解决则给出具体坐标和差异，不擅自全局重设计。
```

## D. 集成与评审纪律

1. 第一批只做全局Header、两类sidebar和一个页面，不先铺12个粗糙页面。
2. 图03先确认列边界、计划卡、审批卡、composer、Graph和diff尺寸。共享问题当场解决，再扩其他页。
3. 所有新页面必须引用共享fixture同一组project/roles/harness名，12页不得出现12个不同的任务故事。
4. fixture主状态与live能力不足状态分别评审；不能以真实数据很长为由改变reference正文，也不能把真实数据截成示例文本。
5. P5映射的roleId/profileId/instanceId/recipientRef分开；P1不能只按名字和数组索引画状态。
6. 先做原图叠图评审，再建立浏览器baseline；自动diff和人眼都要看。
7. 最终文档明确“视觉主状态达到何种程度”“哪些真实流程已经测试”，不把设计稿生成和前端实现混为一项完成。

## E. 实施前需要补齐的资产

- 确定本地字体文件与许可证；没有原字体时使用已指定替代字体，并记录差异。
- AgentForge SVG标记唯一文件，字号/宽高固定。
- Harness正式图标或中性fallback；使用哪一个全套一致。
- 准确的原始12张PNG，不用聊天里的缩略图作为源。
- 固定reference数据与代码片段，修正图中文字生成错误。
- 一个可用于测试的后端project/grant/profile集合；真实写操作只作用于该测试项目。

缺原字体或正式图标不阻塞构建可运行页面，但阻止声称“一模一样”。先做好可量化的其他部分，再列出该资产造成的剩余差异。
