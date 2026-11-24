<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useRoute, useRouter } from 'vue-router'
import { MoreHorizontal, Plus } from 'lucide-vue-next'
import DocumentPreview from '@/features/knowledge/DocumentPreview.vue'
import { useLibraryStore } from '@/stores/library'
import { useFeedbackStore } from '@/stores/feedback'
import AfButton from '@/components/af/AfButton.vue'
import AfErrorState from '@/components/af/AfErrorState.vue'
import { useWorkspaceRepository } from '@/app/runtime'
import { useVisualReady } from '@/composables/useVisualReady'

const route = useRoute()
const repository = useWorkspaceRepository()
const documentId = computed(() => String(route.params.documentId))
const local = useLibraryStore()
const feedback = useFeedbackStore()
const router = useRouter()
const editing = ref(false)
const source = ref('')
const article = ref<HTMLElement>()
const activeHeading = ref(0)
const markdown = computed(() => local.documentEdits[documentId.value] ?? local.documents.find(x=>x.id===documentId.value)?.markdown ?? data.value?.markdown ?? '')

const saving = ref(false)
async function save() {
  if (saving.value) return
  saving.value = true
  try {
    if (repository.updateKnowledgeDocument) {
      await repository.updateKnowledgeDocument(documentId.value, source.value)
      await refetch()
      editing.value = false
      feedback.notify('文档已保存到后端', 'success')
      return
    }
    local.documentEdits[documentId.value]=source.value
    editing.value=false
    feedback.notify('文档修改已保存在此浏览器')
  } catch (error) {
    feedback.notify(error instanceof Error ? error.message : '保存失败', 'error')
  } finally {
    saving.value = false
  }
}
function exportDocument() { const blob=new Blob([markdown.value],{type:'text/markdown;charset=utf-8'}); const url=URL.createObjectURL(blob); const a=document.createElement('a'); a.href=url; a.download=data.value?.name ?? 'document.md'; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000) }
function jump(index:number) { activeHeading.value=index; article.value?.querySelectorAll('.af-prose h1,.af-prose h2,.af-prose h3')[index]?.scrollIntoView({behavior:'smooth',block:'start'}) }

const { data, isPending, isError, error, refetch, isSuccess } = useQuery({
  queryKey: computed(() => ['document', documentId.value]),
  queryFn: ({ signal }) => repository.loadDocument(documentId.value, signal),
})

watch(markdown, value=> { if(!editing.value) source.value=value }, { immediate:true })

useVisualReady(computed(() => isSuccess.value && Boolean(data.value)))

const toc = computed(() => [...markdown.value.matchAll(/^#{1,3}\s+(.+)$/gm)].map(match=>match[1]!))
</script>

<template>
  <div class="af-document-layout" data-page-id="10-knowledge-document" data-region="content">
    <AfErrorState v-if="isError" :message="error?.message" class="af-page" @retry="refetch()" />
    <div v-else-if="isPending" class="af-page">加载文档…</div>
    <template v-else-if="data">
      <article ref="article" class="af-article" data-region="article">
        <header class="af-doc-header">
          <div>
            <h1>{{ data.name.replace(/\.md$/, '') }}</h1>
            <div class="af-meta">
              <span>{{ data.type }}</span>
              <span>{{ data.scope }}</span>
              <span>最近更新 {{ data.updated }}</span>
            </div>
          </div>
          <div class="af-doc-actions">
            <AfButton @click="editing = !editing; source = markdown">{{editing ? '取消编辑' : '编辑'}}</AfButton>
            <AfButton @click="router.push({path:'/tasks/new',query:{attachment:data.name}})">
              <Plus :size="16" :stroke-width="1.75" />
              添加到任务
            </AfButton>
            <button type="button" class="af-more" aria-label="导出 Markdown" title="导出 Markdown" @click="exportDocument">
              <MoreHorizontal :size="18" :stroke-width="1.75" />
            </button>
          </div>
        </header>
        <div v-if="editing" class="af-document-editor"><label for="document-source">Markdown 正文</label><textarea id="document-source" v-model="source" rows="24"/><AfButton tone="primary" :pending="saving" @click="save">保存文档</AfButton></div><DocumentPreview v-else :markdown="markdown" />
      </article>

      <aside class="af-doc-aside" data-region="document-meta">
        <section>
          <h2>文档信息</h2>
          <dl>
            <div><dt>来源</dt><dd>项目文件</dd></div>
            <div><dt>关联项目</dt><dd>{{ data.scope }}</dd></div>
            <div><dt>可见性</dt><dd>当前工作区</dd></div>
          </dl>
        </section>
        <section>
          <h2>引用此文档</h2>
          <p class="af-ref">任务：实现订单导出功能</p>
          <div class="af-chips">
            <span>Frontend</span>
            <span>Backend</span>
          </div>
          <p class="af-note">在创建任务时引用此文档，让执行者遵循同一份约定。</p>
        </section>
        <section>
          <h2>版本记录</h2>
          <p class="af-note">当前版本 · 今天更新</p>
        </section>
        <section>
          <h2>目录</h2>
          <ul class="af-toc">
            <li v-for="(item, index) in toc" :key="item" :data-active="index === activeHeading"><button type="button" @click="jump(index)">{{ item }}</button></li>
          </ul>
        </section>
      </aside>
    </template>
  </div>
</template>

<style scoped>
.af-document-layout {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  overflow: hidden;
  background: var(--af-bg);
}
.af-article {
  min-width: 0;
  min-height: 0;
  overflow: auto;
  background: var(--af-surface);
  border-right: 1px solid var(--af-border);
}
.af-doc-header {
  position: sticky;
  top: 0;
  z-index: 1;
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 24px 32px 16px;
  background: color-mix(in srgb, var(--af-surface) 92%, transparent);
  backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--af-border);
}
.af-doc-header h1 {
  margin: 0;
  font-size: 32px;
  line-height: 44px;
  font-weight: 700;
}
.af-meta {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.af-meta span {
  height: 28px;
  padding: 0 10px;
  border-radius: 8px;
  border: 1px solid var(--af-border);
  display: inline-flex;
  align-items: center;
  font-size: 13px;
  color: var(--af-text-secondary);
  background: var(--af-surface);
}
.af-doc-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.af-more {
  width: 40px;
  height: 40px;
  border: 1px solid var(--af-border);
  border-radius: 8px;
  background: var(--af-surface);
  color: var(--af-text-secondary);
  display: grid;
  place-items: center;
}
.af-article :deep(.af-doc-preview) { padding: 24px 32px 48px; }
.af-doc-aside {
  min-width: 0;
  min-height: 0;
  overflow: auto;
  padding: 24px;
  background: var(--af-surface);
  display: grid;
  gap: 28px;
  align-content: start;
}
.af-doc-aside h2 {
  margin: 0 0 12px;
  font-size: 15px;
  font-weight: 650;
}
.af-doc-aside dl {
  margin: 0;
  display: grid;
  gap: 12px;
}
.af-doc-aside dt {
  margin-bottom: 4px;
  font-size: 12px;
  color: var(--af-text-muted);
}
.af-doc-aside dd { margin: 0; font-size: 15px; }
.af-ref { margin: 0 0 8px; font-size: 14px; }
.af-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.af-chips span {
  height: 28px;
  padding: 0 10px;
  border-radius: 8px;
  border: 1px solid var(--af-border);
  display: inline-flex;
  align-items: center;
  font-size: 12px;
  color: var(--af-text-secondary);
}
.af-note { margin: 10px 0 0; color: var(--af-text-muted); font-size: 13px; line-height: 20px; }
.af-toc {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 4px;
}
.af-toc li {
  min-height: 36px;
  padding: 8px 10px;
  border-radius: 8px;
  color: var(--af-text-secondary);
  font-size: 14px;
}
.af-toc li[data-active='true'] {
  background: var(--af-selected);
  color: var(--af-accent-ink);
  font-weight: 500;
}

@media (max-width: 1439px) {
  .af-document-layout { grid-template-columns: 1fr; }
  .af-doc-aside { border-top: 1px solid var(--af-border); }
}
.af-document-editor { padding:24px 32px; display:grid; gap:16px; }
.af-document-editor textarea { width:100%; padding:16px; border:1px solid var(--af-border); border-radius:8px; background:var(--af-surface); color:var(--af-text); font:15px/1.8 monospace; resize:vertical; }
.af-toc li { padding:0; } .af-toc button { text-align:left; width:100%; background:transparent; border:0; padding:10px; color:inherit; }
@media(max-width:1439px) { .af-document-layout { overflow:auto; display:block; } .af-article { overflow:visible; } .af-doc-aside { grid-template-columns:1fr 1fr; } }
@media(max-width:767px) { .af-doc-header { position:static; flex-direction:column; padding:20px 16px; } .af-doc-header h1 { font-size:26px; } .af-doc-actions { flex-wrap:wrap; } .af-article :deep(.af-doc-preview) { padding:20px 16px; } .af-doc-aside { grid-template-columns:1fr; padding:20px 16px; } .af-document-editor { padding:20px 16px; } }
</style>
