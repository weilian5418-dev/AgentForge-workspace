<script setup lang="ts">
import { Check, LoaderCircle, Circle } from 'lucide-vue-next'

defineProps<{
  steps: readonly { id: string; label: string; status: string }[]
}>()

function iconFor(status: string) {
  if (status === '完成' || status === '已完成') return Check
  if (status === '执行中') return LoaderCircle
  return Circle
}
</script>

<template>
  <section class="af-plan" aria-label="执行计划">
    <h3>执行计划</h3>
    <ol>
      <li v-for="(step, index) in steps" :key="step.id" :data-status="step.status">
        <span class="af-step-icon" aria-hidden="true">
          <component :is="iconFor(step.status)" :size="16" :stroke-width="1.75" />
        </span>
        <div>
          <strong>{{ index + 1 }}. {{ step.label }}</strong>
          <span>{{ step.status }}</span>
        </div>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.af-plan {
  border: 1px solid var(--af-border);
  border-radius: 10px;
  padding: 10px 12px;
  background: var(--af-surface);
}
h3 { margin: 0 0 6px; font-size: 15px; font-weight: 650; }
ol { margin: 0; padding: 0; list-style: none; display: grid; gap: 6px; }
li {
  display: grid;
  grid-template-columns: 24px 1fr;
  gap: 8px;
  align-items: start;
}
.af-step-icon {
  width: 24px;
  height: 24px;
  border-radius: 999px;
  display: grid;
  place-items: center;
  background: #f1eff4;
  color: var(--af-text-secondary);
}
li[data-status='完成'] .af-step-icon,
li[data-status='已完成'] .af-step-icon {
  background: var(--af-success-bg);
  color: var(--af-success);
}
li[data-status='执行中'] .af-step-icon {
  background: var(--af-selected);
  color: var(--af-accent-ink);
}
li[data-status='执行中'] .af-step-icon :deep(svg) {
  animation: af-spin 1s linear infinite;
}
@keyframes af-spin {
  to { transform: rotate(360deg); }
}
@media (prefers-reduced-motion: reduce) {
  li[data-status='执行中'] .af-step-icon :deep(svg) { animation: none; }
}
strong { display: block; font-size: 14px; font-weight: 500; line-height: 20px; }
span { color: var(--af-text-secondary); font-size: 12px; line-height: 16px; }
</style>
