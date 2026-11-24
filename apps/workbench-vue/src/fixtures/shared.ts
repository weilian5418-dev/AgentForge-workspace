export type ReferenceStatus = 'running' | 'completed' | 'waiting' | 'approval' | 'paused'

export interface ReferenceTask {
  id: string
  goal: string
  status: ReferenceStatus
  statusLabel: string
  harnesses: readonly string[]
  updatedLabel: string
}

export const project = {
  id: 'demo-commerce',
  name: '电商平台',
  repository: 'agentforge-shop',
  branch: 'feat/order-export',
} as const

export const tasks: readonly ReferenceTask[] = [
  { id: 'demo-order-export', goal: '实现订单导出功能', status: 'running', statusLabel: '执行中', harnesses: ['Codex CLI', 'Claude Code'], updatedLabel: '刚刚' },
  { id: 'demo-search-cache', goal: '接入商品搜索缓存', status: 'running', statusLabel: '执行中', harnesses: ['Claude Code'], updatedLabel: '3 分钟前' },
  { id: 'demo-migration', goal: '审查数据库迁移', status: 'waiting', statusLabel: '待验收', harnesses: ['OpenCode'], updatedLabel: '20 分钟前' },
  { id: 'demo-login-dependency', goal: '更新登录依赖', status: 'approval', statusLabel: '待批准', harnesses: ['Codex CLI'], updatedLabel: '30 分钟前' },
  { id: 'demo-payment', goal: '修复支付回调', status: 'completed', statusLabel: '已完成', harnesses: ['Claude Code'], updatedLabel: '今天' },
  { id: 'demo-search', goal: '优化商品搜索', status: 'completed', statusLabel: '已完成', harnesses: ['Codex CLI'], updatedLabel: '今天' },
  { id: 'demo-docs', goal: '更新部署文档', status: 'completed', statusLabel: '已完成', harnesses: ['OpenCode'], updatedLabel: '昨天' },
  { id: 'demo-permissions', goal: '重构权限模块', status: 'paused', statusLabel: '已暂停', harnesses: ['Codex CLI'], updatedLabel: '昨天' },
]

export const roles = [
  { id: 'demo-planner', label: 'Planner', harness: 'Codex CLI', status: 'completed', workspace: '', x: 0, y: 130 },
  { id: 'demo-frontend', label: 'Frontend', harness: 'Codex CLI', status: 'running', workspace: 'worktree/ui', x: 386, y: 50 },
  { id: 'demo-backend', label: 'Backend', harness: 'Claude Code', status: 'running', workspace: 'worktree/api', x: 386, y: 184 },
  { id: 'demo-reviewer', label: 'Reviewer', harness: 'OpenCode', status: 'waiting', workspace: '', x: 792, y: 130 },
] as const

export const dependencies = [
  { id: 'planner-frontend', source: 'demo-planner', target: 'demo-frontend', completed: true },
  { id: 'planner-backend', source: 'demo-planner', target: 'demo-backend', completed: true },
  { id: 'frontend-reviewer', source: 'demo-frontend', target: 'demo-reviewer', completed: false },
  { id: 'backend-reviewer', source: 'demo-backend', target: 'demo-reviewer', completed: false },
] as const

export const files = [
  { id: 'demo-toolbar', name: 'OrderToolbar.vue', additions: 38, deletions: 4, language: 'html' },
  { id: 'demo-export', name: 'export.ts', additions: 42, deletions: 0, language: 'typescript' },
  { id: 'demo-api', name: 'order_export.py', additions: 61, deletions: 0, language: 'python' },
  { id: 'demo-tests', name: 'test_export.py', additions: 24, deletions: 0, language: 'python' },
] as const

export const code = {
  original: `<script setup lang="ts">
</script>

<template>
  <div class="toolbar" />
</template>`,
  modified: `<script setup lang="ts">
async function exportOrders() {
  const filters = getActiveFilters()
  await downloadOrders(filters)
}
</script>

<template>
  <el-button type="primary" @click="exportOrders">导出订单</el-button>
</template>`,
} as const

export const deliveryCode = {
  original: `<script setup lang="ts">
</script>

<template>
  <div class="toolbar" />
</template>`,
  modified: `<script setup lang="ts">
const exporting = ref(false)

async function exportOrdersHandler() {
  exporting.value = true
  try {
    const filters = getActiveFilters()
    await downloadOrders(filters)
  } finally {
    exporting.value = false
  }
}
</script>

<template>
  <el-button type="primary" :loading="exporting" @click="exportOrdersHandler">
    导出订单
  </el-button>
</template>`,
} as const

export const deliverySummary = [
  '新增订单导出入口',
  '支持筛选条件与字段校验',
  '补齐导出接口测试',
] as const

export const acceptanceChecklist = [
  '筛选条件与导出结果一致',
  '导出文件字段完整',
  '12 项回归测试通过',
] as const

export const deliveryAttachments = [
  { id: 'note', name: '实现说明.md', action: '下载' as const },
  { id: 'report', name: '测试报告', action: '查看' as const },
  { id: 'sample', name: '导出示例.xlsx', action: '下载' as const },
] as const

export const executionMessages = [
  { id: 'demo-msg-user', kind: 'user', body: '为订单列表增加导出功能，支持筛选条件，前后端一起完成。' },
  { id: 'demo-msg-coordinator', kind: 'coordinator', name: '协调者', body: '前后端并行开发，完成后交给审查者验证。' },
  { id: 'demo-msg-handoff', kind: 'handoff', body: 'Planner 已将接口约定交给开发者' },
  { id: 'demo-msg-frontend', kind: 'agent', roleId: 'demo-frontend', name: 'Frontend · Codex CLI', body: '导出入口已加入工具栏，正在接入筛选参数。' },
] as const

export const approval = {
  id: 'demo-approval-export',
  title: '安装导出依赖',
  command: 'pnpm add xlsx',
  reason: '导出功能需要生成 XLSX 文件，执行者请求安装项目依赖。',
  scope: '当前项目',
  directory: 'agentforge-shop',
  validity: '仅本次操作',
  time: '14:32',
  status: 'approval',
} as const

export const knowledge = [
  { id: 'demo-api-contract', name: '接口约定.md', type: 'Markdown', scope: '电商平台', updated: '今天', ready: true },
  { id: 'demo-coding-standard', name: '编码规范.md', type: 'Markdown', scope: '工作区', updated: '今天', ready: true },
  { id: 'demo-order-dictionary', name: '订单数据字典.md', type: 'Markdown', scope: '订单模块', updated: '昨天', ready: true },
  { id: 'demo-product-openapi', name: '商品接口.openapi.yaml', type: 'OpenAPI', scope: '商品模块', updated: '昨天', ready: true },
  { id: 'demo-export-requirements', name: '导出需求说明.pdf', type: 'PDF', scope: '当前任务', updated: '今天', ready: false },
  { id: 'demo-deployment', name: '部署说明.md', type: 'Markdown', scope: '工作区', updated: '昨天', ready: true },
] as const

export const documentMarkdown = `## 订单导出接口

按当前筛选条件导出订单，返回 XLSX 文件。

\`GET /api/orders/export\`

## 请求参数

| 参数名 | 类型 | 说明 |
| --- | --- | --- |
| status | string | 订单状态 |
| start_date | date | 开始日期 |
| end_date | date | 结束日期 |

## 返回结果

- 文件类型：XLSX
- 字段：订单编号、金额、状态、创建时间

## 实现约束

- 使用当前筛选条件
- 导出范围与权限一致
- 包含接口回归测试
`

export const harnessInventory = [
  {
    id: 'demo-codex',
    name: 'Codex CLI',
    description: '命令行编码执行环境',
    capabilities: ['代码编辑', '终端', '工具调用'],
    profiles: 2,
    activeInstances: 1,
    connected: true,
  },
  {
    id: 'demo-claude',
    name: 'Claude Code',
    description: '多步骤代码与工具执行',
    capabilities: ['代码编辑', '工具调用', '终端'],
    profiles: 1,
    activeInstances: 1,
    connected: true,
  },
  {
    id: 'demo-opencode',
    name: 'OpenCode',
    description: '可配置的开放执行环境',
    capabilities: ['代码编辑', '工具调用'],
    profiles: 1,
    activeInstances: 0,
    connected: true,
  },
] as const

export const toolServices = [
  { id: 'demo-files', name: '项目文件', capabilities: ['读取文件', '搜索代码'], scope: '当前项目', connected: true },
  { id: 'demo-terminal', name: '终端工具', capabilities: ['执行命令', '运行测试'], scope: '按任务授权', connected: true },
  { id: 'demo-git', name: 'Git', capabilities: ['查看差异', '工作区管理'], scope: '当前项目', connected: true },
  { id: 'demo-browser', name: '浏览器工具', capabilities: ['页面预览', '界面验证'], scope: '—', connected: false },
] as const

export function taskCounters() {
  return {
    all: tasks.length,
    active: tasks.filter(task => task.status === 'running').length,
    attention: tasks.filter(task => task.status === 'waiting' || task.status === 'approval').length,
    completed: tasks.filter(task => task.status === 'completed').length,
    paused: tasks.filter(task => task.status === 'paused').length,
  }
}

export function completedDeliveryRoles() {
  return roles.map(role => ({ ...role, status: 'completed' as const }))
}

export function changeTotals() {
  return files.reduce((total, file) => ({ additions: total.additions + file.additions, deletions: total.deletions + file.deletions }), { additions: 0, deletions: 0 })
}
