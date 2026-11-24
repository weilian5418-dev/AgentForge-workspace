<script setup lang="ts">
import { computed, ref } from 'vue'
import { useMediaQuery } from '@vueuse/core'

const props = withDefaults(defineProps<{
  leftWidth?: number
}>(), { leftWidth: 602 })

const width = ref(props.leftWidth)
const host = ref<HTMLElement | null>(null)
const narrow = useMediaQuery('(max-width: 1100px)')
const medium = useMediaQuery('(max-width: 1750px)')
const panel = ref('conversation')
const basis = computed(() => medium.value ? Math.min(width.value,420) : width.value)
function startResize(event: PointerEvent) {
  if (narrow.value || !host.value) return
  const element = event.currentTarget as HTMLElement
  element.setPointerCapture(event.pointerId)
  const rect = host.value.getBoundingClientRect()
  const move = (next: PointerEvent) => { width.value = Math.max(320, Math.min(next.clientX - rect.left, rect.width - 420)) }
  const stop = () => { element.removeEventListener('pointermove', move); element.removeEventListener('pointerup', stop); element.removeEventListener('pointercancel', stop) }
  element.addEventListener('pointermove', move)
  element.addEventListener('pointerup', stop)
  element.addEventListener('pointercancel', stop)
}
</script>

<template>
  <div ref="host" class="af-workspace-split" :data-panel="panel">
    <nav v-if="narrow" class="af-pane-switch" aria-label="工作面切换"><button type="button" :data-active="panel === 'conversation'" @click="panel = 'conversation'">对话与计划</button><button type="button" :data-active="panel === 'workbench'" @click="panel = 'workbench'">执行与产物</button></nav>
    <div v-show="!narrow || panel === 'conversation'" class="af-split-left af-pane" :style="{ flexBasis: `${basis}px` }">
      <slot name="left" />
    </div>
    <div v-if="!narrow" class="af-split-handle" role="separator" aria-label="调整对话宽度" aria-orientation="vertical" tabindex="0" :aria-valuenow="width" :aria-valuemin="320" :aria-valuemax="1000" @pointerdown="startResize" @keydown.left.prevent="width = Math.max(320,width - 20)" @keydown.right.prevent="width = Math.min(1000,width + 20)" />
    <div v-show="!narrow || panel === 'workbench'" class="af-split-right af-pane">
      <slot name="right" />
    </div>
  </div>
</template>

<style scoped>
.af-workspace-split {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  overflow: hidden;
}
.af-split-left {
  flex: 0 0 auto;
  align-self: stretch;
  min-width: 320px;
  max-width: 70%;
  min-height: 0;
  border-right: 1px solid var(--af-border);
  display: flex;
  flex-direction: column;
}
.af-split-handle {
  width: 0;
  position: relative;
  z-index: 3;
  cursor: col-resize;
  touch-action: none;
}
.af-split-handle::before { content: ''; position: absolute; top: 48%; left: -4px; width: 8px; height: 36px; border: 1px solid var(--af-border); border-radius: 4px; background: var(--af-surface); }
.af-pane-switch { display: flex; padding: 8px 16px; gap: 8px; border-bottom: 1px solid var(--af-border); background: var(--af-surface); }
.af-pane-switch button { flex: 1; min-height: 44px; border: 0; border-radius: 8px; background: transparent; color: var(--af-text-secondary); font-size: 16px; }
.af-pane-switch button[data-active='true'] { background: var(--af-selected); color: var(--af-accent-ink); }
@media(max-width:1100px) {
  .af-workspace-split { display: grid; grid-template-rows: auto minmax(0,1fr); }
  .af-workspace-split .af-split-left { min-width: 0; max-width: none; border: 0; }
  .af-split-right { overflow: auto; }
}
.af-split-handle::after {
  content: '';
  position: absolute;
  left: -4px;
  top: 0;
  bottom: 0;
  width: 8px;
}
.af-split-right {
  flex: 1;
  min-width: 0;
  overflow: auto;
  display: flex;
  flex-direction: column;
}
</style>
