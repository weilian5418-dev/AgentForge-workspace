<script setup lang="ts">
defineProps<{ title: string; description?: string }>()
</script>

<template>
  <div class="af-empty">
    <h3>{{ title }}</h3>
    <p v-if="description">{{ description }}</p>
    <div class="af-empty-actions"><slot /></div>
  </div>
</template>

<style scoped>
.af-empty {
  padding: 48px 24px;
  text-align: center;
  color: var(--af-text-secondary);
}
.af-empty h3 { margin: 0 0 8px; font-size: 22px; color: var(--af-text); }
.af-empty p { margin: 0; font-size: 16px; }
.af-empty-actions { margin-top: 16px; display: flex; justify-content: center; gap: 8px; }
</style>
