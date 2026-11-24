export type UiStatus =
  | 'running'
  | 'completed'
  | 'waiting'
  | 'approval'
  | 'paused'
  | 'failed'
  | 'unknown'

export type FeatureAvailability =
  | { supported: true; reason?: string }
  | { supported: false; reason: string }

export interface ProjectViewModel {
  id: string
  name: string
  repository?: string
  branch?: string
  environmentId?: string
  grantStatus?: string
  baselineId?: string | null
}

export interface TaskRowViewModel {
  id: string
  goal: string
  status: UiStatus
  statusLabel: string
  harnesses: readonly string[]
  updatedLabel: string
}

export interface AgentRoleViewModel {
  id: string
  label: string
  harness: string
  status: UiStatus
  workspace?: string
  x: number
  y: number
}

export interface DependencyViewModel {
  id: string
  source: string
  target: string
  completed: boolean
}

export interface FileChangeViewModel {
  id: string
  name: string
  additions: number
  deletions: number
  language: string
}

export interface MessageViewModel {
  id: string
  kind: 'user' | 'coordinator' | 'handoff' | 'agent'
  body: string
  name?: string
  roleId?: string
}

export interface ApprovalViewModel {
  id: string
  title: string
  command: string
  reason: string
  scope: string
  directory: string
  validity: string
  time: string
  status: UiStatus
  actionDigest?: string
}

export interface KnowledgeItemViewModel {
  id: string
  name: string
  type: string
  scope: string
  updated: string
  ready: boolean
}

export interface HarnessInventoryViewModel {
  id: string
  name: string
  description: string
  capabilities: readonly string[]
  profiles: number
  activeInstances: number
  connected: boolean
}

export interface ToolServiceViewModel {
  id: string
  name: string
  capabilities: readonly string[]
  scope: string
  connected: boolean
  configVersion?: number
  status?: string
}

export interface TaskViewModel {
  id: string
  goal: string
  status: UiStatus
  statusLabel: string
  project: ProjectViewModel
  roles: AgentRoleViewModel[]
  dependencies: DependencyViewModel[]
  files: FileChangeViewModel[]
  messages: MessageViewModel[]
  approval: ApprovalViewModel | null
  code: { original: string; modified: string }
  changeTotals: { additions: number; deletions: number }
  live?: {
    status: string
    statusVersion: number
    planVersion: number | null
    latestSequence: number
    guidance: import('@/data/contracts/api.generated').components['schemas']['UserGuidance'] | null
    delivery: import('@/data/contracts/api.generated').components['schemas']['DeliveryOverview'] | null
    recipients: { id: string; label: string }[]
  }
}

export interface SendMessageInput {
  runId: string
  text: string
  idempotencyKey: string
  recipientRef?: string
}

export interface SentMessageViewModel {
  id: string
  body: string
}

export interface ApprovalDecisionInput {
  approvalId: string
  decision: 'allow_once' | 'deny'
  actionDigest: string
  idempotencyKey: string
}

export interface OverviewContinueTaskViewModel extends TaskRowViewModel {
  progressLabel: string
  actionLabel: '继续' | '恢复'
}

export interface OverviewDeliveryViewModel extends TaskRowViewModel {
  deliveredAt: string
}

export interface OverviewAttentionItemViewModel {
  id: string
  kind: 'approval' | 'acceptance'
  label: string
  to: string
}

export interface OverviewViewModel {
  project: ProjectViewModel
  contextBranch: string
  continueTasks: OverviewContinueTaskViewModel[]
  recentDeliveries: OverviewDeliveryViewModel[]
  contextFiles: KnowledgeItemViewModel[]
  attentionItems: OverviewAttentionItemViewModel[]
  attentionCount: number
  harnessConnected: number
  backendConnected: boolean
  counters: {
    all: number
    active: number
    attention: number
    completed: number
    paused: number
  }
}

export interface InboxItemViewModel {
  id: string
  kind: 'approval' | 'acceptance'
  title: string
  subtitle: string
  time: string
  status: UiStatus
  detail: ApprovalViewModel | null
}

export interface SettingsViewModel {
  endpoint: string
  theme: 'light'
  accent: 'lavender'
  services: readonly { id: string; name: string; status: 'ok' | 'warn' | 'down'; detail: string }[]
  observabilityConfigured: boolean
  alerts: readonly string[]
}

export interface ProfileViewModel {
  id: string
  name: string
  harness: string
  environment: string
  model: string
  concurrency: string
  timeout: string
  editable: boolean
  unsupportedReason?: string
  authStatus?: string
  accessLevel?: string
  validationId?: string | null
}

export interface DocumentViewModel {
  id: string
  name: string
  markdown: string
  scope: string
  updated: string
  type: string
}

export interface NewTaskRoleAssignmentViewModel extends AgentRoleViewModel {
  scope: string
}

export interface NewTaskDraftViewModel {
  goal: string
  projectName: string
  branch: string
  attachments: readonly string[]
  roles: NewTaskRoleAssignmentViewModel[]
  acceptance: readonly string[]
  assignmentMode: 'auto' | 'manual'
  attentionNote: string
}
