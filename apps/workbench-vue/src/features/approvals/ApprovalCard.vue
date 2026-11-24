<script setup lang="ts">
import { ShieldAlert } from 'lucide-vue-next'
import type { ApprovalViewModel } from '@/domain/view-models'
import AfButton from '@/components/af/AfButton.vue'

defineProps<{ approval: ApprovalViewModel }>()
defineEmits<{ allow: []; deny: []; detail: [] }>()
</script>

<template>
  <section class="af-approval-card" aria-label="待批准操作">
    <header>
      <ShieldAlert :size="24" :stroke-width="1.75" aria-hidden="true" />
      <div>
        <strong>{{ approval.title }}</strong>
        <p>{{ approval.reason }}</p>
      </div>
    </header>
    <code class="af-mono">{{ approval.command }}</code>
    <footer>
      <AfButton tone="ghost" @click="$emit('detail')">查看详情</AfButton>
      <AfButton tone="primary" @click="$emit('allow')">允许一次</AfButton>
    </footer>
  </section>
</template>

<style scoped>
.af-approval-card {
  border: 1px solid #e7c49a;
  border-radius: 10px;
  background: #fffaf2;
  padding: 10px 12px;
  display: grid;
  gap: 6px;
}
header { display: flex; gap: 8px; align-items: flex-start; }
strong { display: block; font-size: 15px; line-height: 20px; }
p { margin: 2px 0 0; color: var(--af-text-secondary); font-size: 12px; line-height: 16px; }
code {
  display: block;
  padding: 6px 10px;
  border-radius: 8px;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
  font-size: 13px;
}
footer { display: flex; justify-content: flex-end; gap: 8px; }
</style>
