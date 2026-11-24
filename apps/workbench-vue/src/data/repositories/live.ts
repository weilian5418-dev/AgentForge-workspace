import { createApiClient, HttpError } from '@/data/http'
import type { WorkspaceRepository } from './types'
import type { components } from '@/data/contracts/api.generated'
import type {
  ProjectViewModel,
  TaskRowViewModel,
  TaskViewModel,
  UiStatus,
} from '@/domain/view-models'

function unsupported(reason: string): never {
  throw new Error(reason)
}

function formatUpdated(value?: string): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const now = new Date()
  const time = date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  return date.toDateString() === now.toDateString() ? `今天 ${time}` : date.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function mapRunStatus(status: string | undefined): UiStatus {
  switch (status) {
    case 'preparing':
    case 'planning':
    case 'awaiting_confirmation':
    case 'queued':
    case 'running':
    case 'executing':
      return status === 'running' || status === 'executing' ? 'running' : 'waiting'
    case 'completed':
    case 'succeeded':
      return 'completed'
    case 'awaiting_acceptance':
    case 'waiting_acceptance':
    case 'waiting':
      return 'waiting'
    case 'awaiting_approval':
    case 'approval':
    case 'pending':
    case 'manual_required':
    case 'delivery_unknown':
    case 'mapping_unsupported':
      return 'approval'
    case 'confirmed':
    case 'consumed':
    case 'approved':
    case 'user_decided':
    case 'forwarding':
      return 'completed'
    case 'rejected':
    case 'expired':
      return 'failed'
    case 'paused':
      return 'paused'
    case 'failed':
    case 'blocked':
    case 'canceled':
    case 'cancelled':
      return 'failed'
    default:
      return 'unknown'
  }
}

export function createLiveRepository(getToken: () => string | null, getBase = () => '/api/v1', getProjectId: () => string | null = () => null): WorkspaceRepository {
  const request = createApiClient(getToken, getBase)
  type Schema = components['schemas']
  async function projects(signal?: AbortSignal): Promise<ProjectViewModel[]> {
    const response = await request<Schema['ProjectListResponse']>('/projects?limit=100', { signal })
    return (response.items ?? []).map(item => ({ id: item.id, name: item.name ?? item.displayName ?? item.id, branch: item.branch ?? undefined, repository: item.rootPath, environmentId: item.environmentId, grantStatus: item.grantStatus, baselineId: item.currentBaselineId }))
  }

  return {
    loadProjects: projects,
    createRun: input => request('/runs', { method: 'POST', body: JSON.stringify(input) }),
    async uploadInput(projectId, file) {
      const body = new FormData()
      body.append('file', file)
      const response = await request<Schema['ProjectInputResponse']>(`/projects/${encodeURIComponent(projectId)}/inputs`, { method: 'POST', body })
      return response.id
    },
    async loadPlan(runId, signal) {
      try { return await request<Schema['PlanResponse']>(`/runs/${encodeURIComponent(runId)}/plan`, { signal }) }
      catch (error) { if (error instanceof HttpError && error.status === 404) return null; throw error }
    },
    proposePlan: (runId, key) => request(`/runs/${encodeURIComponent(runId)}/plan-proposals`, { method: 'POST', body: JSON.stringify({ source: 'rules', idempotencyKey: key }) }),
    async loadProfiles(signal) {
      const response = await request<Schema['AgentProfileListResponse']>('/agent-profiles?limit=100', { signal })
      return response.items ?? []
    },
    async loadInstances(signal) {
      const project = await this.loadProject(signal)
      if (!project) return []
      const runs = await this.loadTasks(project.id, signal)
      const pages = await Promise.all(runs.filter(run => run.status === 'running' || run.status === 'paused').map(run => request<Schema['AgentInstanceListResponse']>(`/runs/${encodeURIComponent(run.id)}/instances`, { signal })))
      return pages.flatMap(page => page.items ?? [])
    },
    async discoverExecutors() {
      return request('/agent-products/discover', { method: 'POST', body: JSON.stringify({ environments: ['windows_native'], persist: true }) })
    },
    createProfile: (installationId, adapterId) => request('/agent-profiles', { method: 'POST', body: JSON.stringify({ installationId, adapterId }) }),
    assignRoles: (runId, input) => request(`/runs/${encodeURIComponent(runId)}/assignments`, { method: 'PUT', body: JSON.stringify(input) }),
    decidePlan: (runId, input) => request(`/runs/${encodeURIComponent(runId)}/plan-decisions`, { method: 'POST', body: JSON.stringify(input) }),
    runAction: (runId, input) => request(`/runs/${encodeURIComponent(runId)}/actions`, { method: 'POST', body: JSON.stringify(input) }),
    prepareWorkspaces: (runId) => request(`/runs/${encodeURIComponent(runId)}/workspaces/prepare`, { method: 'POST', body: '{}' }),
    async requestExecution(runId, input, key) {
      try {
        await request(`/runs/${encodeURIComponent(runId)}/workspaces/prepare`, { method: 'POST', body: '{}' })
      } catch {
        // Existing active workspaces are fine; execution will validate binding.
      }
      return request(`/runs/${encodeURIComponent(runId)}/execution-requests`, { method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify(input) })
    },
    acceptDelivery: (runId, revision, input, key) => request(`/runs/${encodeURIComponent(runId)}/acceptance`, { method: 'POST', headers: { 'Idempotency-Key': key, 'If-Match': `"delivery:${revision}"` }, body: JSON.stringify(input) }),
    loadDeliveryPointer: (runId, signal) => request(`/runs/${encodeURIComponent(runId)}/delivery`, { signal }),
    loadManifest: (runId, hash, signal) => request(`/runs/${encodeURIComponent(runId)}/delivery-manifests/${encodeURIComponent(hash)}`, { signal }),
    async loadArtifact(candidateId, signal) {
      const token = getToken()
      if (!token) throw new Error('请先连接后端服务')
      const response = await fetch(`${getBase()}/artifacts/candidates/${encodeURIComponent(candidateId)}/content`, { headers: { Authorization: `Bearer ${token}` }, signal })
      if (!response.ok) throw new HttpError(response.status, response.headers.get('X-Request-ID'), await response.text())
      return response.blob()
    },
    registerTool: input => request('/tool-servers', { method: 'POST', body: JSON.stringify(input) }),
    probeTool: (id, version) => request(`/tool-servers/${encodeURIComponent(id)}/probes?expected_config_version=${version}`, { method: 'POST' }),
    async loadTemplates(signal) { return (await request<Schema['TemplateListResponse']>('/templates?limit=100', { signal })).items ?? [] },
    loadProviders: signal => request('/providers', { signal }),
    capabilities: {
      knowledgeCrud: {
        supported: true,
        reason: '项目知识文档基于 project inputs 提供列表/创建/读取/更新/删除',
      },
      profileEdit: {
        supported: true,
        reason: '支持安全字段 PATCH（显示名/并发/超时），不改动 adapter 绑定',
      },
      interactivePty: {
        supported: true,
        reason: '本机 loopback 终端会话支持命令执行；完整 PTY 流式后续增强',
      },
      boardView: {
        supported: true,
        reason: '任务库按状态分组的看板视图可用',
      },
      harnessBrowser: {
        supported: true,
        reason: '交付候选可创建本机 loopback 静态预览',
      },
    },
    async loadProject(signal) {
      const items = await projects(signal)
      return items.find(project => project.id === getProjectId()) ?? items.find(project => project.grantStatus === 'active' && project.baselineId) ?? items[0] ?? null
    },
    async loadOverview(projectId, signal) {
      const [tasks, executors] = await Promise.all([this.loadTasks(projectId, signal), this.loadExecutors(signal)])
      const project = (await this.loadProject(signal)) ?? { id: projectId, name: projectId }
      const attentionTasks = tasks.filter(task => task.status === 'waiting' || task.status === 'approval')
      return {
        project,
        contextBranch: project.branch ?? '分支将在执行工作区中确定',
        continueTasks: tasks
          .filter(task => task.status === 'running' || task.status === 'paused')
          .slice(0, 4)
          .map(task => ({
            ...task,
            progressLabel: task.status === 'running' ? '执行中' : '等待继续',
            actionLabel: task.status === 'running' ? '继续' as const : '恢复' as const,
          })),
        recentDeliveries: tasks
          .filter(task => task.status === 'completed' || task.statusLabel === '等待验收')
          .slice(0, 5)
          .map(task => ({
            ...task,
            deliveredAt: task.updatedLabel,
          })),
        contextFiles: [],
        attentionItems: attentionTasks.slice(0, 2).map(task => ({
          id: task.id,
          kind: task.status === 'approval' ? 'approval' as const : 'acceptance' as const,
          label: task.goal,
          to: task.status === 'approval' ? '/inbox' : task.statusLabel === '等待验收' ? `/tasks/${task.id}/delivery` : `/tasks/${task.id}`,
        })),
        attentionCount: attentionTasks.length,
        harnessConnected: executors.filter(item => item.connected).length,
        backendConnected: true,
        counters: {
          all: tasks.length,
          active: tasks.filter(task => task.status === 'running').length,
          attention: attentionTasks.length,
          completed: tasks.filter(task => task.status === 'completed').length,
          paused: tasks.filter(task => task.status === 'paused').length,
        },
      }
    },
    async loadTasks(projectId, signal) {
      const response = await request<{
        items?: Array<{
          id: string
          goal?: string
          status?: string
          statusLabel?: string
          harnesses?: string[]
          updatedAt?: string
        }>
      }>(`/runs?projectId=${encodeURIComponent(projectId)}&limit=50`, { signal })
      return (response.items ?? []).map((item): TaskRowViewModel => ({
        id: item.id,
        goal: item.goal ?? item.id,
        status: mapRunStatus(item.status),
        statusLabel: item.statusLabel ?? item.status ?? '状态未知',
        harnesses: item.harnesses ?? [],
        updatedLabel: formatUpdated(item.updatedAt),
      }))
    },
    async loadTask(runId, signal) {
      const snapshot = await request<{
        run: Schema['RunOverview']
        latestSequence: number
        guidance?: Schema['UserGuidance'] | null
        currentDelivery?: Schema['DeliveryOverview'] | null
        id?: string
        goal?: string
        status?: string
        statusLabel?: string
        project?: ProjectViewModel
        roles?: Array<{
          id: string
          label?: string
          harness?: string
          status?: string
          workspace?: string
          x?: number
          y?: number
        }>
        dependencies?: Array<{ id: string; source: string; target: string; completed?: boolean }>
        files?: Array<{
          id?: string
          name?: string
          relativePath?: string
          additions?: number
          deletions?: number
          language?: string
        }>
        messages?: Array<{
          id: string
          kind?: string
          body?: string
          name?: string
          roleId?: string
        }>
        approval?: {
          id: string
          title?: string
          command?: string
          reason?: string
          scope?: string
          directory?: string
          validity?: string
          time?: string
          status?: string
          actionDigest?: string
        } | null
        code?: { original?: string; modified?: string }
        changeTotals?: { additions?: number; deletions?: number }
      }>(`/runs/${encodeURIComponent(runId)}/snapshot`, { signal })
      const instances = await request<Schema['AgentInstanceListResponse']>(`/runs/${encodeURIComponent(runId)}/instances`, { signal })
      const task: TaskViewModel = {
        id: snapshot.id ?? runId,
        goal: snapshot.goal ?? runId,
        status: mapRunStatus(snapshot.status),
        statusLabel: snapshot.statusLabel ?? snapshot.status ?? '状态未知',
        project: snapshot.project ?? { id: 'unknown', name: '未命名项目' },
        roles: (snapshot.roles ?? []).map((role, index) => ({
          id: role.id,
          label: role.label ?? role.id,
          harness: role.harness ?? '未分配',
          status: mapRunStatus(role.status),
          workspace: role.workspace,
          x: role.x ?? (index % 3) * 386,
          y: role.y ?? Math.floor(index / 3) * 134,
        })),
        dependencies: (snapshot.dependencies ?? []).map(dependency => ({
          id: dependency.id,
          source: dependency.source,
          target: dependency.target,
          completed: dependency.completed ?? false,
        })),
        files: (snapshot.files ?? []).map((file, index) => ({
          id: file.id ?? file.relativePath ?? `file-${index}`,
          name: file.name ?? file.relativePath ?? '未命名文件',
          additions: file.additions ?? 0,
          deletions: file.deletions ?? 0,
          language: file.language ?? 'text',
        })),
        messages: (snapshot.messages ?? []).map(message => ({
          id: message.id,
          kind: ['user', 'coordinator', 'handoff', 'agent'].includes(message.kind ?? '')
            ? (message.kind as 'user' | 'coordinator' | 'handoff' | 'agent')
            : 'agent',
          body: message.body ?? '',
          name: message.name,
          roleId: message.roleId,
        })),
        approval: snapshot.approval
          ? {
              id: snapshot.approval.id,
              title: snapshot.approval.title ?? snapshot.approval.id,
              command: snapshot.approval.command ?? '',
              reason: snapshot.approval.reason ?? '',
              scope: snapshot.approval.scope ?? '',
              directory: snapshot.approval.directory ?? '',
              validity: snapshot.approval.validity ?? '',
              time: snapshot.approval.time ?? '—',
              status: mapRunStatus(snapshot.approval.status),
              actionDigest: snapshot.approval.actionDigest,
            }
          : null,
        code: {
          original: snapshot.code?.original ?? '',
          modified: snapshot.code?.modified ?? '',
        },
        changeTotals: {
          additions: snapshot.changeTotals?.additions ?? 0,
          deletions: snapshot.changeTotals?.deletions ?? 0,
        },
        live: {
          status: snapshot.run.status,
          statusVersion: snapshot.run.statusVersion,
          planVersion: snapshot.run.planVersion ?? null,
          latestSequence: snapshot.latestSequence,
          guidance: snapshot.guidance ?? null,
          delivery: snapshot.currentDelivery ?? null,
          recipients: (instances.items ?? []).filter(item => item.lifecycle === 'active').map(item => ({ id: item.instanceId, label: `${item.roleId} · ${item.activity}` })),
        },
      }
      return task
    },
    async loadDelivery(runId, signal) {
      return this.loadTask(runId, signal)
    },
    async loadNewTaskDraft(signal) {
      const project = await this.loadProject(signal)
      return {
        goal: '',
        projectName: project?.name ?? '',
        branch: project?.branch ?? '',
        attachments: [],
        roles: [],
        acceptance: [],
        assignmentMode: 'manual' as const,
        attentionNote: '创建后需确认计划与分配，不会自动开始执行',
      }
    },
    async loadInbox(signal) {
        const response = await request<{
          items?: Array<{
            id: string
            title?: string
            command?: string
            reason?: string
            scope?: string
            requestedScope?: string
            actionDigest?: string
            createdAt?: string
            expiresAt?: string
            status?: string
          }>
        }>('/approvals?limit=50', { signal })
        return (response.items ?? []).map(item => ({
          id: item.id,
          kind: 'approval' as const,
          title: item.title ?? item.id,
          subtitle: item.command ?? '',
          time: item.createdAt ?? '—',
          status: mapRunStatus(item.status),
          detail: {
            id: item.id,
            title: item.title ?? item.id,
            command: item.command ?? '',
            reason: item.reason ?? '',
            scope: item.scope ?? item.requestedScope ?? '',
            directory: '',
            validity: item.expiresAt ?? '',
            time: item.createdAt ?? '—',
            status: mapRunStatus(item.status),
            actionDigest: item.actionDigest,
          },
        }))
    },
    async loadExecutors(signal) {
        const summary = await request<{
          products?: Array<{
            id: string
            name: string
            profiles?: number
            activeInstances?: number
            connected?: boolean
          }>
        }>('/agents/summary?environmentId=windows_native', { signal })
        return (summary.products ?? []).map(item => ({
          id: item.id,
          name: item.name,
          description: '已发现的执行环境',
          capabilities: [] as string[],
          profiles: item.profiles ?? 0,
          activeInstances: item.activeInstances ?? 0,
          connected: item.connected ?? false,
        }))
    },
    async loadProfile(profileId, signal) {
      const profile = await request<{
        id?: string
        profileId?: string
        name?: string
        adapterId?: string
        environmentId?: string
        authStatus?: string
        accessLevel?: string
        lastValidationId?: string | null
        concurrency?: string
        timeout?: string
      }>(`/agent-profiles/${encodeURIComponent(profileId)}`, { signal })
      return {
        id: profile.profileId ?? profile.id ?? profileId,
        name: profile.name ?? profile.adapterId ?? profileId,
        harness: profile.adapterId ?? '未知',
        environment: profile.environmentId ?? '—',
        model: '凭据与模型由执行环境管理',
        concurrency: profile.concurrency ?? '—',
        timeout: profile.timeout ?? '—',
        editable: true,
        authStatus: profile.authStatus,
        accessLevel: profile.accessLevel,
        validationId: profile.lastValidationId,
      }
    },
    async updateProfile(profileId, input) {
      return request(`/agent-profiles/${encodeURIComponent(profileId)}`, {
        method: 'PATCH',
        body: JSON.stringify(input),
      })
    },
    async loadKnowledge(signal) {
      const project = await this.loadProject(signal)
      if (!project) return []
      const response = await request<{
        items?: Array<{
          id: string
          name: string
          docType?: string
          scope?: string
          updatedAt?: string
          ready?: boolean
        }>
      }>(`/projects/${encodeURIComponent(project.id)}/knowledge-documents`, { signal })
      return (response.items ?? []).map(item => ({
        id: item.id,
        name: item.name,
        type: item.docType ?? 'Markdown',
        scope: item.scope ?? '当前项目',
        updated: formatUpdated(item.updatedAt),
        ready: item.ready ?? true,
      }))
    },
    async loadDocument(documentId, signal) {
      const project = await this.loadProject(signal)
      if (!project) unsupported('请先选择项目后再打开知识文档')
      const doc = await request<{
        id: string
        name: string
        markdown: string
        scope?: string
        updatedAt?: string
        docType?: string
      }>(`/projects/${encodeURIComponent(project.id)}/knowledge-documents/${encodeURIComponent(documentId)}`, { signal })
      return {
        id: doc.id,
        name: doc.name,
        markdown: doc.markdown,
        scope: doc.scope ?? '当前项目',
        updated: formatUpdated(doc.updatedAt),
        type: doc.docType ?? 'Markdown',
      }
    },
    async createKnowledgeDocument(input) {
      const project = await this.loadProject()
      if (!project) throw new Error('请先选择项目')
      return request(`/projects/${encodeURIComponent(project.id)}/knowledge-documents`, {
        method: 'POST',
        body: JSON.stringify(input),
      })
    },
    async updateKnowledgeDocument(documentId, content) {
      const project = await this.loadProject()
      if (!project) throw new Error('请先选择项目')
      return request(`/projects/${encodeURIComponent(project.id)}/knowledge-documents/${encodeURIComponent(documentId)}`, {
        method: 'PUT',
        body: JSON.stringify({ content }),
      })
    },
    async deleteKnowledgeDocument(documentId) {
      const project = await this.loadProject()
      if (!project) throw new Error('请先选择项目')
      await request(`/projects/${encodeURIComponent(project.id)}/knowledge-documents/${encodeURIComponent(documentId)}`, {
        method: 'DELETE',
      })
    },
    async createTerminalSession(runId) {
      return request(`/runs/${encodeURIComponent(runId)}/terminal-sessions`, {
        method: 'POST',
        body: JSON.stringify({ shell: 'powershell' }),
      })
    },
    async runTerminalCommand(sessionId, command) {
      return request(`/terminal-sessions/${encodeURIComponent(sessionId)}/commands`, {
        method: 'POST',
        body: JSON.stringify({ command }),
      })
    },
    async createArtifactPreview(candidateId) {
      return request(`/artifacts/candidates/${encodeURIComponent(candidateId)}/previews`, {
        method: 'POST',
        body: '{}',
      })
    },
    async createProvider(input) {
      return request('/providers', { method: 'POST', body: JSON.stringify(input) })
    },
    async updateProvider(providerId, input) {
      return request(`/providers/${encodeURIComponent(providerId)}`, {
        method: 'PATCH',
        body: JSON.stringify(input),
      })
    },
    async probeProvider(providerId) {
      return request(`/providers/${encodeURIComponent(providerId)}/probe`, {
        method: 'POST',
        body: JSON.stringify({ authorizeNetwork: true, asyncDispatch: false, probeTools: false }),
      })
    },
    async previewTemplate(runId, input) {
      return request(`/runs/${encodeURIComponent(runId)}/template-previews`, {
        method: 'POST',
        body: JSON.stringify(input),
      })
    },
    async applyTemplatePreview(previewId, input, key) {
      return request(`/template-previews/${encodeURIComponent(previewId)}/apply`, {
        method: 'POST',
        headers: { 'Idempotency-Key': key },
        body: JSON.stringify(input),
      })
    },
    async loadTools(signal) {
        const response = await request<{
          items?: Array<{
            id?: string
            name?: string
            serverId?: string
            displayName?: string
            capabilities?: string[]
            scope?: string
            connected?: boolean
            status?: string
            supportedProtocols?: string[]
            configVersion?: number
          }>
        }>('/tool-servers', { signal })
        return (response.items ?? []).map(item => ({
          id: item.id ?? item.serverId ?? 'unknown-tool-server',
          name: item.name ?? item.displayName ?? item.serverId ?? '未命名工具服务',
          capabilities: item.capabilities ?? [],
          scope: item.scope ?? '当前项目',
          // `enabled` is a persisted configuration state, not proof that the
          // latest probe or catalog connection succeeded.
          connected: item.connected ?? false,
          configVersion: item.configVersion,
          status: item.status,
        }))
    },
    async loadSettings(signal) {
      try {
        const response = await fetch(`${getBase()}/health`, { signal })
        if (!response.ok) throw new HttpError(response.status, response.headers.get('X-Request-ID'), await response.text())
        const health = await response.json() as { status?: string }
        const ready = getToken() ? await request<Schema['ReadinessResponse']>('/ready', { signal }).catch(error => {
          if (error instanceof HttpError && error.status === 503) return JSON.parse(error.responseBody) as Schema['ReadinessResponse']
          throw error
        }) : null
        return {
          endpoint: '127.0.0.1:8765',
          theme: 'light' as const,
          accent: 'lavender' as const,
          services: [
            {
              id: 'api',
              name: 'API 服务',
              status: health.status === 'ok' ? 'ok' as const : 'warn' as const,
              detail: health.status ?? '未知',
            },
            { id: 'database', name: '数据库', status: ready?.database.status === 'available' ? 'ok' as const : 'warn' as const, detail: ready?.database.message ?? '连接后检测' },
            { id: 'admission', name: '任务接收', status: ready?.acceptingWork ? 'ok' as const : 'warn' as const, detail: ready?.acceptingWork ? '允许创建任务' : ready?.reason ?? '等待认证' },
          ],
          observabilityConfigured: false,
          alerts: ready?.reason ? [ready.reason] : [],
        }
      } catch {
        return {
          endpoint: '127.0.0.1:8765',
          theme: 'light' as const,
          accent: 'lavender' as const,
          services: [{ id: 'api', name: 'API 服务', status: 'down' as const, detail: '无法连接' }],
          observabilityConfigured: false,
          alerts: ['后端不可用'],
        }
      }
    },
    async sendMessage(input) {
      if (!input.recipientRef) throw new Error('请先选择一个正在运行的消息接收者')
      return request(`/runs/${encodeURIComponent(input.runId)}/messages`, {
        method: 'POST',
        body: JSON.stringify({
          text: input.text,
          recipientRef: input.recipientRef,
        }),
        headers: { 'Idempotency-Key': input.idempotencyKey },
      })
    },
    async decideApproval(input) {
      if (!input.actionDigest) {
        throw new Error('缺少 actionDigest，无法提交审批决定')
      }
      return request(`/approvals/${encodeURIComponent(input.approvalId)}/decision`, {
        method: 'POST',
        body: JSON.stringify({
          decision: input.decision,
          actionDigest: input.actionDigest,
        }),
        headers: { 'Idempotency-Key': input.idempotencyKey },
      })
    },
  }
}
