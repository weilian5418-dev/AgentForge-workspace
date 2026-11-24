import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

export const usePreferencesStore = defineStore('preferences', () => {
  let saved: Record<string, unknown> = {}
  try { saved = JSON.parse(localStorage.getItem('af-preferences') || '{}') } catch { /* use defaults */ }
  const reducedMotion = ref(saved.reducedMotion === true)
  const theme = ref<'light' | 'dark' | 'system'>(saved.theme === 'dark' || saved.theme === 'system' ? saved.theme : 'light')
  const accent = ref<'lavender' | 'gray' | 'amber'>(saved.accent === 'gray' || saved.accent === 'amber' ? saved.accent : 'lavender')
  const conversationWidth = ref(602)
  const managementCollapsed = ref(false)

  function applyAppearance() {
    document.documentElement.dataset.theme = theme.value === 'system'
      ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : theme.value
    document.documentElement.dataset.accent = accent.value
    document.documentElement.dataset.reducedMotion = String(reducedMotion.value)
  }
  watch([theme, accent, reducedMotion], applyAppearance, { immediate: true })
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyAppearance)
  function save() {
    try {
      localStorage.setItem('af-preferences', JSON.stringify({ theme: theme.value, accent: accent.value, reducedMotion: reducedMotion.value }))
      return true
    } catch { return false }
  }

  function setReducedMotion(value: boolean) {
    reducedMotion.value = value
  }

  return {
    reducedMotion,
    theme,
    accent,
    conversationWidth,
    managementCollapsed,
    setReducedMotion,
    save,
  }
})
