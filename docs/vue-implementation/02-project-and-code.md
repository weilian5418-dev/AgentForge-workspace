# 02 · 项目骨架与代码写法

本文件中的代码是实施模板。按目标路径落盘后运行类型检查和构建；不是当前已运行的 Vue 代码。对外部库使用下面的版本基线，不能让每个 Agent 各自拉取 latest。

## 1. 版本与依赖

2026-11-20 从 npm registry 查询到的基线如下。先做最小 smoke build 并锁定实际成功版本；主版本更新不能不经检查直接替换。Node 选择 24 LTS 的同一补丁版本，pnpm 固定同一版本，提交 lockfile。

| 包 | 查询版本 |
| --- | --- |
| vue / typescript / vue-tsc | 3.5.45 / 7.0.2 / 3.3.14 |
| vite / @vitejs/plugin-vue | 8.3.8 / 6.0.11 |
| tailwindcss / @tailwindcss/vite | 4.3.5 / 4.3.5 |
| reka-ui / @animate-ui/vue / motion-v | 2.11.0 / 2.4.6 / 2.13.0 |
| vue-router / pinia | 5.5.1 / 4.1.0 |
| @tanstack/vue-query | 5.108.1 |
| @vue-flow/core / @vue-flow/background | 1.48.2 / 1.3.2 |
| monaco-editor | 0.57.0 |
| markdown-it / dompurify / lucide-vue-next | 15.0.3 / 3.4.18 / 1.0.0 |
| eventsource-parser | 4.1.3 |
| @playwright/test / vitest / @vue/test-utils | 1.65.1 / 5.1.1 / 2.6.0 |
| shadcn-vue CLI / openapi-typescript / msw | 2.9.0 / 7.13.0 / 3.0.7 |

这些版本包括比常见教程更新的 Vue Router 5、Pinia 4、TypeScript 7，不能照搬旧版本插件配置。仅使用基本 createRouter/createWebHistory API，不引入自动路由插件和另一套服务端缓存。

`shadcn-vue` 是生成组件源代码的 CLI，不是把全部 UI 当作黑盒运行时安装。组件源代码固定放 `src/components/ui/`。Animate UI 的 peer 包以实际使用的 primitives 为准检查；motion-v 还需要兼容的 `@vueuse/core`。不要用忽略所有 peer 错误的方式装出一个不确定环境。

初始化命令（在新目标目录中执行，不能覆盖现有根目录）：

```powershell
Set-Location D:\AgentFront
pnpm create vite@8 apps/workbench-vue --template vue-ts
Set-Location D:\AgentFront\apps\workbench-vue
pnpm add --save-exact vue@3.5.45 vue-router@5.5.1 pinia@4.1.0 @tanstack/vue-query@5.108.1
pnpm add --save-exact reka-ui@2.11.0 @animate-ui/vue@2.4.6 motion-v@2.13.0
pnpm add --save-exact @vue-flow/core@1.48.2 @vue-flow/background@1.3.2
pnpm add --save-exact monaco-editor@0.57.0 markdown-it@15.0.3 dompurify@3.4.18 lucide-vue-next@1.0.0 eventsource-parser@4.1.3
pnpm add -D --save-exact vite@8.3.8 @vitejs/plugin-vue@6.0.11 typescript@7.0.2 vue-tsc@3.3.14 tailwindcss@4.3.5 @tailwindcss/vite@4.3.5
pnpm add -D --save-exact @playwright/test@1.65.1 vitest@5.1.1 @vue/test-utils@2.6.0 openapi-typescript@7.13.0 msw@3.0.7
pnpm add -D @types/node @types/markdown-it sharp pixelmatch
pnpm dlx shadcn-vue@2.9.0 init
pnpm dlx shadcn-vue@2.9.0 add button input textarea label tabs select combobox dialog sheet tooltip checkbox switch dropdown-menu collapsible separator avatar scroll-area
```

CLI 若某个组件名不支持，查看该版本 registry，单独生成所需组合；不能为凑命令换成 Element Plus。`@vueuse/core`、peer包、字体包在M0补齐并固定版本。安装后立即 `pnpm exec vue-tsc --noEmit`，解决工具链兼容再写页面。

## 2. 目录与责任边界

```text
apps/workbench-vue/
  public/fonts/                         本地字体与许可证
  public/brand/                        AgentForge、Harness SVG、avatar
  src/
    main.ts
    App.vue
    app/router.ts
    app/query-client.ts
    app/runtime.ts                     live/reference来源唯一入口
    styles/tokens.css
    styles/base.css
    styles/layout.css
    components/ui/                     shadcn-vue生成组件，仅基础负责人修改
    components/af/                     项目视觉封装，页面只调用此层
      AfButton.vue
      AfIconButton.vue
      AfField.vue
      AfStatusBadge.vue
      AfPanel.vue
      AfEmptyState.vue
      AfErrorState.vue
      AfTabs.vue
    layouts/AppShell.vue
    layouts/GlobalHeader.vue
    layouts/ManagementSidebar.vue
    layouts/TaskSidebar.vue
    layouts/KnowledgeTree.vue
    layouts/WorkspaceSplit.vue
    features/conversation/ConversationPane.vue
    features/conversation/MessageList.vue
    features/conversation/MessageComposer.vue
    features/conversation/ExecutionPlan.vue
    features/approvals/ApprovalCard.vue
    features/approvals/ApprovalDetail.vue
    features/execution/ExecutionCanvas.vue
    features/execution/AgentNode.vue
    features/execution/graph-layout.ts
    features/artifacts/ArtifactInspector.vue
    features/artifacts/CodeDiff.vue
    features/artifacts/ReadOnlyLog.vue
    features/tasks/TaskTable.vue
    features/knowledge/DocumentPreview.vue
    domain/view-models.ts
    domain/presenters/                  后端DTO到页面VM
    data/contracts/api.generated.ts
    data/contracts/contract-snapshot.json
    data/http.ts
    data/api/                          原有纯TS endpoint迁入与补齐
    data/repositories/                 页面需要的聚合与能力适配
    data/realtime/                     SSE解析/重连/控制帧/游标
    stores/preferences.ts
    stores/session.ts                  只放连接状态，不保存生图密钥
    queries/                           query keys、useQuery、useMutation
    fixtures/                          12页确定性数据
    pages/                             12个页面，名称对应manifest
    reference/ReferenceGate.vue         只在显式reference构建开启
  e2e/reference.spec.ts
  e2e/workflow.spec.ts
  scripts/compare-reference.mjs
  visual/reference/                     原始PNG，只用于测试，不进产品DOM
  visual/baseline/                      人工确认后的浏览器截图
  visual/current/
  visual/diff/
  visual/report/
  components.json
  vite.config.ts
  playwright.config.ts
```

不要创建第二份基础Button、把API请求写进图节点、让页面复制同一侧栏、把整个run对象存进多个store。共享组件API发生改变时先更新所有调用者，不能只修自己负责的页。

## 3. Vite、入口和路由

`vite.config.ts`：

```ts
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: {
    host: '127.0.0.1',
    port: 5174,
    strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:8765', changeOrigin: false } },
  },
  preview: { host: '127.0.0.1', port: 4174, strictPort: true },
})
```

同时在应用tsconfig加入 `baseUrl:'.'` 与 `paths:{'@/*':['src/*']}`；启用strict、noUncheckedIndexedAccess。Vite alias不能替代TypeScript paths。

`main.ts`：

```ts
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import App from './App.vue'
import { router } from './app/router'
import './styles/tokens.css'
import './styles/base.css'
import './styles/layout.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5_000,
      retry: false,
      refetchOnWindowFocus: true,
    },
    mutations: { retry: false },
  },
})

createApp(App)
  .use(createPinia())
  .use(VueQueryPlugin, { queryClient })
  .use(router)
  .mount('#app')
```

`App.vue`：根级包裹AnimateUIProvider。该包为通配子路径exports，先核实安装版本的provider入口；把import封装进 `components/af/AfMotionProvider.vue`，不能在12个页面各导入不同入口。未核实入口时不要猜 `@animate-ui/vue` 根导出。`AppShell` 内是GlobalHeader和RouterView，页面通过route.meta选择sidebar，执行页不再套management sidebar。

路由手写：

```ts
import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/workspace' },
    { path: '/workspace', component: () => import('@/pages/OverviewPage.vue'), meta: { shell: 'management' } },
    { path: '/tasks/new', component: () => import('@/pages/NewTaskPage.vue'), meta: { shell: 'task' } },
    { path: '/tasks', component: () => import('@/pages/TaskLibraryPage.vue'), meta: { shell: 'management' } },
    { path: '/tasks/:runId', component: () => import('@/pages/ExecutionPage.vue'), meta: { shell: 'execution' } },
    { path: '/tasks/:runId/delivery', component: () => import('@/pages/DeliveryPage.vue'), meta: { shell: 'task' } },
    { path: '/inbox', component: () => import('@/pages/InboxPage.vue'), meta: { shell: 'management' } },
    { path: '/executors', component: () => import('@/pages/ExecutorsPage.vue'), meta: { shell: 'management' } },
    { path: '/executors/profiles/:profileId', component: () => import('@/pages/ProfilePage.vue'), meta: { shell: 'management' } },
    { path: '/knowledge', component: () => import('@/pages/KnowledgePage.vue'), meta: { shell: 'management' } },
    { path: '/knowledge/:documentId', component: () => import('@/pages/DocumentPage.vue'), meta: { shell: 'knowledgeDetail' } },
    { path: '/extensions', component: () => import('@/pages/ExtensionsPage.vue'), meta: { shell: 'management' } },
    { path: '/settings/connection', component: () => import('@/pages/SettingsPage.vue'), meta: { shell: 'management' } },
    { path: '/:pathMatch(.*)*', component: () => import('@/pages/NotFoundPage.vue') },
  ],
})
```

demo ID只有reference数据源识别；live访问demo应显示404/无此任务，不得加载示例任务。身份未连接时使用非12页主图里的连接状态界面，不以返回示例成功绕过。

## 4. Token与基础组件

`tokens.css` 的核心：

```css
@import 'tailwindcss';

:root {
  --af-bg: #fcfbfe;
  --af-surface: #fff;
  --af-surface-subtle: #f8f5fd;
  --af-selected: #f0ebfa;
  --af-accent: #a391d4;
  --af-accent-ink: #6b5395;
  --af-accent-strong: #795cab;
  --af-text: #24232a;
  --af-text-secondary: #625e6d;
  --af-text-muted: #797482;
  --af-border: #e7e3ed;
  --af-button: #30323a;
  --af-code: #2d3137;
  --af-header-height: 72px;
  --af-management-width: 296px;
  --af-task-width: 314px;
  --af-conversation-width: 602px;
  --af-radius: 8px;
  --background: var(--af-bg);
  --foreground: var(--af-text);
  --primary: var(--af-button);
  --primary-foreground: #fff;
  --border: var(--af-border);
  --ring: var(--af-accent-strong);
}

@theme inline {
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --color-primary: var(--primary);
  --color-primary-foreground: var(--primary-foreground);
  --color-border: var(--border);
  --color-ring: var(--ring);
}
```

把shadcn生成CSS的其他token映射进上述体系，不并存两套互相覆盖的`:root`。基础CSS：

```css
*, *::before, *::after { box-sizing: border-box; }
html, body, #app { margin: 0; width: 100%; height: 100%; }
body { color: var(--af-text); background: var(--af-bg); font-size: 18px; line-height: 1.55; }
button, input, textarea, select { font: inherit; }
button { cursor: pointer; }
button:disabled { cursor: not-allowed; }
:focus-visible { outline: 2px solid var(--af-accent-strong); outline-offset: 2px; }
.af-shell { height: 100dvh; display: grid; grid-template-rows: var(--af-header-height) minmax(0, 1fr); overflow: hidden; }
.af-body { min-height: 0; min-width: 0; display: flex; }
.af-page { flex: 1; min-width: 0; overflow: auto; padding: 28px 32px 24px; }
.af-workspace { flex: 1; min-width: 0; min-height: 0; display: flex; overflow: hidden; }
.af-pane { min-width: 0; min-height: 0; }
.af-conversation { flex: 0 0 var(--af-conversation-width); border-right: 1px solid var(--af-border); }
.af-canvas { flex: 1; }
@media (max-width: 1919px) { :root { --af-task-width: 240px; --af-conversation-width: 420px; } }
@media (max-width: 1439px) { :root { --af-task-width: 72px; --af-management-width: 224px; } }
@media (max-width: 1279px) { .af-conversation { flex: 1; } }
```

`AfButton.vue` 封装已生成的Button，统一variant和尺寸，不在每页写class堆叠改变几何：

```vue
<script setup lang="ts">
import { Button } from '@/components/ui/button'

withDefaults(defineProps<{
  tone?: 'primary' | 'secondary' | 'ghost'
  pending?: boolean
  disabled?: boolean
  type?: 'button' | 'submit' | 'reset'
}>(), { tone: 'secondary', pending: false, disabled: false, type: 'button' })
</script>

<template>
  <Button
    :type="type"
    :variant="tone === 'primary' ? 'default' : tone === 'ghost' ? 'ghost' : 'outline'"
    :disabled="disabled || pending"
    :aria-busy="pending"
    class="af-button"
  >
    <slot name="icon" />
    <slot />
  </Button>
</template>

<style scoped>
.af-button { height: 44px; padding-inline: 18px; border-radius: 8px; gap: 8px; font-size: 18px; font-weight: 500; white-space: nowrap; }
</style>
```

`AfStatusBadge.vue` 的VM输入只能是枚举，不能通过includes('done')猜所有后端状态：

```vue
<script setup lang="ts">
import { Check, Clock3, Pause, CircleAlert, LoaderCircle } from 'lucide-vue-next'

export type UiStatus = 'running' | 'completed' | 'waiting' | 'approval' | 'paused' | 'failed' | 'unknown'
const props = defineProps<{ status: UiStatus; label?: string }>()
const styles = {
  running: { text: '执行中', icon: LoaderCircle, color: '#6b5395', background: '#f0ebfa' },
  completed: { text: '已完成', icon: Check, color: '#238636', background: '#e8f7eb' },
  waiting: { text: '等待依赖', icon: Clock3, color: '#625e6d', background: '#f1eff4' },
  approval: { text: '待批准', icon: CircleAlert, color: '#9a570d', background: '#fff5e5' },
  paused: { text: '已暂停', icon: Pause, color: '#625e6d', background: '#f1eff4' },
  failed: { text: '执行失败', icon: CircleAlert, color: '#c24145', background: '#fff0f0' },
  unknown: { text: '状态未知', icon: CircleAlert, color: '#625e6d', background: '#f1eff4' },
} as const
</script>

<template>
  <span class="af-status" :style="{ color: styles[props.status].color, background: styles[props.status].background }">
    <component :is="styles[props.status].icon" :size="16" :stroke-width="1.75" aria-hidden="true" />
    {{ label ?? styles[props.status].text }}
  </span>
</template>

<style scoped>
.af-status { display: inline-flex; align-items: center; gap: 6px; height: 30px; padding: 0 10px; border-radius: 8px; font-size: 14px; font-weight: 500; white-space: nowrap; }
</style>
```

状态类型更适合实际落到 `domain/view-models.ts`，此处同文件展示便于理解。Waiting-for-acceptance必须显式传label，不等于业务已完成。

## 5. 对话输入框：处理中文输入法和重复提交

```vue
<script setup lang="ts">
import { ref } from 'vue'
import { Paperclip, Send, ChevronDown } from 'lucide-vue-next'

withDefaults(defineProps<{ pending?: boolean; disabled?: boolean }>(), { pending: false, disabled: false })
const emit = defineEmits<{ send: [text: string]; attach: []; routing: [] }>()
const draft = defineModel<string>({ default: '' })
const composing = ref(false)

function submit(isDisabled: boolean) {
  if (isDisabled || composing.value || !draft.value.trim()) return
  emit('send', draft.value.trim())
}

function onKey(event: KeyboardEvent, isDisabled: boolean) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing || composing.value || event.keyCode === 229) return
  event.preventDefault()
  submit(isDisabled)
}
</script>

<template>
  <form class="af-composer" @submit.prevent="submit(pending || disabled)">
    <textarea
      v-model="draft"
      aria-label="给 Agent 的补充要求"
      placeholder="继续描述，或 @ 指定 Agent…"
      :disabled="pending || disabled"
      @compositionstart="composing = true"
      @compositionend="composing = false"
      @keydown="onKey($event, pending || disabled)"
    />
    <div class="af-composer-toolbar">
      <button type="button" class="af-composer-icon" aria-label="添加参考资料" :disabled="pending || disabled" @click="emit('attach')"><Paperclip :size="20" /></button>
      <button type="button" class="af-routing" :disabled="pending || disabled" @click="emit('routing')">自动调度 <ChevronDown :size="16" /></button>
      <span class="af-context">当前项目</span>
      <button type="submit" class="af-send" aria-label="发送消息" :disabled="pending || disabled || !draft.trim()"><Send :size="20" /></button>
    </div>
  </form>
</template>

<style scoped>
.af-composer { border: 1px solid var(--af-accent); border-radius: 10px; padding: 14px; background: #fff; }
textarea { display: block; width: 100%; min-height: 52px; max-height: 220px; padding: 0; border: 0; resize: vertical; background: transparent; color: var(--af-text); outline: none; }
.af-composer:focus-within { outline: 2px solid var(--af-accent-strong); outline-offset: 2px; }
.af-composer-toolbar { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.af-composer-icon, .af-send { width: 40px; height: 36px; display: grid; place-items: center; border: 0; border-radius: 8px; }
.af-composer-icon { background: transparent; }
.af-send { margin-left: auto; background: var(--af-button); color: #fff; }
.af-routing, .af-context { display: inline-flex; align-items: center; gap: 8px; border: 1px solid var(--af-border); border-radius: 8px; padding: 6px 10px; background: #fff; font-size: 16px; }
.af-send:disabled { opacity: .45; }
</style>
```

父组件成功后清空draft；失败保留输入和同一幂等键，显示重试。附件选择真正接通支持接口前需显示说明，不能按钮点了无反馈。@选择器维护recipientRef真实标识，不把'Frontend'显示名当API收件人。

## 6. Vue Flow：自定义节点，禁止默认蓝色主题

图的位置来自工作包依赖，不通过固定的4个名字判断。reference模式使用确定坐标，live模式使用同一拓扑布局函数；后续可引入ELK支持大DAG。任务状态变化只更新node.data，不能每次 `fitView` 重置用户缩放。

`AgentNode.vue`：

```vue
<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core'
import AfStatusBadge from '@/components/af/AfStatusBadge.vue'
import type { UiStatus } from '@/components/af/AfStatusBadge.vue'

defineProps<{ data: { role: string; harness: string; status: UiStatus; workspace?: string }; selected?: boolean }>()
</script>

<template>
  <div class="af-agent-node" :data-selected="selected">
    <Handle type="target" :position="Position.Left" class="af-handle" />
    <div class="af-agent-main">
      <div><strong>{{ data.role }}</strong><span>{{ data.harness }}</span></div>
      <AfStatusBadge :status="data.status" />
    </div>
    <p v-if="data.workspace" class="af-mono">{{ data.workspace }}</p>
    <Handle type="source" :position="Position.Right" class="af-handle" />
  </div>
</template>

<style scoped>
.af-agent-node { width: 280px; min-height: 84px; padding: 12px 14px; border: 1px solid var(--af-border); border-radius: 8px; background: #fff; }
.af-agent-node[data-selected='true'] { border-color: var(--af-accent-strong); background: #fbf9fe; }
.af-agent-main { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
strong, span { display: block; }
strong { font-size: 18px; }
span, p { font-size: 14px; }
p { margin: 8px 0 0; }
.af-handle { opacity: 0; pointer-events: none; }
</style>
```

`ExecutionCanvas.vue` 的库连接骨架：

```vue
<script setup lang="ts">
import { VueFlow, MarkerType } from '@vue-flow/core'
import type { Node, Edge } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import AgentNode from './AgentNode.vue'
import '@vue-flow/core/dist/style.css'

defineProps<{ nodes: Node[]; edges: Edge[] }>()
</script>

<template>
  <section class="af-flow" aria-label="Agent 执行路径">
    <VueFlow
      :nodes="nodes"
      :edges="edges"
      :nodes-draggable="false"
      :nodes-connectable="false"
      :default-viewport="{ x: 16, y: 24, zoom: 1 }"
      :default-edge-options="{ type: 'smoothstep', markerEnd: MarkerType.ArrowClosed }"
      :min-zoom="0.3"
      :max-zoom="1.5"
    >
      <template #node-agent="nodeProps"><AgentNode v-bind="nodeProps" /></template>
      <Background :gap="24" :size="1" pattern-color="#e8e2f1" />
    </VueFlow>
  </section>
</template>

<style scoped>
.af-flow { height: 355px; overflow: hidden; border: 1px solid var(--af-border); border-radius: 10px; }
</style>
```

示例图坐标（画布内部）：Planner `(0,130)`、Frontend `(386,50)`、Backend `(386,184)`、Reviewer `(792,130)`；节点width依据可用宽度在240–280调整；viewport由`graph-layout.ts`根据容器宽和图边界一次计算。边样式直接由VM给出stroke、strokeWidth=1.5；默认蓝色、节点拖拽和新增连线均关闭。小屏提供语义列表替代，SVG画布不应是唯一可访问执行信息。

Splitter仅在基础布局负责人实现。当前Reka版本的 `SplitterGroup/Panel/ResizeHandle` props经最小样例确认后封装到WorkspaceSplit，页面只传命名插槽和初始宽度；不要每页各试一套Splitter库。存储布局时区分用户偏好和reference的固定值。

## 7. Monaco差异与Markdown

Monaco由 `defineAsyncComponent` 懒加载。Vite在专用模块注册editor.worker，模型URI必须稳定且不包含用户敏感绝对路径。

```ts
import EditorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'
import * as monaco from 'monaco-editor'

self.MonacoEnvironment = { getWorker: () => new EditorWorker() }

export function mountDiff(container: HTMLElement, original: string, modified: string, language = 'typescript') {
  monaco.editor.defineTheme('agentforge-diff', {
    base: 'vs-dark', inherit: true, rules: [],
    colors: { 'editor.background': '#2D3137', 'editor.lineHighlightBackground': '#34383F', 'diffEditor.insertedTextBackground': '#23863630' },
  })
  const editor = monaco.editor.createDiffEditor(container, {
    readOnly: true,
    originalEditable: false,
    renderSideBySide: false,
    automaticLayout: true,
    minimap: { enabled: false },
    fontFamily: 'AF Mono',
    fontSize: 17,
    lineHeight: 27,
    scrollBeyondLastLine: false,
    theme: 'agentforge-diff',
    renderOverviewRuler: false,
  })
  const originalModel = monaco.editor.createModel(original, language)
  const modifiedModel = monaco.editor.createModel(modified, language)
  editor.setModel({ original: originalModel, modified: modifiedModel })
  return () => { editor.dispose(); originalModel.dispose(); modifiedModel.dispose() }
}
```

Vue组件 `onMounted` 后mount、`watch`更新模型内容、`onBeforeUnmount`清理。先保证字体加载，否则Monaco字符测量错误。需要更多语言worker时按实际语言扩展。不要把代码中的`<el-button>`误解为产品必须安装Element Plus；它只是示例产物中的字符串。

```ts
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const renderer = new MarkdownIt({ html: false, linkify: true, breaks: false })
export function renderDocument(source: string) {
  return DOMPurify.sanitize(renderer.render(source))
}
```

用 `v-html` 只绑定该函数的返回值。链接只允许http/https和应用内受控路由，不允许任意file协议、脚本URL或带token外链；远程图片按产品策略限制。文档表格、标题、code使用项目prose样式，不依赖浏览器默认大标题间距。

## 8. Query、Pinia、VM分工

- Query：API DTO、run snapshot、plan、messages、deliveries、approvals、profiles。key必须包含projectId/runId/manifestHash，切换项目不能串缓存。
- Pinia：主题、侧栏折叠、分栏宽、草稿、选中pane/文件。收件箱badge从query派生，不手工加减。
- VM/presenter：把真实DTO变为 `TaskRowVM/AgentNodeVM/ApprovalVM/DeliveryVM`；区分`unknown`、`notConfigured`和`loading`，不把缺字段映射为成功。
- reference repository：与live实现同一接口，返回固定VM/DTO，副作用只更新本地fixture；外部API完全不调用。

建议最小接口：

```ts
export type FeatureAvailability =
  | { supported: true }
  | { supported: false; reason: string }

export interface WorkspaceRepository {
  loadTask(runId: string, signal?: AbortSignal): Promise<TaskViewModel>
  loadTasks(projectId: string, signal?: AbortSignal): Promise<TaskRowViewModel[]>
  sendMessage(input: SendMessageInput): Promise<SentMessageViewModel>
  decideApproval(input: ApprovalDecisionInput): Promise<ApprovalViewModel>
  capabilities: Readonly<Record<string, FeatureAvailability>>
}
```

这些 `*ViewModel`/Input 类型必须在`domain`显式定义后再编译，不是从空气里import。ViewModel不得包含真实Key、命令执行函数或Vue组件对象；图标由视图根据kind选择。

reference/live通过应用初始化注入repository，URL参数只在 `VITE_ENABLE_REFERENCE=true` 的专用构建被读取。正式构建为false时，`?reference=1`不得开启数据替换；reference代码可独立入口并由构建移除。

## 9. 可复制的参考数据与脚本约定

本交付的 `examples/reference-data.ts` 提供可直接复制到 `src/fixtures/shared.ts` 的纯TS数据，包含8个任务、4角色、4条依赖、4文件、示例代码、对话、审批、6份资料、3种Harness和4项工具。`taskCounters()`和`changeTotals()`自动派生计数，不能在页面另外硬写数字。代码片段是显示内容，不是要求执行示例业务函数。

02的draft、04完成状态、06选中审批等按page-manifest组合这些共享数据；04使用`completedDeliveryRoles()`，不能修改共享roles导致03也显示完成。reference审批mutation只操作该reference repository的本地副本；demo approval没有actionDigest，因此绝不能被live mutation接受。

目标package.json的scripts至少包含：

```json
{
  "dev": "vite",
  "typecheck": "vue-tsc --noEmit",
  "build": "vue-tsc --noEmit && vite build",
  "build:reference": "vue-tsc --noEmit && vite build --mode reference",
  "test:unit": "vitest run",
  "test:visual": "playwright test e2e/reference.spec.ts",
  "test:workflow": "playwright test e2e/workflow.spec.ts",
  "preview": "vite preview"
}
```

其他需要补齐的基础代码先写清楚签名再分派页面：`useWorkspaceRepository()`使用Vue InjectionKey提供单例repository；`useCurrentProject()`从project查询/选择store得到 `ComputedRef<{id,name}|null>`；`PageHeader`接受title/description和actions插槽；`AfErrorState`接受message/requestId并emit retry。缺省project不能用非空断言绕过，query通过enabled阻止无项目调用，页面显示项目连接入口。
