<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useRouter } from 'vue-router'
import { FileCheck2, Shield } from 'lucide-vue-next'
import AfErrorState from '@/components/af/AfErrorState.vue'
import AfButton from '@/components/af/AfButton.vue'
import ApprovalDetail from '@/features/approvals/ApprovalDetail.vue'
import { useLibraryStore } from '@/stores/library'
import { useFeedbackStore } from '@/stores/feedback'
import { useWorkspaceRepository, shouldUseReferenceFromUrl } from '@/app/runtime'
import { useVisualReady } from '@/composables/useVisualReady'

const router = useRouter()
const repository = useWorkspaceRepository()
const selectedId = ref<string | null>(null)
const tab = ref<'pending' | 'done'>('pending')
const deciding = ref(false)
const local = useLibraryStore()
const feedback = useFeedbackStore()
const kind = ref('all')
const isReference = shouldUseReferenceFromUrl(window.location.search)
const resolved = (item: { id: string; status: string }) => isReference ? Boolean(local.decisions[item.id]) : !['approval', 'waiting'].includes(item.status)

const { data, isPending, isError, error, refetch, isSuccess } = useQuery({
  queryKey: ['inbox'],
  queryFn: ({ signal }) => repository.loadInbox(signal),
})

watch(data, value => {
  if (value?.[0] && !selectedId.value) selectedId.value = value[0].id
}, { immediate: true })

const pendingItems = computed(() =>
  (data.value ?? []).filter(item => !resolved(item)),
)
const visible = computed(() => (tab.value === 'pending' ? pendingItems.value : (data.value ?? []).filter(resolved)).filter(x=>kind.value==='all' || x.kind===kind.value))
watch(visible, items => { if(!items.some(x=>x.id===selectedId.value)) selectedId.value=items[0]?.id ?? null })
const selected = computed(() => data.value?.find(item => item.id === selectedId.value) ?? null)

useVisualReady(computed(() => isSuccess.value && Boolean(data.value)))

async function decide(decision: 'allow_once' | 'deny') {
  if (!selected.value?.detail || deciding.value) return
  deciding.value = true
  try {
    if (!isReference) {
      const approval = selected.value.detail
      if (!approval.actionDigest) throw new Error('缺少审批摘要，请刷新后重试')
      await repository.decideApproval({ approvalId: approval.id, actionDigest: approval.actionDigest, decision, idempotencyKey: crypto.randomUUID() })
      await refetch()
      feedback.notify('审批决定已提交，原生确认状态以后端为准', 'success')
      return
    }
    local.decisions[selected.value.id] = decision
    feedback.notify(decision === 'allow_once' ? '已在预览中记录：允许一次' : '已在预览中记录：拒绝')
    selectedId.value = null
  } catch (error) {
    feedback.notify(error instanceof Error ? error.message : '审批失败', 'error')
  } finally {
    deciding.value = false
  }
}
</script>

<template>
  <main class="af-page af-inbox-page" data-page-id="06-inbox" data-region="content">
    <p class="af-crumb">工作区 / 收件箱</p><header class="af-inbox-page-head"><h1>收件箱</h1><p>需要你决定的事，集中处理。</p></header>
    <AfErrorState v-if="isError" :message="error?.message" @retry="refetch()" />
    <div v-else-if="isPending">加载收件箱…</div>
    <div v-else class="af-inbox" data-region="attention-list">
      <section class="af-inbox-list">
        <header class="af-list-head">
          <div class="af-list-tabs">
            <button type="button" :data-active="tab === 'pending'" @click="tab = 'pending'">
              待处理 <em>{{ pendingItems.length }}</em>
            </button>
            <button type="button" :data-active="tab === 'done'" @click="tab = 'done'">已处理</button>
          </div>
          <select v-model="kind" class="af-filter" aria-label="事项类型"><option value="all">全部事项</option><option value="approval">操作批准</option><option value="acceptance">交付验收</option></select>
        </header>

        <div v-if="!visible.length" class="af-empty-list">{{tab === 'done' ? '暂无已处理事项。' : '没有待处理事项，稍后再来看看。'}}</div>
        <div v-else class="af-items">
          <button
            v-for="item in visible"
            :key="item.id"
            type="button"
            class="af-inbox-item"
            :data-active="item.id === selectedId"
            :data-kind="item.kind"
            @click="selectedId = item.id"
          >
            <span class="af-item-icon" aria-hidden="true">
              <Shield v-if="item.kind === 'approval'" :size="18" :stroke-width="1.75" />
              <FileCheck2 v-else :size="18" :stroke-width="1.75" />
            </span>
            <span class="af-item-copy">
              <strong>{{ item.title }}</strong>
              <span>{{ item.subtitle }}</span>
            </span>
            <em>{{ item.time }}</em>
          </button>
        </div>
      </section>

      <section class="af-inbox-detail">
        <ApprovalDetail
          v-if="selected?.detail"
          :approval="selected.detail"
          :resolved="resolved(selected)"
          :task-goal="isReference ? '实现订单导出功能' : selected.title"
          @allow="decide('allow_once')"
          @deny="decide('deny')"
          @back="router.push('/tasks/demo-order-export')"
          @close="selectedId = null"
        />
        <div v-else-if="selected" class="af-acceptance-detail">
          <header>
            <span class="af-item-icon" data-kind="acceptance" aria-hidden="true">
              <FileCheck2 :size="22" :stroke-width="1.75" />
            </span>
            <div>
              <h2>{{ selected.title }}</h2>
              <p>{{ selected.subtitle }}</p>
            </div>
          </header>
          <p class="af-acceptance-note">交付已准备好。请检查变更、测试记录与交付附件，再确认验收。</p>
          <div class="af-acceptance-actions">
            <AfButton tone="primary" @click="router.push('/tasks/demo-migration/delivery')">前往交付验收</AfButton>
          </div>
        </div>
        <div v-else class="af-empty">选择左侧事项查看详情</div>
      </section>
    </div>
  </main>
</template>

<style scoped>
.af-inbox-page { padding-bottom: 16px; }
.af-inbox {
  display: grid;
  grid-template-columns: 552px minmax(0, 1fr);
  gap: 14px;
  min-height: calc(100dvh - 260px);
}
.af-inbox-list,
.af-inbox-detail {
  border: 1px solid var(--af-border);
  border-radius: 10px;
  background: var(--af-surface);
  min-height: 0;
  overflow: hidden;
}
.af-inbox-list {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
}
.af-list-head {
  padding: 20px 20px 12px;
  border-bottom: 1px solid var(--af-border);
  display: grid;
  gap: 14px;
}
.af-list-head h1 {
  margin: 0;
  font-size: 28px;
  line-height: 36px;
  font-weight: 700;
}
.af-list-head p {
  margin: 6px 0 0;
  color: var(--af-text-secondary);
  font-size: 14px;
}
.af-list-tabs {
  display: inline-flex;
  gap: 4px;
  padding: 3px;
  border-radius: 8px;
  background: var(--af-surface-subtle);
  width: fit-content;
}
.af-list-tabs button {
  height: 32px;
  padding: 0 12px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--af-text-secondary);
  font-size: 14px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.af-list-tabs button[data-active='true'] {
  background: var(--af-surface);
  color: var(--af-accent-ink);
  box-shadow: 0 0 0 1px var(--af-accent);
}
.af-list-tabs em {
  font-style: normal;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 999px;
  background: var(--af-selected);
  font-size: 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.af-filter {
  width: fit-content;
  height: 32px;
  padding: 0 10px;
  border: 1px solid var(--af-border);
  border-radius: 8px;
  background: var(--af-surface);
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--af-text-secondary);
  font-size: 16px;
}
.af-items {
  padding: 8px;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.af-inbox-item {
  min-height: 84px;
  padding: 14px 12px;
  border: 1px solid transparent;
  border-radius: 10px;
  background: transparent;
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr) auto;
  gap: 12px;
  text-align: left;
  align-items: start;
}
.af-inbox-item[data-active='true'] {
  background: var(--af-selected);
  border-color: var(--af-accent);
}
.af-item-icon {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background: var(--af-attention-bg);
  color: var(--af-attention);
}
.af-inbox-item[data-kind='acceptance'] .af-item-icon,
.af-item-icon[data-kind='acceptance'] {
  background: var(--af-selected);
  color: var(--af-accent-ink);
}
.af-item-copy { min-width: 0; display: grid; gap: 4px; }
.af-item-copy strong {
  font-size: 18px;
  font-weight: 600;
  color: var(--af-text);
}
.af-item-copy span {
  font-size: 16px;
  color: var(--af-text-secondary);
}
.af-inbox-item > em {
  font-style: normal;
  font-size: 14px;
  color: var(--af-text-muted);
  white-space: nowrap;
}
.af-empty,
.af-empty-list {
  height: 100%;
  min-height: 240px;
  display: grid;
  place-items: center;
  color: var(--af-text-muted);
  font-size: 14px;
  padding: 24px;
}
.af-acceptance-detail {
  height: 100%;
  padding: 24px;
  display: grid;
  grid-template-rows: auto 1fr auto;
  gap: 20px;
}
.af-acceptance-detail header {
  display: flex;
  gap: 14px;
  align-items: flex-start;
}
.af-acceptance-detail h2 {
  margin: 0;
  font-size: 22px;
}
.af-acceptance-detail header p {
  margin: 6px 0 0;
  color: var(--af-text-secondary);
}
.af-acceptance-note {
  margin: 0;
  color: var(--af-text-secondary);
  line-height: 24px;
}
.af-acceptance-actions { display: flex; justify-content: flex-end; }

@media (max-width: 1279px) {
  .af-inbox { grid-template-columns: 1fr; }
}
.af-crumb { margin:0 0 12px; color:var(--af-text-muted); font-size:16px; }.af-inbox-page-head { margin-bottom:24px; }.af-inbox-page-head h1 { font-size:36px; line-height:46px; margin:0; }.af-inbox-page-head p { margin:8px 0 0; font-size:18px; color:var(--af-text-secondary); }.af-list-head { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; }.af-list-tabs button { height:42px; font-size:16px; }
</style>
