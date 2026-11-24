<script setup lang="ts">
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const props = defineProps<{ markdown: string; title?: string }>()
const renderer = new MarkdownIt({ html: false, linkify: true, breaks: false })
const html = computed(() => DOMPurify.sanitize(renderer.render(props.markdown)))
</script>

<template>
  <article class="af-doc-preview" data-region="article">
    <h2 v-if="title">{{ title }}</h2>
    <div class="af-prose" v-html="html" />
  </article>
</template>

<style scoped>
.af-doc-preview {
  padding: 24px;
  overflow: auto;
  min-height: 0;
}
.af-doc-preview > h2 {
  margin: 0 0 16px;
  font-size: 32px;
  line-height: 44px;
}
.af-prose :deep(h2) {
  margin: 28px 0 12px;
  font-size: 22px;
  scroll-margin-top: 24px;
}
.af-prose :deep(p),
.af-prose :deep(li) {
  font-size: 18px;
  line-height: 30px;
}
.af-prose :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 16px 0;
}
.af-prose :deep(th),
.af-prose :deep(td) {
  border: 1px solid var(--af-border);
  padding: 10px 12px;
  text-align: left;
}
.af-prose :deep(code) {
  font-family: 'AF Mono', monospace;
  font-size: 16px;
}
.af-prose { overflow-wrap:anywhere; }
.af-prose :deep(h1) { font-size:28px; line-height:1.5; margin:0 0 20px; }
.af-prose :deep(h2) { border-left:3px solid var(--af-accent); padding-left:12px; }
.af-prose :deep(pre) { padding:20px; background:var(--af-code); color:#f0eaf8; border-radius:10px; overflow:auto; }
.af-prose :deep(pre code) { font-size:15px; line-height:1.8; white-space:pre; }
.af-prose :deep(blockquote) { margin:20px 0; padding:8px 20px; border-left:3px solid var(--af-accent); background:var(--af-selected); }
.af-prose :deep(th) { background:var(--af-surface-subtle); }
.af-prose :deep(a) { color:var(--af-accent-ink); text-decoration:underline; }
@media(max-width:639px) { .af-prose :deep(p),.af-prose :deep(li) { font-size:16px; line-height:28px; } .af-prose :deep(table) { display:block; overflow:auto; } }
</style>
