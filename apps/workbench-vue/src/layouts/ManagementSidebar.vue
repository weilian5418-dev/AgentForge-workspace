<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  LayoutDashboard,
  ListTodo,
  Inbox,
  Bot,
  BookOpen,
  Puzzle,
  Settings,
  FolderGit2,
  ChevronDown,
} from 'lucide-vue-next'

import { shouldUseReferenceFromUrl } from '@/app/runtime'
import { useLibraryStore } from '@/stores/library'
const route = useRoute()
const router = useRouter()
const local = useLibraryStore()

const primary = [
  { label: '概览', to: '/workspace', icon: LayoutDashboard },
  { label: '全部任务', to: '/tasks', icon: ListTodo },
  { label: '收件箱', to: '/inbox', icon: Inbox },
] as const

const secondary = [
  { label: '执行者', to: '/executors', icon: Bot },
  { label: '知识库', to: '/knowledge', icon: BookOpen },
  { label: '扩展', to: '/extensions', icon: Puzzle },
] as const

function active(to: string) {
  if (to === '/workspace') return route.path === '/workspace'
  if (to === '/tasks') {
    return route.path === '/tasks' || route.path === '/tasks/new'
  }
  return route.path === to || route.path.startsWith(`${to}/`)
}

const attention = computed(() => (shouldUseReferenceFromUrl(window.location.search) ? Math.max(0,2-Object.keys(local.decisions).length) : 0))
</script>

<template>
  <aside class="af-management-sidebar" data-region="sidebar" aria-label="管理导航">
    <div class="af-side-top">
      <p class="af-side-label">管理</p>
      <nav>
        <RouterLink
          v-for="item in primary"
          :key="item.to"
          :to="item.to"
          class="af-nav-item"
          :title="item.label"
          :aria-label="item.label"
          :data-active="active(item.to)"
        >
          <component :is="item.icon" :size="24" :stroke-width="1.75" aria-hidden="true" />
          <span>{{ item.label }}</span>
          <em v-if="item.to === '/inbox' && attention">{{ attention }}</em>
        </RouterLink>
      </nav>
      <nav class="af-side-secondary">
        <RouterLink
          v-for="item in secondary"
          :key="item.to"
          :to="item.to"
          class="af-nav-item"
          :title="item.label"
          :aria-label="item.label"
          :data-active="active(item.to)"
        >
          <component :is="item.icon" :size="24" :stroke-width="1.75" aria-hidden="true" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>
    </div>
    <div class="af-side-bottom">
      <button type="button" class="af-workspace-chip" aria-label="本地工作区设置" @click="router.push('/settings/connection')">
        <FolderGit2 :size="20" :stroke-width="1.75" aria-hidden="true" />
        <span>本地工作区</span>
        <ChevronDown :size="16" :stroke-width="1.75" aria-hidden="true" />
      </button>
      <RouterLink
        to="/settings/connection"
        class="af-nav-item af-settings"
        :data-active="active('/settings/connection')"
      >
        <Settings :size="24" :stroke-width="1.75" aria-hidden="true" />
        <span>设置</span>
      </RouterLink>
    </div>
  </aside>
</template>

<style scoped>
.af-management-sidebar {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}
.af-side-top { padding: 16px 12px 8px; }
.af-side-label {
  margin: 0 0 8px 16px;
  font-size: 14px;
  color: var(--af-text-muted);
  font-weight: 500;
}
nav { display: flex; flex-direction: column; gap: 4px; }
.af-side-secondary { margin-top: 16px; }
.af-side-bottom {
  padding: 12px;
  border-top: 1px solid var(--af-border);
  display: grid;
  gap: 4px;
}
.af-workspace-chip {
  height: 48px;
  padding: 0 14px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  display: flex;
  align-items: center;
  gap: 12px;
  color: var(--af-text-secondary);
  font-size: 16px;
}
.af-workspace-chip span:first-of-type { flex: 1; text-align: left; }
.af-nav-item {
  position: relative;
  height: 56px;
  padding: 0 16px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  gap: 14px;
  color: var(--af-text-secondary);
  font-size: 18px;
}
.af-nav-item[data-active='true'] {
  background: var(--af-selected);
  color: var(--af-accent-ink);
}
.af-nav-item[data-active='true']::before {
  content: '';
  position: absolute;
  left: 0;
  top: 12px;
  bottom: 12px;
  width: 4px;
  border-radius: 0 4px 4px 0;
  background: var(--af-accent-strong);
}
.af-nav-item em {
  margin-left: auto;
  font-style: normal;
  min-width: 24px;
  height: 24px;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--af-attention-bg);
  color: var(--af-attention);
  font-size: 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
</style>
