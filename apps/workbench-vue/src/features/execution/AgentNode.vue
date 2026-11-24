<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core'
import AfStatusBadge from '@/components/af/AfStatusBadge.vue'
import type { UiStatus } from '@/domain/view-models'
import { CalendarDays, CodeXml, Layers, Search, Folder } from 'lucide-vue-next'

defineProps<{
  data: { role: string; harness: string; status: UiStatus; workspace?: string }
  selected?: boolean
}>()
</script>

<template>
  <div
    class="af-agent-node"
    :data-selected="selected"
    :data-status="data.status"
  >
    <Handle type="target" :position="Position.Left" class="af-handle" />
    <div class="af-agent-main">
      <component :is="data.role === 'Planner' ? CalendarDays : data.role === 'Frontend' ? CodeXml : data.role === 'Backend' ? Layers : Search" :size="24" class="af-role-icon" />
      <div class="af-agent-copy">
        <strong>{{ data.role }}</strong>
        <span>{{ data.harness }}</span>
      </div>
      <AfStatusBadge :status="data.status" />
    </div>
    <p v-if="data.workspace" class="af-mono"><Folder :size="16" />{{ data.workspace }}</p>
    <Handle type="source" :position="Position.Right" class="af-handle" />
  </div>
</template>

<style scoped>
.af-agent-node {
  width: 280px;
  min-height: 84px;
  padding: 12px 14px;
  border: 1.5px solid var(--af-border);
  border-radius: 8px;
  background: var(--af-surface);
}
.af-agent-node[data-status='completed'] { border-color: var(--af-success); }
.af-agent-node[data-status='running'] {
  border-color: var(--af-accent-strong);
  background: #fbf9fe;
}
.af-agent-node[data-status='waiting'] { border-color: #d9d4e0; }
.af-agent-node[data-selected='true'] {
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--af-accent) 35%, transparent);
}
.af-agent-main {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}
.af-agent-copy { min-width: 0; flex: 1; }
.af-role-icon { margin-top: 2px; }
strong { display: block; }
.af-agent-copy > span { display:block; }
strong { font-size: 18px; line-height: 24px; font-weight: 650; }
span, p { font-size: 14px; color: var(--af-text-secondary); line-height: 20px; }
p { margin: 8px 0 0 34px; display: flex; align-items: center; gap: 8px; }
.af-handle { opacity: 0; pointer-events: none; }
</style>
