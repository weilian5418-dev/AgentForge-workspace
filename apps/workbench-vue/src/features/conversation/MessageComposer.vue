<script setup lang="ts">
import { ref } from 'vue'
import { Paperclip, Send } from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  pending?: boolean
  disabled?: boolean
  placeholder?: string
  routingLabel?: string
  contextLabel?: string
  recipients?: { id: string; label: string }[]
  allowAttachments?: boolean
}>(), {
  pending: false,
  disabled: false,
  placeholder: '继续描述，或 @ 指定 Agent…',
  routingLabel: '自动调度',
  contextLabel: '当前项目',
  allowAttachments: true,
})
const emit = defineEmits<{ send: [text: string, recipient?: string]; attach: []; routing: [] }>()
const draft = defineModel<string>({ default: '' })
const composing = ref(false)
const attachmentInput = ref<HTMLInputElement | null>(null)
const attached = ref<string[]>([])
const recipient = ref(props.recipients ? '' : props.routingLabel)
function selectFiles(event: Event) {
  attached.value = Array.from((event.target as HTMLInputElement).files ?? []).map(file => file.name)
}

function submit(isDisabled: boolean) {
  if (isDisabled || composing.value || !draft.value.trim() || (props.recipients && !recipient.value)) return
  emit('send', draft.value.trim(), props.recipients ? recipient.value : undefined)
}

function onKey(event: KeyboardEvent, isDisabled: boolean) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing || composing.value || event.keyCode === 229) return
  event.preventDefault()
  submit(isDisabled)
}
</script>

<template>
  <form class="af-composer" data-region="composer" @submit.prevent="submit(pending || disabled)">
    <textarea
      v-model="draft"
      aria-label="给 Agent 的补充要求"
      :placeholder="props.placeholder"
      :disabled="pending || disabled"
      @compositionstart="composing = true"
      @compositionend="composing = false"
      @keydown="onKey($event, pending || disabled)"
    />
    <div v-if="attached.length" class="af-composer-files"><span v-for="name in attached" :key="name">{{ name }}</span><button type="button" aria-label="清空参考资料" @click="attached = []">×</button></div>
    <input ref="attachmentInput" type="file" multiple hidden @change="selectFiles" />
    <div class="af-composer-toolbar">
      <button v-if="allowAttachments" type="button" class="af-composer-icon" aria-label="添加参考资料" :disabled="pending || disabled" @click="attachmentInput?.click(); emit('attach')">
        <Paperclip :size="20" :stroke-width="1.75" />
      </button>
      <label class="af-routing"><select v-model="recipient" :disabled="pending || disabled" aria-label="选择消息接收者" @change="emit('routing')"><template v-if="recipients"><option value="">{{ recipients.length ? '选择接收者' : '暂无运行中的执行者' }}</option><option v-for="item in recipients" :key="item.id" :value="item.id">{{ item.label }}</option></template><template v-else><option>{{ props.routingLabel }}</option><option>Frontend · Codex CLI</option><option>Backend · Claude Code</option><option>Reviewer · OpenCode</option></template></select></label>
      <span v-if="props.contextLabel" class="af-context">{{ props.contextLabel }}</span>
      <button type="submit" class="af-send" aria-label="发送消息" :disabled="pending || disabled || !draft.trim() || (recipients !== undefined && !recipient)">
        <Send :size="20" :stroke-width="1.75" />
      </button>
    </div>
  </form>
</template>

<style scoped>
.af-routing select { min-width: 0; max-width: 180px; border: 0; background: transparent; font-size: inherit; color: inherit; }
.af-composer-files { display: flex; flex-wrap: wrap; gap: 6px; font-size: 12px; color: var(--af-accent-ink); }
.af-composer-files span { padding: 3px 6px; background: var(--af-selected); border-radius: 4px; }
.af-composer-files button { border: 0; background: transparent; color: inherit; }
@media(max-width:639px) { .af-routing select { max-width: 140px; } .af-composer-toolbar { gap: 4px; } .af-context { display: none !important; } }
.af-composer {
  border: 1px solid var(--af-accent);
  border-radius: 10px;
  padding: 14px;
  background: var(--af-surface);
}
textarea {
  display: block;
  width: 100%;
  min-height: 40px;
  max-height: 180px;
  padding: 0;
  border: 0;
  resize: vertical;
  background: transparent;
  color: var(--af-text);
  outline: none;
}
.af-composer:focus-within { outline: 2px solid var(--af-accent-strong); outline-offset: 2px; }
.af-composer-toolbar { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.af-composer-icon, .af-send {
  width: 40px;
  height: 36px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 8px;
}
.af-composer-icon { background: transparent; }
.af-send { margin-left: auto; background: var(--af-button); color: #fff; }
.af-routing, .af-context {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  border: 1px solid var(--af-border);
  border-radius: 8px;
  padding: 6px 10px;
  background: var(--af-surface);
  font-size: 16px;
}
.af-send:disabled { opacity: 0.45; }
.af-context { white-space:nowrap; flex-shrink:0; }
@media(max-width:1535px) { .af-context { display:none; } }
</style>
