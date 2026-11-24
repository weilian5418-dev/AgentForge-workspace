<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { useRoute, useRouter } from 'vue-router'
import WorkspaceSplit from '@/layouts/WorkspaceSplit.vue'
import ConversationPane from '@/features/conversation/ConversationPane.vue'
import ExecutionCanvas from '@/features/execution/ExecutionCanvas.vue'
import ArtifactInspector from '@/features/artifacts/ArtifactInspector.vue'
import AfTabs from '@/components/af/AfTabs.vue'
import AfButton from '@/components/af/AfButton.vue'
import AfErrorState from '@/components/af/AfErrorState.vue'
import { useWorkspaceRepository } from '@/app/runtime'
import { useVisualReady } from '@/composables/useVisualReady'
import { usePreviewStore } from '@/stores/preview'
import { useFeedbackStore } from '@/stores/feedback'
import { shouldUseReferenceFromUrl } from '@/app/runtime'
import LivePlan from '@/features/execution/LivePlan.vue'
import { useTaskStream } from '@/composables/useTaskStream'

const route = useRoute()
const router = useRouter()
const repository = useWorkspaceRepository()
const runId = computed(() => String(route.params.runId))
const tab = ref(shouldUseReferenceFromUrl(window.location.search) ? 'canvas' : 'plan')
const preview = usePreviewStore()
const feedback = useFeedbackStore()
const isPreview = shouldUseReferenceFromUrl(window.location.search)
const approvalDismissed = ref(false)
const client = useQueryClient()
const sending = ref(false)
const controlling = ref(false)
const sentVersion = ref(0)
const terminalPending = ref(false)
const terminalSessionId = ref('')
const terminalCommand = ref('Get-Location')
const terminalOutput = ref('')
const runLog = computed(() => {
  if (isPreview) {
    return [
      'Planner 完成接口与数据范围梳理',
      'Frontend、Backend 开始并行实现',
      preview.paused[runId.value] ? '执行已由用户暂停' : '等待审查者验证产物',
    ]
  }
  const lines: string[] = []
  if (data.value?.live?.guidance?.stageLabel) lines.push(String(data.value.live.guidance.stageLabel))
  if (data.value?.live?.guidance?.stageDetail) lines.push(String(data.value.live.guidance.stageDetail))
  for (const blocker of data.value?.live?.guidance?.blockers ?? []) lines.push(blocker.message)
  for (const message of data.value?.messages ?? []) {
    if (message.body) lines.push(`${message.name ?? message.kind}: ${message.body.slice(0, 120)}`)
  }
  return lines
})
async function openTerminal() {
  if (!repository.createTerminalSession || terminalPending.value) return
  terminalPending.value = true
  try {
    const session = await repository.createTerminalSession(runId.value)
    terminalSessionId.value = session.sessionId
    terminalOutput.value = `会话 ${session.sessionId}\n工作目录：${session.cwd ?? '—'}\n${session.note ?? ''}`
    feedback.notify('终端会话已创建', 'success')
  } catch (error) {
    feedback.notify(error instanceof Error ? error.message : '无法创建终端会话', 'error')
  } finally {
    terminalPending.value = false
  }
}
async function runTerminal() {
  if (!repository.runTerminalCommand || !terminalSessionId.value || !terminalCommand.value.trim() || terminalPending.value) return
  terminalPending.value = true
  try {
    const result = await repository.runTerminalCommand(terminalSessionId.value, terminalCommand.value.trim())
    terminalOutput.value = `exit ${result.exitCode}\n${result.stdout}${result.stderr ? `\n[stderr]\n${result.stderr}` : ''}`
  } catch (error) {
    feedback.notify(error instanceof Error ? error.message : '命令执行失败', 'error')
  } finally {
    terminalPending.value = false
  }
}
let messageAttempt = { body: '', key: '' }
async function allow() {
  const approval = data.value?.approval
  if (!approval) return
  if (isPreview) { approvalDismissed.value = true; feedback.notify('已在当前预览中允许本次操作'); return }
  if (!approval.actionDigest) return feedback.notify('缺少审批摘要，请刷新后重试', 'error')
  try { await repository.decideApproval({ approvalId: approval.id, actionDigest: approval.actionDigest, decision: 'allow_once', idempotencyKey: crypto.randomUUID() }); await client.invalidateQueries(); feedback.notify('审批决定已提交，生效状态以后端确认为准') }
  catch (error) { feedback.notify(error instanceof Error ? error.message : '审批失败', 'error') }
}
const displayedTask = computed(() => {
  if (!data.value) return null
  const all = [...data.value.messages, ...(preview.messages[runId.value] ?? [])]
  return { ...data.value, messages: all.filter((item,index) => all.findIndex(other => other.id === item.id) === index), approval: approvalDismissed.value ? null : data.value.approval }
})
async function send(text: string, recipient?: string) {
  if (!isPreview) {
    if (sending.value) return
    sending.value = true
    const body = JSON.stringify({ runId: runId.value, text, recipient })
    if (messageAttempt.body !== body) messageAttempt = { body, key: crypto.randomUUID() }
    try { await repository.sendMessage({ runId: runId.value, text, recipientRef: recipient, idempotencyKey: messageAttempt.key }); sentVersion.value++; messageAttempt = { body: '', key: '' }; await refetch(); feedback.notify('消息已保存，等待执行者接收', 'success') }
    catch (error) { feedback.notify(error instanceof Error ? error.message : '发送失败', 'error') }
    finally { sending.value = false }
    return
  }
  preview.addMessage(runId.value, text)
}
async function pause() {
  if (!isPreview) {
    if (!data.value?.live || controlling.value) return
    controlling.value = true
    try { const result = await repository.runAction!(runId.value, { action: data.value.live.status === 'paused' ? 'resume' : 'pause', expectedStatusVersion: data.value.live.statusVersion, idempotencyKey: crypto.randomUUID() }); feedback.notify(`执行控制请求已提交：${result.status}`); await client.invalidateQueries() }
    catch (error) { feedback.notify(error instanceof Error ? error.message : '控制请求失败', 'error') }
    finally { controlling.value = false }
    return
  }
  preview.paused[runId.value] = !preview.paused[runId.value]
  feedback.notify(preview.paused[runId.value] ? '已切换为暂停预览' : '已恢复执行预览')
}

const { data, isPending, isError, error, refetch, isSuccess } = useQuery({
  queryKey: computed(() => ['task', runId.value]),
  queryFn: ({ signal }) => repository.loadTask(runId.value, signal),
})

useVisualReady(computed(() => isSuccess.value && Boolean(data.value)))
useTaskStream(data)

watch(runId,()=>{approvalDismissed.value=false;tab.value=isPreview ? 'canvas' : 'plan'})
const runningCount = computed(() => preview.paused[runId.value] ? 0 : data.value?.roles.filter(role => role.status === 'running').length ?? 0)
const waitingCount = computed(() => data.value?.roles.filter(role => role.status === 'waiting').length ?? 0)

function onTab(next: string) {
  tab.value = next
  if (next === 'delivery') void router.push(`/tasks/${runId.value}/delivery`)
}
</script>

<template>
  <div class="af-workspace" data-page-id="03-execution" data-region="content">
    <AfErrorState v-if="isError" :message="error?.message" class="af-page" @retry="refetch()" />
    <div v-else-if="isPending" class="af-page">加载执行视图…</div>
    <WorkspaceSplit v-else-if="data" :left-width="602">
      <template #left>
        <ConversationPane v-if="displayedTask" :task="displayedTask" :pending="sending" :sent-version="sentVersion" @send="send" @allow="allow" />
      </template>
      <template #right>
        <section class="af-workbench af-canvas af-pane" :data-tab="tab" data-region="canvas">
          <AfTabs
            :model-value="tab"
            :tabs="[
              ...(!isPreview ? [{ id: 'plan', label: '计划与分配' }] : []),
              { id: 'canvas', label: '执行画布' },
              { id: 'changes', label: `变更 ${data.files.length}` },
              { id: 'delivery', label: '交付' },
            ]"
            @update:model-value="onTab"
          />
          <LivePlan v-if="tab === 'plan'" :task="data" />
          <div v-if="tab !== 'plan'" class="af-run-status">
            <div>
              <strong>{{ !isPreview ? data.statusLabel : preview.paused[runId] ? '执行已暂停' : '并行执行中' }}</strong>
              <span>{{ runningCount }} 个执行中 · {{ waitingCount }} 个审查中</span>
            </div>
            <AfButton tone="ghost" :pending="controlling" :disabled="!isPreview && !data.live?.guidance?.allowedActions?.some(action => action.action === (data.live?.status === 'paused' ? 'resume' : 'pause') && action.allowed)" @click="pause">{{ (!isPreview ? data.live?.status === 'paused' : preview.paused[runId]) ? '恢复执行' : '暂停全部' }}</AfButton>
          </div>
          <ExecutionCanvas v-if="tab === 'canvas'" :roles="data.roles.map(role => preview.paused[runId] && role.status === 'running' ? { ...role, status: 'paused' } : role)" :dependencies="data.dependencies" />
          <ArtifactInspector
            v-if="tab !== 'plan'"
            :live="!isPreview"
            :files="data.files"
            :original="data.code.original"
            :modified="data.code.modified"
            :additions="data.changeTotals.additions"
            :deletions="data.changeTotals.deletions"
          />
          <details v-if="tab !== 'plan'" class="af-run-log">
            <summary>运行记录 <em>{{ runLog.length }}</em></summary>
            <ol v-if="runLog.length"><li v-for="(item, index) in runLog" :key="index">{{ item }}</li></ol>
            <p v-else class="af-empty-log">暂无后端投影的运行事件。确认计划并开始执行后会在此显示。</p>
            <div v-if="!isPreview" class="af-terminal">
              <div class="af-terminal-actions">
                <AfButton :pending="terminalPending" @click="openTerminal">打开本机终端会话</AfButton>
                <AfButton :disabled="!terminalSessionId || terminalPending" @click="runTerminal">执行命令</AfButton>
              </div>
              <input v-model="terminalCommand" aria-label="终端命令" placeholder="例如：Get-ChildItem" />
              <pre v-if="terminalOutput">{{ terminalOutput }}</pre>
            </div>
          </details>
        </section>
      </template>
    </WorkspaceSplit>
  </div>
</template>

<style scoped>
.af-workbench {
  display: grid;
  grid-template-rows: 76px 62px 355px minmax(0, 1fr) 56px;
  min-height: 0;
  background: var(--af-surface);
  padding: 0 16px;
}
.af-workbench[data-tab='changes'] { grid-template-rows: 76px 62px minmax(320px,1fr) 56px; }
.af-workbench :deep(.af-tabs) {
  height: 76px;
  align-items: flex-end;
  margin: 0 -16px;
  padding: 0 16px;
}
.af-workbench :deep(.af-tab) {
  height: 52px;
}
.af-run-status {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0;
}
.af-run-status strong {
  display: block;
  font-size: 16px;
}
.af-run-status span {
  color: var(--af-text-secondary);
  font-size: 14px;
}
.af-empty-log { margin: 8px 0; color: var(--af-text-secondary); font-size: 14px; }
.af-terminal { display: grid; gap: 8px; margin-top: 12px; }
.af-terminal-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.af-terminal input { width: 100%; padding: 8px 10px; border: 1px solid var(--af-border); border-radius: 8px; background: var(--af-surface); color: var(--af-text); }
.af-terminal pre { margin: 0; max-height: 180px; overflow: auto; padding: 10px; border-radius: 8px; background: var(--af-surface-subtle); font-size: 13px; white-space: pre-wrap; }
.af-run-log {
  margin: 0 -16px;
  border-top: 1px solid var(--af-border);
  padding: 0 16px;
}
.af-workbench :deep(.af-inspector) {
  margin: 0 -16px;
  border-left: 0;
  border-right: 0;
}
.af-run-log summary {
  height: 56px;
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
}
.af-run-log em {
  font-style: normal;
  min-width: 22px;
  height: 22px;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--af-surface-subtle);
  color: var(--af-text-secondary);
  font-size: 13px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.af-run-log p {
  margin: 0 0 12px;
  color: var(--af-text-secondary);
  font-size: 14px;
}
.af-run-log ol { margin: 0; padding: 0 0 16px 24px; font-size: 14px; color: var(--af-text-secondary); }
@media(max-height:960px), (max-width:1100px) { .af-workbench { grid-template-rows: 64px 62px 280px minmax(320px,1fr) auto; min-height: 800px; } .af-workbench[data-tab='changes'] { grid-template-rows: 64px 62px minmax(420px,1fr) auto; } .af-workbench :deep(.af-flow) { height: 280px; } .af-workbench :deep(.af-tabs) { height: 64px; } }
</style>
