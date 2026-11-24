<script setup lang="ts">
defineProps<{ message?: string; requestId?: string | null }>()
defineEmits<{ retry: [] }>()
</script>

<template>
  <div class="af-error" role="alert">
    <p>{{ message ?? '加载失败，请重试。' }}</p>
    <p v-if="requestId" class="af-mono">requestId: {{ requestId }}</p>
    <button type="button" class="af-retry" @click="$emit('retry')">重试</button>
  </div>
</template>

<style scoped>
.af-error {
  padding: 24px;
  border: 1px solid #f0c7c8;
  border-radius: 10px;
  background: #fff0f0;
  color: var(--af-danger);
}
.af-error p { margin: 0 0 8px; }
.af-retry {
  margin-top: 8px;
  height: 36px;
  padding: 0 14px;
  border-radius: 8px;
  border: 1px solid var(--af-border);
  background: var(--af-surface);
  color: var(--af-text);
}
</style>
