<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import type { TaskViewModel } from '@/domain/view-models'
import { useWorkspaceRepository } from '@/app/runtime'
import { useFeedbackStore } from '@/stores/feedback'
import AfButton from '@/components/af/AfButton.vue'
import AfErrorState from '@/components/af/AfErrorState.vue'

const props = defineProps<{ task: TaskViewModel }>()
const repository = useWorkspaceRepository()
const feedback = useFeedbackStore()
const client = useQueryClient()
const pending = ref(false)
const failure = ref('')
const assignments = ref<Record<string, string>>({})
const planKey = computed(() => ['plan', props.task.id, props.task.live?.planVersion])
const { data: plan, isPending, isError, refetch } = useQuery({ queryKey: planKey, queryFn: ({ signal }) => repository.loadPlan!(props.task.id, signal), refetchInterval: 5000 })
const { data: profiles } = useQuery({ queryKey: ['agent-profiles'], queryFn: ({ signal }) => repository.loadProfiles!(signal) })
watch(plan, value => {
  if (!value) return
  const current = Object.fromEntries((value.assignments ?? []).map(item => [item.roleId, item.agentProfileId]))
  assignments.value = Object.fromEntries((value.roles ?? []).map(role => [role.roleId, assignments.value[role.roleId] ?? current[role.roleId] ?? '']))
}, { immediate: true })
const canAssign = computed(() => Boolean(plan.value?.roles?.length) && (plan.value!.roles ?? []).every(role => assignments.value[role.roleId]))
const actions = computed(() => props.task.live?.guidance?.allowedActions ?? [])
async function perform(action: () => Promise<unknown>, success: string) {
  if (pending.value) return
  pending.value = true
  failure.value = ''
  try { await action(); feedback.notify(success, 'success'); await client.invalidateQueries() }
  catch (error) { failure.value = error instanceof Error ? error.message : '操作失败'; feedback.notify(failure.value, 'error'); await client.invalidateQueries() }
  finally { pending.value = false }
}
function propose() { void perform(() => repository.proposePlan!(props.task.id, crypto.randomUUID()), '计划已生成，请检查角色与工作包') }
function assign() {
  const value = plan.value
  if (!value) return
  void perform(() => repository.assignRoles!(props.task.id, { planVersion: value.planVersion, assignments: (value.roles ?? []).map(role => ({ roleId: role.roleId, agentProfileId: assignments.value[role.roleId]! })) }), '角色分配已保存，后端已重新校验能力')
}
function decide() {
  const value = plan.value?.confirmation
  if (!value || value.status !== 'pending') return
  void perform(() => repository.decidePlan!(props.task.id, { decision: 'approve', confirmationRequestId: value.requestId, planVersion: value.planVersion, planHash: value.planHash, assignmentVersion: value.assignmentVersion, gatePolicyVersion: value.gatePolicyVersion, statusVersion: value.statusVersion }), '计划确认已提交')
}
function execute() {
  const value = plan.value
  if (!value || !value.assignmentVersion || !value.gatePolicyVersion || !props.task.live) return
  void perform(() => repository.requestExecution!(props.task.id, { planVersion: value.planVersion, planHash: value.planHash, assignmentVersion: value.assignmentVersion!, gatePolicyVersion: value.gatePolicyVersion! }, crypto.randomUUID()), '已提交执行请求，等待后端准备工作区')
}
</script>

<template>
  <section class="af-live-plan" aria-label="任务计划">
    <header><div><h2>{{ task.live?.guidance?.stageLabel ?? task.statusLabel }}</h2><p>{{ task.live?.guidance?.stageDetail }}</p></div><AfButton :pending="pending" :disabled="!actions.some(action => action.action === 'propose_plan' && action.allowed)" @click="propose">{{ plan ? '重新生成计划' : '生成计划' }}</AfButton></header>
    <p v-if="failure" role="alert">{{ failure }}</p>
    <ul v-if="task.live?.guidance?.blockers?.length" class="af-plan-notices"><li v-for="blocker in task.live.guidance.blockers" :key="blocker.code">{{ blocker.message }}</li></ul>
    <AfErrorState v-if="isError" @retry="refetch()" />
    <p v-else-if="isPending">正在读取计划…</p>
    <p v-else-if="!plan">任务已保存，尚未生成计划。点击“生成计划”开始规则规划。</p>
    <template v-else>
      <p>{{ plan.summary }}</p><p>计划版本 {{ plan.planVersion }} · {{ plan.compilation?.status ?? '待校验' }} · {{ plan.confirmationState }}</p>
      <h3>角色分配</h3>
      <div v-for="role in plan.roles" :key="role.roleId" class="af-plan-role"><label><strong>{{ role.roleId }}</strong><span>{{ role.responsibilities }}</span><select v-model="assignments[role.roleId]" :aria-label="`${role.roleId} 执行配置`" :disabled="pending || plan.status === 'approved'"><option value="">选择实际执行配置</option><option v-for="profile in profiles" :key="profile.profileId" :value="profile.profileId">{{ profile.adapterId }} · {{ profile.authStatus }} · {{ profile.accessLevel }} · {{ profile.profileId }}</option></select></label><small>所需能力：{{ role.requiredCapabilities.join('、') }}</small><small>写入范围：{{ (role.writeScopes ?? []).join('、') || '无' }}</small></div>
      <p v-if="!profiles?.length">暂无执行配置，请先在执行者页面扫描和注册。</p>
      <AfButton :pending="pending" :disabled="!canAssign || plan.status === 'approved'" @click="assign">保存角色分配并校验</AfButton>
      <h3>工作包</h3><ol><li v-for="item in plan.workPackages" :key="item.id"><strong>{{ item.objective }}</strong><p>{{ item.ownerRoleId }} · 依赖：{{ (item.dependsOn ?? []).join('、') || '无' }}</p></li></ol>
      <ul v-if="plan.compilation?.issues?.length || plan.compilation?.warnings?.length" class="af-plan-notices"><li v-for="issue in [...(plan.compilation?.issues ?? []), ...(plan.compilation?.warnings ?? [])]" :key="`${issue.code}-${issue.entityId}-${issue.fieldPath}`">{{ issue.message }}<small v-if="issue.suggestedResolution">{{ issue.suggestedResolution }}</small></li></ul>
      <footer><AfButton tone="primary" :pending="pending" :disabled="plan.confirmation?.status !== 'pending' || plan.compilation?.status !== 'valid'" @click="decide">确认当前计划与分配</AfButton><AfButton :pending="pending" :disabled="plan.status !== 'approved' || !actions.some(action => action.action === 'request_execution' && action.allowed)" @click="execute">开始执行</AfButton></footer>
    </template>
  </section>
</template>

<style scoped>
.af-live-plan { padding:24px; overflow:auto; min-height:0; min-width:0; grid-row:2 / -1; }
header, footer { display:flex; justify-content:space-between; gap:16px; align-items:center; flex-wrap:wrap; }
h2 { margin:0; font-size:22px; } h3 { margin-top:24px; }
p, small { color:var(--af-text-secondary); line-height:1.6; overflow-wrap:anywhere; }
.af-plan-role { border-top:1px solid var(--af-border); padding:14px 0; }
label { display:grid; grid-template-columns:minmax(0,1fr); gap:8px; min-width:0; } small { display:block; margin-top:8px; }
select { width:100%; min-width:0; max-width:100%; text-overflow:ellipsis; padding:10px; border:1px solid var(--af-border); border-radius:8px; background:var(--af-surface); color:var(--af-text); }
.af-plan-notices { padding:12px 12px 12px 30px; background:var(--af-surface-subtle); border-radius:8px; }
footer { margin-top:24px; justify-content:flex-end; }
</style>
