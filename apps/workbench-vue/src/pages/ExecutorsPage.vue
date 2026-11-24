<script setup lang="ts">
import { computed, ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { Check, MoreHorizontal, Plus, Search } from 'lucide-vue-next'
import PageHeader from '@/components/af/PageHeader.vue'
import AfDialog from '@/components/af/AfDialog.vue'
import { useLibraryStore } from '@/stores/library'
import { useFeedbackStore } from '@/stores/feedback'
import AfButton from '@/components/af/AfButton.vue'
import AfErrorState from '@/components/af/AfErrorState.vue'
import AfTabs from '@/components/af/AfTabs.vue'
import AfStatusBadge from '@/components/af/AfStatusBadge.vue'
import { useWorkspaceRepository, shouldUseReferenceFromUrl } from '@/app/runtime'
import { useVisualReady } from '@/composables/useVisualReady'
import { roles } from '@/fixtures/shared'
import type { components } from '@/data/contracts/api.generated'

const repository = useWorkspaceRepository()
const isReference = shouldUseReferenceFromUrl(window.location.search)
const found = ref<components['schemas']['DiscoveredInstallationView'][]>([])
const registering = ref(false)
const { data: profiles, refetch: reloadProfiles } = useQuery({ queryKey: ['agent-profiles'], queryFn: ({ signal }) => repository.loadProfiles!(signal), enabled: !isReference })
const { data: instances } = useQuery({ queryKey: ['agent-instances'], queryFn: ({ signal }) => repository.loadInstances!(signal), enabled: !isReference, refetchInterval: isReference ? false : 5000 })
async function register(installationId: string, adapterId: string) {
  if (registering.value) return
  registering.value = true
  try { const result = await repository.createProfile!(installationId, adapterId); await reloadProfiles(); await refetch(); feedback.notify(`配置已注册：${result.accessLevel}；调度仍需能力校验`, 'success') }
  catch (error) { feedback.notify(error instanceof Error ? error.message : '注册失败', 'error') }
  finally { registering.value = false }
}
async function connect() { if (isReference) connectOpen.value = true; else { connectOpen.value = true; await discover() } }
const tab = ref('products')
const discovering = ref(false)
const query = ref('')
const local = useLibraryStore()
const feedback = useFeedbackStore()
const connectOpen = ref(false)
const harnessName = ref('')
const harnessCommand = ref('')
const connectionFilter = ref('all')
const allItems = computed(() => [...(isReference ? local.harnesses : []), ...(data.value ?? [])])
function connectHarness() { if(!harnessName.value.trim() || !harnessCommand.value.trim()) return; local.harnesses.push({id:crypto.randomUUID(),name:harnessName.value.trim(),description:harnessCommand.value.trim(),capabilities:['代码编辑','终端执行'],profiles:0,activeInstances:0,connected:false}); connectOpen.value=false; harnessName.value=''; harnessCommand.value=''; feedback.notify('Harness 配置已保存到本地预览，等待连接') }
function assign(roleId:string, event:Event) { local.roleAssignments[roleId]=(event.target as HTMLSelectElement).value; feedback.notify('角色分配已更新到本地预览') }


const { data, isPending, isError, error, refetch, isSuccess } = useQuery({
  queryKey: ['executors'],
  queryFn: ({ signal }) => repository.loadExecutors(signal),
})

const totals = computed(() => {
  const items = allItems.value
  return {
    connected: items.filter(item => item.connected).length,
    profiles: items.reduce((sum, item) => sum + item.profiles, 0),
    instances: items.reduce((sum, item) => sum + item.activeInstances, 0),
  }
})

const filtered = computed(() => {
  const rows = allItems.value.filter(x=>connectionFilter.value==='all' || (connectionFilter.value==='connected' ? x.connected : !x.connected))
  const q = query.value.trim()
  if (!q) return rows
  return rows.filter(item =>
    item.name.includes(q) || item.capabilities.some(cap => cap.includes(q)),
  )
})

const previewRuns = [
  { role: 'Frontend', goal: '实现订单导出功能', status: 'running' as const },
  { role: 'Backend', goal: '实现订单导出功能', status: 'running' as const },
]
const activeRuns = computed(() => isReference ? previewRuns : (instances.value ?? []).map(instance => ({ role: instance.roleId, goal: instance.runId, status: instance.lifecycle === 'active' ? 'running' as const : 'waiting' as const })))

useVisualReady(computed(() => isSuccess.value && Boolean(data.value)))

async function discover() {
  if (discovering.value) return
  discovering.value = true
  try {
    if (!isReference) { const result = await repository.discoverExecutors!(); found.value = result.items ?? []; await refetch(); feedback.notify(`扫描完成，发现 ${found.value.length} 个安装；可在“接入 Harness”中注册配置`, 'success'); return }
    feedback.notify('本机预览包含 Codex CLI、Claude Code 和 OpenCode；接入服务后可重新扫描','info')
  } catch (error) { feedback.notify(error instanceof Error ? error.message : '扫描失败', 'error')
  } finally {
    discovering.value = false
  }
}
</script>

<template>
  <main class="af-page af-executors-page" data-page-id="07-executors" data-region="content">
    <p class="af-crumb">工作区 / 执行者</p>
    <PageHeader title="执行者" description="连接 Harness，为不同角色选择合适的执行环境。">
      <template #actions>
        <AfButton :pending="discovering" @click="discover">扫描本机</AfButton>
        <AfButton tone="primary" @click="connect">
          <Plus :size="18" :stroke-width="1.75" />
          接入 Harness
        </AfButton>
      </template>
    </PageHeader>

    <div class="af-stats">
      <span><strong>{{ totals.connected }}</strong> 个已连接</span>
      <span><strong>{{ totals.profiles }}</strong> 个{{ isReference ? '可调度' : '已注册' }}配置</span>
      <span><strong>{{ totals.instances }}</strong> 个运行实例</span>
    </div>

    <AfTabs
      v-model="tab"
      :tabs="[
        { id: 'products', label: 'Harness 产品' },
        { id: 'profiles', label: '角色配置' },
        { id: 'instances', label: '运行实例' },
      ]"
    />

    <div class="af-filters">
      <label class="af-search">
        <Search :size="16" :stroke-width="1.75" aria-hidden="true" />
        <input v-model="query" type="search" placeholder="搜索名称或能力…" aria-label="搜索名称或能力" />
      </label>
      <select class="af-select" aria-label="运行环境"><option>本机</option></select>
      <select v-model="connectionFilter" class="af-select" aria-label="连接状态"><option value="all">全部状态</option><option value="connected">已连接</option><option value="offline">未连接</option></select>
    </div>

    <AfErrorState v-if="isError" :message="error?.message" @retry="refetch()" />
    <div v-else-if="isPending">加载库存…</div>
    <div v-else class="af-executors">
      <section v-if="tab === 'products'" class="af-products">
        <article v-for="item in filtered" :key="item.id" class="af-product">
          <div class="af-product-main">
            <div class="af-product-title">
              <h2>{{ item.name }}</h2>
              <span class="af-connected" :data-ok="item.connected">
                <Check v-if="item.connected" :size="14" :stroke-width="2" />
                {{ item.connected ? '已连接' : '未连接' }}
              </span>
            </div>
            <p>{{ item.description }}</p>
            <div class="af-caps">
              <span v-for="cap in item.capabilities" :key="cap">{{ cap }}</span>
            </div>
          </div>
          <div class="af-product-side">
            <div class="af-metrics">
              <span>配置 <strong>{{ item.profiles }}</strong></span>
              <span>运行中 <strong>{{ item.activeInstances }}</strong></span>
            </div>
            <div class="af-product-actions">
              <RouterLink v-if="isReference" class="af-manage" to="/executors/profiles/demo-frontend">管理配置</RouterLink><button v-else class="af-manage" type="button" @click="tab='profiles'">管理配置</button>
              <button type="button" class="af-more" :aria-label="`查看 ${item.name} 配置`" @click="tab='profiles'"><MoreHorizontal :size="18" :stroke-width="1.75" /></button>
            </div>
          </div>
        </article>
        <p v-if="!filtered.length" class="af-empty-state">没有找到匹配的 Harness。</p>
        <button type="button" class="af-add-product" @click="connect">
          <Plus :size="18" :stroke-width="1.75" />
          连接其他 Harness
        </button>
      </section>

      <section v-else-if="tab === 'profiles'" class="af-tab-panel">
        <div v-if="isReference" class="af-profile-cards"><RouterLink v-for="role in roles" :key="role.id" :to="`/executors/profiles/${role.id}`"><div><strong>{{role.label}}</strong><span>{{local.roleAssignments[role.id] ?? role.harness}}</span></div><span>管理配置 →</span></RouterLink></div>
        <div v-else class="af-profile-cards"><RouterLink v-for="profile in profiles" :key="profile.profileId" :to="`/executors/profiles/${profile.profileId}`"><div><strong>{{ profile.adapterId }}</strong><span>{{ profile.authStatus }} · {{ profile.accessLevel }}</span><small>{{ profile.profileId }}</small></div><span>查看配置 →</span></RouterLink><p v-if="!profiles?.length">暂无执行配置，请扫描并注册。</p></div>
      </section>
      <section v-else class="af-tab-panel">
        <ul class="af-instance-list">
          <li v-for="run in activeRuns" :key="run.role">
            <strong>{{ run.role }}</strong>
            <span>{{ run.goal }}</span>
            <AfStatusBadge :status="run.status" />
          </li>
        </ul>
      </section>

      <aside class="af-aside">
        <section v-if="isReference">
          <h2>角色分配</h2>
          <ul class="af-role-assign">
            <li v-for="role in roles" :key="role.id">
              <span>{{ role.label }}</span>
              <select class="af-role-select" :value="local.roleAssignments[role.id] ?? role.harness" :aria-label="`${role.label} 执行环境`" @change="assign(role.id,$event)"><option v-for="item in allItems" :key="item.id">{{item.name}}</option></select>
            </li>
          </ul>
        </section>
        <section>
          <h2>当前运行</h2>
          <ul class="af-running">
            <li v-for="run in activeRuns" :key="run.role">
              <div>
                <strong>{{ run.role }}</strong>
                <span>{{ run.goal }}</span>
              </div>
              <AfStatusBadge :status="run.status" />
            </li>
          </ul>
        </section>
        <p class="af-note">Harness 是执行环境，角色决定本次分工。</p>
      </aside>
    </div>
  <AfDialog v-model:open="connectOpen" title="接入 Harness" :description="isReference ? '先保存执行环境信息，在页面中预览角色分配。' : '从本机扫描结果注册执行配置。是否可调度以后端能力校验为准。'"><form v-if="isReference" class="af-dialog-form" @submit.prevent="connectHarness"><label>名称<input v-model="harnessName" required placeholder="例如：Codex CLI"/></label><label>启动命令或路径<input v-model="harnessCommand" required placeholder="例如：codex"/></label><div class="af-dialog-actions"><AfButton @click="connectOpen=false">取消</AfButton><AfButton tone="primary" type="submit">保存配置</AfButton></div></form><div v-else class="af-dialog-form"><p v-if="discovering">正在扫描本机安装…</p><article v-for="item in found" :key="item.installationId ?? item.locationRef"><strong>{{ item.productId }}</strong><p>{{ item.locationRef }}</p><AfButton v-if="item.installationId && item.adapterId" :pending="registering" @click="register(item.installationId!, item.adapterId!)">注册执行配置</AfButton></article><p v-if="!discovering && !found.length">没有发现支持的安装。</p></div></AfDialog>
  </main>
</template>

<style scoped>
.af-crumb {
  margin: 0 0 12px;
  color: var(--af-text-muted);
  font-size: 18px;
}
.af-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 20px;
  margin: -8px 0 20px;
  color: var(--af-text-secondary);
  font-size: 18px;
}
.af-stats strong {
  color: var(--af-text);
  font-weight: 650;
  margin-right: 4px;
}
.af-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 16px 0 20px;
}
.af-search {
  height: 40px;
  min-width: 240px;
  padding: 0 12px;
  border: 1px solid var(--af-border);
  border-radius: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--af-surface);
  color: var(--af-text-muted);
}
.af-search input {
  border: 0;
  outline: none;
  width: 100%;
  background: transparent;
  font-size: 18px;
}
.af-select {
  height: 40px;
  padding: 0 12px;
  border: 1px solid var(--af-border);
  border-radius: 8px;
  background: var(--af-surface);
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--af-text-secondary);
  font-size: 18px;
}
.af-executors {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 510px;
  gap: 32px;
  align-items: start;
}
.af-products { display: grid; gap: 16px; }
.af-product {
  min-height: 124px;
  border: 1px solid var(--af-border);
  border-radius: 10px;
  padding: 20px;
  background: var(--af-surface);
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 20px;
}
.af-product-title {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.af-product h2 {
  margin: 0;
  font-size: 22px;
  line-height: 28px;
}
.af-connected {
  height: 28px;
  padding: 0 10px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 16px;
  font-weight: 500;
  background: #f1eff4;
  color: var(--af-text-secondary);
}
.af-connected[data-ok='true'] {
  background: var(--af-success-bg);
  color: var(--af-success);
}
.af-product p {
  margin: 8px 0 0;
  color: var(--af-text-secondary);
  font-size: 18px;
}
.af-caps {
  margin-top: 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.af-caps span {
  height: 28px;
  padding: 0 10px;
  border-radius: 8px;
  border: 1px solid var(--af-border);
  display: inline-flex;
  align-items: center;
  font-size: 16px;
  color: var(--af-text-secondary);
  background: var(--af-surface-subtle);
}
.af-product-side {
  display: grid;
  align-content: space-between;
  justify-items: end;
  gap: 16px;
}
.af-metrics {
  display: grid;
  gap: 6px;
  text-align: right;
  color: var(--af-text-secondary);
  font-size: 16px;
}
.af-metrics strong { color: var(--af-text); font-weight: 600; }
.af-product-actions { display: flex; align-items: center; gap: 6px; }
.af-manage {
  height: 36px;
  padding: 0 12px;
  border-radius: 8px;
  border: 1px solid var(--af-border);
  display: inline-flex;
  align-items: center;
  font-size: 18px;
  color: var(--af-text);
  background: var(--af-surface);
}
.af-more {
  width: 36px;
  height: 36px;
  border: 1px solid var(--af-border);
  border-radius: 8px;
  background: var(--af-surface);
  color: var(--af-text-secondary);
  display: grid;
  place-items: center;
}
.af-add-product {
  min-height: 88px;
  border: 1px dashed var(--af-border);
  border-radius: 10px;
  background: transparent;
  color: var(--af-text-secondary);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-size: 18px;
}
.af-aside {
  border: 1px solid var(--af-border);
  border-radius: 10px;
  padding: 20px;
  background: var(--af-surface);
  display: grid;
  gap: 24px;
}
.af-aside h2 {
  margin: 0 0 12px;
  font-size: 16px;
  font-weight: 650;
}
.af-role-assign,
.af-running,
.af-instance-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 10px;
}
.af-role-assign li,
.af-running li,
.af-instance-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 44px;
}
.af-role-select {
  height: 36px;
  padding: 0 10px;
  border: 1px solid var(--af-border);
  border-radius: 8px;
  background: var(--af-surface);
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 16px;
  color: var(--af-text-secondary);
}
.af-running strong,
.af-instance-list strong { display: block; font-size: 18px; }
.af-running span,
.af-instance-list span {
  display: block;
  margin-top: 2px;
  font-size: 14px;
  color: var(--af-text-secondary);
}
.af-note {
  margin: 0;
  padding: 12px;
  border-radius: 8px;
  background: var(--af-selected);
  color: var(--af-accent-ink);
  font-size: 16px;
  line-height: 20px;
}
.af-tab-panel {
  border: 1px solid var(--af-border);
  border-radius: 10px;
  padding: 20px;
  background: var(--af-surface);
  color: var(--af-text-secondary);
}

@media (max-width: 1439px) {
  .af-executors { grid-template-columns: 1fr; }
}
.af-profile-cards { display:grid; gap:12px; }.af-profile-cards a { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:20px 4px; border-bottom:1px solid var(--af-border); }.af-profile-cards strong { display:block; font-size:20px; color:var(--af-text); }.af-profile-cards div span { display:block; margin-top:8px; font-size:15px; }
.af-aside { padding:0; border:0; background:transparent; }.af-aside section { padding:24px; border:1px solid var(--af-border); background:var(--af-surface); border-radius:10px; }.af-aside h2 { font-size:24px; padding-bottom:18px; border-bottom:1px solid var(--af-border); }.af-role-assign li { min-height:58px; font-size:20px; }.af-role-select { height:44px; font-size:17px; }.af-running li { min-height:76px; border-bottom:1px solid var(--af-border); }.af-running strong { font-size:20px; }.af-running span { font-size:17px; margin-top:8px; }.af-stats { font-size:20px; margin:24px 0 32px; }.af-filters { margin:28px 0 32px; }.af-search { min-width:36%; height:48px; }.af-select { height:48px; }.af-filters .af-select:first-of-type { margin-left:auto; }.af-product { min-height:140px; }.af-product-main { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:10px 16px; align-items:center; }.af-product-title { grid-column:1; }.af-product-main > p { grid-column:1; margin:0; }.af-caps { grid-column:2; grid-row:1/3; margin:0; max-width:340px; }.af-caps span { height:36px; }.af-product-actions .af-manage { height:44px; font-size:17px; }
@media(min-width:1751px) { .af-executors-page > :deep(.af-page-header),.af-executors-page > .af-stats,.af-executors-page > :deep(.af-tabs),.af-executors-page > .af-filters { max-width:calc(100% - 542px); }.af-aside { margin-top:-330px; } }
@media(max-width:1750px) { .af-product-main { display:block; }.af-caps { margin-top:12px; }.af-product-main>p { margin-top:8px; } }
@media(max-width:767px) { .af-executors { grid-template-columns:minmax(0,1fr); }.af-product { grid-template-columns:1fr; }.af-product-side { display:flex; align-items:center; justify-content:space-between; }.af-metrics { display:flex; gap:10px; }.af-role-assign li { font-size:17px; }.af-role-select { font-size:15px; }.af-stats { font-size:15px; gap:12px; margin:16px 0 20px; }.af-filters .af-select:first-of-type { margin-left:0; } }
.af-running li :deep(.af-status) { display:inline-flex; flex-shrink:0; margin:0; }.af-product-side { align-content:center; gap:12px; }.af-product-main { align-content:center; }.af-product-title h2 { font-weight:650; }
</style>
