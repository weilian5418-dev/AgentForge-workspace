<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useRoute } from 'vue-router'
import { ArrowLeft, Check, Download, ExternalLink, FileText } from 'lucide-vue-next'
import AfDialog from '@/components/af/AfDialog.vue'
import { usePreviewStore } from '@/stores/preview'
import { useFeedbackStore } from '@/stores/feedback'
import { useMediaQuery } from '@vueuse/core'
import AfTabs from '@/components/af/AfTabs.vue'
import AfButton from '@/components/af/AfButton.vue'
import AfStatusBadge from '@/components/af/AfStatusBadge.vue'
import AfErrorState from '@/components/af/AfErrorState.vue'
import MessageComposer from '@/features/conversation/MessageComposer.vue'
import { contentForFile } from '@/features/artifacts/file-content'
import CodeDiff from '@/features/artifacts/CodeDiff.vue'
import { useWorkspaceRepository } from '@/app/runtime'
import { useVisualReady } from '@/composables/useVisualReady'
import { shouldUseReferenceFromUrl } from '@/app/runtime'
import LiveDelivery from '@/features/artifacts/LiveDelivery.vue'
import { useTaskStream } from '@/composables/useTaskStream'
import {
  acceptanceChecklist,
  deliveryAttachments,
  deliverySummary,
} from '@/fixtures/shared'

const route = useRoute()
const isReference = shouldUseReferenceFromUrl(window.location.search)
const preview = usePreviewStore()
const feedback = useFeedbackStore()
const narrow = useMediaQuery('(max-width: 1100px)')
const pane = ref('delivery')
const decisionDialog = ref(false)
const decision = ref<'accepted' | 'changes'>('accepted')
const reason = ref('')
const attachmentDialog = ref(false)
const attachmentName = ref('')
const decisionLabel = computed(() => preview.deliveryDecisions[runId.value] === 'accepted' ? '已完成验收' : preview.deliveryDecisions[runId.value] === 'changes' ? '已提出修改意见' : '等待你的验收')
const localMessages = computed(() => preview.messages[runId.value] ?? [])
function send() { if (!draft.value.trim()) return; preview.addMessage(runId.value, draft.value.trim()); draft.value = ''; leftTab.value = 'conversation' }
function confirmDecision() {
  if (decision.value === 'changes' && !reason.value.trim()) return
  preview.deliveryDecisions[runId.value] = decision.value
  if (reason.value.trim()) preview.addMessage(runId.value, reason.value.trim())
  decisionDialog.value = false
  feedback.notify(decision.value === 'accepted' ? '已在本次预览中确认验收' : '修改意见已记录到本次预览')
}
function openAttachment(name: string) { attachmentName.value = name; attachmentDialog.value = true }
const repository = useWorkspaceRepository()
const runId = computed(() => String(route.params.runId))
const draft = ref('')
const leftTab = ref('delivery')
const rightTab = ref('delivery')
const selectedFileId = ref('')

const { data, isPending, isError, error, refetch, isSuccess } = useQuery({
  queryKey: computed(() => ['delivery', runId.value]),
  queryFn: ({ signal }) => repository.loadDelivery(runId.value, signal),
})

useVisualReady(computed(() => isSuccess.value && Boolean(data.value)))
useTaskStream(data)

const selectedFile = computed(() => {
  const files = data.value?.files ?? []
  return files.find(file => file.id === selectedFileId.value) ?? files[0]
})

const selectedContent = computed(()=>contentForFile(selectedFile.value, data.value?.code ?? {original:'',modified:''}))

watch(
  () => data.value?.files,
  (files) => {
    if (!selectedFileId.value && files?.[0]) selectedFileId.value = files[0].id
  },
  { immediate: true },
)
</script>

<template>
  <div class="af-workspace af-delivery" data-page-id="04-delivery-review" data-region="content">
    <AfErrorState v-if="isError" :message="error?.message" class="af-page" @retry="refetch()" />
    <div v-else-if="isPending" class="af-page">加载交付…</div>
    <LiveDelivery v-else-if="data && !isReference" :task="data" />
    <template v-else-if="data">
      <header class="af-delivery-header" data-region="header-local">
        <div class="af-delivery-title">
          <RouterLink class="af-back" to="/tasks">
            <ArrowLeft :size="18" :stroke-width="1.75" />
            全部任务
          </RouterLink>
          <h1>{{ data.goal }}</h1>
          <div class="af-meta">
            <code class="af-mono">{{ data.project.branch }}</code>
            <span class="af-tech-pass">
              <Check :size="14" :stroke-width="2" />
              技术检查已通过
            </span>
            <span class="af-wait">{{ decisionLabel }}</span>
          </div>
          <p class="af-desc">为订单列表增加导出功能，支持筛选条件，前后端一起完成。</p>
        </div>
      </header>

      <div v-if="narrow" class="af-pane-switch"><button type="button" :data-active="pane === 'chat'" @click="pane = 'chat'">对话与意见</button><button type="button" :data-active="pane === 'delivery'" @click="pane = 'delivery'">交付与验收</button></div>
      <div class="af-delivery-split">
        <section v-show="!narrow || pane === 'chat'" class="af-delivery-chat af-pane">
          <AfTabs
            v-model="leftTab"
            :tabs="[
              { id: 'conversation', label: '对话' },
              { id: 'delivery', label: '交付与验收' },
            ]"
          />
          <div class="af-chat-scroll">
            <article class="af-delivery-msg">
              <header>
                <strong>协调者</strong>
                <time>16:20</time>
              </header>
              <p>前后端实现完成，审查者已通过回归验证。</p>
              <div v-if="leftTab === 'delivery'" class="af-summary">
                <h3>交付摘要</h3>
                <ul>
                  <li v-for="item in deliverySummary" :key="item">
                    <Check :size="16" :stroke-width="2" />
                    {{ item }}
                  </li>
                </ul>
              </div>
              <div v-if="leftTab === 'delivery'" class="af-role-grid">
                <div v-for="role in data.roles" :key="role.id" class="af-role-card">
                  <div>
                    <strong>{{ role.label }}</strong>
                    <span>{{ role.harness }}</span>
                  </div>
                  <AfStatusBadge status="completed" />
                </div>
              </div>
            </article>

            <article class="af-delivery-msg">
              <header>
                <strong>Reviewer · OpenCode</strong>
                <time>16:32</time>
              </header>
              <p>未发现阻塞问题，可以进入验收。</p>
            </article>
            <article v-for="message in localMessages" :key="message.id" class="af-delivery-msg"><header><strong>你</strong><time>刚刚</time></header><p>{{ message.body }}</p></article>
          </div>
          <div class="af-composer-wrap">
            <MessageComposer
              v-model="draft"
              @send="send"
              placeholder="提出修改意见…"
              routing-label="Reviewer · OpenCode"
              context-label=""
            />
          </div>
        </section>

        <section v-show="!narrow || pane === 'delivery'" class="af-delivery-work af-pane" data-region="artifact">
          <AfTabs
            v-model="rightTab"
            :tabs="[
              { id: 'delivery', label: '交付' },
              { id: 'changes', label: `变更 ${data.files.length}` },
              { id: 'tests', label: '测试记录' },
            ]"
          />

          <div v-if="rightTab !== 'tests'" class="af-change-panel">
            <ul class="af-file-list" aria-label="变更文件">
              <li v-for="file in data.files" :key="file.id">
                <button
                  type="button"
                  :data-active="file.id === selectedFile?.id"
                  @click="selectedFileId = file.id"
                >
                  <span class="af-truncate">{{ file.name }}</span>
                  <em>+{{ file.additions }}</em>
                </button>
              </li>
            </ul>
            <div class="af-code-wrap">
              <CodeDiff
                :original="selectedContent.original"
                :modified="selectedContent.modified"
                :language="selectedFile?.language"
              />
            </div>
          </div>
          <div v-else class="af-tests-empty">
            <div><h2>12 项回归测试全部通过</h2><p>订单筛选 · 导出字段 · 文件生成 · 权限校验</p><p>检查完成于今天 16:32</p><span class="af-tech-pass"><Check :size="16" /> 全部通过</span></div>
          </div>

          <div v-if="rightTab !== 'changes'" class="af-checklist">
            <h2>验收清单</h2>
            <ul>
              <li v-for="item in acceptanceChecklist" :key="item">
                <span class="af-check"><Check :size="14" :stroke-width="2" /></span>
                {{ item }}
              </li>
            </ul>
          </div>

          <div v-if="rightTab !== 'changes'" class="af-attachments">
            <h2>交付附件</h2>
            <div class="af-attach-grid">
              <article v-for="item in deliveryAttachments" :key="item.id">
                <FileText :size="18" :stroke-width="1.75" />
                <strong class="af-truncate">{{ item.name }}</strong>
                <button type="button" class="af-attach-action" @click="openAttachment(item.name)">
                  <Download v-if="item.action === '下载'" :size="14" :stroke-width="1.75" />
                  <ExternalLink v-else :size="14" :stroke-width="1.75" />
                  {{ item.action }}
                </button>
              </article>
            </div>
          </div>

          <footer class="af-accept-footer">
            <p>技术检查已通过，仍需人工验收后才能关闭任务。</p>
            <div class="af-accept-actions">
              <AfButton @click="decision = 'changes'; reason = ''; decisionDialog = true">要求修改</AfButton>
              <AfButton tone="primary" :disabled="preview.deliveryDecisions[runId] === 'accepted'" @click="decision = 'accepted'; reason = ''; decisionDialog = true">{{ preview.deliveryDecisions[runId] === 'accepted' ? '已验收' : '确认验收' }}</AfButton>
            </div>
          </footer>
        </section>
      </div>
    </template>
    <AfDialog v-model:open="decisionDialog" :title="decision === 'accepted' ? '确认本次交付' : '提出修改意见'" description="操作将记录在当前前端预览中。">
      <form class="af-dialog-form" @submit.prevent="confirmDecision"><label>验收意见<textarea v-model="reason" :required="decision === 'changes'" rows="4" :placeholder="decision === 'changes' ? '请说明需要调整的内容…' : '可选：记录验收备注…'" /></label><div class="af-dialog-actions"><AfButton @click="decisionDialog = false">取消</AfButton><AfButton type="submit" tone="primary">{{ decision === 'accepted' ? '确认验收' : '提交修改意见' }}</AfButton></div></form>
    </AfDialog>
    <AfDialog v-model:open="attachmentDialog" :title="attachmentName" description="交付附件预览"><div class="af-attachment-preview"><h3>{{ attachmentName.includes('测试') ? '回归测试报告' : attachmentName.includes('xlsx') ? '订单导出示例' : '订单导出实现说明' }}</h3><p>订单导出支持当前筛选条件，包含订单号、金额、状态和创建时间。</p><ul><li>筛选条件与导出结果一致</li><li>导出字段完整</li><li>12 项回归检查通过</li></ul><p>当前为前端内容预览，正式附件将在任务交付后生成。</p></div></AfDialog>
  </div>
</template>

<style scoped>
.af-delivery {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  min-height: 0;
  background: var(--af-bg);
}
.af-delivery-header {
  min-height: 148px;
  padding: 14px 28px 12px;
  border-bottom: 1px solid var(--af-border);
  background: var(--af-surface);
}
.af-back {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--af-text-secondary);
  font-size: 16px;
  margin-bottom: 8px;
}
.af-delivery-header h1 {
  margin: 0;
  font-size: 26px;
  line-height: 34px;
  font-weight: 650;
}
.af-meta {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}
.af-meta code {
  height: 28px;
  padding: 0 10px;
  border-radius: 8px;
  border: 1px solid var(--af-border);
  display: inline-flex;
  align-items: center;
  font-size: 13px;
  background: var(--af-surface);
  color: var(--af-text-secondary);
}
.af-tech-pass {
  height: 28px;
  padding: 0 10px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: var(--af-success-bg);
  color: var(--af-success);
  font-size: 13px;
  font-weight: 500;
}
.af-wait {
  height: 28px;
  padding: 0 10px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  background: var(--af-selected);
  color: var(--af-accent-ink);
  font-size: 13px;
  font-weight: 500;
}
.af-desc {
  margin: 8px 0 0;
  color: var(--af-text-secondary);
  font-size: 16px;
  line-height: 20px;
  max-width: 720px;
}
.af-delivery-split {
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(340px, 0.52fr) minmax(0, 1fr);
  gap: 12px;
  padding: 12px;
}
.af-delivery-chat,
.af-delivery-work {
  display: grid;
  min-height: 0;
  border: 1px solid var(--af-border);
  border-radius: 10px;
  background: var(--af-surface);
  overflow: hidden;
}
.af-delivery-chat {
  grid-template-rows: 52px minmax(0, 1fr) auto;
}
.af-delivery-work {
  grid-template-rows: 52px minmax(300px, 1fr) auto auto auto;
  overflow: auto;
}
.af-delivery-chat :deep(.af-tabs),
.af-delivery-work :deep(.af-tabs) {
  height: 52px;
  padding: 0 12px;
  align-items: flex-end;
}
.af-delivery-chat :deep(.af-tab),
.af-delivery-work :deep(.af-tab) {
  height: 44px;
  font-size: 16px;
  padding: 0 14px;
}
.af-chat-scroll {
  min-height: 0;
  overflow: auto;
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.af-delivery-msg {
  border: 1px solid var(--af-border);
  border-radius: 10px;
  padding: 14px;
  background: var(--af-surface);
}
.af-delivery-msg header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}
.af-delivery-msg header strong {
  color: var(--af-accent-ink);
  font-size: 16px;
}
.af-delivery-msg header time {
  color: var(--af-text-muted);
  font-size: 13px;
}
.af-delivery-msg > p {
  margin: 0;
  font-size: 18px;
  line-height: 24px;
}
.af-summary {
  margin-top: 12px;
  padding: 12px;
  border-radius: 8px;
  background: var(--af-surface-subtle);
}
.af-summary h3 {
  margin: 0 0 8px;
  font-size: 16px;
  font-weight: 650;
}
.af-summary ul {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 8px;
}
.af-summary li {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  color: var(--af-text);
}
.af-summary li :deep(svg) { color: var(--af-success); flex-shrink: 0; }
.af-role-grid {
  margin-top: 12px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.af-role-card {
  border: 1px solid var(--af-border);
  border-radius: 8px;
  padding: 10px 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  background: var(--af-surface);
}
.af-role-card strong,
.af-role-card span { display: block; }
.af-role-card strong { font-size: 16px; }
.af-role-card span { font-size: 12px; color: var(--af-text-secondary); margin-top: 2px; }
.af-composer-wrap { padding: 10px 16px 14px; border-top: 1px solid var(--af-border); }
.af-change-panel {
  min-height: 0;
  display: grid;
  grid-template-columns: 29% minmax(0, 1fr);
  border-bottom: 1px solid var(--af-border);
}
.af-file-list {
  margin: 0;
  padding: 8px;
  list-style: none;
  border-right: 1px solid var(--af-border);
  overflow: auto;
}
.af-file-list button {
  width: 100%;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 0 10px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  text-align: left;
  font-size: 13px;
}
.af-file-list button[data-active='true'] { background: var(--af-selected); }
.af-file-list em {
  font-style: normal;
  color: var(--af-success);
  flex-shrink: 0;
}
.af-code-wrap {
  min-width: 0;
  min-height: 0;
  padding: 8px;
  background: #f7f5fa;
}
.af-code-wrap :deep(.af-code-diff) { min-height: 160px; }
.af-tests-empty {
  min-height: 160px;
  display: grid;
  place-items: center;
  padding: 24px;
  color: var(--af-text-secondary);
  font-size: 16px;
  border-bottom: 1px solid var(--af-border);
}
.af-checklist,
.af-attachments {
  padding: 14px 16px;
  border-bottom: 1px solid var(--af-border);
}
.af-checklist h2,
.af-attachments h2 {
  margin: 0 0 10px;
  font-size: 18px;
  font-weight: 650;
}
.af-checklist ul {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 6px;
}
.af-checklist li {
  height: 40px;
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 16px;
}
.af-check {
  width: 22px;
  height: 22px;
  border-radius: 999px;
  background: var(--af-success-bg);
  color: var(--af-success);
  display: grid;
  place-items: center;
  flex-shrink: 0;
}
.af-attach-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}
.af-attach-grid article {
  border: 1px solid var(--af-border);
  border-radius: 8px;
  padding: 12px;
  display: grid;
  gap: 8px;
  background: var(--af-surface);
  color: var(--af-text-secondary);
}
.af-attach-grid strong {
  color: var(--af-text);
  font-size: 13px;
  font-weight: 500;
}
.af-attach-action {
  width: fit-content;
  height: 28px;
  padding: 0 8px;
  border: 0;
  border-radius: 6px;
  background: var(--af-surface-subtle);
  color: var(--af-accent-ink);
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
}
.af-accept-footer {
  padding: 12px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  background: var(--af-selected);
}
.af-accept-footer p {
  margin: 0;
  font-size: 13px;
  color: var(--af-accent-ink);
  line-height: 20px;
}
.af-accept-actions { display: flex; gap: 8px; flex-shrink: 0; }
.af-accept-actions :deep(.af-button) {
  height: 40px;
  font-size: 18px;
  padding-inline: 14px;
}

 .af-pane-switch { display:flex; gap:8px; padding:10px 12px 0; }
.af-pane-switch button { flex:1; padding:10px; border:1px solid var(--af-border); border-radius:8px; background:var(--af-surface); }
.af-pane-switch button[data-active='true'] { background:var(--af-selected); color:var(--af-accent-ink); }
.af-attachment-preview { line-height:1.8; color:var(--af-text-secondary); }
@media (max-height: 950px) and (min-width:1101px) { .af-delivery-split { overflow:auto; } .af-delivery-chat,.af-delivery-work { min-height:800px; } }
@media (max-width: 1100px) {
 .af-delivery { grid-template-rows:auto auto minmax(0,1fr); }
 .af-delivery-split { grid-template-columns:minmax(0,1fr); overflow:auto; }
 .af-delivery-chat { min-height:700px; }
 .af-delivery-work { min-height:850px; }
}
@media (max-width: 639px) {
 .af-delivery-header { padding:16px; } .af-delivery-header h1 { font-size:24px; }
 .af-change-panel { grid-template-columns:minmax(0,1fr); grid-template-rows:auto minmax(240px,1fr); }
 .af-file-list { display:flex; border-right:0; } .af-file-list li { flex-shrink:0; }
 .af-role-grid { grid-template-columns:1fr; } .af-attach-grid { grid-template-columns:1fr; }
 .af-attach-grid article { display:flex; align-items:center; } .af-attach-action { margin-left:auto; flex-shrink:0; }
 .af-delivery-work { min-height:1100px; } .af-accept-footer { flex-direction:column; align-items:stretch; }
 .af-accept-actions { justify-content:flex-end; } .af-delivery-split { padding:8px; }
}
.af-role-card :deep(.af-status) { flex-shrink:0; }.af-attach-grid article { display:flex; align-items:center; gap:10px; }.af-attach-grid article strong { flex:1; font-size:16px; }.af-attach-action { flex-shrink:0; font-size:14px; }.af-role-card div > span { display:block; }.af-role-card strong { font-size:16px; }
.af-delivery-work:has(.af-change-panel):not(:has(.af-checklist)) { grid-template-rows:52px minmax(400px,1fr) auto; }
</style>
