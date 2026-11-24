# 03 · 12页逐页实现配方

共享外壳、颜色、字体、按钮由01契约统一。本文件每页给出父子组件、布局、数据和交互，页面Agent只负责内容层。尺寸均为2048×1152的初始实现值；最终按叠图校准。以真实CSS布局处理长内容，不使用整页绝对坐标拼图。

## 0. 页面通用写法

每页顶层设置 `data-page-id`，给关键区域 `data-region`，便于截图和锚点测量。公共模板：

```vue
<script setup lang="ts">
import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useWorkspaceRepository } from '@/app/runtime'
import { useCurrentProject } from '@/queries/current-project'

const repository = useWorkspaceRepository()
const project = useCurrentProject()
const { data, isPending, isError, refetch } = useQuery({
  queryKey: computed(() => ['tasks', project.value?.id]),
  enabled: computed(() => Boolean(project.value?.id)),
  queryFn: ({ signal }) => repository.loadTasks(project.value!.id, signal),
})
</script>

<template>
  <main class="af-page" data-page-id="05-task-library">
    <PageHeader title="全部任务" description="跟踪每次执行，从目标到交付。" />
    <AfErrorState v-if="isError" @retry="refetch()" />
    <TaskTableSkeleton v-else-if="isPending" />
    <TaskTable v-else :items="data ?? []" />
  </main>
</template>
```

Query返回值解构成顶层ref，模板直接使用自动解包值，避免nested refs的`.value`被误写。`PageHeader`等组件由公共负责人定义，不自行导入不存在的文件。

## 01 工作台首页

参考：`01-overview.png`；路由 `/workspace`；shell management。

```text
OverviewPage
  PageHeader
  OverviewColumns
    Main
      GoalComposer
      QuickTaskActions
      ContinueTaskList
      RecentDeliveryTable
    ProjectContextAside
      ProjectSummary
      ContextFileList
      AttentionLinks
```

- 侧栏296；main padding32；main/aside列 `minmax(0,1fr) 360px`、gap32。参考图原右栏更宽时，在不更改公共Sidebar下调到384，记录测量值。
- PageHeader约y128，主标题36/46，说明18/28。不要把首页做成巨型营销hero。
- GoalComposer top margin24、高174、padding20；建议项一行h40。主列和aside在composer区域顶对齐。
- `继续进行` 距composer下方36，任务行h104；最近交付表格行h64。不要改成4个大统计卡。
- Fixture：继续进行2项：订单导出2个Agent执行中，权限重构暂停；最近交付3行；右栏3文档、2个待处理。
- 初次无项目时显示“连接项目”，不能把当前目录名当已授权项目；无任务时隐藏continue列表，保留可行动empty state。
- 创建任务按钮先导航02并传draft，或直接创建run后进入计划确认流程；不能直接显示Agent已开始运行。
- 继续/恢复分别读取真实run和allowedActions；最近交付打开04；右侧文档打开10；收件箱打开06。
- reference内容文字固定，不随机用户名、不用“早上好/晚上好”动态文案。

## 02 新建任务与角色分配

参考：`02-new-task.png`；`/tasks/new`；shell task。

```text
NewTaskPage
  PageHeader
  NewTaskGrid
    TaskForm
      GoalField
      ProjectAndBranchFields
      ReferenceAttachmentList
      AssignmentModeTabs
      RoleAssignmentTable
      AcceptanceCriteria
      AdvancedExecutionDisclosure
      FormActions
    ExecutionPreview
      DependencyPreview
      AssignmentSummary
      PermissionSummary
```

- 任务侧栏314；内容padding32；列 `minmax(0,1fr) 420px`、gap32；表单白底边框radius10、padding24。
- 目标textarea min-h144；项目/分支二列gap24；参考chip h36；分配表行h56。
- Preview占右列，上部流程约280高，下面4个角色/3个Harness、权限说明；不能把表单挤成半屏小弹窗。
- Fixture目标、分支、2附件、4角色见原图。acceptance条件3项；不要造一个会直接操作本机的本地路径输入。
- 图中“创建并开始”需要产品纠正：live主按钮文案为“创建并规划”，随后展示计划确认Sheet；用户确认后才允许执行。reference按图保留原文案用于视觉，但模拟事件只进入本地确认步骤，不调用生产API。
- 表单验证：目标非空，项目已授权，角色profile可调度，分配能力满足；更换规划结果时旧分配不可带着过期版本提交。
- branch字段若后端未支持指定分支则只读展示拟议/实际分支，不随意塞进createRun body。文件导入走inputRefs；没有合法sourceRef时附件是待上传草稿，不能假装已提供给Agent。
- 保存草稿只本地持久化非敏感表单，提示保存位置；恢复草稿要重新验证project/profile。

## 03 对话与执行画布

参考：`03-execution.png`，共享视觉主基准；`/tasks/:runId`。

```text
ExecutionPage
  TaskSidebar
  WorkspaceSplit
    ConversationPane
      ConversationHeader
      TaskContextChips
      MessageList
        UserMessage
        CoordinatorMessage
        ExecutionPlan
        HandoffEvent
        AgentMessage
        ToolCallDisclosure
        ApprovalCard
      MessageComposer
      KeyboardHint
    ExecutionWorkbench
      WorkspaceTabs
      RunStatusToolbar
      ExecutionCanvas
      ArtifactInspector
        InspectorToolbar
        FileList
        CodeDiff | ReadOnlyLog | PreviewFrame
        ReviewGateFooter
      RunLogDisclosure
```

- TaskSidebar314，会话602，右工作区余量1132。严格使用01文件锚点。
- Workbench `grid-template-rows:76px 62px 355px minmax(0,1fr) 56px`，在固定高度下调panel间padding而不是挤掉footer。Inspector包含自己的toolbar64+content+footer。
- Graph不是截图背景：4个节点、4条边；Planner→Frontend、Planner→Backend、Frontend→Reviewer、Backend→Reviewer。节点VM关联workPackage和instance，role显示名不是ID。
- reference：Planner完成、前后端执行、Reviewer等待；4文件 +38/+42/+61/+24；合计+165/-4；审批只有一张。
- 差异视图默认inline，代码语言来自产物metadata；没有original内容则展示“文件内容”，不能虚构删除前版本。
- 终端Tab默认日志查看器；真正PTY未接通时禁止用户输入命令，不能把只读日志伪装为shell。
- 预览仅打开后端授权、允许的产物URL，使用sandbox iframe和明确策略；没有预览产物显示empty，不能导航任意本地文件路径。
- 暂停全部是run action请求，不是前端停止spinner；返回202后展示“暂停请求已提交”，等待权威状态。
- 消息失败保留draft、错误和重试；发送成功不等于native received。消息的transport/reply分别展示。
- 实时内容增长时只有用户距底部<80px才自动滚到底；否则显示“有新消息”按钮，避免抢滚动。
- 选择文件、节点、Tab不触发任务重新执行。核心组件不应因为query刷新失去选择或重置编辑器。

## 04 交付与验收

参考：`04-delivery-review.png`；`/tasks/:runId/delivery`；shell task。

```text
DeliveryPage
  TaskHeader
  DeliverySplit
    DeliveryConversation
      CompletionSummary
      RoleCompletionGrid
      ReviewerMessage
      MessageComposer
    DeliveryWorkbench
      DeliveryTabs
      ChangeSummary
      FileAndDiffSplit
      AcceptanceChecklist
      DeliveryAttachments
      AcceptanceActions
```

- 侧栏314；上方TaskHeader h148左右；下方会话580，交付余量；主gap12。基准页面对话与检查区底边一致。
- 内容区3:2左右差异可调，但不更改整个header；文件rail300，代码区flex；附件3列，验收清单每行40。
- reference：4角色都完成，12项测试通过，+165/-4，4文件。状态为技术检查通过/等待验收，不是已验收。
- 确认验收读取最新delivery pointer+manifest，带integrationVersion/manifestHash/revision/statusVersion；409需要用户重新查看，而非自动确认新版本。
- 要求修改收集说明和拒绝项；部分接受要提交成员ID；不能将“验收”替换成Git合并。
- 附件下载使用受鉴权content/export API。HTML `<a href>` 不能给Bearer header；通过fetch得到Blob/objectURL，再下载并revoke。空/二进制/大文件各有正确状态。
- 读取过程中hash变化时提示“交付已更新”，保留用户批注草稿，禁止将旧测试证据套在新产物上。

## 05 全部任务

参考：`05-task-library.png`；`/tasks`；shell management。

```text
TaskLibraryPage
  PageHeader + NewTaskAction + ViewToggle
  TaskFilters
  TaskTable
  CursorPagination
  ArchivedTasksDisclosure
```

- main padding32；标题到toolbar24、toolbar到table28；表头64，8行各64；table radius10。
- 列宽（内容约1688）：checkbox64，goal minmax(300,1fr)，status200，executors420，updated200，actions140。中宽屏先减metadata宽度，窄屏内部横滚不裁重要按钮。
- Fixture精确8项：执行中2、待处理2（待验收1/待批准1）、完成3、暂停1，计数总和8。数据见套图05提示词，不自己造10项。
- 后端分页使用cursor，不能伪造总页数。reference显示1/1；live若没有total显示“已加载8项”，不用“全部8”误导为服务端总数。
- 状态筛选如需组合多个真实status，查询全部已加载还是后端分页必须说清。切换筛选重置cursor、清空旧批量选择。
- Board按钮只有实际实现看板后才能启用；第一期若无则disabled+解释，不留空点击。
- 行点击导航task；checkbox不触发行点击；按钮、链接用语义控件，不能tr上冒充button。

## 06 审批收件箱

参考：`06-inbox.png`；`/inbox`；shell management。

```text
InboxPage
  PageHeader
  InboxSplit
    AttentionList
      AttentionFilters
      ApprovalItem
      DeliveryAcceptanceItem
    ApprovalDetail | AcceptanceDetail | AttentionEmpty
```

- main宽1752左右、padding32；list552/detail余量，gap14；detail header108、sections gap24，footer pinned在detail内部。
- fixture两条待处理；安装xlsx审批和数据库迁移验收。收件箱pending数量包含两种类型；不能把待验收都打成权限申请。
- 操作卡必须显式展示请求原因、精确命令、作用范围、有效期、执行者。技术命令是后端信息，不能由用户输入的自然语言猜出。
- `允许一次`提交用户实际看过的actionDigest。发现目标/digest变化时中断，请用户重新审阅；不能拉最新digest后悄悄批准。
- 状态区分decision已保存、forward处理中、nativeConfirmed；native拒绝/未知时给真实错误。
- 过期审批隐藏/禁用决定按钮，显示“已过期，返回任务”；已处理项可查看记录，不重复提交。
- 在≤1024用list/detail路由式切换或Sheet，不保留两列各300挤字。关闭详情回到列表并保持选中/滚动。

## 07 执行者管理

参考：`07-executors.png`；`/executors`；shell management，全局执行者选中。

```text
ExecutorsPage
  PageHeader + DiscoverAction + ConnectAction
  InlineInventoryCounts
  InventoryTabs
  InventoryFilters
  ExecutorColumns
    HarnessProductList
    ExecutionContextAside
      RoleAssignmentSummary
      ActiveInstanceList
```

- main/aside `minmax(0,1fr) 510px`、gap32。产品行h124、gap16，capability用chip而非文字段落。
- Fixture：3产品/4配置/2实例；Codex配置2运行1，Claude配置1运行1，OpenCode配置1运行0。右侧4角色、2运行项。
- 真实模式产品名称来自安装/adapter registry，不把示例Codex CLI/OpenCode当成一定发现。扫描结果可未知/未登录/需调和，不自动“连接成功”。
- 扫描按钮pending防重复；discover只改变库存，不自动创建profile/执行任务。
- “接入Harness”先选择已支持adapter和环境，缺adapter时显示不支持，不靠填写产品名伪造可执行能力。
- 管理配置进入08；运行实例进入对应run/instance详情；切Tab查询独立key。

## 08 Harness配置

参考：`08-harness-profile.png`；`/executors/profiles/:profileId`。

```text
ProfilePage
  ProfileHeader
  ProfileTabs
  ProfileColumns
    ProfileForm
      EnvironmentFields
      ModelConnectionFields
      SchedulingPreferences
      AdvancedParameters
      FormActions
    SchedulingPreview
    PermissionBoundary
    ConnectionChecks
```

- main/aside `minmax(0,1fr) 510px` gap32；form padding24；fields2列gap24，行间20；summary段间24。
- 与参考图保持字段位置，即使live只读。当前profile创建只接受installationId+adapterId；未核实的名称、并发、模型继承、权限编辑不能提交不存在的PATCH。
- 表单由能力schema驱动：可编辑、只读、未支持3类；readonly保留值和说明；保存按钮仅对存在的mutation启用。
- API Key在生产profile页面不回填假“已保存”圆点；后端只有secretRef时显示“凭据由执行环境管理”，不推断真实secret可读。
- “测试连接”没有直接profile probe接口就标“重新检查库存/能力”并说明来源；不能拿provider probe替代Harness认证。
- 继承Harness模型与平台规划provider是两件事，分别绑定数据源，不共用一个modelId字段。

## 09 知识库

参考：`09-knowledge-library.png`；`/knowledge`。

```text
KnowledgePage
  PageHeader + CreateDocumentAction + ImportAction
  KnowledgeSearch
  CategoryTabs
  KnowledgeColumns
    DocumentTable
    DocumentPreview
```

- 主体列 `minmax(0,1fr) 568px`，gap28；表头60行72；右文档preview padding24。
- Fixture6资料，5可引用、1处理中：接口约定、编码规范、订单字典、OpenAPI、PDF处理中、部署说明。选中接口约定。
- 搜索页只在合法project/run/scope下调用retrieval；搜索结果不等于完整资料目录。知识catalog需要额外只读聚合或新的后端契约，不能伪造`GET /knowledge`。
- 导入需上传/授权source snapshot/索引状态闭环；documents/markdown/preview不是导入资料接口。
- 已有检索结果可映射片段预览和sourceRef，支持“打开来源”；完整目录/新建/更新缺口按04明确处理。
- reference右侧可展示源文档示例；live没有引用记录时显示“引用记录暂不可用”，不要统计为0或虚构任务引用。

## 10 文档详情

参考：`10-knowledge-document.png`；`/knowledge/:documentId`。

```text
DocumentPage
  ManagementSidebar
  KnowledgeTree
  DocumentArticle
    DocumentHeader
    SanitizedMarkdown
  DocumentContextAside
    SourceMetadata
    TaskReferences
    VersionHistory
    TableOfContents
```

- 此页有二级树：management296，knowledge tree260，document minmax(600,1fr)，aside480；原图最左272统一296后余量从文章扣。主体字体18/30，标题32/44，右元数据16。
- 树和文章各自滚动；目录锚点只滚文章区，不带Header移走；文档h2加scroll-margin-top。
- FixtureMarkdown正文按图的订单导出接口、3参数表、字段、3约束，文件chips；不把整张文档PNG塞进页面。
- 数据来自合法slice/source，不允许前端直接读取Windows绝对路径。PDF只读预览需实际preview能力。
- 完整版本历史需真实历史接口；没有就“暂无可读取版本记录”，不能凭updatedAt造两条。
- 文章编辑需保存版本、冲突处理和source授权；当前未支持则保持编辑按钮只读说明。

## 11 扩展与工具服务

参考：`11-extensions.png`；`/extensions`。

```text
ExtensionsPage
  PageHeader
  ExtensionTabs
  ExtensionsColumns
    ToolServiceFilters
    ToolServiceTable
    RelatedExtensionLinks
    ToolServiceDetail
      DetailTabs
      CapabilityList
      ScopePolicy
      AdvancedServiceConfig
      DetailActions
```

- main/aside `minmax(0,1fr) 610px`、gap32；表头64，行92。图11紫色添加按钮统一石墨灰，不做另一款Button。
- fixture4服务：项目文件、终端、Git已连接，浏览器未配置；选中Git。
- 真实工具目录来自MCP工具servers+catalog；内置filesystem/terminal/git不一定是MCP server，分来源标记，不能强行一一映射。
- probe提交expectedConfigVersion，202后轮询/查询operation；active catalog变更明确显示配置版本。
- templates页与providers页同一route的query tab state，保留列表选中。模板导入走yaml真实接口；provider credentials和profile分别保存。
- 保存工具配置不存在PATCH接口时不可假保存；“添加服务”可使用已核实POST，但完整schema必须从后端类型生成。

## 12 设置与服务状态

参考：`12-settings-system.png`；`/settings/connection`。

```text
SettingsPage
  PageHeader
  SettingsColumns
    SettingsSectionNav
    ConnectionAndAppearanceForm
    ServiceHealthAside
```

- 管理侧栏296；main内二级nav254、form minmax(600,1fr)、aside440，gap24。内容多时main纵滚，保存区与当前form绑定，不盖住最后一行。
- fixture连接127.0.0.1:8765、3服务正常、可观测性未配置，无告警，浅色+紫色选中。真实连接状态需实际检查，不硬编码green。
- 后端地址若运行时可改，放connection repository的受控配置，不能只改`VITE_API_BASE`（这是构建时变量）；跨源CORS/本机loopback校验需要实际验证。
- 默认dev通过同源`/api`代理，输入绝对地址用于连接管理显示或受控反代；不静默将Bearer发给任意输入域名。
- 外观/键盘/减少动态效果属于本地偏好可保存；服务运行方式与权限策略属于后端能力，不能同一个保存按钮假装都成功。
- 保存操作返回分项结果：本地偏好已保存；后端配置不支持或失败则明示，保留草稿。
- 技术密钥与生图密钥完全分开，不读取历史聊天Key、不写入示例构建变量。

## 通用六类状态

每页fixture至少覆盖reference主状态；核心页另建loading/empty/error/partial/success/permission子场景。图中没有画出的状态遵循同样颜色与布局，不另换主题。

| 状态 | 处理 |
| --- | --- |
| Loading | 保持标题、列宽、行高；局部骨架，不整页spinner使布局消失 |
| Empty | 说明当前项目无内容，给新建/连接/返回，不能填随机示例 |
| Error | 原位错误、重试、requestId；保留草稿与选中，不展示完整敏感响应 |
| Partial | 如messages可读plan不可读，只在失败区域提示，不把整页判无任务 |
| Success | 显示后端权威结果；202只显示已受理，业务完成另等事件/快照 |
| Permission | 真实范围与审批状态；过期/未知/无权限分别处理 |

必须另测长中文任务名、长路径、英文Harness名、多行命令、20个工作包、无files、二进制artifact、无recipient、断线重连、权限撤销。reference只证明视觉主状态，不能代替这些交互验证。
