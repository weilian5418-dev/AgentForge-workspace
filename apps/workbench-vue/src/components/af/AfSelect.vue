<script setup lang="ts">
import { computed, useAttrs } from 'vue'
import { Check, ChevronDown } from 'lucide-vue-next'
import {
  SelectContent,
  SelectIcon,
  SelectItem,
  SelectItemIndicator,
  SelectItemText,
  SelectPortal,
  SelectRoot,
  SelectTrigger,
  SelectValue,
  SelectViewport,
} from 'reka-ui'

defineOptions({ inheritAttrs: false })

export interface AfSelectOption {
  value: string
  label: string
}

const props = withDefaults(defineProps<{
  options: AfSelectOption[]
  variant?: 'chip' | 'field'
  disabled?: boolean
  placeholder?: string
}>(), {
  variant: 'chip',
  disabled: false,
  placeholder: '请选择',
})

const model = defineModel<string>({ required: true })
const attrs = useAttrs()

const selectedLabel = computed(() =>
  props.options.find(option => option.value === model.value)?.label ?? props.placeholder,
)

const triggerAriaLabel = computed(() => {
  const label = attrs['aria-label']
  return typeof label === 'string' ? label : undefined
})
</script>

<template>
  <SelectRoot v-model="model" :disabled="disabled">
    <SelectTrigger
      class="af-select"
      :class="[`af-select--${variant}`]"
      v-bind="attrs"
      :aria-label="triggerAriaLabel"
      :disabled="disabled"
    >
      <span v-if="$slots.leading" class="af-select-leading">
        <slot name="leading" />
      </span>
      <SelectValue class="af-select-value" :placeholder="placeholder">
        {{ selectedLabel }}
      </SelectValue>
      <SelectIcon class="af-select-icon">
        <ChevronDown :size="12" :stroke-width="1.75" aria-hidden="true" />
      </SelectIcon>
    </SelectTrigger>

    <SelectPortal>
      <SelectContent class="af-select-content" :side-offset="6" position="popper">
        <SelectViewport class="af-select-viewport">
          <SelectItem
            v-for="option in options"
            :key="option.value"
            class="af-select-item"
            :value="option.value"
          >
            <SelectItemText>{{ option.label }}</SelectItemText>
            <SelectItemIndicator class="af-select-check">
              <Check :size="14" :stroke-width="2" aria-hidden="true" />
            </SelectItemIndicator>
          </SelectItem>
        </SelectViewport>
      </SelectContent>
    </SelectPortal>
  </SelectRoot>
</template>

<style scoped>
.af-select {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  max-width: 100%;
  margin: 0;
  border: 1px solid color-mix(in srgb, var(--af-border) 92%, var(--af-text) 8%);
  background: color-mix(in srgb, var(--af-surface-subtle, var(--af-surface)) 88%, var(--af-surface));
  color: var(--af-text);
  font: inherit;
  font-weight: 500;
  line-height: 1;
  cursor: pointer;
  transition: border-color 140ms ease, background-color 140ms ease, box-shadow 140ms ease, color 140ms ease;
}

.af-select:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.af-select--chip {
  height: 40px;
  min-height: 40px;
  padding: 0 12px;
  border-radius: 999px;
  box-shadow: inset 0 1px 0 color-mix(in srgb, #fff 55%, transparent);
  color: var(--af-text-secondary);
  font-size: 14px;
}

.af-select--field {
  height: 46px;
  min-height: 46px;
  width: 100%;
  padding: 0 12px;
  border-radius: 8px;
  font-size: 16px;
}

.af-select:hover:not(:disabled) {
  border-color: color-mix(in srgb, var(--af-accent) 35%, var(--af-border));
  background: color-mix(in srgb, var(--af-selected) 55%, var(--af-surface));
  color: var(--af-text);
}

.af-select[data-state='open'],
.af-select:focus-visible {
  border-color: color-mix(in srgb, var(--af-accent-strong, var(--af-accent)) 55%, var(--af-border));
  box-shadow:
    inset 0 1px 0 color-mix(in srgb, #fff 55%, transparent),
    0 0 0 3px color-mix(in srgb, var(--af-accent) 18%, transparent);
  color: var(--af-text);
  outline: none;
}

.af-select-leading {
  display: inline-flex;
  flex-shrink: 0;
  color: inherit;
  opacity: 0.78;
}

.af-select:hover .af-select-leading,
.af-select[data-state='open'] .af-select-leading,
.af-select:focus-visible .af-select-leading {
  opacity: 1;
  color: var(--af-accent-ink);
}

.af-select-value {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: left;
}

.af-select-icon {
  display: inline-flex;
  flex-shrink: 0;
  margin-left: 2px;
  color: var(--af-text-muted, var(--af-text-secondary));
  transition: transform 140ms ease;
}

.af-select[data-state='open'] .af-select-icon {
  transform: rotate(180deg);
}

@media (max-width: 639px) {
  .af-select--chip {
    height: 44px;
    min-height: 44px;
  }
}
</style>

<!-- Portal content mounts outside this component; keep unscoped prefixed styles. -->
<style>
.af-select-content {
  z-index: 80;
  min-width: var(--reka-select-trigger-width);
  max-width: min(280px, calc(100vw - 24px));
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--af-border) 90%, var(--af-text) 10%);
  border-radius: 12px;
  background: var(--af-surface);
  color: var(--af-text);
  box-shadow:
    0 1px 0 color-mix(in srgb, #fff 65%, transparent),
    0 12px 32px color-mix(in srgb, #1e1832 14%, transparent);
  animation: af-select-in 140ms ease;
}

.af-select-viewport {
  max-height: min(280px, 50vh);
  padding: 6px;
  overflow: auto;
}

.af-select-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 36px;
  padding: 8px 10px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  line-height: 1.3;
  cursor: pointer;
  outline: none;
  user-select: none;
}

.af-select-item[data-highlighted] {
  background: var(--af-selected);
  color: var(--af-text);
}

.af-select-item[data-state='checked'] {
  color: var(--af-accent-ink);
}

.af-select-check {
  display: inline-flex;
  flex-shrink: 0;
  color: var(--af-accent-ink);
}

@keyframes af-select-in {
  from {
    opacity: 0;
    transform: translateY(-4px) scale(0.98);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}
</style>
