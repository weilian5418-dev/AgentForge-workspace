<script setup lang="ts">
defineProps<{ label: string; disabled?: boolean }>()
</script>

<template>
  <button type="button" class="af-icon-button" :aria-label="label" :disabled="disabled">
    <slot />
  </button>
</template>

<style scoped>
.af-icon-button {
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--af-text);
}
.af-icon-button:disabled { opacity: 0.45; }
</style>
