<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useRouter } from 'vue-router'
import {
  Paperclip,
  ChevronDown,
  Send,
  Shield,
  FileCheck2,
  Info,
  Code2,
  Bug,
  SearchCode,
  Folder, GitBranch, FileText, Github, ExternalLink, ArrowRight, Play, CirclePause, CircleDashed, Layers,
} from 'lucide-vue-next'
import AfButton from '@/components/af/AfButton.vue'
import AfErrorState from '@/components/af/AfErrorState.vue'
import AfSelect from '@/components/af/AfSelect.vue'
import { useWorkspaceRepository } from '@/app/runtime'
import { useCurrentProject, useCurrentProjectQuery } from '@/queries/current-project'
import { useVisualReady } from '@/composables/useVisualReady'

const router = useRouter()
const repository = useWorkspaceRepository()
const project = useCurrentProject()
const projectQuery = useCurrentProjectQuery()
const draft = ref('')
const attachmentInput = ref<HTMLInputElement | null>(null)
const attachedNames = ref<string[]>([])
const schedule = ref('自动调度')
const scheduleOptions = [
  { value: '自动调度', label: '自动调度' },
  { value: '手动分配', label: '手动分配' },
]
const selectedProject = ref('')
function selectFiles(event: Event) {
  attachedNames.value = Array.from((event.target as HTMLInputElement).files ?? []).map(file => file.name)
}

const { data, isPending, isError, error, refetch, isSuccess } = useQuery({
  queryKey: computed(() => ['overview', project.value?.id ?? 'demo']),
  queryFn: ({ signal }) => repository.loadOverview(project.value?.id ?? 'demo-commerce', signal),
  enabled: computed(() => Boolean(project.value)),
})

const projectName = computed(() => data.value?.project.name ?? project.value?.name ?? '项目')
const projectOptions = computed(() => [{ value: projectName.value, label: projectName.value }])
watch(projectName, name => { selectedProject.value = name }, { immediate: true })

useVisualReady(computed(() => isSuccess.value && Boolean(data.value)))

const quickActions = [
  { label: '开发新功能', icon: Code2, text: '开发新功能：' },
  { label: '修复问题', icon: Bug, text: '修复问题：' },
  { label: '审查代码', icon: SearchCode, text: '审查代码：' },
] as const

function createTask() {
  void router.push({path:'/tasks/new',query:{draft:draft.value.trim() || undefined, attachment:attachedNames.value[0], mode:schedule.value==='自动调度'?'auto':'manual'}})
}

function applyQuick(text: string) {
  draft.value = text
}
</script>

<template>
  <main class="af-page af-overview-page" data-page-id="01-overview" data-region="content">
    <AfErrorState v-if="projectQuery.isError.value" :message="projectQuery.error.value?.message" @retry="projectQuery.refetch()" />
    <p v-else-if="projectQuery.isSuccess.value && !project">暂无已注册的项目，请先通过 AgentForge 注册并授权项目目录。</p>
    <AfErrorState v-else-if="isError" :message="error?.message" @retry="refetch()" />
    <div v-else-if="isPending" class="af-skeleton">加载中…</div>
    <div v-else-if="data" class="af-overview">
      <section class="af-overview-main">
        <div class="af-overview-top">
          <p class="af-breadcrumb">
            <span>{{ data.project.name }}</span>
            <span>/</span>
            <strong>概览</strong>
          </p>
          <AfButton @click="router.push('/settings/connection')">项目设置</AfButton>
        </div>

        <section class="af-goal-composer" aria-label="创建任务">
          <h1>今天，准备完成什么？</h1>
          <p class="af-goal-sub">描述目标，让合适的 Agent 一起完成。</p>
          <div class="af-goal-box">
          <label class="af-goal-label" for="overview-goal">任务目标</label>
          <textarea id="overview-goal"
            v-model="draft"
            rows="3"
            placeholder="例如：为订单列表增加导出功能，并补齐接口测试…"
            aria-label="任务目标"
          />
          <div v-if="attachedNames.length" class="af-attached"><span v-for="name in attachedNames" :key="name"><FileText :size="14" />{{ name }}</span></div>
          <div class="af-composer-bar">
            <input ref="attachmentInput" type="file" multiple hidden aria-label="选择参考资料" @change="selectFiles" />
            <button type="button" class="af-chip" aria-label="添加参考资料" @click="attachmentInput?.click()">
              <Paperclip :size="18" :stroke-width="1.75" />
            </button>
            <AfSelect v-model="selectedProject" variant="chip" aria-label="项目" :options="projectOptions">
              <template #leading><Folder :size="16" :stroke-width="1.75" /></template>
            </AfSelect>
            <AfSelect v-model="schedule" variant="chip" aria-label="调度方式" :options="scheduleOptions">
              <template #leading><Layers :size="16" :stroke-width="1.75" /></template>
            </AfSelect>
            <span class="af-harness-avail"><i />{{ data.harnessConnected }} 个 Harness 已连接</span>
            <button type="button" class="af-create" @click="createTask">
              创建任务
              <Send :size="18" :stroke-width="1.75" />
            </button>
          </div>
          </div>
        </section>

        <div class="af-quick-actions">
          <button
            v-for="action in quickActions"
            :key="action.label"
            type="button"
            class="af-quick"
            @click="applyQuick(action.text)"
          >
            <component :is="action.icon" :size="18" :stroke-width="1.75" aria-hidden="true" />
            {{ action.label }}
          </button>
        </div>

        <div class="af-section-head"><h2>继续进行</h2><RouterLink to="/tasks">查看全部任务 <ArrowRight :size="18" /></RouterLink></div>
        <ul class="af-continue">
          <li v-for="task in data.continueTasks" :key="task.id">
            <component :is="task.status === 'paused' ? CirclePause : CircleDashed" :size="28" class="af-task-state" />
            <div class="af-continue-main">
              <div class="af-continue-title"><strong>{{ task.goal }}</strong><span class="af-progress" :data-status="task.status">{{ task.progressLabel }}</span></div>
              <p>{{ task.id === 'demo-permissions' ? '前后端并行开发，完成后交给审查者验证。' : '为订单列表增加导出功能，支持筛选条件，前后端一起完成。' }}</p>
            </div>
            <div class="af-task-roles" aria-label="Planner、开发者与审查者"><span>P</span><Code2 :size="22" /><Layers :size="22" /><SearchCode :size="22" /></div>
            <span class="af-meta">最近更新：{{ task.updatedLabel }}</span>
            <AfButton
              tone="ghost"
              @click="router.push(`/tasks/${task.id}`)"
            >
              <Play :size="18" />{{ task.actionLabel }}
            </AfButton>
          </li>
        </ul>

        <h2>最近交付</h2>
        <div class="af-recent-wrap"><table class="af-recent">
          <thead>
            <tr>
              <th>任务</th>
              <th>交付状态</th>
              <th>交付时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in data.recentDeliveries" :key="item.id">
              <td><span class="af-task-name"><FileCheck2 :size="22" />{{ item.goal }}</span></td>
              <td>
                <span class="af-done">{{ item.statusLabel }}</span>
              </td>
              <td>{{ item.deliveredAt }}</td>
              <td>
                <RouterLink :to="`/tasks/${item.id}/delivery`"><ExternalLink :size="18" />查看交付</RouterLink>
              </td>
            </tr>
          </tbody>
        </table></div>
      </section>

      <aside class="af-overview-aside">
        <section class="af-aside-card">
          <h2>项目上下文</h2>
          <p class="af-repo"><Github :size="24" />{{ data.project.repository }}<ExternalLink :size="18" /></p>
          <p class="af-branch"><GitBranch :size="22" />{{ data.contextBranch }}<ChevronDown :size="18" /></p>
          <h3>参考资料</h3>
          <ul>
            <li v-for="file in data.contextFiles" :key="file.id">
              <RouterLink :to="`/knowledge/${file.id}`"><FileText :size="22" />{{ file.name }}</RouterLink>
            </li>
          </ul>
        </section>

        <section class="af-aside-card">
          <h2>待你处理</h2>
          <ul class="af-attention">
            <li v-for="item in data.attentionItems" :key="item.id">
              <RouterLink :to="item.to">
                <Shield v-if="item.kind === 'approval'" :size="18" :stroke-width="1.75" aria-hidden="true" />
                <FileCheck2 v-else :size="18" :stroke-width="1.75" aria-hidden="true" />
                <span>{{ item.label }}</span>
              </RouterLink>
            </li>
          </ul>
        </section>

        <div class="af-backend" :data-on="data.backendConnected">
          <span class="af-backend-dot" />
          <span>{{ data.backendConnected ? '本地后端已连接' : '本地后端未连接' }}</span>
          <Info :size="16" :stroke-width="1.75" aria-hidden="true" />
        </div>
      </aside>
    </div>
  </main>
</template>

<style scoped>
.af-overview-page { padding-top: 20px; }
.af-overview {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 384px;
  gap: 32px;
  align-items: start;
}
.af-overview-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
}
.af-breadcrumb {
  margin: 0;
  display: flex;
  gap: 8px;
  color: var(--af-text-secondary);
  font-size: 16px;
}
.af-breadcrumb strong { color: var(--af-text); font-weight: 500; }
.af-goal-composer { padding: 0; }
.af-goal-box {
  border: 1px solid color-mix(in srgb, var(--af-accent) 55%, var(--af-border));
  border-radius: 14px;
  padding: 16px 16px 14px;
  background: var(--af-surface);
  box-shadow:
    0 1px 0 color-mix(in srgb, #fff 70%, transparent),
    0 8px 24px color-mix(in srgb, var(--af-accent) 8%, transparent);
}
.af-goal-composer h1 {
  margin: 0;
  font-size: 36px;
  line-height: 46px;
  font-weight: 700;
}
.af-goal-sub {
  margin: 8px 0 28px;
  color: var(--af-text-secondary);
  font-size: 18px;
  line-height: 28px;
}
.af-goal-composer textarea {
  display: block;
  width: 100%;
  min-height: 96px;
  border: 0;
  border-radius: 0;
  padding: 0;
  resize: vertical;
  background: transparent;
  outline: none;
  line-height: 28px;
}
.af-goal-label {
  display: block;
  margin-bottom: 8px;
  font-size: 14px;
  font-weight: 600;
  color: var(--af-text-secondary);
}
.af-goal-box:focus-within {
  border-color: var(--af-accent-strong);
  box-shadow: 0 0 0 3px color-mix(in srgb,var(--af-accent) 15%,transparent);
}
.af-goal-composer textarea:focus-visible { outline: none; }
.af-composer-bar {
  margin-top: 14px;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding-top: 12px;
  border-top: 1px solid color-mix(in srgb, var(--af-border) 85%, transparent);
}
.af-chip {
  --af-chip-pad-x: 12px;
  height: 40px;
  min-height: 40px;
  padding: 0 var(--af-chip-pad-x);
  border: 1px solid color-mix(in srgb, var(--af-border) 92%, var(--af-text) 8%);
  border-radius: 999px;
  background: color-mix(in srgb, var(--af-surface-subtle, var(--af-surface)) 88%, var(--af-surface));
  box-shadow: inset 0 1px 0 color-mix(in srgb, #fff 55%, transparent);
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--af-text-secondary);
  font-size: 14px;
  font-weight: 500;
  line-height: 1;
  cursor: pointer;
  transition: border-color 140ms ease, background-color 140ms ease, box-shadow 140ms ease, color 140ms ease;
}
.af-chip:hover {
  border-color: color-mix(in srgb, var(--af-accent) 35%, var(--af-border));
  background: color-mix(in srgb, var(--af-selected) 55%, var(--af-surface));
  color: var(--af-text);
}
.af-chip:focus-within {
  border-color: color-mix(in srgb, var(--af-accent-strong, var(--af-accent)) 55%, var(--af-border));
  box-shadow:
    inset 0 1px 0 color-mix(in srgb, #fff 55%, transparent),
    0 0 0 3px color-mix(in srgb, var(--af-accent) 18%, transparent);
  color: var(--af-text);
}
.af-chip :deep(svg),
.af-chip > svg {
  flex-shrink: 0;
  width: 16px;
  height: 16px;
  opacity: 0.78;
}
.af-chip:hover :deep(svg),
.af-chip:hover > svg,
.af-chip:focus-within :deep(svg),
.af-chip:focus-within > svg {
  opacity: 1;
  color: var(--af-accent-ink);
}
.af-harness-avail {
  color: var(--af-text-secondary);
  font-size: 14px;
  font-weight: 500;
  margin-right: auto;
  display: flex;
  align-items: center;
  gap: 8px;
  padding-left: 4px;
  letter-spacing: 0.01em;
}
.af-harness-avail i {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--af-success);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--af-success) 22%, transparent);
}
.af-attached { display: flex; flex-wrap: wrap; gap: 8px; font-size: 13px; color: var(--af-accent-ink); }
.af-attached span { display: flex; align-items: center; gap: 4px; background: var(--af-selected); padding: 4px 8px; border-radius: 6px; }
.af-create {
  height: 40px;
  padding: 0 16px 0 18px;
  border: 0;
  border-radius: 999px;
  background: var(--af-button);
  color: #fff;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.01em;
  box-shadow: 0 1px 2px color-mix(in srgb, #000 16%, transparent);
  transition: transform 140ms ease, filter 140ms ease, box-shadow 140ms ease;
}
.af-create:hover {
  filter: brightness(1.06);
  box-shadow: 0 2px 8px color-mix(in srgb, var(--af-button) 35%, transparent);
}
.af-create:active {
  transform: translateY(1px);
}
.af-create:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--af-accent) 70%, #fff);
  outline-offset: 2px;
}
.af-quick-actions {
  display: grid;
  grid-template-columns: repeat(3,minmax(0,1fr));
  gap: 18px;
  margin: 22px 0 36px;
}
.af-quick {
  height: 62px;
  padding: 0 16px;
  border: 1px solid var(--af-border);
  border-radius: 10px;
  background: var(--af-surface);
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--af-text);
  font-size: 18px;
  justify-content: center;
}
.af-quick svg { width: 26px; height: 26px; color: var(--af-accent-ink); }
h2 { margin: 0 0 16px; font-size: 22px; line-height: 30px; font-weight: 650; }
.af-section-head { display: flex; justify-content: space-between; gap: 12px; align-items: baseline; }
.af-section-head > a { display: flex; align-items: center; gap: 8px; color: var(--af-accent-ink); font-size: 17px; }
.af-continue { list-style: none; margin: 0 0 28px; padding: 0; display: grid; gap: 6px; }
.af-continue li {
  min-height: 94px;
  padding: 16px 20px;
  border: 1px solid var(--af-border);
  border-radius: 10px;
  background: var(--af-surface);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.af-continue-main { display: grid; gap: 6px; min-width: 0; flex: 1; }
.af-continue-main strong { font-size: 20px; font-weight: 650; }
.af-continue-title { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.af-continue-main p { margin: 0; color: var(--af-text-secondary); font-size: 17px; }
.af-task-state { color: var(--af-accent-strong); }
.af-task-roles { display: flex; gap: 8px; align-items: center; color: var(--af-accent-ink); }
.af-task-roles > * { padding: 4px; box-sizing: content-box; background: var(--af-selected); border-radius: 7px; }
.af-task-roles span { width: 22px; text-align: center; font-weight: 650; }
.af-continue :deep(.af-button) { color: var(--af-accent-ink); background: var(--af-selected); }
.af-recent-wrap { border: 1px solid var(--af-border); border-radius: 10px; overflow: auto; }
.af-task-name { display: flex; align-items: center; gap: 10px; white-space: nowrap; font-weight: 550; }
.af-recent a { display: inline-flex; align-items: center; gap: 8px; color: var(--af-accent-ink); white-space: nowrap; min-height: 44px; }
.af-progress {
  display: inline-flex;
  width: fit-content;
  height: 28px;
  padding: 0 10px;
  border-radius: 8px;
  align-items: center;
  font-size: 14px;
  font-weight: 500;
  background: var(--af-selected);
  color: var(--af-accent-ink);
}
.af-progress[data-status='paused'] {
  background: #f1eff4;
  color: var(--af-text-secondary);
}
.af-meta { color: var(--af-text-muted); font-size: 14px; }
.af-recent {
  width: 100%;
  border-collapse: collapse;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
  border-radius: 10px;
  overflow: hidden;
}
.af-recent th, .af-recent td {
  height: 48px;
  padding: 0 20px;
  border-bottom: 1px solid var(--af-border);
  text-align: left;
  font-size: 18px;
}
.af-recent th { color: var(--af-text-secondary); font-weight: 500; }
.af-done {
  display: inline-flex;
  align-items: center;
  height: 28px;
  padding: 0 10px;
  border-radius: 8px;
  background: var(--af-success-bg);
  color: var(--af-success);
  font-size: 14px;
  font-weight: 500;
}
.af-overview-aside {
  display: grid;
  gap: 16px;
  align-content: start;
}
.af-aside-card {
  border: 1px solid var(--af-border);
  border-radius: 10px;
  padding: 20px;
  background: var(--af-surface);
}
.af-aside-card h2 { font-size: 24px; margin-bottom: 20px; }
.af-aside-card h3 {
  margin: 20px 0 12px;
  font-size: 18px;
  color: var(--af-text-secondary);
  font-weight: 500;
}
.af-repo { margin: 0; font-weight: 500; padding: 10px 12px; display: flex; align-items: center; gap: 12px; border: 1px solid var(--af-border); border-radius: 8px; }
.af-repo svg:last-child { margin-left: auto; }
.af-branch {
  margin: 8px 0 0;
  display: flex;
  height: 46px;
  padding: 0 10px;
  border-radius: 8px;
  border: 1px solid var(--af-border);
  align-items: center;
  font-size: 18px;
  color: var(--af-text-secondary);
  gap: 12px;
}
.af-branch svg:last-child { margin-left: auto; }
.af-aside-card ul:not(.af-attention) { gap: 0; }
.af-aside-card ul:not(.af-attention) a { display: flex; gap: 12px; align-items: center; min-height: 52px; padding: 10px 8px; border-top: 1px solid var(--af-border); font-size: 18px; }
.af-aside-card ul { margin: 0; padding: 0; list-style: none; display: grid; gap: 10px; }
.af-attention a {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 48px;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--af-surface-subtle);
  color: var(--af-text);
}
.af-backend {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 48px;
  padding: 0 14px;
  border: 1px solid var(--af-border);
  border-radius: 10px;
  background: var(--af-surface);
  color: var(--af-text-secondary);
  font-size: 14px;
}
.af-backend-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: #c5c1cc;
}
.af-backend[data-on='true'] .af-backend-dot { background: var(--af-success); }
.af-backend[data-on='true'] { color: var(--af-text); }
.af-skeleton { padding: 48px; color: var(--af-text-muted); }
@media (max-width: 1439px) {
  .af-overview { grid-template-columns: minmax(0, 1fr) 320px; }
  .af-goal-composer h1 { font-size: 28px; line-height: 38px; }
}
@media(max-width:1750px) { .af-task-roles { display: none; } .af-continue li { flex-wrap: wrap; } .af-continue-main { flex-basis: calc(100% - 60px); } .af-meta { margin-left: 44px; margin-right: auto; } }
@media(max-width:1100px) { .af-overview { grid-template-columns: 1fr; gap: 28px; } .af-overview-aside { grid-template-columns: 1fr 1fr; } .af-backend { grid-column: 1/-1; } }
@media(max-width:639px) { .af-overview-page { padding-top: 16px; } .af-overview-top { margin-bottom: 20px; } .af-overview-top :deep(.af-button) { font-size: 14px; padding-inline: 10px; } .af-goal-composer h1 { font-size: 28px; line-height: 38px; } .af-goal-sub { font-size: 16px; margin-bottom: 20px; } .af-goal-box { padding: 12px; } .af-goal-composer textarea { min-height: 112px; font-size: 16px; } .af-chip { padding-inline: 9px; font-size: 14px; gap: 4px; } .af-composer-bar { gap: 6px; } .af-harness-avail { width: 100%; font-size: 14px; padding: 8px 0; } .af-create { width: 100%; justify-content: center; } .af-quick-actions { gap: 8px; margin-bottom: 28px; } .af-quick { flex-direction: column; gap: 6px; height: 82px; padding: 8px 4px; font-size: 14px; } .af-quick svg { width: 22px; height: 22px; } .af-section-head > a { font-size: 13px; } .af-section-head h2, h2 { font-size: 21px; } .af-continue li { padding: 14px; gap: 10px; } .af-continue-main strong { font-size: 17px; } .af-continue-main p { font-size: 14px; } .af-progress { font-size: 12px; } .af-continue-main { flex-basis: calc(100% - 40px); } .af-meta { margin-left: 38px; font-size: 12px; } .af-continue :deep(.af-button) { font-size: 14px; padding: 0 12px; } .af-overview-aside { grid-template-columns: 1fr; } .af-recent { min-width: 590px; } .af-recent th, .af-recent td { font-size: 15px; padding-inline: 12px; } }
@media(max-width:639px) {
  .af-chip { height: 44px; min-height: 44px; }
  .af-create { height: 44px; }
  .af-section-head a { display: inline-flex; align-items: center; min-height: 44px; }
}
</style>
