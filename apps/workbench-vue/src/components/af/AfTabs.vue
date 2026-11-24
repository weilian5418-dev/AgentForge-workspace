<script setup lang="ts">
import { ref } from 'vue'
const model = defineModel<string>({ required: true })
const props = defineProps<{ tabs: readonly { id: string; label: string }[] }>()
const host = ref<HTMLElement | null>(null)
function move(event: KeyboardEvent) {
  if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return
  event.preventDefault()
  const current = props.tabs.findIndex(tab => tab.id === model.value)
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? props.tabs.length - 1 : (current + (event.key === 'ArrowRight' ? 1 : -1) + props.tabs.length) % props.tabs.length
  if (props.tabs[next]) model.value = props.tabs[next].id
  host.value?.querySelectorAll<HTMLButtonElement>('button')[next]?.focus()
}
</script>

<template>
  <div ref="host" class="af-tabs" role="tablist" aria-label="页面内容" @keydown="move">
    <button
      v-for="tab in tabs"
      :key="tab.id"
      type="button"
      role="tab"
      class="af-tab"
      :aria-selected="model === tab.id"
      :tabindex="model === tab.id ? 0 : -1"
      :data-active="model === tab.id"
      @click="model = tab.id"
    >
      {{ tab.label }}
    </button>
  </div>
</template>

<style scoped>
.af-tabs {
  display: flex;
  height: 44px;
  gap: 4px;
  border-bottom: 1px solid var(--af-border);
}
.af-tab {
  height: 44px;
  padding: 0 20px;
  border: 0;
  background: transparent;
  color: var(--af-text-secondary);
  font-size: 18px;
  font-weight: 500;
  border-bottom: 2px solid transparent;
}
.af-tab[data-active='true'] {
  color: var(--af-accent-ink);
  background: var(--af-selected);
  border-bottom-color: var(--af-accent-strong);
}
</style>
