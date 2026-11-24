<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Search, Bell, ChevronDown, Menu, Layers, Users, BookOpen, Folder, Command, ArrowUpRight } from 'lucide-vue-next'
import { onKeyStroke } from '@vueuse/core'
import BrandMark from '@/components/af/BrandMark.vue'
import AfDialog from '@/components/af/AfDialog.vue'
import { useSessionStore } from '@/stores/session'
import { useCurrentProject } from '@/queries/current-project'
import { project as referenceProject } from '@/fixtures/shared'
import { shouldUseReferenceFromUrl } from '@/app/runtime'
import { useWorkspaceRepository } from '@/app/runtime'
import { useQuery, useQueryClient } from '@tanstack/vue-query'

defineProps<{ menuOpen?: boolean }>()
defineEmits<{ menu: [] }>()
const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const repository = useWorkspaceRepository()
const queryClient = useQueryClient()
const { data: projects } = useQuery({ queryKey: ['projects'], queryFn: ({ signal }) => repository.loadProjects?.(signal) ?? Promise.resolve([]), enabled: !shouldUseReferenceFromUrl(window.location.search) })
async function selectProject(id: string) {
  session.projectId = id
  projectOpen.value = false
  await queryClient.invalidateQueries()
  void router.push('/workspace')
}
const currentProject = useCurrentProject()
const isReference = shouldUseReferenceFromUrl(window.location.search)
const searchOpen = ref(false)
const projectOpen = ref(false)
const query = ref('')
const projectName = computed(() => isReference ? referenceProject.name : currentProject.value?.name ?? '选择项目')
const links = [
  { label: '工作台', to: '/workspace', icon: Layers },
  { label: '执行者', to: '/executors', icon: Users },
  { label: '知识库', to: '/knowledge', icon: BookOpen },
]
const commands = [
  { label: '工作台概览', to: '/workspace', hint: '查看项目与最近任务' },
  { label: '新建任务', to: '/tasks/new', hint: '描述目标并分配角色' },
  { label: '全部任务', to: '/tasks', hint: '搜索与筛选任务' },
  { label: '收件箱', to: '/inbox', hint: '处理审批与交付' },
  { label: '执行者', to: '/executors', hint: 'Harness 与角色配置' },
  { label: '知识库', to: '/knowledge', hint: '文档与参考资料' },
  { label: '扩展', to: '/extensions', hint: '工具服务与协作模板' },
  { label: '设置', to: '/settings/connection', hint: '服务连接与外观' },
]
const results = computed(() => commands.filter(item => `${item.label}${item.hint}`.includes(query.value.trim())))
function active(to: string) {
  return to === '/workspace' ? route.path === to || route.path.startsWith('/tasks') || route.path === '/inbox'
    : route.path.startsWith(to)
}
watch(() => route.fullPath, () => { searchOpen.value = false; projectOpen.value = false })
onKeyStroke('k', event => { if (event.ctrlKey || event.metaKey) { event.preventDefault(); searchOpen.value = !searchOpen.value } })
</script>

<template>
  <header class="af-header" data-region="header">
    <button type="button" class="af-mobile-menu af-icon" aria-label="打开导航菜单" :aria-expanded="Boolean(menuOpen)" @click="$emit('menu')"><Menu :size="22" /></button>
    <RouterLink to="/workspace" class="af-home" aria-label="AgentForge 工作台"><BrandMark /></RouterLink>
    <button type="button" class="af-project" :title="projectName" @click="projectOpen = true"><Folder :size="22" /><span>{{ projectName }}</span><ChevronDown :size="16" /></button>
    <nav class="af-nav" aria-label="全局导航">
      <RouterLink v-for="item in links" :key="item.to" :to="item.to" class="af-nav-link" :data-active="active(item.to)"><component :is="item.icon" :size="23" aria-hidden="true" /><span>{{ item.label }}</span></RouterLink>
    </nav>
    <button type="button" class="af-search" aria-label="搜索任务或执行命令" @click="searchOpen = true"><Search :size="20" /><span>搜索任务或执行命令…</span><kbd>⌘ K</kbd></button>
    <div class="af-header-right">
      <button type="button" class="af-icon af-bell" aria-label="打开收件箱" @click="router.push('/inbox')"><Bell :size="22" /><em v-if="isReference">2</em></button>
      <span class="af-harness" :title="isReference ? '前端预览 · 演示连接状态' : session.endpointLabel"><span class="af-dot" :data-on="isReference || session.connected" /><span class="af-connection-text">{{ isReference ? '3 个 Harness 已连接' : session.connected ? '工作区已连接' : '尚未连接' }}</span></span>
      <button type="button" class="af-user" aria-label="打开个人设置" @click="router.push('/settings/connection')"><img src="/brand/avatar-neutral.svg" width="36" height="36" alt="" /><ChevronDown :size="16" /></button>
    </div>
  </header>
  <AfDialog v-model:open="searchOpen" title="搜索与命令" description="输入页面名称，快速前往工作区。">
    <label class="af-command-search"><Search :size="20" /><input v-model="query" placeholder="搜索页面、任务或设置…" aria-label="搜索页面" autofocus /></label>
    <div class="af-command-results">
      <RouterLink v-for="item in results" :key="item.to" :to="item.to"><Command :size="18" /><span><strong>{{ item.label }}</strong><small>{{ item.hint }}</small></span><ArrowUpRight :size="18" /></RouterLink>
      <p v-if="!results.length" class="af-empty-inline">未找到相关页面，请换一个关键词。</p>
    </div>
  </AfDialog>
  <AfDialog v-model:open="projectOpen" title="选择项目" description="当前工作区中的项目。">
    <div v-if="!isReference" class="af-command-results"><button v-for="project in projects" :key="project.id" class="af-project-option" type="button" @click="selectProject(project.id)"><Folder :size="22" /><span><strong>{{ project.name }}</strong><small>{{ project.repository }} · {{ project.grantStatus === 'active' ? '已授权' : '授权已撤销' }}</small></span></button><p v-if="!projects?.length">暂无项目，请先通过 AgentForge 注册并授权项目目录。</p></div>
    <div v-else class="af-command-results"><RouterLink to="/workspace"><Folder :size="22" /><span><strong>{{ projectName }}</strong><small>agentforge-shop · 本地工作区</small></span><ArrowUpRight :size="20" /></RouterLink></div>
  </AfDialog>
</template>

<style scoped>
.af-header { display: grid; grid-template-columns: 260px 220px minmax(340px,1fr) minmax(230px,400px) auto; align-items: center; gap: 20px; height: var(--af-header-height); padding: 0 24px; border-bottom: 1px solid var(--af-border); background: var(--af-surface); min-width: 0; }
.af-home { min-width: 0; }
.af-project { height: 44px; border: 0; border-left: 1px solid var(--af-border); padding: 0 14px; background: transparent; display: flex; align-items: center; gap: 12px; min-width: 0; color: var(--af-text); }
.af-project span { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; text-align: left; }
.af-nav { display: flex; gap: 12px; align-items: center; min-width: 0; }
.af-nav-link { flex-shrink: 0; height: 46px; padding: 0 18px; border-radius: 9px; display: inline-flex; align-items: center; gap: 10px; white-space: nowrap; color: var(--af-text-secondary); font-weight: 550; }
.af-nav-link[data-active='true'] { background: var(--af-selected); color: var(--af-accent-ink); box-shadow: inset 0 -2px var(--af-accent-strong); }
.af-search { display: flex; align-items: center; gap: 10px; height: 44px; padding: 0 14px; border: 1px solid var(--af-border); border-radius: 9px; background: var(--af-surface); color: var(--af-text-secondary); min-width: 0; text-align: left; }
.af-search span { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.af-search kbd { font: 13px 'AF Mono',monospace; white-space: nowrap; background: var(--af-surface-subtle); padding: 3px 6px; border-radius: 4px; }
.af-header-right { display: flex; align-items: center; justify-content: flex-end; gap: 18px; min-width: 0; }
.af-harness { display: inline-flex; align-items: center; gap: 10px; font-size: 16px; white-space: nowrap; }
.af-dot { width: 10px; height: 10px; flex-shrink: 0; border-radius: 50%; background: var(--af-text-muted); }
.af-dot[data-on='true'] { background: var(--af-success); }
.af-icon { flex-shrink: 0; width: 44px; height: 44px; display: grid; place-items: center; border: 0; border-radius: 8px; background: transparent; color: var(--af-text-secondary); }
.af-bell { position: relative; }
.af-bell em { position: absolute; top: 0; right: 0; min-width: 22px; height: 22px; border-radius: 50%; background: var(--af-selected); color: var(--af-accent-ink); font-size: 13px; font-style: normal; }
.af-user { display: flex; align-items: center; gap: 8px; border: 0; padding: 4px; background: transparent; color: var(--af-text-secondary); }
.af-user img { border-radius: 50%; }
.af-mobile-menu { display: none; }
.af-command-search { display: flex; align-items: center; gap: 12px; border: 1px solid var(--af-border); padding: 10px 14px; border-radius: 8px; }
.af-command-search input { min-width: 0; width: 100%; border: 0; outline: 0; background: transparent; color: var(--af-text); }
.af-command-results { display: grid; gap: 4px; margin-top: 12px; }
.af-command-results a { display: flex; align-items: center; gap: 14px; min-height: 68px; padding: 12px; border-radius: 8px; }
.af-command-results a:hover { background: var(--af-selected); }
.af-command-results a > span { flex: 1; }
.af-project-option { display:flex; align-items:center; gap:12px; text-align:left; border:1px solid var(--af-border); border-radius:8px; padding:12px; background:var(--af-surface); color:var(--af-text); }
.af-command-results strong, .af-command-results small { display: block; }
.af-command-results small { font-size: 13px; color: var(--af-text-secondary); margin-top: 4px; }
@media(max-width:1750px) { .af-header { grid-template-columns: 240px 180px minmax(310px,1fr) minmax(180px,300px) auto; gap: 12px; } .af-connection-text { display: none; } .af-nav-link { padding-inline: 14px; } }
@media(min-width:1280px) and (max-width:1439px) { .af-header { grid-template-columns:240px 150px minmax(300px,1fr) 44px auto; gap:10px; padding-inline:16px; } .af-search { padding:0; justify-content:center; } .af-search span,.af-search kbd { display:none; } .af-nav-link { padding-inline:10px; } }
@media(max-width:1279px) { .af-header { grid-template-columns: 224px 160px 1fr 44px auto; gap: 10px; padding-inline: 16px; } .af-search { padding: 0; justify-content: center; } .af-search span, .af-search kbd { display: none; } .af-nav-link { padding-inline: 10px; font-size: 16px; gap: 6px; } .af-harness { display: none; } }
@media(max-width:1023px) { .af-header { grid-template-columns: 44px minmax(0,1fr) 44px auto; gap: 8px; } .af-mobile-menu { display: grid; } .af-project, .af-nav { display: none; } .af-header-right { gap: 8px; } .af-home :deep(.af-brand strong) { font-size: 26px; } .af-home :deep(.af-brand img) { width: 30px; height: 30px; } }
@media(max-width:479px) { .af-header { gap: 4px; padding-inline: 8px; } .af-home :deep(.af-brand) { gap: 7px; } .af-home :deep(.af-brand strong) { font-size: 21px; } .af-user { display: none; } }
.af-home { display:flex; align-items:center; min-height:44px; }
@media(min-width:1024px) and (max-width:1100px) { .af-header { grid-template-columns:44px minmax(0,1fr) 44px auto; gap:12px; }.af-mobile-menu { display:grid; }.af-header .af-project,.af-header .af-nav { display:none; } }
</style>
