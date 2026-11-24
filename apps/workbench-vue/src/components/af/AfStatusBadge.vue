<script setup lang="ts">
import { Check, Clock3, Pause, CircleAlert, LoaderCircle } from 'lucide-vue-next'
import type { UiStatus } from '@/domain/view-models'

const props = defineProps<{ status: UiStatus; label?: string }>()

const styles = {
  running: { text: '执行中', icon: LoaderCircle, color: '#6b5395', background: '#f0ebfa' },
  completed: { text: '已完成', icon: Check, color: '#238636', background: '#e8f7eb' },
  waiting: { text: '等待依赖', icon: Clock3, color: '#625e6d', background: '#f1eff4' },
  approval: { text: '待批准', icon: CircleAlert, color: '#9a570d', background: '#fff5e5' },
  paused: { text: '已暂停', icon: Pause, color: '#625e6d', background: '#f1eff4' },
  failed: { text: '执行失败', icon: CircleAlert, color: '#c24145', background: '#fff0f0' },
  unknown: { text: '状态未知', icon: CircleAlert, color: '#625e6d', background: '#f1eff4' },
} as const
</script>

<template>
  <span
    class="af-status"
    :style="{ color: styles[props.status].color, background: styles[props.status].background }"
  >
    <component :is="styles[props.status].icon" :size="16" :stroke-width="1.75" aria-hidden="true" />
    {{ label ?? styles[props.status].text }}
  </span>
</template>

<style scoped>
.af-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 10px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  white-space: nowrap;
}
</style>
