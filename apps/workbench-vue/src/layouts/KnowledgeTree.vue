<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { FileText, Folder } from 'lucide-vue-next'
import { useWorkspaceRepository } from '@/app/runtime'

import { useLibraryStore } from '@/stores/library'
const local = useLibraryStore()
const route = useRoute()
const repository = useWorkspaceRepository()
const { data } = useQuery({
  queryKey: ['knowledge-tree'],
  queryFn: ({ signal }) => repository.loadKnowledge(signal),
})

const groups = computed(() => {
  const items = [...local.documents, ...(data.value ?? [])]
  const specs = items.filter(item =>
    item.name.includes('规范') || item.name.includes('约定') || item.name.includes('字典'),
  )
  const refs = items.filter(item => !specs.some(spec => spec.id === item.id))
  return [
    { id: 'specs', label: '项目规范', items: specs },
    { id: 'refs', label: '参考资料', items: refs },
  ]
})
</script>

<template>
  <aside class="af-knowledge-tree" data-region="tree" aria-label="知识目录">
    <h2>知识库</h2>
    <nav>
      <div v-for="group in groups" :key="group.id" class="af-tree-group">
        <p class="af-group-label">
          <Folder :size="14" :stroke-width="1.75" />
          {{ group.label }}
        </p>
        <RouterLink
          v-for="item in group.items"
          :key="item.id"
          :to="`/knowledge/${item.id}`"
          class="af-tree-item"
          :data-active="route.params.documentId === item.id"
        >
          <FileText :size="16" :stroke-width="1.75" aria-hidden="true" />
          <span class="af-truncate">{{ item.name }}</span>
        </RouterLink>
      </div>
    </nav>
  </aside>
</template>

<style scoped>
.af-knowledge-tree {
  flex: 0 0 260px;
  border-right: 1px solid var(--af-border);
  background: var(--af-surface);
  overflow: auto;
  padding: 16px 12px;
  min-height: 0;
}
h2 {
  margin: 0 0 14px;
  font-size: 16px;
  font-weight: 650;
}
nav { display: grid; gap: 14px; }
.af-group-label {
  margin: 0 0 6px;
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--af-text-muted);
  font-size: 12px;
}
.af-tree-item {
  height: 36px;
  padding: 0 10px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--af-text-secondary);
  font-size: 14px;
  margin-bottom: 2px;
}
.af-tree-item[data-active='true'] {
  background: var(--af-selected);
  color: var(--af-accent-ink);
}
</style>
