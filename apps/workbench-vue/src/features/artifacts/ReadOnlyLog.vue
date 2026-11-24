<script setup lang="ts">
withDefaults(defineProps<{
  lines?: readonly string[]
  reason?: string
}>(), {
  lines: () => [
    '[14:31:02] Frontend started export toolbar',
    '[14:31:18] Backend drafted /api/orders/export',
    '[14:32:01] Approval requested: pnpm add xlsx',
  ],
  reason: '运行输出',
})
</script>

<template>
  <section class="af-log" aria-label="运行日志">
    <p class="af-log-reason">{{ reason }}</p>
    <pre class="af-mono">{{ lines.join('\n') }}</pre>
  </section>
</template>

<style scoped>
.af-log {
  height: 100%;
  min-height: 0;
  border-radius: 8px;
  background: var(--af-code);
  color: #e8e8ea;
  padding: 14px;
  overflow: auto;
}
.af-log-reason {
  margin: 0 0 12px;
  color: #cfc8db;
  font-size: 14px;
}
pre {
  margin: 0;
  white-space: pre-wrap;
  font-size: 14px;
  line-height: 22px;
}
</style>
