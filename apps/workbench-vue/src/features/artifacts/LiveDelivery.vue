<script setup lang="ts">
import { computed, ref } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import type { TaskViewModel } from '@/domain/view-models'
import { useWorkspaceRepository } from '@/app/runtime'
import { useFeedbackStore } from '@/stores/feedback'
import AfButton from '@/components/af/AfButton.vue'
import AfDialog from '@/components/af/AfDialog.vue'
import AfErrorState from '@/components/af/AfErrorState.vue'

const props = defineProps<{ task: TaskViewModel }>()
const repository = useWorkspaceRepository()
const feedback = useFeedbackStore()
const client = useQueryClient()
const note = ref('')
const decision = ref<'accept' | 'reject'>('accept')
const open = ref(false)
const pending = ref(false)
const previewPending = ref(false)
const previewUrl = ref('')
const selectedId = ref('')
let attempt = { body: '', key: '' }
const { data: pointer, isError, refetch } = useQuery({ queryKey: computed(() => ['delivery-pointer', props.task.id, props.task.live?.delivery?.deliveryRevision]), queryFn: ({ signal }) => repository.loadDeliveryPointer!(props.task.id, signal) })
const { data: manifest, isError: manifestError, refetch: reloadManifest } = useQuery({ queryKey: computed(() => ['delivery-manifest', props.task.id, pointer.value?.manifestHash]), queryFn: ({ signal }) => repository.loadManifest!(props.task.id, pointer.value!.manifestHash!, signal), enabled: computed(() => Boolean(pointer.value?.manifestHash)) })
const selected = computed(() => manifest.value?.payload.members?.find(member => member.memberId === selectedId.value) ?? manifest.value?.payload.members?.[0])
function memberName(member: NonNullable<typeof selected.value>) {
  return props.task.files.find(file => file.id === member.sourceCandidateRef)?.name ?? member.safeRelativePath
}
const { data: content, isPending: loadingContent, isError: contentError, refetch: reloadContent } = useQuery({ queryKey: computed(() => ['artifact-content', selected.value?.sourceCandidateRef]), enabled: computed(() => Boolean(selected.value?.sourceCandidateRef)), queryFn: async ({ signal }) => {
  const blob = await repository.loadArtifact!(selected.value!.sourceCandidateRef!, signal)
  if (blob.size > 2 * 1024 * 1024) return '文件超过 2 MB，请下载查看。'
  const bytes = await blob.arrayBuffer()
  try { return new TextDecoder('utf-8', { fatal: true }).decode(bytes) }
  catch { return '此文件为二进制产物，请下载查看。' }
} })
const acceptance = computed(() => ({ none: '尚未交付', awaiting_acceptance: '等待验收', accepted: '已验收', rejected: '已要求修改', partially_accepted: '部分验收', invalidated: '验收已失效' })[pointer.value?.acceptanceStatus ?? 'none'])
const canDecide = computed(() => Boolean(manifest.value && pointer.value?.technicalStatus === 'passed' && props.task.live?.guidance?.allowedActions?.some(action => action.action === 'decide_acceptance' && action.allowed)))
async function download() {
  const member = selected.value
  if (!member?.sourceCandidateRef || pending.value) return
  pending.value = true
  try {
    const blob = await repository.loadArtifact!(member.sourceCandidateRef)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url; link.download = memberName(member).split(/[\\/]/).pop() ?? 'artifact'; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (error) { feedback.notify(error instanceof Error ? error.message : '下载失败', 'error') }
  finally { pending.value = false }
}
async function openPreview() {
  const member = selected.value
  if (!member?.sourceCandidateRef || !repository.createArtifactPreview || previewPending.value) return
  previewPending.value = true
  try {
    const result = await repository.createArtifactPreview(member.sourceCandidateRef)
    previewUrl.value = result.url.startsWith('http') ? result.url : result.url
    feedback.notify('本机预览已创建', 'success')
  } catch (error) {
    feedback.notify(error instanceof Error ? error.message : '预览失败', 'error')
  } finally {
    previewPending.value = false
  }
}
async function confirm() {
  if (!pointer.value || !manifest.value || !props.task.live || pending.value || (decision.value === 'reject' && !note.value.trim())) return
  const body = { integrationVersion: manifest.value.versionId, manifestHash: manifest.value.manifestHash, manifestRevision: manifest.value.revision, expectedStatusVersion: props.task.live.statusVersion, decision: decision.value, note: note.value.trim() || null }
  const fingerprint = JSON.stringify(body)
  if (attempt.body !== fingerprint) attempt = { body: fingerprint, key: crypto.randomUUID() }
  pending.value = true
  try { await repository.acceptDelivery!(props.task.id, pointer.value.deliveryRevision, body, attempt.key); open.value = false; await client.invalidateQueries(); feedback.notify('验收决定已保存到后端', 'success') }
  catch (error) { feedback.notify(error instanceof Error ? error.message : '提交失败', 'error'); await client.invalidateQueries() }
  finally { pending.value = false }
}
</script>

<template>
  <main class="af-live-delivery" aria-label="真实任务交付">
    <RouterLink :to="`/tasks/${task.id}`">← 返回任务</RouterLink>
    <h1>{{ task.goal }}</h1><p>{{ task.project.name }} · {{ task.statusLabel }} · {{ acceptance }}</p>
    <AfErrorState v-if="isError" @retry="refetch()" />
    <template v-else>
      <p>技术检查：{{ pointer?.technicalStatus === 'passed' ? '已通过' : pointer?.technicalStatus ?? '正在读取' }}</p>
      <p v-if="pointer?.invalidatedReason" role="alert">{{ pointer.invalidatedReason }}</p>
      <p v-if="pointer && !pointer.manifestHash">当前任务尚未发布交付清单。</p>
      <AfErrorState v-if="manifestError" @retry="reloadManifest()" />
      <template v-if="manifest">
        <h2>交付文件</h2><div class="af-live-artifacts"><nav><button v-for="member in manifest.payload.members" :key="member.memberId" type="button" :data-active="member.memberId === selected?.memberId" @click="selectedId=member.memberId">{{ memberName(member) }}</button></nav><div class="af-live-source"><AfErrorState v-if="contentError" @retry="reloadContent()" /><p v-else-if="loadingContent && selected?.sourceCandidateRef">正在读取产物…</p><pre v-else>{{ content ?? '当前文件无可预览的候选内容，请查看下方交付位置。' }}</pre><div class="af-live-actions"><AfButton :pending="pending" :disabled="!selected?.sourceCandidateRef" @click="download">下载所选文件</AfButton><AfButton :pending="previewPending" :disabled="!selected?.sourceCandidateRef || previewPending" @click="openPreview">本机预览</AfButton></div><p v-if="previewUrl" class="af-preview-link">预览地址：<a :href="previewUrl" target="_blank" rel="noreferrer">{{ previewUrl }}</a></p></div></div>
        <h2>检查记录</h2><ul><li v-for="check in manifest.payload.checks" :key="check.checkId">{{ check.checkId }} · {{ check.outcome }}<span v-if="check.reportId"> · {{ check.reportId }}</span></li></ul><p v-if="!manifest.payload.checks?.length">清单中没有检查记录。</p>
        <h2 v-if="manifest.payload.knownRisks?.length">已知风险</h2><ul><li v-for="risk in manifest.payload.knownRisks" :key="risk.detail">{{ risk.detail }}</li></ul>
        <footer><AfButton :disabled="!canDecide" @click="decision='reject'; note=''; open=true">要求修改</AfButton><AfButton tone="primary" :disabled="!canDecide" @click="decision='accept'; note=''; open=true">{{ pointer?.acceptanceStatus === 'accepted' ? '已验收' : '确认验收' }}</AfButton></footer>
      </template>
      <h2>交付位置</h2><ul><li v-for="location in pointer?.artifactLocations" :key="`${location.kind}-${location.path}`"><strong>{{ location.label ?? location.kind }}</strong><code>{{ location.path }}</code></li></ul>
    </template>
    <AfDialog v-model:open="open" :title="decision === 'accept' ? '确认本次交付' : '要求修改'" description="决定绑定当前交付清单与版本，提交后将保存到后端。"><form class="af-dialog-form" @submit.prevent="confirm"><label>验收意见<textarea v-model="note" :required="decision==='reject'" maxlength="4096" rows="4" /></label><div class="af-dialog-actions"><AfButton :disabled="pending" @click="open=false">取消</AfButton><AfButton type="submit" tone="primary" :pending="pending">提交决定</AfButton></div></form></AfDialog>
  </main>
</template>

<style scoped>
.af-live-delivery { overflow:auto; min-height:0; padding:28px; }
h1 { font-size:26px; line-height:1.5; } h2 { font-size:20px; margin-top:28px; }
p, li { color:var(--af-text-secondary); line-height:1.7; } code { display:block; overflow-wrap:anywhere; }
.af-live-artifacts { display:grid; grid-template-columns:minmax(180px, 24%) minmax(0,1fr); border:1px solid var(--af-border); border-radius:8px; overflow:hidden; }
nav { display:grid; align-content:start; border-right:1px solid var(--af-border); } nav button { text-align:left; padding:12px; background:var(--af-surface); color:var(--af-text); border:0; overflow-wrap:anywhere; }
nav button[data-active='true'] { background:var(--af-selected); }
.af-live-source { padding:16px; min-width:0; } pre { white-space:pre-wrap; overflow-wrap:anywhere; max-height:460px; overflow:auto; font:14px/1.7 monospace; }
footer { display:flex; justify-content:flex-end; gap:12px; margin-top:24px; }
@media(max-width:767px) { .af-live-delivery { padding:18px; } .af-live-artifacts { grid-template-columns:1fr; } nav { border-right:0; border-bottom:1px solid var(--af-border); } }
</style>
