<script setup lang="ts">
import { computed, ref } from 'vue'
import type { FileChangeViewModel } from '@/domain/view-models'
import { contentForFile } from './file-content'
import AfDialog from '@/components/af/AfDialog.vue'
import AfButton from '@/components/af/AfButton.vue'
import CodeDiff from './CodeDiff.vue'
import ReadOnlyLog from './ReadOnlyLog.vue'
import { useQuery } from '@tanstack/vue-query'
import { useWorkspaceRepository } from '@/app/runtime'

const props = defineProps<{
  files: readonly FileChangeViewModel[]
  original: string
  modified: string
  additions: number
  deletions: number
  live?: boolean
}>()

const fullOpen = ref(false)
const role = ref('all')
const filteredFiles = computed(()=>props.files.filter(x=>role.value==='all' || (role.value==='frontend' ? ['html','typescript'].includes(x.language) : x.language==='python')))
const mode = ref<'diff' | 'log' | 'preview'>('diff')
const selectedId = ref(props.files[0]?.id ?? '')
const selected = computed(() => filteredFiles.value.find(file => file.id === selectedId.value) ?? filteredFiles.value[0])
const repository = useWorkspaceRepository()
const { data: liveContent, isPending: loading, error } = useQuery({ queryKey: computed(() => ['artifact-content', selected.value?.id]), enabled: computed(() => Boolean(props.live && selected.value?.id.startsWith('cand_'))), queryFn: async ({ signal }) => {
  const blob = await repository.loadArtifact!(selected.value!.id, signal)
  if (blob.size > 2 * 1024 * 1024) return '文件超过 2 MB，请前往交付页下载查看。'
  try { return new TextDecoder('utf-8', { fatal:true }).decode(await blob.arrayBuffer()) }
  catch { return '二进制产物，请前往交付页下载查看。' }
} })
const selectedContent = computed(()=> props.live ? { original: '', modified: liveContent.value ?? '' } : contentForFile(selected.value, {original:props.original, modified:props.modified}))
</script>

<template>
  <section class="af-inspector" data-region="artifact">
    <header class="af-inspector-head">
      <div>
        <h3>产物检查</h3>
        <select v-if="!live" v-model="role" class="af-role-filter" aria-label="产物角色筛选"><option value="all">全部角色</option><option value="frontend">Frontend</option><option value="backend">Backend</option></select><span v-else>候选文件内容</span>
      </div>
      <div class="af-mode">
        <button type="button" :data-active="mode === 'diff'" @click="mode = 'diff'">{{ live ? '文件内容' : '差异' }}</button>
        <button type="button" :data-active="mode === 'log'" @click="mode = 'log'">终端</button>
        <button v-if="!live" type="button" :data-active="mode === 'preview'" @click="mode = 'preview'">预览</button>
      </div>
    </header>
    <div class="af-inspector-body">
      <ul class="af-file-list" aria-label="变更文件">
        <li v-for="file in filteredFiles" :key="file.id">
          <button
            type="button"
            :data-active="file.id === selected?.id"
            @click="selectedId = file.id"
          >
            <span class="af-truncate">{{ file.name }}</span>
            <em>+{{ file.additions }}</em>
          </button>
        </li>
      </ul>
      <div class="af-inspector-surface">
        <p v-if="live && !selected">当前任务尚无可查看的候选文件。</p>
        <p v-else-if="live && error" role="alert">{{ error.message }}</p>
        <p v-else-if="live && loading && mode === 'diff'">正在读取候选文件…</p>
        <p v-else-if="live && mode === 'log'">日志需由后端提供有效日志 ID，当前尚无日志记录。</p>
        <CodeDiff
          v-else-if="mode === 'diff'"
          :original="selectedContent.original"
          :modified="selectedContent.modified"
          :language="selected?.language"
        />
        <ReadOnlyLog v-else-if="mode === 'log'" />
        <div v-else class="af-order-preview"><h3>订单列表</h3><div><span>状态：已付款</span><button type="button" @click="fullOpen=true">查看导出实现</button></div><table><thead><tr><th>订单号</th><th>金额</th><th>状态</th></tr></thead><tbody><tr><td>ORD-1024</td><td>¥ 299.00</td><td>已付款</td></tr><tr><td>ORD-1025</td><td>¥ 158.00</td><td>已付款</td></tr></tbody></table></div>
      </div>
    </div>
    <footer class="af-inspector-footer">
      <span>{{ live ? '文件内容来自后端候选产物，基线差异尚未提供' : '等待审查者验证' }}</span>
      <div class="af-footer-actions">
        <AfButton @click="fullOpen=true">查看完整差异</AfButton>
        <AfButton tone="primary" disabled title="等待审查者验证后可合并">合并变更</AfButton>
      </div>
    </footer>
    <AfDialog v-model:open="fullOpen" :title="selected?.name ?? '代码变更'" description="当前所选文件的完整差异。"><div class="af-full-diff"><CodeDiff :original="selectedContent.original" :modified="selectedContent.modified" :language="selected?.language"/></div></AfDialog>
  </section>
</template>

<style scoped>
.af-inspector {
  display: grid;
  grid-template-rows: 66px minmax(0, 1fr) 64px;
  min-height: 0;
  flex: 1;
  border-top: 1px solid var(--af-border);
  background: var(--af-surface);
}
.af-inspector-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 16px;
  border-bottom: 1px solid var(--af-border);
}
.af-inspector-head h3 {
  margin: 0 0 4px;
  font-size: 16px;
  font-weight: 650;
}
.af-role-filter {
  height: 28px;
  padding: 0 8px;
  border: 1px solid var(--af-border);
  border-radius: 8px;
  background: var(--af-surface);
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: var(--af-text-secondary);
}
.af-mode {
  display: inline-flex;
  padding: 3px;
  border-radius: 8px;
  background: var(--af-surface-subtle);
  gap: 2px;
}
.af-mode button {
  height: 30px;
  padding: 0 12px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--af-text-secondary);
  font-size: 14px;
}
.af-mode button[data-active='true'] {
  background: var(--af-surface);
  color: var(--af-text);
}
.af-inspector-body {
  display: grid;
  grid-template-columns: 290px minmax(0, 1fr);
  min-height: 0;
}
.af-file-list {
  margin: 0;
  padding: 8px;
  list-style: none;
  border-right: 1px solid var(--af-border);
  overflow: auto;
}
.af-file-list button {
  width: 100%;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 0 10px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  text-align: left;
}
.af-file-list button[data-active='true'] { background: var(--af-selected); }
.af-file-list em {
  font-style: normal;
  font-size: 14px;
  color: var(--af-success);
  flex-shrink: 0;
}
.af-inspector-surface {
  min-width: 0;
  min-height: 0;
  padding: 8px;
  background: #f7f5fa;
}
.af-inspector-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 16px;
  border-top: 1px solid var(--af-border);
  color: var(--af-text-secondary);
  font-size: 14px;
}
.af-footer-actions { display: flex; gap: 8px; }
.af-preview-empty {
  height: 100%;
  min-height: 200px;
  display: grid;
  place-items: center;
  border-radius: 8px;
  background: var(--af-surface);
  border: 1px dashed var(--af-border);
  color: var(--af-text-secondary);
  font-size: 14px;
}
.af-full-diff { height:60dvh; min-height:320px; }.af-order-preview { height:100%; overflow:auto; padding:20px; background:var(--af-surface); border:1px solid var(--af-border); border-radius:8px; }.af-order-preview h3 { margin:0 0 16px; }.af-order-preview>div { display:flex; justify-content:space-between; gap:12px; font-size:14px; }.af-order-preview button { border:1px solid var(--af-border); border-radius:6px; padding:6px; background:var(--af-selected); }.af-order-preview table { width:100%; margin-top:20px; font-size:14px; border-collapse:collapse; }.af-order-preview th,.af-order-preview td { text-align:left; padding:14px 8px; border-bottom:1px solid var(--af-border); }
@media(max-width:1450px) { .af-inspector-body { grid-template-columns:180px minmax(0,1fr); } }
@media(max-width:639px) { .af-inspector-body { grid-template-columns:minmax(0,1fr); grid-template-rows:auto minmax(240px,1fr); }.af-file-list { display:flex; border-right:0; border-bottom:1px solid var(--af-border); }.af-file-list li { flex-shrink:0; }.af-inspector { grid-template-rows:100px minmax(0,1fr) auto; }.af-inspector-head { align-items:center; flex-wrap:wrap; gap:8px; padding:10px 12px; }.af-inspector-footer { flex-wrap:wrap; padding:12px; }.af-inspector-footer>span { width:100%; }.af-footer-actions { margin-left:auto; }.af-mode button { min-height:36px; padding-inline:10px; } }
</style>
