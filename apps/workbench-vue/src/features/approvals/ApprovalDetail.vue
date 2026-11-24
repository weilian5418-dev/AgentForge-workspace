<script setup lang="ts">
import { useFeedbackStore } from '@/stores/feedback'
import { Copy, Info, ShieldAlert, X } from 'lucide-vue-next'
import type { ApprovalViewModel } from '@/domain/view-models'
import AfButton from '@/components/af/AfButton.vue'
import AfStatusBadge from '@/components/af/AfStatusBadge.vue'

defineProps<{
  approval: ApprovalViewModel
  taskGoal?: string
  resolved?: boolean
}>()
defineEmits<{ allow: []; deny: []; close: []; back: [] }>()

async function copyCommand(command: string) {
  try {
    await navigator.clipboard.writeText(command)
    useFeedbackStore().notify('命令已复制')
  } catch {
    useFeedbackStore().notify('无法复制，请手动选择命令','error')
  }
}
</script>

<template>
  <section class="af-approval-detail" data-region="approval-detail">
    <header class="af-detail-head">
      <div class="af-detail-title">
        <span class="af-shield" aria-hidden="true">
          <ShieldAlert :size="22" :stroke-width="1.75" />
        </span>
        <div>
          <div class="af-title-row">
            <h2>批准{{ approval.title }}</h2>
            <AfStatusBadge status="approval" :label="resolved ? '已处理' : '等待决定'" />
          </div>
          <p class="af-path">任务 / {{ taskGoal ?? '实现订单导出功能' }}</p>
        </div>
      </div>
      <div class="af-head-actions">
        
        <button type="button" aria-label="关闭" @click="$emit('close')"><X :size="18" :stroke-width="1.75" /></button>
      </div>
    </header>

    <div class="af-detail-body">
      <div class="af-field">
        <span class="af-label">执行者</span>
        <span class="af-executor">Frontend / Codex CLI</span>
      </div>
      <div class="af-field">
        <span class="af-label">请求原因</span>
        <p>{{ approval.reason }}</p>
      </div>
      <div class="af-field">
        <span class="af-label">将执行的命令</span>
        <div class="af-command">
          <code class="af-mono">{{ approval.command }}</code>
          <button type="button" aria-label="复制命令" @click="copyCommand(approval.command)">
            <Copy :size="16" :stroke-width="1.75" />
          </button>
        </div>
      </div>
      <dl class="af-meta-grid">
        <div>
          <dt>作用范围</dt>
          <dd>{{ approval.scope }}</dd>
        </div>
        <div>
          <dt>执行目录</dt>
          <dd class="af-mono">{{ approval.directory }}</dd>
        </div>
        <div>
          <dt>授权有效期</dt>
          <dd>{{ approval.validity }}</dd>
        </div>
        <div>
          <dt>请求时间</dt>
          <dd>{{ approval.time }}</dd>
        </div>
      </dl>
      <details class="af-params">
        <summary>参数与执行记录</summary>
        <p>授权仅在当前任务内生效。执行前会核对命令和作用目录。</p>
      </details>
    </div>

    <footer class="af-detail-foot">
      <p>
        <Info :size="16" :stroke-width="1.75" />
        批准后继续当前任务；此授权仅用于上面的操作。
      </p>
      <div class="af-foot-actions">
        <button type="button" class="af-back" @click="$emit('back')">← 返回任务</button>
        <AfButton v-if="!resolved" @click="$emit('deny')">拒绝</AfButton>
        <AfButton v-if="!resolved" tone="primary" @click="$emit('allow')">允许一次</AfButton>
      </div>
    </footer>
  </section>
</template>

<style scoped>
.af-approval-detail {
  height: 100%;
  min-height: 0;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  background: var(--af-surface);
}
.af-detail-head {
  min-height: 108px;
  padding: 20px 24px;
  border-bottom: 1px solid var(--af-border);
  display: flex;
  justify-content: space-between;
  gap: 16px;
}
.af-detail-title { display: flex; gap: 14px; min-width: 0; }
.af-shield {
  width: 44px;
  height: 44px;
  border-radius: 10px;
  background: var(--af-attention-bg);
  color: var(--af-attention);
  display: grid;
  place-items: center;
  flex-shrink: 0;
}
.af-title-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}
.af-title-row h2 {
  margin: 0;
  font-size: 22px;
  line-height: 30px;
  font-weight: 650;
}
.af-path {
  margin: 6px 0 0;
  color: var(--af-text-secondary);
  font-size: 14px;
}
.af-head-actions { display: flex; gap: 4px; }
.af-head-actions button {
  width: 36px;
  height: 36px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--af-text-secondary);
  display: grid;
  place-items: center;
}
.af-detail-body {
  min-height: 0;
  overflow: auto;
  padding: 24px;
  display: grid;
  gap: 24px;
  align-content: start;
}
.af-field { display: grid; gap: 8px; }
.af-label { font-size: 13px; color: var(--af-text-muted); }
.af-field p { margin: 0; font-size: 16px; line-height: 24px; }
.af-executor {
  width: fit-content;
  height: 32px;
  padding: 0 12px;
  border-radius: 8px;
  border: 1px solid var(--af-border);
  display: inline-flex;
  align-items: center;
  font-size: 14px;
  background: var(--af-surface-subtle);
}
.af-command {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  border-radius: 10px;
  background: var(--af-code);
  color: #f5f2fa;
}
.af-command code { flex: 1; font-size: 15px; }
.af-command button {
  width: 32px;
  height: 32px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #d7d0e4;
  display: grid;
  place-items: center;
}
.af-meta-grid {
  margin: 0;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 18px 24px;
}
.af-meta-grid dt {
  margin-bottom: 6px;
  font-size: 13px;
  color: var(--af-text-muted);
}
.af-meta-grid dd { margin: 0; font-size: 16px; }
.af-params summary {
  cursor: pointer;
  color: var(--af-text-secondary);
  font-size: 14px;
}
.af-params p {
  margin: 10px 0 0;
  color: var(--af-text-muted);
  font-size: 14px;
}
.af-detail-foot {
  padding: 16px 24px;
  border-top: 1px solid var(--af-border);
  background: var(--af-selected);
  display: grid;
  gap: 14px;
}
.af-detail-foot > p {
  margin: 0;
  display: flex;
  align-items: flex-start;
  gap: 8px;
  color: var(--af-accent-ink);
  font-size: 13px;
  line-height: 20px;
}
.af-foot-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}
.af-back {
  margin-right: auto;
  border: 0;
  background: transparent;
  color: var(--af-accent-ink);
  font-size: 14px;
}
</style>
