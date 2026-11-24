<script setup lang="ts">
import type { ApprovalViewModel, MessageViewModel } from '@/domain/view-models'
import ExecutionPlan from './ExecutionPlan.vue'
import ApprovalCard from '@/features/approvals/ApprovalCard.vue'
import { ref, watch, nextTick } from 'vue'

const props = defineProps<{
  messages: readonly MessageViewModel[]
  approval?: ApprovalViewModel | null
  planSteps?: readonly { id: string; label: string; status: string }[]
  fileHint?: { name: string; additions: number; deletions: number } | null
}>()
defineEmits<{ detail: []; allow: [] }>()
const host = ref<HTMLElement | null>(null)
watch(() => props.messages.length, async () => { await nextTick(); if (host.value) host.value.scrollTop = host.value.scrollHeight })
</script>

<template>
  <div ref="host" class="af-messages" data-region="conversation" role="log" aria-label="任务对话">
    <template v-for="message in messages" :key="message.id">
      <article class="af-message" :data-kind="message.kind">
        <header v-if="message.name">{{ message.name }}</header>
        <p>{{ message.body }}</p>
        <div
          v-if="message.kind === 'agent' && fileHint"
          class="af-file-hint"
        >
          <span>{{ fileHint.name }}</span>
          <em>+{{ fileHint.additions }} -{{ fileHint.deletions }}</em>
        </div>
      </article>
      <ExecutionPlan
        v-if="message.kind === 'coordinator' && planSteps?.length"
        :steps="planSteps"
      />
    </template>
    <ApprovalCard v-if="approval" :approval="approval" @detail="$emit('detail')" @allow="$emit('allow')" />
  </div>
</template>

<style scoped>
.af-messages {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px 24px;
  overflow: auto;
  min-height: 0;
  min-width: 0;
  height: 100%;
}
.af-message {
  max-width: 480px;
  padding: 10px 14px;
  border-radius: 10px;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
}
.af-message[data-kind='user'] {
  align-self: flex-end;
  background: var(--af-selected);
  max-width: 420px;
}
.af-message[data-kind='handoff'] {
  align-self: center;
  max-width: 100%;
  background: transparent;
  border-style: dashed;
  color: var(--af-text-secondary);
  font-size: 14px;
  text-align: center;
}
.af-message[data-kind='agent'] {
  max-width: 520px;
  border-color: color-mix(in srgb, var(--af-accent) 55%, var(--af-border));
  background: #fdfcff;
}
.af-message[data-kind='coordinator'] {
  max-width: 520px;
  border-color: transparent;
  background: transparent;
  padding-left: 0;
  padding-right: 0;
}
.af-message header {
  margin-bottom: 6px;
  font-size: 14px;
  font-weight: 600;
  color: var(--af-accent-ink);
}
.af-message p { margin: 0; white-space: pre-wrap; font-size: 18px; line-height: 28px; overflow-wrap: anywhere; }
.af-file-hint {
  margin-top: 10px;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  height: 32px;
  padding: 0 10px;
  border-radius: 8px;
  background: var(--af-surface-subtle);
  border: 1px solid var(--af-border);
  font-size: 14px;
}
.af-file-hint em {
  font-style: normal;
  color: var(--af-success);
}
</style>
