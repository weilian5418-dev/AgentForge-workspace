<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useRouter } from 'vue-router'
import { Check, Plus, Search } from 'lucide-vue-next'
import AfDialog from '@/components/af/AfDialog.vue'
import { useLibraryStore } from '@/stores/library'
import { useFeedbackStore } from '@/stores/feedback'
import { useQueryClient } from '@tanstack/vue-query'
import AfButton from '@/components/af/AfButton.vue'
import AfErrorState from '@/components/af/AfErrorState.vue'
import DocumentPreview from '@/features/knowledge/DocumentPreview.vue'
import { useWorkspaceRepository, shouldUseReferenceFromUrl } from '@/app/runtime'
import { useVisualReady } from '@/composables/useVisualReady'
import { documentMarkdown } from '@/fixtures/shared'

const router = useRouter()
const repository = useWorkspaceRepository()
const isReference = shouldUseReferenceFromUrl(window.location.search)
const tab = ref('all')
const query = ref('')
const selectedId = ref('demo-api-contract')
const local = useLibraryStore()
const feedback = useFeedbackStore()
const queryClient = useQueryClient()
const createOpen = ref(false)
const docName = ref('')
const docBody = ref('')
const fileInput = ref<HTMLInputElement>()
const allDocuments = computed(() => [...local.documents, ...(data.value ?? [])])
const selectedMarkdown = computed(() => {
 const item = selected.value
 if(!item) return ''
 return local.documentEdits[item.id] ?? local.documents.find(x=>x.id===item.id)?.markdown ?? (item.id==='demo-api-contract' ? documentMarkdown : `# ${item.name.replace(/\.[^.]+$/, '')}\n\n## 项目说明\n\n${item.scope}的共享资料，供执行者在任务中引用。\n\n## 使用约定\n\n- 开始任务前阅读相关规范。\n- 修改应包含清晰的说明与验证记录。\n- 对外接口变更需同步更新项目文档。`)
})
async function addDocument() {
 if(!docName.value.trim() || !docBody.value.trim()) return
 if (!isReference && repository.createKnowledgeDocument) {
  try {
   const created = await repository.createKnowledgeDocument({
     name: docName.value.trim().replace(/(?:\.md)?$/, '.md'),
     content: docBody.value,
     contentType: 'text/markdown',
   }) as { id?: string }
   selectedId.value = created.id ?? selectedId.value
   createOpen.value=false; docName.value=''; docBody.value=''; tab.value='all'; query.value=''
   await queryClient.invalidateQueries({queryKey:['knowledge']})
   feedback.notify('文档已保存到后端知识库', 'success')
  } catch (error) {
   feedback.notify(error instanceof Error ? error.message : '保存失败', 'error')
  }
  return
 }
 const id = `local-${crypto.randomUUID()}`
 local.documents.unshift({id, name:docName.value.trim().replace(/(?:\.md)?$/, '.md'), markdown:docBody.value, type:'Markdown', scope:'当前工作区', updated:'刚刚', ready:true})
 selectedId.value=id; createOpen.value=false; docName.value=''; docBody.value=''; tab.value='all'; query.value=''
 void queryClient.invalidateQueries({queryKey:['knowledge']})
 feedback.notify('文档已保存到此浏览器')
}
async function importFile(event:Event) {
 const files = (event.target as HTMLInputElement).files
 if(!files?.length) return
 for(const file of Array.from(files)) {
  if(!/\.(md|txt|json|ya?ml)$/i.test(file.name)) { feedback.notify('请选择 Markdown、文本或接口说明文件','info'); continue }
  if(file.size>2*1024*1024) { feedback.notify('请选择小于 2 MB 的文档','info'); continue }
  const text = await file.text()
  if (!isReference && repository.createKnowledgeDocument) {
   try {
    const created = await repository.createKnowledgeDocument({ name: file.name, content: text }) as { id?: string }
    if (created.id) selectedId.value = created.id
   } catch (error) {
    feedback.notify(error instanceof Error ? error.message : `导入 ${file.name} 失败`, 'error')
   }
   continue
  }
  const id=`local-${crypto.randomUUID()}`
  local.documents.unshift({id,name:file.name,markdown:text,type:'Markdown',scope:'当前工作区',updated:'刚刚',ready:true}); selectedId.value=id
 }
 (event.target as HTMLInputElement).value=''
 if (!isReference) await queryClient.invalidateQueries({queryKey:['knowledge']})
 feedback.notify(isReference ? '资料已导入本地知识库' : '资料已导入后端知识库')
}
function addToTask() { void router.push({path:'/tasks/new',query:{attachment:selected.value?.name}}) }

const { data, isPending, isError, error, refetch, isSuccess } = useQuery({
  queryKey: ['knowledge'],
  queryFn: ({ signal }) => repository.loadKnowledge(signal),
})

watch(data, value => {
  if (value?.[0] && !value.find(item => item.id === selectedId.value)) {
    selectedId.value = value[0].id
  }
}, { immediate: true })

const filtered = computed(() => {
  const rows = allDocuments.value
  const byTab = rows.filter(item => {
    if (tab.value === 'all') return true
    if (tab.value === 'spec') return item.scope.includes('工作区') || item.name.includes('规范') || item.name.includes('约定')
    if (tab.value === 'api') return item.type === 'OpenAPI' || item.name.includes('接口')
    return item.type === 'PDF' || item.scope.includes('任务')
  })
  const q = query.value.trim()
  if (!q) return byTab
  return byTab.filter(item => item.name.includes(q))
})

const selected = computed(() => allDocuments.value.find(item => item.id === selectedId.value) ?? null)
useVisualReady(computed(() => isSuccess.value && Boolean(data.value)))
</script>

<template>
  <main class="af-page af-knowledge-page" data-page-id="09-knowledge-library" data-region="content">
    <header class="af-knowledge-head">
      <div>
        <h1>让 Agent 理解你的项目</h1>
        <p>集中维护规范、接口约定和可复用的项目资料</p>
      </div>
      <div class="af-actions">
        <AfButton @click="createOpen = true">新建文档</AfButton>
        <AfButton tone="primary" @click="fileInput?.click()">
          <Plus :size="18" :stroke-width="1.75" />
          导入资料
        </AfButton>
      </div>
    </header>

    <input ref="fileInput" type="file" hidden multiple accept=".md,.txt,.json,.yaml,.yml" @change="importFile" />
    <div class="af-toolbar">
      <label class="af-search">
        <Search :size="16" :stroke-width="1.75" aria-hidden="true" />
        <input v-model="query" type="search" placeholder="搜索文档或片段…" aria-label="搜索知识库" />
      </label>
      <div class="af-cats" role="tablist">
        <button type="button" role="tab" :data-active="tab === 'all'" @click="tab = 'all'">全部</button>
        <button type="button" role="tab" :data-active="tab === 'spec'" @click="tab = 'spec'">项目规范</button>
        <button type="button" role="tab" :data-active="tab === 'api'" @click="tab = 'api'">接口文档</button>
        <button type="button" role="tab" :data-active="tab === 'ref'" @click="tab = 'ref'">参考资料</button>
      </div>
    </div>

    <AfErrorState v-if="isError" :message="error?.message" @retry="refetch()" />
    <div v-else-if="isPending">加载知识库…</div>
    <div v-else class="af-knowledge">
      <div class="af-table-wrap">
        <table>
          <thead>
            <tr>
              <th>名称</th>
              <th>类型</th>
              <th>范围</th>
              <th>更新</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in filtered"
              :key="item.id"
              :data-active="item.id === selectedId"
              tabindex="0"
              @keydown.enter="selectedId = item.id"
              @click="selectedId = item.id"
            >
              <td><strong>{{ item.name }}</strong></td>
              <td>{{ item.type }}</td>
              <td>{{ item.scope }}</td>
              <td>{{ item.updated }}</td>
              <td>
                <span class="af-status" :data-ready="item.ready">
                  {{ item.ready ? '可引用' : '处理中' }}
                </span>
              </td>
            </tr>
          <tr v-if="!filtered.length"><td colspan="5">没有找到资料，试试其他关键词或导入文档。</td></tr></tbody>
        </table>
      </div>

      <aside v-if="selected" class="af-preview">
        <header>
          <div>
            <h2>{{ selected.name }}</h2>
            <p>{{ selected.type }} · 更新 {{ selected.updated }}</p>
          </div>
        </header>
        <DocumentPreview :markdown="selectedMarkdown" />
        <div class="af-preview-meta">
          <h3>引用范围</h3>
          <div class="af-chips">
            <span>{{ selected.scope }}</span>
            <span>Frontend</span>
            <span>Backend</span>
          </div>
          <h3>被这些任务引用</h3>
          <ul>
            <li><Check :size="14" :stroke-width="2" /> 实现订单导出功能</li>
            <li><Check :size="14" :stroke-width="2" /> 修复支付回调</li>
          </ul>
          <p class="af-source">来源：项目文件</p>
          <div class="af-preview-actions">
            <AfButton tone="primary" @click="router.push(`/knowledge/${selected.id}`)">打开文档</AfButton>
            <AfButton @click="addToTask">
              + 添加到任务
            </AfButton>
          </div>
          
        </div>
      </aside>
    </div>
    <AfDialog v-model:open="createOpen" title="新建文档" description="保存为 Markdown，可在创建任务时引用。"><form class="af-dialog-form" @submit.prevent="addDocument"><label>文档名称<input v-model="docName" required placeholder="例如：前端开发规范" /></label><label>正文<textarea v-model="docBody" rows="10" required placeholder="# 文档标题" /></label><div class="af-dialog-actions"><AfButton @click="createOpen=false">取消</AfButton><AfButton tone="primary" type="submit">保存文档</AfButton></div></form></AfDialog>
  </main>
</template>

<style scoped>
.af-knowledge-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 24px;
}
.af-knowledge-head h1 {
  margin: 0;
  font-size: 32px;
  line-height: 42px;
  font-weight: 700;
}
.af-knowledge-head p {
  margin: 8px 0 0;
  color: var(--af-text-secondary);
  font-size: 16px;
  line-height: 26px;
  max-width: 560px;
}
.af-actions { display: flex; gap: 8px; flex-shrink: 0; }
.af-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  margin-bottom: 20px;
}
.af-search {
  height: 40px;
  min-width: 260px;
  padding: 0 12px;
  border: 1px solid var(--af-border);
  border-radius: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--af-surface);
  color: var(--af-text-muted);
}
.af-search input {
  border: 0;
  outline: none;
  width: 100%;
  background: transparent;
  font-size: 18px;
}
.af-cats {
  display: inline-flex;
  gap: 4px;
  padding: 3px;
  border-radius: 8px;
  background: var(--af-surface-subtle);
}
.af-cats button {
  height: 34px;
  padding: 0 12px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--af-text-secondary);
  font-size: 18px;
}
.af-cats button[data-active='true'] {
  background: var(--af-surface);
  color: var(--af-accent-ink);
  box-shadow: 0 0 0 1px var(--af-accent);
}
.af-knowledge {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 568px;
  gap: 28px;
}
.af-table-wrap {
  border: 1px solid var(--af-border);
  border-radius: 10px;
  overflow: auto;
  background: var(--af-surface);
  align-self: start;
}
table { width: 100%; border-collapse: collapse; }
th, td {
  height: 72px;
  padding: 0 18px;
  border-bottom: 1px solid var(--af-border);
  text-align: left;
  font-size: 18px;
}
th {
  height: 60px;
  background: var(--af-surface-subtle);
  color: var(--af-text-secondary);
  font-weight: 500;
}
tr { cursor: pointer; }
tr[data-active='true'] { background: var(--af-selected); }
strong { font-weight: 600; }
.af-status {
  height: 28px;
  padding: 0 10px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  font-size: 16px;
  background: var(--af-attention-bg);
  color: var(--af-attention);
}
.af-status[data-ready='true'] {
  background: var(--af-success-bg);
  color: var(--af-success);
}
.af-preview {
  border: 1px solid var(--af-border);
  border-radius: 10px;
  background: var(--af-surface);
  min-height: 560px;
  overflow: auto;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
}
.af-preview > header {
  padding: 20px 24px 0;
}
.af-preview > header h2 { margin: 0; font-size: 20px; }
.af-preview > header p { margin: 6px 0 0; color: var(--af-text-secondary); font-size: 16px; }
.af-preview :deep(.af-doc-preview) { padding-top: 12px; }
.af-preview :deep(.af-doc-preview > h2) { display: none; }
.af-preview-meta {
  padding: 0 24px 24px;
  border-top: 1px solid var(--af-border);
}
.af-preview-meta h3 {
  margin: 16px 0 8px;
  font-size: 16px;
  color: var(--af-text-muted);
  font-weight: 500;
}
.af-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.af-chips span {
  height: 28px;
  padding: 0 10px;
  border-radius: 8px;
  border: 1px solid var(--af-border);
  display: inline-flex;
  align-items: center;
  font-size: 14px;
  color: var(--af-text-secondary);
}
.af-preview-meta ul {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 8px;
}
.af-preview-meta li {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 18px;
  color: var(--af-text);
}
.af-preview-meta li :deep(svg) { color: var(--af-success); }
.af-source { margin: 14px 0 0; color: var(--af-text-muted); font-size: 14px; }
.af-preview-actions {
  margin-top: 16px;
  display: flex;
  gap: 8px;
}
.af-note { margin: 12px 0 0; color: var(--af-text-muted); font-size: 16px; }

@media (max-width: 1439px) {
  .af-knowledge { grid-template-columns: 1fr; }
}
</style>
