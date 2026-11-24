<script setup lang="ts">
import { useRouter } from 'vue-router'
import { ArrowRight, MoreHorizontal } from 'lucide-vue-next'
import type { TaskRowViewModel } from '@/domain/view-models'
import AfStatusBadge from '@/components/af/AfStatusBadge.vue'

defineProps<{ items: readonly TaskRowViewModel[] }>()
const router = useRouter()

function openTask(id: string) {
  void router.push(`/tasks/${id}`)
}
</script>

<template>
  <div class="af-table-wrap">
    <table class="af-table">
      <thead>
        <tr>
          <th class="af-check" scope="col">
            <span class="af-sr">选择</span>
          </th>
          <th scope="col">任务目标</th>
          <th scope="col">状态</th>
          <th scope="col">执行者</th>
          <th scope="col">最近更新</th>
          <th class="af-actions" scope="col">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="item in items"
          :key="item.id"
          tabindex="0"
          @click="openTask(item.id)"
          @keydown.enter="openTask(item.id)"
        >
          <td class="af-check" @click.stop>
            <input type="checkbox" :aria-label="`选择 ${item.goal}`" />
          </td>
          <td><strong class="af-truncate">{{ item.goal }}</strong></td>
          <td><AfStatusBadge :status="item.status" :label="item.statusLabel" /></td>
          <td>
            <div class="af-harnesses">
              <span v-for="name in item.harnesses" :key="name" class="af-harness-chip">{{ name }}</span>
            </div>
          </td>
          <td class="af-updated">{{ item.updatedLabel }}</td>
          <td class="af-actions" @click.stop>
            <RouterLink class="af-open" :to="`/tasks/${item.id}`" aria-label="打开任务">
              <ArrowRight :size="18" :stroke-width="1.75" />
            </RouterLink>
            <button type="button" class="af-more" aria-label="查看交付" @click="router.push(`/tasks/${item.id}/delivery`)">
              <MoreHorizontal :size="18" :stroke-width="1.75" />
            </button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.af-table-wrap {
  border: 1px solid var(--af-border);
  border-radius: 10px;
  overflow: auto;
  background: var(--af-surface);
}
.af-table {
  width: 100%;
  border-collapse: collapse;
  min-width: 1100px;
  table-layout: fixed;
}
th, td {
  height: 64px;
  padding: 0 16px;
  border-bottom: 1px solid var(--af-border);
  text-align: left;
  vertical-align: middle;
  font-size: 18px;
}
th {
  font-size: 16px;
  font-weight: 500;
  color: var(--af-text-secondary);
  background: var(--af-surface-subtle);
}
.af-check { width: 64px; text-align: center; }
.af-actions { width: 140px; }
.af-updated { width: 200px; color: var(--af-text-secondary); }
tbody tr { cursor: pointer; }
tbody tr:hover { background: #fbf9fe; }
tbody tr:last-child td { border-bottom: 0; }
strong { font-weight: 600; display: block; }
.af-harnesses { display: flex; flex-wrap: wrap; gap: 6px; }
.af-harness-chip {
  height: 28px;
  padding: 0 10px;
  border-radius: 999px;
  border: 1px solid var(--af-border);
  display: inline-flex;
  align-items: center;
  font-size: 16px;
  color: var(--af-text-secondary);
  background: var(--af-surface);
}
.af-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}
.af-open, .af-more {
  width: 36px;
  height: 36px;
  border: 0;
  border-radius: 8px;
  display: grid;
  place-items: center;
  color: var(--af-text-secondary);
  background: transparent;
}
.af-open:hover, .af-more:hover { background: var(--af-surface-subtle); color: var(--af-text); }
.af-sr {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}
</style>
