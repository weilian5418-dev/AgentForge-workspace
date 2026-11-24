<script setup lang="ts">
withDefaults(defineProps<{
  tone?: 'primary' | 'secondary' | 'ghost'
  pending?: boolean
  disabled?: boolean
  type?: 'button' | 'submit' | 'reset'
}>(), { tone: 'secondary', pending: false, disabled: false, type: 'button' })
</script>

<template>
  <button
    :type="type"
    class="af-button"
    :data-tone="tone"
    :disabled="disabled || pending"
    :aria-busy="pending"
  >
    <slot name="icon" />
    <slot />
  </button>
</template>

<style scoped>
.af-button {
  height: 44px;
  padding-inline: 18px;
  border-radius: 8px;
  gap: 8px;
  font-size: 18px;
  font-weight: 500;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--af-border);
  background: var(--af-surface);
  color: var(--af-text);
}
.af-button[data-tone='primary'] {
  background: var(--af-button);
  color: #fff;
  border-color: var(--af-button);
}
.af-button[data-tone='ghost'] {
  background: transparent;
  border-color: transparent;
}
.af-button:disabled { opacity: 0.45; }
</style>
