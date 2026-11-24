<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps<{
  original: string
  modified: string
  language?: string
}>()

const host = ref<HTMLElement | null>(null)
let dispose: (() => void) | undefined
let revision = 0
let destroyed = false

async function mount() {
  if (!host.value) return
  const current = ++revision
  dispose?.()
  const { mountDiff } = await import('./monaco')
  if(destroyed || current !== revision || !host.value) return
  dispose = mountDiff(host.value, props.original, props.modified, props.language ?? 'typescript')
}

onMounted(() => { void mount() })
watch(() => [props.original, props.modified, props.language], () => { void mount() })
onBeforeUnmount(() => { destroyed = true; revision++; dispose?.() })
</script>

<template>
  <div ref="host" class="af-code-diff" data-region="artifact" />
</template>

<style scoped>
.af-code-diff {
  height: 100%;
  min-height: 0;
  border-radius: 8px;
  overflow: hidden;
  background: var(--af-code);
}
</style>
