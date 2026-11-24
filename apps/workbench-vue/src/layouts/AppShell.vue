<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import GlobalHeader from './GlobalHeader.vue'
import ManagementSidebar from './ManagementSidebar.vue'
import TaskSidebar from './TaskSidebar.vue'
import KnowledgeTree from './KnowledgeTree.vue'
import AfDialog from '@/components/af/AfDialog.vue'
import { useFeedbackStore } from '@/stores/feedback'
import { X } from 'lucide-vue-next'
import { useSessionStore } from '@/stores/session'
import { shouldUseReferenceFromUrl } from '@/app/runtime'

const route = useRoute()
const menuOpen = ref(false)
const feedback = useFeedbackStore()
const session = useSessionStore()
const isReference = shouldUseReferenceFromUrl(window.location.search)
watch(() => route.fullPath, () => { menuOpen.value = false })
const shell = computed(() => (route.meta.shell as string | undefined) ?? 'management')
const showManagement = computed(() =>
  shell.value === 'management'
  || shell.value === 'knowledgeDetail'
  || shell.value === 'taskManage',
)
const showTask = computed(() =>
  shell.value === 'task'
  || shell.value === 'execution'
  || shell.value === 'taskManage',
)
</script>

<template>
  <div class="af-shell" :data-shell="shell">
    <a class="af-skip-link" href="#main-content">跳到主要内容</a>
    <GlobalHeader :menu-open="menuOpen" @menu="menuOpen = true" />
    <div v-if="!isReference && !session.connected" class="af-connection-alert" role="alert">本机服务未连接：{{ session.error || '请检查服务连接' }} <RouterLink to="/settings/connection">前往连接设置</RouterLink></div>
    <div id="main-content" class="af-body" tabindex="-1">
      <ManagementSidebar v-if="showManagement" />
      <TaskSidebar v-if="showTask" />
      <KnowledgeTree v-if="shell === 'knowledgeDetail'" />
      <RouterView />
    </div>
    <AfDialog v-model:open="menuOpen" title="工作区导航" description="选择页面或继续最近的任务。">
      <div class="af-mobile-navigation">
        <ManagementSidebar />
        <TaskSidebar v-if="showTask" />
        <KnowledgeTree v-if="shell === 'knowledgeDetail'" />
      </div>
    </AfDialog>
    <div v-if="feedback.message" class="af-toast" :data-tone="feedback.tone" role="status" aria-live="polite">
      <span>{{ feedback.message }}</span>
      <button type="button" aria-label="关闭通知" @click="feedback.message = ''"><X :size="18" /></button>
    </div>
  </div>
</template>

<style scoped>
.af-connection-alert { position:fixed; top:var(--af-header-height); left:0; right:0; z-index:80; padding:10px 24px; background:var(--af-danger-bg, #fff0f0); color:var(--af-danger); font-size:14px; }
.af-connection-alert a { text-decoration:underline; margin-left:12px; }
</style>
