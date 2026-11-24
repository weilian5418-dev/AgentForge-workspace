import type { Edge, Node } from '@vue-flow/core'
import type { AgentRoleViewModel, DependencyViewModel, UiStatus } from '@/domain/view-models'

export function buildGraph(
  roles: readonly AgentRoleViewModel[],
  dependencies: readonly DependencyViewModel[],
): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = roles.map(role => ({
    id: role.id,
    type: 'agent',
    position: { x: role.x, y: role.y },
    data: {
      role: role.label,
      harness: role.harness,
      status: role.status as UiStatus,
      workspace: role.workspace,
    },
    draggable: false,
    selectable: true,
  }))

  const edgeLabel = (dep: DependencyViewModel) => {
    if (dep.source === 'demo-planner') return '接口约定'
    if (dep.target === 'demo-reviewer') return '合并审查'
    return undefined
  }

  const edges: Edge[] = dependencies.map(dep => ({
    id: dep.id,
    source: dep.source,
    target: dep.target,
    type: 'smoothstep',
    label: edgeLabel(dep),
    labelStyle: { fill: '#625e6d', fontSize: 12 },
    labelBgStyle: { fill: '#fbfafe' },
    labelBgPadding: [4, 6] as [number, number],
    labelBgBorderRadius: 4,
    style: {
      stroke: dep.completed ? '#a391d4' : '#cfc8db',
      strokeWidth: 1.5,
    },
  }))

  return { nodes, edges }
}
