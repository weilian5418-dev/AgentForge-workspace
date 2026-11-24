import type { WorkspaceRepository } from './types'
import { useLibraryStore } from '@/stores/library'
import { usePreviewStore } from '@/stores/preview'
import type {
  ApprovalViewModel,
  TaskRowViewModel,
  TaskViewModel,
  UiStatus,
} from '@/domain/view-models'
import {
  approval,
  changeTotals,
  code,
  completedDeliveryRoles,
  deliveryCode,
  dependencies,
  documentMarkdown,
  executionMessages,
  files,
  harnessInventory,
  knowledge,
  project,
  roles,
  taskCounters,
  tasks,
  toolServices,
} from '@/fixtures/shared'

function mapStatus(status: string): UiStatus {
  if (
    status === 'running' ||
    status === 'completed' ||
    status === 'waiting' ||
    status === 'approval' ||
    status === 'paused' ||
    status === 'failed'
  ) {
    return status
  }
  return 'unknown'
}

function toTaskRows(): TaskRowViewModel[] {
  return [...usePreviewStore().addedTasks, ...tasks].map(task => ({
    id: task.id,
    goal: task.goal,
    status: mapStatus(task.status),
    statusLabel: task.statusLabel,
    harnesses: [...task.harnesses],
    updatedLabel: task.updatedLabel,
  }))
}

function buildTask(runId: string, delivery = false): TaskViewModel {
  const preview = usePreviewStore()
  const task = [...preview.addedTasks, ...tasks].find(item => item.id === runId)
  if (!task) throw new Error(`未找到任务 ${runId}`)
  const mappedRoles = delivery
    ? completedDeliveryRoles().map(role => ({
        id: role.id,
        label: role.label,
        harness: role.harness,
        status: 'completed' as const,
        workspace: role.workspace,
        x: role.x,
        y: role.y,
      }))
    : roles.map(role => ({
        id: role.id,
        label: role.label,
        harness: role.harness,
        status: mapStatus(role.status),
        workspace: role.workspace,
        x: role.x,
        y: role.y,
      }))

  return {
    id: task.id,
    goal: task.goal,
    status: delivery ? 'waiting' : mapStatus(task.status),
    statusLabel: delivery ? '等待验收' : task.statusLabel,
    project: { ...project },
    roles: preview.taskRoles[runId] ?? mappedRoles,
    dependencies: dependencies.filter(dep => (preview.taskRoles[runId] ?? mappedRoles).some(role=>role.id===dep.source) && (preview.taskRoles[runId] ?? mappedRoles).some(role=>role.id===dep.target)).map(dep => ({ ...dep })),
    files: files.map(file => ({ ...file })),
    messages: [...(runId.startsWith('preview-') ? [{id:`${runId}-goal`,kind:'user' as const,body:task.goal},{id:`${runId}-plan`,kind:'coordinator' as const,body:'任务安排已确认，可以继续补充目标和验收条件。'}] : executionMessages.map(message => ({
      id: message.id,
      kind: message.kind,
      body: message.body,
      name: 'name' in message ? message.name : undefined,
      roleId: 'roleId' in message ? message.roleId : undefined,
    }))), ...(preview.messages[runId] ?? [])],
    approval: delivery || runId.startsWith('preview-')
      ? null
      : {
          id: approval.id,
          title: approval.title,
          command: approval.command,
          reason: approval.reason,
          scope: approval.scope,
          directory: approval.directory,
          validity: approval.validity,
          time: approval.time,
          status: mapStatus(approval.status),
          actionDigest: 'demo-digest-export',
        },
    code: delivery ? { ...deliveryCode } : { ...code },
    changeTotals: changeTotals(),
  }
}

let localApproval: ApprovalViewModel = {
  id: approval.id,
  title: approval.title,
  command: approval.command,
  reason: approval.reason,
  scope: approval.scope,
  directory: approval.directory,
  validity: approval.validity,
  time: approval.time,
  status: mapStatus(approval.status),
  actionDigest: 'demo-digest-export',
}

export function createReferenceRepository(): WorkspaceRepository {
  return {
    capabilities: {
      knowledgeCrud: { supported: false, reason: 'reference 模式仅展示设计态，不写入真实知识库' },
      profileEdit: { supported: false, reason: 'reference 模式只读展示配置字段' },
      interactivePty: { supported: false, reason: '终端为只读日志视图' },
      boardView: { supported: true, reason: '看板视图可切换展示' },
    },
    async loadProject() {
      return { ...project }
    },
    async loadOverview() {
      const rows = toTaskRows()
      const continueSource = rows.filter(task => task.id === 'demo-order-export' || task.id === 'demo-permissions')
      const deliveries = rows.filter(task =>
        task.id === 'demo-payment' || task.id === 'demo-search' || task.id === 'demo-docs',
      )
      return {
        project: { ...project },
        contextBranch: 'main',
        continueTasks: continueSource.map(task => ({
          ...task,
          progressLabel: task.id === 'demo-order-export' ? '2 个 Agent 执行中' : '等待继续',
          actionLabel: task.id === 'demo-order-export' ? '继续' as const : '恢复' as const,
          updatedLabel: task.id === 'demo-order-export' ? '刚刚' : '1 小时前',
        })),
        recentDeliveries: deliveries.map((task, index) => ({
          ...task,
          deliveredAt: index === 2 ? '2025-01-14' : '2025-01-15',
        })),
        contextFiles: knowledge.slice(0, 3).map(item => ({ ...item })),
        attentionItems: [
          { id: 'attn-approval', kind: 'approval' as const, label: '1 项操作批准', to: '/inbox' },
          { id: 'attn-acceptance', kind: 'acceptance' as const, label: '1 项交付待验收', to: '/inbox' },
        ],
        attentionCount: 2,
        harnessConnected: harnessInventory.filter(item => item.connected).length,
        backendConnected: true,
        counters: taskCounters(),
      }
    },
    async loadTasks() {
      return toTaskRows()
    },
    async loadTask(runId) {
      return buildTask(runId, false)
    },
    async loadDelivery(runId) {
      return buildTask(runId, true)
    },
    async loadNewTaskDraft() {
      const scopes: Record<string, string> = {
        'demo-planner': '接口约定',
        'demo-frontend': '前端导出入口',
        'demo-backend': '导出接口',
        'demo-reviewer': '审查与回归',
      }
      return {
        goal: '为订单列表增加导出功能，支持当前筛选条件，前后端并行实现并补齐回归测试。',
        projectName: project.name,
        branch: project.branch,
        attachments: ['接口约定.md', '订单数据字典.md'],
        roles: roles.map(role => ({
          id: role.id,
          label: role.label,
          harness: role.harness,
          status: mapStatus(role.status),
          workspace: role.workspace,
          x: role.x,
          y: role.y,
          scope: scopes[role.id] ?? role.label,
        })),
        acceptance: ['支持当前筛选条件', '导出文件字段完整', '回归测试通过'],
        assignmentMode: 'manual',
        attentionNote: '安装依赖时向你请求批准',
      }
    },
    async loadInbox() {
      return [
        {
          id: localApproval.id,
          kind: 'approval' as const,
          title: '安装导出依赖',
          subtitle: '实现订单导出功能 · Frontend',
          time: '刚刚',
          status: localApproval.status,
          detail: { ...localApproval },
        },
        {
          id: 'demo-acceptance-migration',
          kind: 'acceptance' as const,
          title: '数据库迁移待验收',
          subtitle: '审查数据库迁移 · Reviewer',
          time: '20 分钟前',
          status: 'waiting' as const,
          detail: null,
        },
      ]
    },
    async loadExecutors() {
      return harnessInventory.map(item => ({
        id: item.id,
        name: item.name,
        description: item.description,
        capabilities: [...item.capabilities],
        profiles: item.profiles,
        activeInstances: item.activeInstances,
        connected: item.connected,
      }))
    },
    async loadProfile(profileId) {
      return {
        id: profileId,
        name: 'Frontend · Codex CLI',
        harness: 'Codex CLI',
        environment: 'host_authorized',
        model: '继承 Harness 默认模型',
        concurrency: '1',
        timeout: '1800s',
        editable: false,
        unsupportedReason: '当前后端仅支持 installationId + adapterId 创建，任意字段编辑未核实',
      }
    },
    async loadKnowledge() {
      return knowledge.map(item => ({ ...item }))
    },
    async loadDocument(documentId) {
      const local = useLibraryStore()
      const item = local.documents.find(doc=>doc.id===documentId) ?? knowledge.find(doc => doc.id === documentId) ?? knowledge[0]!
      return {
        id: item.id,
        name: item.name,
        markdown: local.documentEdits[documentId] ?? ('markdown' in item ? item.markdown : item.id==='demo-api-contract' ? documentMarkdown : `# ${item.name.replace(/\.[^.]+$/, '')}\n\n## 项目说明\n\n${item.scope}的共享资料，供执行者在任务中引用。\n\n## 使用约定\n\n- 开始任务前阅读相关规范。\n- 修改应包含清晰的说明与验证记录。\n- 对外接口变更需同步更新项目文档。`),
        scope: item.scope,
        updated: item.updated,
        type: item.type,
      }
    },
    async loadTools() {
      return toolServices.map(item => ({ ...item }))
    },
    async loadSettings() {
      return {
        endpoint: '127.0.0.1:8765',
        theme: 'light',
        accent: 'lavender',
        services: [
          { id: 'api', name: '后端服务', status: 'ok' as const, detail: '可用' },
          { id: 'db', name: '数据库', status: 'ok' as const, detail: '可用' },
          { id: 'events', name: '事件流', status: 'ok' as const, detail: '已连接' },
          { id: 'obs', name: '可观测性', status: 'warn' as const, detail: '未配置' },
        ],
        observabilityConfigured: false,
        alerts: [],
      }
    },
    async sendMessage(input) {
      return { id: `demo-msg-${Date.now()}`, body: input.text }
    },
    async decideApproval(input) {
      localApproval = {
        ...localApproval,
        status: input.decision === 'allow_once' ? 'completed' : 'failed',
      }
      return { ...localApproval }
    },

  }
}
