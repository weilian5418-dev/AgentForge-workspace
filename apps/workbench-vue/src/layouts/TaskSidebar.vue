<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Plus, Search, Pin, Settings } from 'lucide-vue-next'
import AfStatusBadge from '@/components/af/AfStatusBadge.vue'
import { useWorkspaceRepository } from '@/app/runtime'
import { useCurrentProject } from '@/queries/current-project'
import { shouldUseReferenceFromUrl } from '@/app/runtime'

const route = useRoute()
const router = useRouter()
const repository = useWorkspaceRepository()
const project = useCurrentProject()
const query = ref('')
const isReference = shouldUseReferenceFromUrl(window.location.search)

const { data } = useQuery({
  queryKey: computed(() => ['task-sidebar', project.value?.id ?? 'ref']),
  queryFn: ({ signal }) => repository.loadTasks(project.value?.id ?? 'demo-commerce', signal),
  enabled: computed(() => Boolean(project.value)),
})

const isNewTask = computed(() => route.path === '/tasks/new')
const isExecution = computed(() =>
  Boolean(route.params.runId) && !route.path.includes('/delivery'),
)
const isDelivery = computed(() => route.path.includes('/delivery'))

const items = computed(() => {
  const rows = data.value ?? []
  if (!isReference) return rows.filter(task => !query.value || task.goal.includes(query.value))
  const preferred = isNewTask.value
    ? ['demo-permissions', 'demo-payment', 'demo-search', 'demo-migration']
    : ['demo-order-export', 'demo-permissions', 'demo-payment', 'demo-search', 'demo-migration', 'demo-docs']
  return [...rows.filter(x=>x.id.startsWith('preview-')).map(x=>x.id), ...preferred]
    .map(id => rows.find(task => task.id === id))
    .filter((task): task is NonNullable<typeof task> => Boolean(task))
    .map(task => ({
      ...task,
      statusLabel: task.id === 'demo-migration' ? '待审核' : task.statusLabel,
    }))
    .filter(task => !query.value || task.goal.includes(query.value))
})

const pinned = computed(() =>
  items.value.filter(task => task.id === 'demo-order-export' || task.id === 'demo-permissions'),
)
const today = computed(() =>
  items.value.filter(task =>
    task.id !== 'demo-order-export'
    && task.id !== 'demo-permissions'
    && (task.updatedLabel.includes('今天') || task.updatedLabel.includes('分钟') || task.updatedLabel === '刚刚'),
  ),
)
const earlier = computed(() =>
  items.value.filter(task =>
    task.id !== 'demo-order-export'
    && task.id !== 'demo-permissions'
    && !today.value.some(item => item.id === task.id),
  ),
)

const listCount = computed(() => items.value.length + (isNewTask.value ? 1 : 0))

function progressLabel(taskId: string, statusLabel: string) {
  if (taskId === 'demo-order-export' && isDelivery.value) return '待验收'
  if (taskId === 'demo-order-export' && isExecution.value) return '2 个 Agent 正在执行'
  return statusLabel
}
</script>

<template>
  <aside class="af-task-sidebar" data-region="task-sidebar" aria-label="任务列表">
    <template v-if="isNewTask">
      <div class="af-task-sidebar-head">
        <RouterLink class="af-back" to="/tasks" aria-label="返回全部任务">
          <ArrowLeft :size="18" :stroke-width="1.75" />
          <span>全部任务</span>
        </RouterLink>
        <div class="af-list-title">
          <h2>任务列表</h2>
          <em>{{ listCount }}</em>
          <RouterLink class="af-new" to="/tasks/new" aria-label="新建任务">
            <Plus :size="18" :stroke-width="1.75" />
          </RouterLink>
        </div>
      </div>
      <nav>
        <RouterLink to="/tasks/new" class="af-task-row" data-active="true">
          <strong class="af-truncate">{{ isReference ? '实现订单导出功能' : '新建任务' }}</strong>
          <span class="af-draft">{{ isReference ? '拟创建 · feat/order-export' : '填写目标后创建' }}</span>
        </RouterLink>
        <RouterLink
          v-for="task in items"
          :key="task.id"
          :to="`/tasks/${task.id}`"
          class="af-task-row"
        >
          <strong class="af-truncate">{{ task.goal }}</strong>
          <AfStatusBadge :status="task.status" :label="task.statusLabel" />
        </RouterLink>
      </nav>
    </template>

    <template v-else>
      <div class="af-exec-head">
        <h2>任务</h2>
        <RouterLink class="af-new" to="/tasks/new" aria-label="新建任务">
          <Plus :size="18" :stroke-width="1.75" />
        </RouterLink>
      </div>
      <label class="af-search">
        <Search :size="16" :stroke-width="1.75" aria-hidden="true" />
        <input v-model="query" type="search" placeholder="搜索任务…" aria-label="搜索任务" />
      </label>
      <nav>
        <p v-if="pinned.length" class="af-group"><Pin :size="14" :stroke-width="1.75" /> 置顶</p>
        <RouterLink
          v-for="task in pinned"
          :key="task.id"
          :to="isDelivery && route.params.runId === task.id ? `/tasks/${task.id}/delivery` : `/tasks/${task.id}`"
          class="af-task-row"
          :data-active="route.params.runId === task.id"
        >
          <strong class="af-truncate">{{ task.goal }}</strong>
          <span
            class="af-progress"
            :data-status="task.id === 'demo-order-export' && isDelivery ? 'waiting' : task.status"
          >{{ progressLabel(task.id, task.statusLabel) }}</span>
        </RouterLink>

        <p v-if="today.length" class="af-group">今天</p>
        <RouterLink
          v-for="task in today"
          :key="task.id"
          :to="`/tasks/${task.id}`"
          class="af-task-row"
          :data-active="route.params.runId === task.id"
        >
          <strong class="af-truncate">{{ task.goal }}</strong>
          <AfStatusBadge :status="task.status" :label="task.statusLabel" />
        </RouterLink>

        <p v-if="earlier.length" class="af-group">{{ isReference ? '昨天' : '更早' }}</p>
        <RouterLink
          v-for="task in earlier"
          :key="task.id"
          :to="`/tasks/${task.id}`"
          class="af-task-row"
          :data-active="route.params.runId === task.id"
        >
          <strong class="af-truncate">{{ task.goal }}</strong>
          <AfStatusBadge :status="task.status" :label="task.statusLabel" />
        </RouterLink>
      </nav>
    </template>

    <div class="af-task-sidebar-foot">
      <div>
        <span>本地工作区</span>
        <code>{{ isReference ? 'feat/order-export' : project?.branch ?? '按任务创建工作区' }}</code>
      </div>
      <button type="button" class="af-foot-settings" aria-label="工作区设置" @click="router.push('/settings/connection')">
        <Settings :size="18" :stroke-width="1.75" />
      </button>
    </div>
  </aside>
</template>

<style scoped>
.af-task-sidebar {
  display: flex;
  flex-direction: column;
  flex: 0 0 var(--af-task-width);
  border-right: 1px solid var(--af-border);
  background: var(--af-surface);
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.af-task-sidebar-head {
  padding: 12px 12px 8px;
  border-bottom: 1px solid var(--af-border);
  display: grid;
  gap: 10px;
}
.af-exec-head {
  padding: 14px 12px 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.af-exec-head h2 { margin: 0; font-size: 18px; }
.af-back {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--af-text-secondary);
  font-size: 14px;
  width: fit-content;
}
.af-list-title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.af-list-title h2 { margin: 0; font-size: 18px; line-height: 24px; }
.af-list-title em,
.af-progress {
  font-style: normal;
  min-width: 22px;
  height: 28px;
  padding: 0 10px;
  border-radius: 8px;
  background: var(--af-selected);
  color: var(--af-accent-ink);
  font-size: 13px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: fit-content;
  font-weight: 500;
}
.af-progress[data-status='paused'] {
  background: #f1eff4;
  color: var(--af-text-secondary);
}
.af-progress[data-status='waiting'] {
  background: var(--af-selected);
  color: var(--af-accent-ink);
}
.af-new {
  margin-left: auto;
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background: var(--af-button);
  color: #fff;
}
.af-search {
  margin: 0 12px 8px;
  height: 36px;
  padding: 0 10px;
  border: 1px solid var(--af-border);
  border-radius: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
  background: #fff;
  color: var(--af-text-muted);
}
.af-search input {
  border: 0;
  outline: none;
  width: 100%;
  background: transparent;
  font-size: 14px;
}
.af-group {
  margin: 10px 12px 4px;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--af-text-muted);
}
nav { flex: 1; overflow: auto; padding: 8px; display: flex; flex-direction: column; gap: 4px; }
.af-task-row {
  min-height: 72px;
  padding: 12px;
  border-radius: 8px;
  display: grid;
  gap: 8px;
  color: var(--af-text-secondary);
  font-size: 14px;
  border: 1px solid transparent;
}
.af-task-row strong {
  font-size: 16px;
  color: var(--af-text);
  font-weight: 600;
}
.af-task-row[data-active='true'] {
  background: var(--af-selected);
  border-color: var(--af-accent);
}
.af-draft {
  display: inline-flex;
  width: fit-content;
  height: 28px;
  padding: 0 10px;
  border-radius: 8px;
  align-items: center;
  background: #fff;
  color: var(--af-accent-ink);
  font-size: 13px;
  font-weight: 500;
  border: 1px solid var(--af-accent);
}
.af-task-sidebar-foot {
  margin-top: auto;
  padding: 14px 16px;
  border-top: 1px solid var(--af-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 13px;
  color: var(--af-text-muted);
}
.af-task-sidebar-foot > div {
  display: grid;
  gap: 6px;
  min-width: 0;
}
.af-task-sidebar-foot code {
  font-family: 'AF Mono', monospace;
  font-size: 13px;
  color: var(--af-text-secondary);
}
.af-foot-settings {
  width: 36px;
  height: 36px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--af-text-secondary);
  display: grid;
  place-items: center;
  flex-shrink: 0;
}
.af-task-row :deep(.af-status) { width:fit-content; }.af-task-row { align-content:center; }
</style>
