<script setup lang="ts">
import { computed, ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useRouter } from 'vue-router'
import { Plus, Search } from 'lucide-vue-next'
import AfButton from '@/components/af/AfButton.vue'
import AfErrorState from '@/components/af/AfErrorState.vue'
import TaskTable from '@/features/tasks/TaskTable.vue'
import { useWorkspaceRepository } from '@/app/runtime'
import { useCurrentProject, useCurrentProjectQuery } from '@/queries/current-project'
import { useVisualReady } from '@/composables/useVisualReady'
import AfStatusBadge from '@/components/af/AfStatusBadge.vue'

type FilterId = 'all' | 'running' | 'attention' | 'completed' | 'paused'

const router = useRouter()
const repository = useWorkspaceRepository()
const project = useCurrentProject()
const projectQuery = useCurrentProjectQuery()
const filter = ref<FilterId>('all')
const query = ref('')
const owner = ref('all')
const view = ref<'list' | 'board'>('list')

const { data, isPending, isError, error, refetch, isSuccess } = useQuery({
  queryKey: computed(() => ['tasks', project.value?.id ?? 'demo']),
  queryFn: ({ signal }) => repository.loadTasks(project.value?.id ?? 'demo-commerce', signal),
  enabled: computed(() => Boolean(project.value)),
})

const counters = computed(() => { const rows = data.value ?? []; return { all:rows.length, active:rows.filter(x=>x.status==='running').length, attention:rows.filter(x=>['waiting','approval'].includes(x.status)).length, completed:rows.filter(x=>x.status==='completed').length, paused:rows.filter(x=>x.status==='paused').length } })

const filters = computed(() => [
  { id: 'all' as const, label: '全部', count: counters.value.all },
  { id: 'running' as const, label: '进行中', count: counters.value.active },
  { id: 'attention' as const, label: '待处理', count: counters.value.attention },
  { id: 'completed' as const, label: '已完成', count: counters.value.completed },
  { id: 'paused' as const, label: '已暂停', count: counters.value.paused },
])

const filtered = computed(() => {
  const rows = (data.value ?? []).filter(x => owner.value === 'all' || x.harnesses.includes(owner.value))
  const byStatus = rows.filter(item => {
    if (filter.value === 'all') return true
    if (filter.value === 'running') return item.status === 'running'
    if (filter.value === 'attention') return item.status === 'waiting' || item.status === 'approval'
    if (filter.value === 'completed') return item.status === 'completed'
    return item.status === 'paused'
  })
  const q = query.value.trim()
  if (!q) return byStatus
  return byStatus.filter(item => item.goal.includes(q))
})

useVisualReady(computed(() => isSuccess.value && Boolean(data.value)))

function setFilter(next: FilterId) {
  filter.value = next
}
</script>

<template>
  <main class="af-page af-task-library" data-page-id="05-task-library" data-region="content">
    <p class="af-crumb">{{ project?.name ?? '电商平台' }} / 任务</p>
    <header class="af-library-header">
      <div>
        <h1>全部任务</h1>
        <p>跟踪每次执行，从目标到交付。</p>
      </div>
      <div class="af-library-actions">
        <div class="af-view-toggle" role="group" aria-label="视图切换">
          <button type="button" :data-active="view === 'list'" @click="view = 'list'">列表</button>
          <button type="button" :data-active="view === 'board'" @click="view = 'board'">看板</button>
        </div>
        <AfButton tone="primary" @click="router.push('/tasks/new')">
          <Plus :size="18" :stroke-width="1.75" />
          新建任务
        </AfButton>
      </div>
    </header>

    <div class="af-toolbar">
      <div class="af-status-tabs" role="tablist" aria-label="任务状态">
        <button
          v-for="item in filters"
          :key="item.id"
          type="button"
          role="tab"
          :aria-selected="filter === item.id"
          :data-active="filter === item.id"
          @click="setFilter(item.id)"
        >
          {{ item.label }} <em>{{ item.count }}</em>
        </button>
      </div>
      <div class="af-toolbar-filters">
        <label class="af-search">
          <Search :size="16" :stroke-width="1.75" aria-hidden="true" />
          <input v-model="query" type="search" placeholder="搜索任务目标…" aria-label="搜索任务目标" />
        </label>
        <select class="af-select" aria-label="项目"><option>{{ project?.name ?? '电商平台' }}</option></select>
        <select v-model="owner" class="af-select" aria-label="执行者筛选"><option value="all">全部执行者</option><option>Codex CLI</option><option>Claude Code</option><option>OpenCode</option></select>
      </div>
    </div>

    <AfErrorState v-if="projectQuery.isError.value" :message="projectQuery.error.value?.message" @retry="projectQuery.refetch()" />
    <p v-else-if="projectQuery.isSuccess.value && !project">暂无已注册的项目，请先注册并授权项目目录。</p>
    <AfErrorState v-else-if="isError" :message="error?.message" @retry="refetch()" />
    <div v-else-if="isPending">加载任务列表…</div>
    <template v-else>
      <div v-if="!filtered.length" class="af-empty-state"><Search :size="30"/><h2>没有找到匹配的任务</h2><p>试试其他关键词或状态。</p><AfButton @click="query = ''; filter = 'all'; owner = 'all'">清除筛选</AfButton></div>
      <TaskTable v-else-if="view === 'list'" :items="filtered" />
      <div v-else class="af-task-board"><section v-for="column in [{id:'running',label:'进行中'},{id:'attention',label:'待处理'},{id:'completed',label:'已完成'},{id:'paused',label:'已暂停'}]" :key="column.id"><h2>{{column.label}} <span>{{filtered.filter(x=> column.id==='attention' ? ['approval','waiting'].includes(x.status) : x.status===column.id).length}}</span></h2><RouterLink v-for="item in filtered.filter(x=> column.id==='attention' ? ['approval','waiting'].includes(x.status) : x.status===column.id)" :key="item.id" :to="`/tasks/${item.id}`"><AfStatusBadge :status="item.status" :label="item.statusLabel"/><h3>{{item.goal}}</h3><p>{{item.harnesses.join(' · ')}}</p><small>{{item.updatedLabel}}</small></RouterLink></section></div>
      <div class="af-paging">
        <span>共 {{ filtered.length }} 个任务</span>
        <div class="af-pages">
          <button type="button" disabled aria-label="上一页">‹</button>
          <em>1 / 1</em>
          <button type="button" disabled aria-label="下一页">›</button>
        </div>
      </div>
      <details class="af-archived">
        <summary>已归档任务</summary>
        <p>暂无归档任务。</p>
      </details>
    </template>
  </main>
</template>

<style scoped>
.af-crumb {
  margin: 0 0 12px;
  color: var(--af-text-muted);
  font-size: 14px;
}
.af-library-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 24px;
}
.af-library-header h1 {
  margin: 0;
  font-size: 36px;
  line-height: 46px;
  font-weight: 700;
}
.af-library-header p {
  margin: 8px 0 0;
  font-size: 18px;
  line-height: 28px;
  color: var(--af-text-secondary);
}
.af-library-actions { display: flex; align-items: center; gap: 12px; }
.af-view-toggle {
  display: inline-flex;
  padding: 3px;
  border-radius: 8px;
  background: var(--af-surface-subtle);
  gap: 2px;
}
.af-view-toggle button {
  height: 36px;
  padding: 0 14px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--af-text-secondary);
  font-size: 15px;
}
.af-view-toggle button[data-active='true'] {
  background: var(--af-surface);
  color: var(--af-text);
  box-shadow: 0 0 0 1px var(--af-border);
}
.af-view-toggle button:disabled { opacity: 0.45; }
.af-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 20px;
}
.af-status-tabs {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 4px;
  border-radius: 10px;
  background: var(--af-surface-subtle);
}
.af-status-tabs button {
  height: 36px;
  padding: 0 12px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--af-text-secondary);
  font-size: 14px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.af-status-tabs button[data-active='true'] {
  background: var(--af-surface);
  color: var(--af-accent-ink);
  box-shadow: 0 0 0 1px var(--af-accent);
}
.af-status-tabs em {
  font-style: normal;
  min-width: 18px;
  height: 20px;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--af-selected);
  font-size: 12px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.af-toolbar-filters { display: flex; flex-wrap: wrap; gap: 8px; }
.af-search {
  height: 40px;
  min-width: 220px;
  padding: 0 12px;
  border: 1px solid var(--af-border);
  border-radius: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--af-surface);
  color: var(--af-text-muted);
}
.af-search input {
  border: 0;
  outline: none;
  width: 100%;
  background: transparent;
  font-size: 14px;
}
.af-select {
  height: 40px;
  padding: 0 12px;
  border: 1px solid var(--af-border);
  border-radius: 8px;
  background: var(--af-surface);
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--af-text-secondary);
  font-size: 14px;
}
.af-paging {
  margin-top: 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--af-text-secondary);
  font-size: 14px;
}
.af-pages {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.af-pages button {
  width: 32px;
  height: 32px;
  border: 1px solid var(--af-border);
  border-radius: 8px;
  background: var(--af-surface);
}
.af-pages em { font-style: normal; }
.af-archived {
  margin-top: 20px;
  border-top: 1px solid var(--af-border);
  padding-top: 12px;
}
.af-archived summary {
  cursor: pointer;
  color: var(--af-text-secondary);
  font-size: 14px;
}
.af-archived p {
  margin: 10px 0 0;
  color: var(--af-text-muted);
  font-size: 14px;
}
.af-task-board { display:grid; grid-template-columns:repeat(4,minmax(240px,1fr)); gap:16px; overflow:auto; padding-bottom:12px; }
.af-task-board section { background:var(--af-surface-subtle); border-radius:10px; padding:14px; }
.af-task-board h2 { font-size:16px; margin:0 0 16px; display:flex; justify-content:space-between; }
.af-task-board a { display:block; padding:18px; border:1px solid var(--af-border); border-radius:10px; background:var(--af-surface); margin-bottom:12px; }
.af-task-board h3 { font-size:18px; line-height:1.6; margin:12px 0; }
.af-task-board p,.af-task-board small { color:var(--af-text-secondary); font-size:13px; }
</style>
