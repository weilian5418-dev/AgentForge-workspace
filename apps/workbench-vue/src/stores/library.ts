import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import type { DocumentViewModel, ToolServiceViewModel, HarnessInventoryViewModel } from '@/domain/view-models'

export const useLibraryStore = defineStore('local-library', () => {
  let saved: Record<string, any> = {}
  try { saved = JSON.parse(localStorage.getItem('af-local-library') || '{}') } catch { /* default local state */ }
  const documents = ref<(DocumentViewModel & {ready:boolean})[]>(saved.documents ?? [])
  const documentEdits = ref<Record<string, string>>(saved.documentEdits ?? {})
  const tools = ref<ToolServiceViewModel[]>(saved.tools ?? [])
  const harnesses = ref<HarnessInventoryViewModel[]>(saved.harnesses ?? [])
  const profiles = ref<Record<string, Record<string, string>>>(saved.profiles ?? {})
  const decisions = ref<Record<string, 'allow_once' | 'deny'>>(saved.decisions ?? {})
  const roleAssignments = ref<Record<string, string>>(saved.roleAssignments ?? {})
  watch([documents, documentEdits, tools, harnesses, profiles, decisions, roleAssignments], () => {
    try { localStorage.setItem('af-local-library', JSON.stringify({ documents:documents.value, documentEdits:documentEdits.value, tools:tools.value, harnesses:harnesses.value, profiles:profiles.value, decisions:decisions.value, roleAssignments:roleAssignments.value })) } catch { /* session remains usable */ }
  }, { deep: true })
  return { documents, documentEdits, tools, harnesses, profiles, decisions, roleAssignments }
})
