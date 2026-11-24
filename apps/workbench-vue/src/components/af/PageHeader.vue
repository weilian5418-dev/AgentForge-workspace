<script setup lang="ts">
defineProps<{ title: string; description?: string }>()
</script>

<template>
  <header class="af-page-header" data-region="page-header">
    <div>
      <h1>{{ title }}</h1>
      <p v-if="description">{{ description }}</p>
    </div>
    <div class="af-page-header-actions"><slot name="actions" /></div>
  </header>
</template>

<style scoped>
.af-page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 24px;
}
.af-page-header h1 {
  margin: 0;
  font-size: 36px;
  line-height: 46px;
  font-weight: 700;
}
.af-page-header p {
  margin: 8px 0 0;
  font-size: 18px;
  line-height: 28px;
  color: var(--af-text-secondary);
}
.af-page-header-actions { display: flex; gap: 8px; flex-shrink: 0; }
</style>
