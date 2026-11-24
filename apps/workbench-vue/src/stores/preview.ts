import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import type { MessageViewModel, NewTaskRoleAssignmentViewModel, TaskRowViewModel } from '@/domain/view-models'

export const usePreviewStore = defineStore('preview', () => {
  let saved: Record<string, any> = {}
  try { saved=JSON.parse(localStorage.getItem('af-preview-session') || '{}') } catch {}
  const addedTasks = ref<TaskRowViewModel[]>(saved.addedTasks ?? [])
  const taskRoles = ref<Record<string, NewTaskRoleAssignmentViewModel[]>>(saved.taskRoles ?? {})
  const messages = ref<Record<string, MessageViewModel[]>>(saved.messages ?? {})
  const paused = ref<Record<string, boolean>>(saved.paused ?? {})
  const deliveryDecisions = ref<Record<string, 'accepted' | 'changes'>>(saved.deliveryDecisions ?? {})
  watch([addedTasks,taskRoles,messages,paused,deliveryDecisions],()=>{ try { localStorage.setItem('af-preview-session',JSON.stringify({addedTasks:addedTasks.value,taskRoles:taskRoles.value,messages:messages.value,paused:paused.value,deliveryDecisions:deliveryDecisions.value})) } catch {} },{deep:true})
  function createTask(goal: string, roles: NewTaskRoleAssignmentViewModel[]) {
    const id = `preview-${crypto.randomUUID()}`
    addedTasks.value.unshift({ id, goal, status: 'running', statusLabel: '执行中', harnesses: [...new Set(roles.map(role => role.harness))], updatedLabel: '刚刚' })
    taskRoles.value[id] = roles.map(role => ({ ...role }))
    return id
  }
  function addMessage(runId: string, text: string) {
    const message: MessageViewModel = { id: crypto.randomUUID(), kind: 'user', body: text }
    messages.value[runId] = [...(messages.value[runId] ?? []), message]
  }
  return { addedTasks, taskRoles, messages, paused, deliveryDecisions, createTask, addMessage }
})
