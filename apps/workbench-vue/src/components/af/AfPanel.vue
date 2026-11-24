<script setup lang="ts">
defineProps<{ title?: string }>()
</script>

<template>
  <section class="af-panel">
    <header v-if="title || $slots.actions" class="af-panel-header">
      <h2 v-if="title">{{ title }}</h2>
      <div class="af-panel-actions"><slot name="actions" /></div>
    </header>
    <div class="af-panel-body"><slot /></div>
  </section>
</template>

<style scoped>
.af-panel {
  border: 1px solid var(--af-border);
  border-radius: 10px;
  background: var(--af-surface);
}
.af-panel-header {
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 20px;
  border-bottom: 1px solid var(--af-border);
}
.af-panel-header h2 {
  margin: 0;
  font-size: 22px;
  line-height: 30px;
  font-weight: 650;
}
.af-panel-body { padding: 20px; }
.af-panel-actions { display: flex; gap: 8px; }
</style>
