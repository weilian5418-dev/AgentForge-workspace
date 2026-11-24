import { defineStore } from 'pinia'
import { ref } from 'vue'
export const useFeedbackStore = defineStore('feedback', () => {
  const message = ref('')
  const tone = ref<'success' | 'info' | 'error'>('info')
  let timer: ReturnType<typeof setTimeout> | undefined
  function notify(text: string, type: typeof tone.value = 'info') {
    clearTimeout(timer)
    message.value = text
    tone.value = type
    timer = setTimeout(() => { message.value = '' }, 4500)
  }
  return { message, tone, notify }
})
