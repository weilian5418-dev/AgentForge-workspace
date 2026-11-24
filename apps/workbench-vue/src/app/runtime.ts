import { inject, type InjectionKey } from 'vue'
import type { WorkspaceRepository } from '@/data/repositories/types'
import { createReferenceRepository } from '@/data/repositories/reference'
import { createLiveRepository } from '@/data/repositories/live'
import { useSessionStore } from '@/stores/session'

export const workspaceRepositoryKey: InjectionKey<WorkspaceRepository> = Symbol('workspaceRepository')

export function isReferenceBuildEnabled(): boolean {
  return import.meta.env.VITE_ENABLE_REFERENCE === 'true'
}

export function shouldUseReferenceFromUrl(search: string): boolean {
  if (!isReferenceBuildEnabled()) return false
  const params = new URLSearchParams(search)
  return params.get('reference') === '1'
}

export function createWorkspaceRepository(search = window.location.search): WorkspaceRepository {
  if (shouldUseReferenceFromUrl(search)) {
    return createReferenceRepository()
  }
  const session = useSessionStore()
  return createLiveRepository(() => session.token, () => session.apiBase, () => session.projectId)
}

export function useWorkspaceRepository(): WorkspaceRepository {
  const repository = inject(workspaceRepositoryKey)
  if (!repository) throw new Error('WorkspaceRepository 未注入')
  return repository
}
