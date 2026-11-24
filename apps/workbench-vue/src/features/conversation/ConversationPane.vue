<script setup lang="ts">
import { computed, ref } from 'vue'
import { Settings } from 'lucide-vue-next'
import type { ApprovalViewModel, MessageViewModel, TaskViewModel } from '@/domain/view-models'
import MessageList from './MessageList.vue'
import MessageComposer from './MessageComposer.vue'
import { useRouter } from 'vue-router'
const router = useRouter()

const props = defineProps<{
  task: TaskViewModel
  pending?: boolean
  sentVersion?: number
}>()

const emit = defineEmits<{ send: [text: string, recipient?: string]; allow: [] }>()
const draft = ref('')

const previewSteps = [
  { id: 'plan', label: '整理接口与数据范围', status: '完成' },
  { id: 'build', label: '前后端并行实现', status: '执行中' },
  { id: 'review', label: '审查与回归验证', status: '等待' },
]
const planSteps = computed(() => props.task.live ? [] : previewSteps)

const fileHint = computed(() => {
  const file = props.task.files[0]
  if (!file) return null
  return { name: file.name, additions: file.additions, deletions: file.deletions }
})

function onSend(text: string, recipient?: string) {
  emit('send', text, recipient)
  if (!props.task.live) draft.value = ''
}
import { watch } from 'vue'
watch(() => props.sentVersion, () => { draft.value = '' })

const approval = computed(() => props.task.approval as ApprovalViewModel | null)
const messages = computed(() => props.task.messages as readonly MessageViewModel[])
</script>

<template>
  <section class="af-conversation-pane af-conversation af-pane">
    <header class="af-conversation-header">
      <div>
        <p class="af-kicker">对话</p>
        <h2>{{ task.goal }}</h2>
        <div class="af-chips">
          <span>{{ task.live ? task.project.name : '订单模块' }}</span>
          <span v-if="task.project.branch" class="af-mono">{{ task.project.branch }}</span>
        </div>
      </div>
      <button type="button" class="af-settings" aria-label="任务设置" @click="router.push('/settings/connection')">
        <Settings :size="20" :stroke-width="1.75" />
      </button>
    </header>
    <MessageList
      :messages="messages"
      :approval="approval"
      :plan-steps="planSteps"
      :file-hint="fileHint"
      @detail="router.push('/inbox')"
      @allow="emit('allow')"
    />
    <div class="af-composer-wrap">
      <MessageComposer v-model="draft" :pending="pending" :recipients="task.live?.recipients" :allow-attachments="!task.live" @send="onSend" />
    </div>
  </section>
</template>

<style scoped>
.af-conversation-pane {
  flex: 1;
  height: 100%;
  min-height: 0;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  background: var(--af-surface);
}
.af-conversation-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 24px 10px;
  border-bottom: 1px solid var(--af-border);
}
.af-kicker {
  margin: 0 0 2px;
  font-size: 22px;
  color: var(--af-text);
  font-weight: 650;
}
.af-conversation-header h2 {
  margin: 0;
  font-size: 20px;
  line-height: 28px;
  font-weight: 650;
}
.af-chips {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.af-chips span {
  height: 28px;
  padding: 0 10px;
  border-radius: 8px;
  border: 1px solid var(--af-border);
  display: inline-flex;
  align-items: center;
  font-size: 13px;
  color: var(--af-text-secondary);
  background: var(--af-surface);
}
.af-settings {
  width: 40px;
  height: 40px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--af-text-secondary);
  display: grid;
  place-items: center;
}
.af-composer-wrap { padding: 8px 24px 12px; }
@media(max-width:639px) { .af-conversation-header { padding: 16px; } .af-composer-wrap { padding: 8px 16px 16px; } .af-chips span { font-size: 12px; } }
</style>
