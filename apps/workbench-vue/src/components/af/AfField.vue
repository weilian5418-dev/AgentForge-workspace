<script setup lang="ts">
defineProps<{ label: string; hint?: string; htmlFor?: string }>()
</script>

<template>
  <label class="af-field" :for="htmlFor">
    <span class="af-field-label">{{ label }}</span>
    <slot />
    <span v-if="hint" class="af-field-hint">{{ hint }}</span>
  </label>
</template>

<style scoped>
.af-field { display: flex; flex-direction: column; gap: 8px; }
.af-field-label { font-size: 16px; font-weight: 500; color: var(--af-text); }
.af-field-hint { font-size: 14px; color: var(--af-text-muted); }
.af-field :deep(input),
.af-field :deep(textarea),
.af-field :deep(select) {
  height: 46px;
  border: 1px solid var(--af-border);
  border-radius: 8px;
  padding: 0 14px;
  background: var(--af-surface);
  color: var(--af-text);
}
.af-field :deep(textarea) { height: auto; min-height: 144px; padding: 12px 14px; resize: vertical; }
</style>
