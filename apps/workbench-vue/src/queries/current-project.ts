import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useWorkspaceRepository } from '@/app/runtime'
import { useSessionStore } from '@/stores/session'

export function useCurrentProjectQuery() {
  const repository = useWorkspaceRepository()
  const session = useSessionStore()
  return useQuery({
    queryKey: computed(() => ['current-project', session.projectId]),
    queryFn: ({ signal }) => repository.loadProject(signal),
  })
}

export function useCurrentProject() {
  const query = useCurrentProjectQuery()
  return computed(() => query.data.value ?? null)
}
