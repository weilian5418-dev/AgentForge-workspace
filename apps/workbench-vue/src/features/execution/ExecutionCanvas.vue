<script setup lang="ts">
import { computed, ref, useId, nextTick, watch } from 'vue'
import { VueFlow, MarkerType, useVueFlow } from '@vue-flow/core'
import { useResizeObserver } from '@vueuse/core'
import { Minus, Plus, Maximize2 } from 'lucide-vue-next'
import { Background } from '@vue-flow/background'
import type { AgentRoleViewModel, DependencyViewModel } from '@/domain/view-models'
import AgentNode from './AgentNode.vue'
import { buildGraph } from './graph-layout'
import '@vue-flow/core/dist/style.css'

const props = defineProps<{
  roles: readonly AgentRoleViewModel[]
  dependencies: readonly DependencyViewModel[]
  compact?: boolean
}>()

const graph = computed(() => buildGraph(props.roles, props.dependencies))
const flowId = useId()
const host = ref<HTMLElement | null>(null)
const { fitView, zoomIn, zoomOut, viewport, onPaneReady } = useVueFlow({ id: flowId })
const zoomLabel = computed(() => `${Math.round(viewport.value.zoom * 100)}%`)
async function fit() {
  await nextTick()
  await fitView({ padding: .12, minZoom: .15, maxZoom: 1, duration: 0 })
}
onPaneReady(fit)
useResizeObserver(host, fit)
watch(() => props.roles, fit, { deep: true })
</script>

<template>
  <section ref="host" class="af-flow" :data-compact="compact" aria-label="Agent 执行路径" data-region="graph">
    <div class="af-flow-toolbar"><strong>{{ compact ? '协作路径' : '执行路径' }}</strong><div v-if="!compact"><button type="button" aria-label="缩小画布" @click="zoomOut()"><Minus :size="16" /></button><span>{{ zoomLabel }}</span><button type="button" aria-label="放大画布" @click="zoomIn()"><Plus :size="16" /></button><button type="button" aria-label="适应画布" @click="fit"><Maximize2 :size="16" /></button></div></div>
    <div v-if="compact" class="af-compact-graph"><svg class="af-compact-edges" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><path d="M 26 50 H 32 V 25 H 38 M 32 50 V 75 H 38 M 63 25 H 68 V 50 H 75 M 63 75 H 68 V 50" /></svg><div v-for="role in roles" :key="role.id" :class="['af-compact-role',role.label.toLowerCase()]"><strong>{{role.label}}</strong><span>{{role.harness}}</span></div></div>
    <VueFlow v-else
      :id="flowId"
      :nodes="graph.nodes"
      :edges="graph.edges"
      :nodes-draggable="false"
      :nodes-connectable="false"
      :default-edge-options="{ type: 'smoothstep', markerEnd: MarkerType.ArrowClosed }"
      :min-zoom="0.15"
      :max-zoom="1.5"
    >
      <template #node-agent="nodeProps">
        <AgentNode v-bind="nodeProps" />
      </template>
      <Background :gap="24" :size="1" pattern-color="#e8e2f1" />
    </VueFlow>
    <ul class="af-flow-a11y">
      <li v-for="role in roles" :key="role.id">
        {{ role.label }} · {{ role.harness }} · {{ role.status }}
      </li>
    </ul>
  </section>
</template>

<style scoped>
.af-flow {
  height: 355px;
  overflow: hidden;
  border: 1px solid var(--af-border);
  border-radius: 10px;
  position: relative;
  background: #fbfafe;
  display: grid;
  grid-template-rows: 48px minmax(0,1fr);
}
.af-flow-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 0 14px; font-size: 17px; }
.af-flow-toolbar > div { display: flex; align-items: center; gap: 2px; }
.af-flow-toolbar button { display: grid; place-items: center; width: 32px; height: 32px; border: 1px solid var(--af-border); background: var(--af-surface); border-radius: 6px; color: var(--af-text-secondary); }
.af-flow-toolbar span { min-width: 48px; text-align: center; font-size: 14px; }
.af-flow[data-compact='true'] { height: 252px; }
.af-flow-a11y {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}
.af-compact-graph { display:grid; grid-template-columns:1fr 1.15fr 1fr; grid-template-rows:1fr 1fr; align-items:center; gap:18px; padding:24px 12px; position:relative; }
.af-compact-role { border:1px solid var(--af-accent); border-radius:8px; padding:12px 6px; background:var(--af-surface); text-align:center; position:relative; z-index:1; }
.af-compact-role strong { display:block; font-size:15px; }.af-compact-role span { display:block; font-size:12px; margin-top:6px; color:var(--af-text-secondary); }
.af-compact-role.planner { grid-area:1/1/3/2; }.af-compact-role.frontend { grid-area:1/2; }.af-compact-role.backend { grid-area:2/2; }.af-compact-role.reviewer { grid-area:1/3/3/4; }
.af-compact-role.frontend::before,.af-compact-role.backend::before { content:''; width:18px; height:1px; position:absolute; left:-19px; top:50%; background:var(--af-accent); }
.af-compact-role.frontend::after,.af-compact-role.backend::after { content:''; width:18px; height:1px; position:absolute; right:-19px; top:50%; background:var(--af-accent); }
.af-flow[data-compact='true'] { background:transparent; border:0; height:240px; grid-template-rows:0 minmax(0,1fr); }.af-flow[data-compact='true'] .af-flow-toolbar { visibility:hidden; }
@media(max-width:639px) { .af-compact-graph { gap:10px; padding-inline:0; }.af-compact-role strong { font-size:13px; }.af-compact-role span { font-size:11px; }.af-compact-role::before,.af-compact-role::after { max-width:10px; } }
.af-compact-edges { position:absolute; inset:0; width:100%; height:100%; overflow:visible; }.af-compact-edges path { stroke:var(--af-accent); fill:none; stroke-width:1.5; vector-effect:non-scaling-stroke; }.af-compact-role::before,.af-compact-role::after { display:none; }
</style>
