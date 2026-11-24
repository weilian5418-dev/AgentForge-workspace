import type {
  ApprovalDecisionInput,
  ApprovalViewModel,
  DocumentViewModel,
  FeatureAvailability,
  HarnessInventoryViewModel,
  InboxItemViewModel,
  KnowledgeItemViewModel,
  NewTaskDraftViewModel,
  OverviewViewModel,
  ProfileViewModel,
  ProjectViewModel,
  SendMessageInput,
  SentMessageViewModel,
  SettingsViewModel,
  TaskRowViewModel,
  TaskViewModel,
  ToolServiceViewModel,
} from '@/domain/view-models'
import type { components } from '@/data/contracts/api.generated'

type Schema = components['schemas']

export interface WorkspaceRepository {
  loadProjects?(signal?: AbortSignal): Promise<ProjectViewModel[]>
  createRun?(input: Schema['RunCreateRequest']): Promise<Schema['RunAcceptedResponse']>
  uploadInput?(projectId: string, file: File): Promise<string>
  loadPlan?(runId: string, signal?: AbortSignal): Promise<Schema['PlanResponse'] | null>
  proposePlan?(runId: string, idempotencyKey: string): Promise<Schema['PlanResponse']>
  loadProfiles?(signal?: AbortSignal): Promise<Schema['AgentProfileSummary'][]>
  loadInstances?(signal?: AbortSignal): Promise<Schema['AgentInstance'][]>
  discoverExecutors?(): Promise<Schema['DiscoveryRunResponse']>
  createProfile?(installationId: string, adapterId: string): Promise<Schema['ProfileValidateResponse']>
  assignRoles?(runId: string, input: Schema['RoleAssignmentsPutRequest']): Promise<Schema['RoleAssignmentsResponse']>
  decidePlan?(runId: string, input: Schema['PlanDecisionRequest']): Promise<Schema['PlanResponse']>
  runAction?(runId: string, input: Schema['RunActionRequest']): Promise<Schema['OperationAccepted']>
  prepareWorkspaces?(runId: string): Promise<unknown>
  requestExecution?(runId: string, input: Schema['ExecutionRequest'], key: string): Promise<Schema['OperationAccepted']>
  acceptDelivery?(runId: string, revision: number, input: Schema['AcceptanceDecisionRequest'], key: string): Promise<Schema['AcceptanceResult']>
  loadDeliveryPointer?(runId: string, signal?: AbortSignal): Promise<Schema['DeliveryPointerView']>
  loadManifest?(runId: string, hash: string, signal?: AbortSignal): Promise<Schema['DeliveryManifestView']>
  loadArtifact?(candidateId: string, signal?: AbortSignal): Promise<Blob>
  registerTool?(input: Schema['McpServerConfigCreateRequest']): Promise<Schema['McpServerSummary']>
  probeTool?(serverId: string, configVersion: number): Promise<Schema['McpOperationView']>
  loadTemplates?(signal?: AbortSignal): Promise<Schema['TemplateSummary'][]>
  loadProviders?(signal?: AbortSignal): Promise<Schema['ProviderProfile'][]>
  loadProject(signal?: AbortSignal): Promise<ProjectViewModel | null>
  loadOverview(projectId: string, signal?: AbortSignal): Promise<OverviewViewModel>
  loadTasks(projectId: string, signal?: AbortSignal): Promise<TaskRowViewModel[]>
  loadTask(runId: string, signal?: AbortSignal): Promise<TaskViewModel>
  loadDelivery(runId: string, signal?: AbortSignal): Promise<TaskViewModel>
  loadNewTaskDraft(signal?: AbortSignal): Promise<NewTaskDraftViewModel>
  loadInbox(signal?: AbortSignal): Promise<InboxItemViewModel[]>
  loadExecutors(signal?: AbortSignal): Promise<HarnessInventoryViewModel[]>
  loadProfile(profileId: string, signal?: AbortSignal): Promise<ProfileViewModel>
  loadKnowledge(signal?: AbortSignal): Promise<KnowledgeItemViewModel[]>
  loadDocument(documentId: string, signal?: AbortSignal): Promise<DocumentViewModel>
  createKnowledgeDocument?(input: { name: string; content: string; contentType?: string }): Promise<unknown>
  updateKnowledgeDocument?(documentId: string, content: string): Promise<unknown>
  deleteKnowledgeDocument?(documentId: string): Promise<void>
  updateProfile?(profileId: string, input: { label?: string; concurrency?: string; timeout?: string }): Promise<unknown>
  createTerminalSession?(runId: string): Promise<{ sessionId: string; cwd?: string; note?: string }>
  runTerminalCommand?(sessionId: string, command: string): Promise<{ exitCode: number; stdout: string; stderr: string }>
  createArtifactPreview?(candidateId: string): Promise<{ previewId: string; url: string }>
  createProvider?(input: Record<string, unknown>): Promise<Schema['ProviderProfile']>
  updateProvider?(providerId: string, input: Record<string, unknown>): Promise<Schema['ProviderProfile']>
  probeProvider?(providerId: string): Promise<unknown>
  previewTemplate?(runId: string, input: Record<string, unknown>): Promise<unknown>
  applyTemplatePreview?(previewId: string, input: Record<string, unknown>, key: string): Promise<unknown>
  loadTools(signal?: AbortSignal): Promise<ToolServiceViewModel[]>
  loadSettings(signal?: AbortSignal): Promise<SettingsViewModel>
  sendMessage(input: SendMessageInput): Promise<SentMessageViewModel>
  decideApproval(input: ApprovalDecisionInput): Promise<ApprovalViewModel>
  capabilities: Readonly<Record<string, FeatureAvailability>>
}
