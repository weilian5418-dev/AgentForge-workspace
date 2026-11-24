<script setup lang="ts">
import { DialogRoot, DialogPortal, DialogOverlay, DialogContent, DialogTitle, DialogDescription, DialogClose } from 'reka-ui'
import { X } from 'lucide-vue-next'
const open = defineModel<boolean>('open', { required: true })
defineProps<{ title: string; description?: string }>()
</script>

<template>
  <DialogRoot v-model:open="open">
    <DialogPortal>
      <DialogOverlay class="af-dialog-overlay" />
      <DialogContent class="af-dialog-content">
        <header class="af-dialog-head">
          <DialogTitle class="af-dialog-title">{{ title }}</DialogTitle>
          <DialogClose class="af-dialog-close" aria-label="关闭弹窗"><X :size="20" /></DialogClose>
        </header>
        <DialogDescription class="af-dialog-description">{{ description || '调整选项后确认，或关闭返回页面。' }}</DialogDescription>
        <slot />
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>

<style>
.af-dialog-overlay { position: fixed; inset: 0; z-index: 90; background: rgb(26 22 39 / 28%); backdrop-filter: blur(3px); }
.af-dialog-content { position: fixed; z-index: 91; left: 50%; top: 50%; transform: translate(-50%, -50%); width: min(560px, calc(100vw - 32px)); max-height: calc(100dvh - 48px); overflow: auto; border: 1px solid var(--af-border); border-radius: 16px; background: var(--af-surface); color: var(--af-text); padding: 24px; box-shadow: 0 24px 90px rgb(30 24 50 / 16%); }
.af-dialog-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.af-dialog-title { margin: 0; font-size: 22px; font-weight: 650; }
.af-dialog-description { margin: 8px 0 24px; font-size: 15px; color: var(--af-text-secondary); }
.af-dialog-close { width: 44px; height: 44px; flex-shrink: 0; border: 0; background: var(--af-surface-subtle); border-radius: 8px; color: var(--af-text-secondary); display: grid; place-items: center; }
.af-dialog-form { display: grid; gap: 18px; }
.af-dialog-form label { display: grid; gap: 8px; font-size: 16px; }
.af-dialog-form input, .af-dialog-form select, .af-dialog-form textarea { width: 100%; min-width: 0; min-height: 46px; padding: 10px 12px; border: 1px solid var(--af-border); border-radius: 8px; background: var(--af-surface); color: var(--af-text); }
.af-dialog-actions { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
</style>
