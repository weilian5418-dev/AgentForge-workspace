<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { useRoute, useRouter } from 'vue-router'
import {
  Plus,
  ChevronDown,
  AlertTriangle,
  X,
  Trash2,
  GitBranch,
  Hand,
} from 'lucide-vue-next'
import AfButton from '@/components/af/AfButton.vue'
import AfErrorState from '@/components/af/AfErrorState.vue'
import ExecutionCanvas from '@/features/execution/ExecutionCanvas.vue'
import { useWorkspaceRepository } from '@/app/runtime'
import { useVisualReady } from '@/composables/useVisualReady'
import { dependencies } from '@/fixtures/shared'
import type { NewTaskRoleAssignmentViewModel } from '@/domain/view-models'
import { shouldUseReferenceFromUrl } from '@/app/runtime'
import AfDialog from '@/components/af/AfDialog.vue'
import { useFeedbackStore } from '@/stores/feedback'
import { usePreviewStore } from '@/stores/preview'
import { useCurrentProject } from '@/queries/current-project'

const route = useRoute()
const router = useRouter()
const feedback = useFeedbackStore()
const preview = usePreviewStore()
const project = useCurrentProject()
const queryClient = useQueryClient()
const creating = ref(false)
const createError = ref('')
const output = ref<'code' | 'markdown' | 'pptx'>('code')
const maxTokens = ref(20000)
const maxWallTime = ref(1800)
const inputFiles = ref<File[]>([])
const filePicker = ref<HTMLInputElement | null>(null)
const uploaded = new Map<File, string>()
let createAttempt = { body: '', key: '' }
function pickInputs(event: Event) { inputFiles.value = [...inputFiles.value, ...Array.from((event.target as HTMLInputElement).files ?? [])]; (event.target as HTMLInputElement).value = '' }
const roleRows = ref<NewTaskRoleAssignmentViewModel[]>([])
const roleOpen = ref(false)
const roleLabel = ref('Frontend')
const roleHarness = ref('Codex CLI')
const roleScope = ref('实现前端界面')
const attachmentOpen = ref(false)
const attachmentName = ref('')
const repository = useWorkspaceRepository()
const confirmOpen = ref(false)
const goal = ref('')
const assignmentMode = ref<'auto' | 'manual'>('manual')
const accepted = ref<Record<string, boolean>>({})
const attachments = ref<string[]>([])

const { data, isPending, isError, error, refetch, isSuccess } = useQuery({
  queryKey: computed(() => ['new-task-draft', project.value?.id]),
  queryFn: ({ signal }) => repository.loadNewTaskDraft(signal),
})

watch(data, value => {
  if (!value) return
  roleRows.value = value.roles.map(role => ({ ...role }))
  if (!goal.value) {
    const draft = typeof route.query.draft === 'string' ? route.query.draft : ''
    goal.value = draft || value.goal
  }
  assignmentMode.value = route.query.mode === 'auto' ? 'auto' : value.assignmentMode
  if (attachments.value.length === 0) attachments.value = [...new Set([...value.attachments, ...(typeof route.query.attachment==='string' ? [route.query.attachment] : [])])]
  if (Object.keys(accepted.value).length === 0) {
    accepted.value = Object.fromEntries(value.acceptance.map(item => [item, true]))
  }
}, { immediate: true })

useVisualReady(computed(() => isSuccess.value && Boolean(data.value)))

const isReference = shouldUseReferenceFromUrl(window.location.search)
const primaryLabel = isReference ? '创建并开始' : '创建并生成计划'

const previewRoles = computed<NewTaskRoleAssignmentViewModel[]>(() =>
  roleRows.value.map(role => ({
    ...role,
    status: role.label === 'Planner' ? 'completed' : 'waiting',
    workspace: '',
  })),
)

watch(assignmentMode, mode=>{ if(mode==='auto' && data.value) roleRows.value=data.value.roles.map(role=>({...role})) })

function removeAttachment(name: string) {
  attachments.value = attachments.value.filter(item => item !== name)
}
function addRole() {
  roleRows.value.push({ id: `role-${crypto.randomUUID()}`, label: roleLabel.value, harness: roleHarness.value, scope: roleScope.value, status: 'waiting', x: 0, y: roleRows.value.length * 110 })
  roleOpen.value = false
}
function addAttachment() {
  const value = attachmentName.value.trim()
  if (value && !attachments.value.includes(value)) attachments.value.push(value)
  attachmentName.value = ''
  attachmentOpen.value = false
}
function saveDraft() {
  try {
    localStorage.setItem('af-task-draft', JSON.stringify({ goal: goal.value, assignmentMode: assignmentMode.value, roles: roleRows.value, attachments: attachments.value, accepted: accepted.value }))
    feedback.notify('任务草稿已保存在此浏览器', 'success')
  } catch { feedback.notify('浏览器无法保存草稿，请检查存储权限', 'error') }
}
function restoreDraft() {
  try {
    const saved = JSON.parse(localStorage.getItem('af-task-draft') || 'null')
    if (!saved || typeof saved.goal !== 'string') return feedback.notify('没有可恢复的草稿')
    goal.value = saved.goal
    if (saved.assignmentMode === 'auto' || saved.assignmentMode === 'manual') assignmentMode.value = saved.assignmentMode
    if (Array.isArray(saved.roles)) roleRows.value = saved.roles
    if (Array.isArray(saved.attachments)) attachments.value = saved.attachments
    if(saved.accepted) accepted.value=saved.accepted
    feedback.notify('已恢复上次保存的草稿', 'success')
  } catch { feedback.notify('无法读取草稿，请重新填写', 'error') }
}
async function createPreview() {
  if (creating.value) return
  if (!isReference) {
    if (!project.value || !repository.createRun || !goal.value.trim()) return
    creating.value = true
    createError.value = ''
    try {
      const inputRefs: string[] = []
      for (const file of inputFiles.value) {
        let ref = uploaded.get(file)
        if (!ref) { ref = await repository.uploadInput!(project.value.id, file); uploaded.set(file, ref) }
        inputRefs.push(ref)
      }
      const body = { projectId: project.value.id, goal: goal.value.trim(), mode: 'auto' as const, inputRefs, outputs: [output.value], executionProfile: 'container' as const, failurePolicy: 'dependency-aware' as const, budget: { maxTokens: maxTokens.value, maxWallTimeSeconds: maxWallTime.value } }
      const fingerprint = JSON.stringify(body)
      if (createAttempt.body !== fingerprint) createAttempt = { body: fingerprint, key: crypto.randomUUID() }
      const result = await repository.createRun({ ...body, idempotencyKey: createAttempt.key })
      confirmOpen.value = false
      await queryClient.invalidateQueries()
      void router.push(`/tasks/${result.runId}`)
      feedback.notify('任务已创建，请检查计划并分配执行者', 'success')
      try { await repository.proposePlan?.(result.runId, `plan-${createAttempt.key}`) }
      catch (error) { feedback.notify(`任务已保存，生成计划失败：${error instanceof Error ? error.message : '请在任务页重试'}`, 'error') }
      await queryClient.invalidateQueries()
    } catch (error) { createError.value = error instanceof Error ? error.message : '创建失败'; feedback.notify(createError.value, 'error') }
    finally { creating.value = false }
    return
  }
  if(!goal.value.trim() || !roleRows.value.length) { confirmOpen.value=false; feedback.notify('请填写任务目标并至少分配一个角色','info'); return }
  const id = preview.createTask(goal.value.trim(), roleRows.value)
  confirmOpen.value = false
  feedback.notify('已打开任务预览，操作仅保留在当前前端会话')
  void router.push(`/tasks/${id}`)
}
const previewDependencies = computed(() => dependencies.filter(dep => previewRoles.value.some(role => role.id === dep.source) && previewRoles.value.some(role => role.id === dep.target)))
</script>

<template>
  <main class="af-page af-new-task-page" data-page-id="02-new-task" data-region="content">
    <AfErrorState v-if="isError" :message="error?.message" @retry="refetch()" />
    <div v-else-if="isPending">加载中…</div>
    <div v-else-if="data" class="af-new-task">
      <section class="af-form-wrap">
        <div class="af-form-head">
          <p class="af-breadcrumb">
            <span>{{ data.projectName }}</span>
            <span>/</span>
            <strong>新建任务</strong>
          </p>
          <h1>让 Agent 开始协作</h1>
          <p class="af-subtitle">先明确目标，再选择执行方式。</p>
        </div>

        <form class="af-form" @submit.prevent="confirmOpen = true">
          <label class="af-field">
            <span>任务目标</span>
            <textarea v-model="goal" rows="3" required :maxlength="isReference ? 4096 : 512" aria-label="任务目标" />
          </label>

          <div class="af-two">
            <label class="af-field">
              <span>项目</span>
              <input :value="data.projectName" readonly />
            </label>
            <label class="af-field">
              <span>工作分支</span>
              <div class="af-branch">
                <GitBranch :size="16" :stroke-width="1.75" aria-hidden="true" />
                <input :value="data.branch" readonly />
              </div>
            </label>
          </div>

          <div v-if="isReference" class="af-field">
            <span>参考资料</span>
            <div class="af-chips">
              <span v-for="item in attachments" :key="item" class="af-chip">
                {{ item }}
                <button type="button" :aria-label="`移除 ${item}`" @click="removeAttachment(item)">
                  <X :size="14" :stroke-width="1.75" />
                </button>
              </span>
              <button type="button" class="af-add-chip" @click="attachmentOpen = true">
                <Plus :size="16" :stroke-width="1.75" />
                添加资料
              </button>
            </div>
          </div>

          <div v-else class="af-field"><span>参考资料</span><input ref="filePicker" type="file" multiple hidden @change="pickInputs" /><div class="af-chips"><span v-for="(file,index) in inputFiles" :key="index" class="af-chip">{{ file.name }}<button type="button" :aria-label="`移除 ${file.name}`" @click="inputFiles.splice(index,1)">×</button></span><AfButton @click="filePicker?.click()">选择文件</AfButton></div><small>文件将在创建时上传到当前项目。</small></div>

          <div v-if="isReference" class="af-field">
            <span>协作方式</span>
            <div class="af-mode" role="tablist" aria-label="协作方式">
              <button
                type="button"
                role="tab"
                :aria-selected="assignmentMode === 'auto'"
                :data-active="assignmentMode === 'auto'"
                @click="assignmentMode = 'auto'"
              >
                自动分配
              </button>
              <button
                type="button"
                role="tab"
                class="af-mode-manual"
                :aria-selected="assignmentMode === 'manual'"
                :data-active="assignmentMode === 'manual'"
                @click="assignmentMode = 'manual'"
              >
                <Hand :size="16" :stroke-width="1.75" aria-hidden="true" />
                手动分配
              </button>
            </div>
          </div>

          <div v-if="isReference" class="af-field">
            <span>角色分配</span>
            <table>
              <thead>
                <tr>
                  <th>角色</th>
                  <th>Harness</th>
                  <th>工作范围</th>
                  <th aria-label="操作" />
                </tr>
              </thead>
              <tbody>
                <tr v-for="role in roleRows" :key="role.id">
                  <td>{{ role.label }}</td>
                  <td><select v-model="role.harness" :aria-label="`${role.label} Harness`"><option>Codex CLI</option><option>Claude Code</option><option>OpenCode</option></select></td>
                  <td><input v-model="role.scope" :aria-label="`${role.label} 工作范围`" /></td>
                  <td>
                    <button type="button" class="af-icon-del" :aria-label="`移除 ${role.label} 角色`" @click="roleRows = roleRows.filter(item => item.id !== role.id)">
                      <Trash2 :size="16" :stroke-width="1.75" />
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
            <button type="button" class="af-add-role" @click="roleOpen = true">
              <Plus :size="16" :stroke-width="1.75" />
              添加角色
            </button>
          </div>

          <div v-if="isReference" class="af-field">
            <span>完成条件</span>
            <ul class="af-acceptance">
              <li v-for="item in data.acceptance" :key="item">
                <label>
                  <input v-model="accepted[item]" type="checkbox" />
                  {{ item }}
                </label>
              </li>
            </ul>
          </div>

          <div v-if="!isReference" class="af-field"><span>交付类型</span><select v-model="output" aria-label="交付类型"><option value="code">代码</option><option value="markdown">Markdown 文档</option><option value="pptx">PPTX 演示文稿</option></select><div class="af-two"><label>Token 上限<input v-model.number="maxTokens" type="number" min="1" required aria-label="Token 上限" /></label><label>最长执行时间（秒）<input v-model.number="maxWallTime" type="number" min="1" required aria-label="最长执行时间" /></label></div><p>使用规则生成初始计划。创建后可查看工作包、分配实际执行配置，再确认执行。</p></div>

          <details class="af-advanced">
            <summary>
              <span>
                <strong>权限与执行环境</strong>
                <small>每个执行者使用独立工作区</small>
              </span>
              <ChevronDown :size="16" :stroke-width="1.75" />
            </summary>
            <p>创建后不会自动执行；需确认计划与分配。写操作与依赖安装按审批策略处理。</p>
          </details>

          <div class="af-actions">
            <button type="button" class="af-restore" @click="restoreDraft">恢复草稿</button>
            <AfButton type="button" @click="saveDraft">保存草稿</AfButton>
            <AfButton tone="primary" type="submit" :pending="creating" :disabled="!isReference && (!project || project.grantStatus !== 'active' || !project.baselineId)">{{ primaryLabel }} →</AfButton>
          </div>
        </form>
      </section>

      <aside class="af-preview">
        <div class="af-preview-head">
          <h2>执行预览</h2>
          <p>{{ roleRows.length }} 个角色 · {{ new Set(roleRows.map(role => role.harness)).size }} 个 Harness</p>
        </div>
        <ExecutionCanvas v-if="isReference" :roles="previewRoles" :dependencies="previewDependencies" compact />
        <p v-else class="af-tip">创建任务后，这里会由真实计划确定角色、依赖关系与工作范围。</p>
        <div class="af-attention">
          <AlertTriangle :size="18" :stroke-width="1.75" aria-hidden="true" />
          <div>
            <strong>需要关注</strong>
            <p>{{ data.attentionNote }}</p>
          </div>
        </div>
        <p class="af-tip">执行前可继续调整角色分配。</p>
      </aside>
    </div>
    <AfDialog v-model:open="roleOpen" title="添加角色" description="为任务添加一位协作执行者。"><form class="af-dialog-form" @submit.prevent="addRole"><label>角色名称<input v-model="roleLabel" required /></label><label>Harness<select v-model="roleHarness"><option>Codex CLI</option><option>Claude Code</option><option>OpenCode</option></select></label><label>工作范围<input v-model="roleScope" required /></label><div class="af-dialog-actions"><AfButton @click="roleOpen = false">取消</AfButton><AfButton tone="primary" type="submit">添加角色</AfButton></div></form></AfDialog>
    <AfDialog v-model:open="attachmentOpen" title="添加参考资料" description="填写工作区中的文档名称，作为任务参考。"><form class="af-dialog-form" @submit.prevent="addAttachment"><label>资料名称<input v-model="attachmentName" placeholder="例如：接口约定.md" required /></label><div class="af-dialog-actions"><AfButton tone="primary" type="submit">添加资料</AfButton></div></form></AfDialog>
    <AfDialog v-model:open="confirmOpen" title="确认任务安排" :description="isReference ? '前端预览会展示任务执行页面，不会启动真实执行者。' : '任务与参考文件将保存到后端，生成计划后等待你确认分配。'"><div class="af-task-confirm"><h3>{{ goal }}</h3><p>{{ data?.projectName }} · {{ isReference ? `${roleRows.length} 个角色` : `交付类型：${output}` }}</p><p v-if="!isReference">预算：{{ maxTokens }} tokens · {{ maxWallTime }} 秒</p><ul v-if="isReference"><li v-for="role in roleRows" :key="role.id"><strong>{{ role.label }}</strong><span>{{ role.harness }} · {{ role.scope }}</span></li></ul><p v-if="createError" role="alert">{{ createError }}</p><div class="af-dialog-actions"><AfButton :disabled="creating" @click="confirmOpen = false">继续编辑</AfButton><AfButton tone="primary" :pending="creating" @click="createPreview">{{ isReference ? '打开任务预览' : '确认创建任务' }}</AfButton></div></div></AfDialog>
  </main>
</template>

<style scoped>
.af-new-task-page { padding-top: 20px; }
.af-new-task {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(300px, 36%);
  gap: 28px;
  align-items: start;
}
.af-breadcrumb {
  margin: 0 0 8px;
  display: flex;
  gap: 8px;
  color: var(--af-text-secondary);
  font-size: 16px;
}
.af-breadcrumb strong { color: var(--af-text); font-weight: 500; }
.af-form-head h1 {
  margin: 0;
  font-size: 28px;
  line-height: 38px;
  font-weight: 650;
}
.af-subtitle {
  margin: 8px 0 20px;
  color: var(--af-text-secondary);
  font-size: 18px;
  line-height: 28px;
}
.af-form {
  border: 1px solid var(--af-border);
  border-radius: 10px;
  padding: 24px;
  background: var(--af-surface);
  display: grid;
  gap: 16px;
}
.af-field { display: grid; gap: 8px; }
.af-field > span { font-size: 16px; font-weight: 500; color: var(--af-text-secondary); }
.af-field textarea,
.af-field input {
  width: 100%;
  min-height: 46px;
  border: 1px solid var(--af-border);
  border-radius: 8px;
  padding: 12px 14px;
  background: var(--af-surface);
}
.af-field textarea { min-height: 110px; resize: vertical; }
.af-branch {
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid var(--af-border);
  border-radius: 8px;
  padding: 0 12px;
  background: var(--af-surface);
  color: var(--af-text-secondary);
}
.af-branch input { border: 0; padding-left: 0; min-height: 44px; }
.af-two { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
.af-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.af-chip,
.af-add-chip,
.af-add-role {
  height: 36px;
  padding: 0 12px;
  border-radius: 8px;
  border: 1px solid var(--af-border);
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: var(--af-surface-subtle);
  color: var(--af-text-secondary);
  font-size: 16px;
}
.af-chip button {
  border: 0;
  background: transparent;
  display: grid;
  place-items: center;
  color: var(--af-text-muted);
  padding: 0;
}
.af-add-chip,
.af-add-role {
  background: var(--af-surface);
  width: fit-content;
}
.af-mode {
  display: inline-flex;
  padding: 4px;
  border: 1px solid var(--af-border);
  border-radius: 10px;
  background: var(--af-surface-subtle);
  gap: 4px;
}
.af-mode button {
  height: 40px;
  padding: 0 16px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--af-text-secondary);
  font-size: 16px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.af-mode button[data-active='true'] {
  background: var(--af-surface);
  color: var(--af-accent-ink);
  border-color: var(--af-accent);
  box-shadow: 0 1px 2px rgb(36 35 42 / 8%);
}
table { width: 100%; border-collapse: collapse; }
th, td {
  height: 48px;
  border-bottom: 1px solid var(--af-border);
  text-align: left;
  padding: 0 8px;
  font-size: 16px;
}
th { color: var(--af-text-secondary); font-weight: 500; }
.af-icon-del {
  width: 32px;
  height: 32px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--af-text-muted);
  display: grid;
  place-items: center;
}
.af-acceptance { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }
.af-acceptance label {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 40px;
  font-size: 16px;
}
.af-acceptance input {
  width: 18px;
  height: 18px;
  accent-color: var(--af-success);
}
.af-advanced {
  border: 1px solid var(--af-border);
  border-radius: 8px;
  padding: 12px 14px;
}
.af-advanced summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
  list-style: none;
}
.af-advanced summary span { display: grid; gap: 4px; }
.af-advanced small {
  color: var(--af-text-muted);
  font-size: 14px;
}
.af-advanced p {
  margin: 12px 0 0;
  color: var(--af-text-secondary);
  font-size: 14px;
}
.af-actions { display: flex; justify-content: flex-end; gap: 8px; }
.af-restore { margin-right: auto; background: none; border: 0; color: var(--af-accent-ink); font-size: 14px; }
td input, td select { width: 100%; min-width: 0; max-width: 100%; border: 1px solid var(--af-border); border-radius: 6px; padding: 6px 8px; height: 36px; font-size: 14px; background: var(--af-surface); }
.af-task-confirm h3 { margin: 0; font-size: 19px; }
.af-task-confirm p { color: var(--af-text-secondary); font-size: 15px; }
.af-task-confirm ul { list-style: none; padding: 0; }
.af-task-confirm li { display: grid; gap: 4px; padding: 12px 0; border-bottom: 1px solid var(--af-border); font-size: 15px; }
.af-task-confirm li span { color: var(--af-text-secondary); }
.af-preview {
  border: 1px solid var(--af-border);
  border-radius: 10px;
  padding: 20px;
  background: var(--af-surface);
  display: grid;
  gap: 16px;
  position: sticky;
  top: 0;
}
.af-preview-head h2 { margin: 0; font-size: 22px; }
.af-preview-head p { margin: 8px 0 0; color: var(--af-text-secondary); font-size: 14px; }
.af-attention {
  display: flex;
  gap: 12px;
  padding: 14px;
  border-radius: 10px;
  background: var(--af-attention-bg);
  color: var(--af-attention);
}
.af-attention strong { display: block; margin-bottom: 4px; color: var(--af-text); }
.af-attention p {
  margin: 0;
  color: var(--af-text-secondary);
  font-size: 14px;
}
.af-tip {
  margin: 0;
  color: var(--af-text-muted);
  font-size: 14px;
}
.af-note { margin: 0; color: var(--af-attention); font-size: 14px; }
@media (max-width: 1439px) {
  .af-new-task { grid-template-columns: minmax(0, 1fr) 320px; }
}
@media(max-width:1100px) { .af-new-task { grid-template-columns: 1fr; } .af-preview { position: static; } }
@media(max-width:639px) { .af-form { padding: 16px; } .af-two { grid-template-columns: 1fr; gap: 16px; } .af-mode { display: flex; } .af-mode button { flex: 1; padding: 0 10px; } .af-acceptance { gap: 4px; } .af-field table { table-layout: fixed; } th, td { font-size: 13px; padding: 0 3px; } th:first-child { width: 23%; } th:last-child { width: 36px; } td input, td select { font-size: 12px; padding: 4px; } .af-actions { flex-wrap: wrap; } .af-actions .af-restore { width: 100%; text-align: left; } }
.af-form { gap:12px; padding:20px; }.af-field textarea { min-height:86px; padding:10px 14px; }.af-field input { min-height:42px; padding:8px 12px; }.af-field td input { min-height:34px; height:34px; padding:6px 8px; }
.af-acceptance { display:flex; flex-wrap:wrap; gap:12px 24px; }.af-acceptance input { min-height:0; padding:0; flex-shrink:0; }.af-acceptance label { min-height:30px; }
.af-mode { background:transparent; border:0; padding:0; gap:12px; }.af-mode button { flex:1; justify-content:center; border:1px solid var(--af-border); }.af-mode button[data-active='true'] { background:var(--af-selected); border-color:var(--af-accent); }
.af-advanced summary span { display:flex; align-items:center; gap:20px; }.af-advanced { padding:10px 12px; }
.af-preview { margin-top:126px; min-height:600px; align-content:start; }.af-preview-head h2 { font-weight:650; }.af-actions { border-top:1px solid var(--af-border); padding-top:14px; }
@media(max-width:1100px) { .af-preview { margin-top:0; min-height:0; } }
@media(max-width:639px) { .af-advanced summary span { display:grid; gap:6px; }.af-acceptance { display:grid; }.af-form { padding:16px; }.af-field td input { font-size:12px; padding:4px; } }
.af-field textarea { height:86px; }.af-field:has(table) > span { display:none; }
</style>
