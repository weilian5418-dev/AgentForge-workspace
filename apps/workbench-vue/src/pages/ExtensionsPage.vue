<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { Check, Plus, Search } from 'lucide-vue-next'
import PageHeader from '@/components/af/PageHeader.vue'
import AfDialog from '@/components/af/AfDialog.vue'
import { useLibraryStore } from '@/stores/library'
import { useFeedbackStore } from '@/stores/feedback'
import { useRouter } from 'vue-router'
import AfButton from '@/components/af/AfButton.vue'
import AfErrorState from '@/components/af/AfErrorState.vue'
import AfTabs from '@/components/af/AfTabs.vue'
import { useWorkspaceRepository, shouldUseReferenceFromUrl } from '@/app/runtime'
import { useVisualReady } from '@/composables/useVisualReady'

const repository = useWorkspaceRepository()
const isReference = shouldUseReferenceFromUrl(window.location.search)
const saving = ref(false)
const networkPolicy = ref('')
const probeResult = ref('')
const providerOpen = ref(false)
const providerLabel = ref('')
const providerAdapter = ref('openai_compatible')
const providerBaseUrl = ref('')
const providerModelId = ref('')
const { data: backendTemplates } = useQuery({ queryKey: ['templates'], queryFn: ({ signal }) => repository.loadTemplates!(signal), enabled: !isReference })
const { data: providers, refetch: reloadProviders } = useQuery({ queryKey: ['providers'], queryFn: ({ signal }) => repository.loadProviders!(signal), enabled: !isReference })
async function probeProvider(providerId: string) {
  if (!repository.probeProvider || saving.value) return
  saving.value = true
  try {
    await repository.probeProvider(providerId)
    feedback.notify('提供方探测已提交', 'info')
    await reloadProviders()
  } catch (error) {
    feedback.notify(error instanceof Error ? error.message : '探测失败', 'error')
  } finally {
    saving.value = false
  }
}
async function toggleProvider(providerId: string, enable: boolean) {
  if (!repository.updateProvider || saving.value) return
  saving.value = true
  try {
    await repository.updateProvider(providerId, { enabled: enable })
    feedback.notify(enable ? '提供方已启用' : '提供方已停用', 'success')
    await reloadProviders()
  } catch (error) {
    feedback.notify(error instanceof Error ? error.message : '更新失败', 'error')
  } finally {
    saving.value = false
  }
}
async function addProvider() {
  if (!repository.createProvider || saving.value) return
  saving.value = true
  try {
    await repository.createProvider({
      adapterType: providerAdapter.value,
      baseUrl: providerBaseUrl.value.trim(),
      modelId: providerModelId.value.trim(),
      authMode: 'none',
      label: providerLabel.value.trim(),
    })
    providerOpen.value = false
    providerLabel.value = ''
    providerBaseUrl.value = ''
    providerModelId.value = ''
    await reloadProviders()
    feedback.notify('模型提供方已保存', 'success')
  } catch (error) {
    feedback.notify(error instanceof Error ? error.message : '保存失败', 'error')
  } finally {
    saving.value = false
  }
}
async function testConnection() {
  if (isReference || !selected.value) return
  saving.value = true
  try { const result = await repository.probeTool!(selected.value.id, selected.value.configVersion ?? 1); probeResult.value = `${result.operationId} · ${result.status}`; feedback.notify(`探测请求已提交：${result.status}；尚未确认连通`, 'info'); await refetch() }
  catch (error) { feedback.notify(error instanceof Error ? error.message : '探测失败', 'error') }
  finally { saving.value = false }
}
const tab = ref('tools')
const detailTab = ref('overview')
const selectedId = ref('demo-git')
const query = ref('')
const local = useLibraryStore()
const feedback = useFeedbackStore()
const router = useRouter()
const addOpen = ref(false)
const serviceName = ref('')
const serviceCommand = ref('')
const scope = ref('当前工作区')
const connectionFilter = ref('all')
const enabled = ref(true)
const allowWrite = ref(false)
const allItems = computed(() => [...(isReference ? local.tools : []), ...(data.value ?? [])])
async function addService() {
 if(!serviceName.value.trim() || !serviceCommand.value.trim() || saving.value) return
 if (!isReference) {
  saving.value = true
  try {
   const result = await repository.registerTool!({ displayName: serviceName.value.trim(), transport: 'streamable_http', endpoint: { url: serviceCommand.value.trim(), networkPolicyRef: networkPolicy.value.trim(), allowLoopback: false }, enabled: false })
   await refetch(); selectedId.value = result.serverId; addOpen.value = false; feedback.notify('工具服务草稿已保存到后端，尚未连接', 'success')
  } catch (error) { feedback.notify(error instanceof Error ? error.message : '保存失败', 'error') }
  finally { saving.value = false }
  return
 }
 const id=crypto.randomUUID(); local.tools.unshift({id,name:serviceName.value.trim(),capabilities:[serviceCommand.value.trim()],scope:scope.value,connected:false}); selectedId.value=id; addOpen.value=false; serviceName.value=''; serviceCommand.value=''; tab.value='tools'; feedback.notify('服务配置已保存在本地预览')
}
function save() { feedback.notify('扩展偏好已保存在本地预览'); try {localStorage.setItem(`af-tool-${selectedId.value}`,JSON.stringify({enabled:enabled.value,allowWrite:allowWrite.value}))} catch {} }
const templates = [{name:'全栈功能开发',description:'规划、前后端实现与审查，分工完成一个功能。',roles:'Planner · Frontend · Backend · Reviewer'},{name:'问题排查与修复',description:'定位问题，提交修复并补充回归验证。',roles:'Investigator · Developer · Reviewer'},{name:'代码审查',description:'检查变更范围、实现质量与测试覆盖。',roles:'Reviewer · Tester'}]


const { data, isPending, isError, error, refetch, isSuccess } = useQuery({
  queryKey: ['tools'],
  queryFn: ({ signal }) => repository.loadTools(signal),
})

watch(data, value => {
  if (value?.length && !value.find(item => item.id === selectedId.value)) {
    selectedId.value = value[0]!.id
  }
}, { immediate: true })

const filtered = computed(() => {
  const rows = allItems.value.filter(x=>(connectionFilter.value==='all' || (connectionFilter.value==='connected' ? x.connected : !x.connected)) && (scope.value==='全部范围' || x.scope===scope.value || scope.value==='当前工作区'))
  const q = query.value.trim()
  if (!q) return rows
  return rows.filter(item => item.name.includes(q) || item.capabilities.some(cap => cap.includes(q)))
})

const selected = computed(() => allItems.value.find(item => item.id === selectedId.value) ?? null)
watch(selectedId,id=>{ try { const saved=JSON.parse(localStorage.getItem(`af-tool-${id}`)||'{}'); enabled.value=saved.enabled ?? true; allowWrite.value=saved.allowWrite ?? false } catch {} },{immediate:true})
useVisualReady(computed(() => isSuccess.value && Boolean(data.value)))
</script>

<template>
  <main class="af-page af-extensions-page" data-page-id="11-extensions" data-region="content">
    <p class="af-crumb">工作区 / 扩展</p>
    <PageHeader title="扩展" description="为执行者接入工具、模板和模型提供方。">
      <template #actions>
        <AfButton tone="primary" @click="addOpen=true">
          <Plus :size="18" :stroke-width="1.75" />
          添加服务
        </AfButton>
      </template>
    </PageHeader>

    <AfTabs
      v-model="tab"
      :tabs="[
        { id: 'tools', label: '工具服务' },
        { id: 'templates', label: '协作模板' },
        { id: 'providers', label: '模型提供方' },
      ]"
    />

    <div class="af-filters">
      <label class="af-search">
        <Search :size="16" :stroke-width="1.75" aria-hidden="true" />
        <input v-model="query" type="search" placeholder="搜索工具服务…" aria-label="搜索工具服务" />
      </label>
      <select v-model="connectionFilter" class="af-select" aria-label="服务状态"><option value="all">全部状态</option><option value="connected">已连接</option><option value="offline">未配置</option></select>
      <select v-model="scope" class="af-select" aria-label="作用范围"><option>当前工作区</option><option>当前项目</option><option>全部范围</option></select>
    </div>

    <AfErrorState v-if="isError" :message="error?.message" @retry="refetch()" />
    <div v-else-if="isPending">加载扩展…</div>
    <div v-else-if="tab === 'tools'" class="af-extensions">
      <div class="af-main-col">
        <div class="af-table-wrap">
          <table>
            <thead>
              <tr>
                <th>服务名称</th>
                <th>状态</th>
                <th>能力</th>
                <th>作用范围</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="item in filtered"
                :key="item.id"
                :data-active="item.id === selectedId" tabindex="0" @keydown.enter="selectedId=item.id"
                @click="selectedId = item.id"
              >
                <td><strong>{{ item.name }}</strong></td>
                <td>
                  <span class="af-status" :data-ok="item.connected">
                    {{ item.connected ? '已连接' : '未配置' }}
                  </span>
                </td>
                <td>{{ item.capabilities.join('、') }}</td>
                <td>{{ item.scope }}</td>
                <td @click.stop>
                  <button type="button" class="af-config" @click="selectedId=item.id; detailTab='overview'">
                    {{ item.connected ? '配置' : '去配置' }}
                  </button>
                </td>
              </tr>
            <tr v-if="!filtered.length"><td colspan="5">没有找到匹配的服务。</td></tr></tbody>
          </table>
        </div>
        <div class="af-links">
          <button type="button" @click="tab = 'templates'">查看协作模板</button>
          <button type="button" @click="tab = 'providers'">管理模型提供方</button>
        </div>
      </div>

      <aside v-if="selected" class="af-detail">
        <header>
          <div>
            <h2>{{ selected.name }}</h2>
            <p>{{selected.capabilities.join(' · ')}}</p>
          </div>
          <span class="af-available" :data-ok="selected.connected">
            {{ selected.connected ? '可用' : '未配置' }}
          </span>
        </header>
        <div class="af-detail-tabs">
          <button type="button" :data-active="detailTab === 'overview'" @click="detailTab = 'overview'">概览</button>
          <button type="button" :data-active="detailTab === 'perms'" @click="detailTab = 'perms'">权限</button>
          <button type="button" :data-active="detailTab === 'logs'" @click="detailTab = 'logs'">运行记录</button>
        </div>
        <template v-if="detailTab === 'overview'">
          <h3>能力</h3>
          <ul class="af-caps">
            <li v-for="cap in selected.capabilities" :key="cap">
              <Check :size="14" :stroke-width="2" />
              {{ cap }}
            </li>
          </ul>
          <h3>作用范围</h3>
          <p>{{ isReference ? '当前项目：电商平台' : selected.scope }}</p>
          <h3>授权策略</h3>
          <p>写操作按任务请求批准</p>
          <details v-if="isReference">
            <summary>服务配置</summary>
            <label class="af-extension-toggle"><input v-model="enabled" type="checkbox"/> 在当前工作区启用</label>
          </details>
        </template>
        <template v-else>
          <div v-if="detailTab==='perms'" class="af-permission-list"><p>此服务可以访问当前项目的文件与上下文。</p><label class="af-extension-toggle"><input v-model="allowWrite" type="checkbox"/> 允许在任务批准后执行写操作</label><p class="af-note">新的访问范围将向你单独请求批准。</p></div><div v-else class="af-empty-state"><Check :size="28"/><strong>暂无执行记录</strong><p>在任务中调用此服务后，记录将显示在这里。</p></div>
        </template>
        <p v-if="!isReference">配置状态：{{ selected.status }} · {{ probeResult || '尚无本次探测结果' }}</p>
        <footer>
          <AfButton :pending="saving" @click="testConnection">测试连接</AfButton>
          <AfButton v-if="isReference" tone="primary" @click="save">保存配置</AfButton>
        </footer>
      </aside>
    </div>
    <div v-else-if="tab==='templates'" class="af-template-grid"><template v-if="isReference"><article v-for="item in templates" :key="item.name"><h2>{{item.name}}</h2><p>{{item.description}}</p><span>{{item.roles}}</span><AfButton @click="router.push({path:'/tasks/new',query:{draft:item.description}})">使用此模板 →</AfButton></article></template><template v-else><article v-for="item in backendTemplates" :key="item.id"><h2>{{ item.name }}</h2><p>版本 {{ item.activeVersion }} · {{ item.trustState }}</p><p>在新任务创建后，于任务页选择模板预览并应用；此处可跳转创建任务并携带模板 ID。</p><AfButton @click="router.push({path:'/tasks/new',query:{templateId:item.id,templateVersion:String(item.activeVersion ?? '')}})">使用此模板 →</AfButton></article><p v-if="!backendTemplates?.length">后端暂无已注册的协作模板。</p></template></div>
    <div v-else class="af-provider-panel"><h2>模型提供方</h2><template v-if="!isReference"><div class="af-provider-actions"><AfButton tone="primary" @click="providerOpen=true">添加提供方</AfButton></div><article v-for="provider in providers" :key="provider.providerId"><div><strong>{{ provider.label ?? provider.modelId }}</strong><p>{{ provider.adapterType }} · {{ provider.modelId }} · {{ provider.status }}</p><p>{{ provider.baseUrl }} · 凭据：{{ provider.credentialStatus }}</p></div><div class="af-provider-row-actions"><AfButton :pending="saving" @click="probeProvider(provider.providerId)">探测</AfButton><AfButton @click="toggleProvider(provider.providerId, provider.status !== 'enabled')">{{ provider.status === 'enabled' ? '停用' : '启用' }}</AfButton></div></article><p v-if="!providers?.length">后端暂无模型提供方配置。</p></template><template v-else><p>管理角色使用的模型与连接偏好。</p><article><div><strong>工作区默认提供方</strong><p>由各个 Harness 管理模型连接</p></div><AfButton @click="router.push('/executors/profiles/demo-frontend')">管理模型配置</AfButton></article></template></div>
    <AfDialog v-model:open="providerOpen" title="添加模型提供方" description="注册提供方配置；密钥请先通过 credentials 接口登记后再引用。"><form class="af-dialog-form" @submit.prevent="addProvider"><label>显示名称<input v-model="providerLabel" required /></label><label>适配器类型<select v-model="providerAdapter"><option value="openai_compatible">openai_compatible</option><option value="ollama">ollama</option></select></label><label>Base URL<input v-model="providerBaseUrl" required type="url" placeholder="https://…" /></label><label>模型 ID<input v-model="providerModelId" required /></label><div class="af-dialog-actions"><AfButton @click="providerOpen=false">取消</AfButton><AfButton tone="primary" type="submit" :pending="saving">保存</AfButton></div></form></AfDialog>
    <AfDialog v-model:open="addOpen" title="添加工具服务" :description="isReference ? '配置将在此浏览器中保存，供前端流程预览。' : '保存 MCP Streamable HTTP 服务草稿，连接探测需单独授权。'"><form class="af-dialog-form" @submit.prevent="addService"><label>服务名称<input v-model="serviceName" required placeholder="例如：项目文档检索"/></label><label>{{ isReference ? '启动命令或服务地址' : 'MCP HTTP 服务地址' }}<input v-model="serviceCommand" required :type="isReference ? 'text' : 'url'" placeholder="https://…/mcp"/></label><label v-if="!isReference">网络策略引用<input v-model="networkPolicy" required placeholder="已授权的 networkPolicyRef"/></label><label v-else>作用范围<select v-model="scope"><option>当前工作区</option><option>当前项目</option></select></label><div class="af-dialog-actions"><AfButton @click="addOpen=false">取消</AfButton><AfButton tone="primary" type="submit" :pending="saving">保存服务</AfButton></div></form></AfDialog>
  </main>
</template>

<style scoped>
.af-crumb { margin: 0 0 12px; color: var(--af-text-muted); font-size: 18px; }
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
.af-extensions {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 610px;
  gap: 32px;
  align-items: start;
}
.af-main-col { display: grid; gap: 16px; min-width: 0; }
.af-table-wrap {
  border: 1px solid var(--af-border);
  border-radius: 10px;
  overflow: auto;
  background: var(--af-surface);
}
table { width: 100%; border-collapse: collapse; }
th, td {
  height: 92px;
  padding: 0 18px;
  border-bottom: 1px solid var(--af-border);
  text-align: left;
  vertical-align: middle;
  font-size: 18px;
}
th {
  height: 64px;
  background: var(--af-surface-subtle);
  color: var(--af-text-secondary);
  font-weight: 500;
}
tr { cursor: pointer; }
tr[data-active='true'] { background: var(--af-selected); }
strong { font-weight: 600; }
.af-status {
  height: 28px;
  padding: 0 10px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  font-size: 16px;
  background: #f1eff4;
  color: var(--af-text-secondary);
}
.af-status[data-ok='true'] {
  background: var(--af-success-bg);
  color: var(--af-success);
}
.af-config {
  height: 34px;
  padding: 0 12px;
  border: 1px solid var(--af-border);
  border-radius: 8px;
  background: var(--af-surface);
  font-size: 16px;
}
.af-links {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.af-links button {
  min-height: 72px;
  border: 1px solid var(--af-border);
  border-radius: 10px;
  background: var(--af-surface);
  color: var(--af-text-secondary);
  font-size: 18px;
}
.af-detail {
  border: 1px solid var(--af-border);
  border-radius: 10px;
  background: var(--af-surface);
  padding: 24px;
  display: grid;
  gap: 16px;
  align-content: start;
}
.af-detail header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}
.af-detail h2 { margin: 0; font-size: 22px; }
.af-detail header p { margin: 6px 0 0; color: var(--af-text-secondary); font-size: 16px; }
.af-available {
  height: 28px;
  padding: 0 10px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  font-size: 16px;
  background: #f1eff4;
  color: var(--af-text-secondary);
}
.af-available[data-ok='true'] {
  background: var(--af-success-bg);
  color: var(--af-success);
}
.af-detail-tabs {
  display: inline-flex;
  gap: 4px;
  padding: 3px;
  border-radius: 8px;
  background: var(--af-surface-subtle);
  width: fit-content;
}
.af-detail-tabs button {
  height: 32px;
  padding: 0 12px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--af-text-secondary);
  font-size: 16px;
}
.af-detail-tabs button[data-active='true'] {
  background: var(--af-surface);
  color: var(--af-accent-ink);
  box-shadow: 0 0 0 1px var(--af-accent);
}
.af-detail h3 {
  margin: 4px 0 0;
  font-size: 16px;
  color: var(--af-text-muted);
  font-weight: 500;
}
.af-detail > p { margin: 0; font-size: 18px; }
.af-caps {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 8px;
}
.af-caps li {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 18px;
}
.af-caps li :deep(svg) { color: var(--af-success); }
.af-detail details summary { cursor: pointer; color: var(--af-text-secondary); font-size: 18px; }
.af-note { margin: 8px 0 0; color: var(--af-text-muted); font-size: 16px; }
.af-detail footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
  padding-top: 16px;
  border-top: 1px solid var(--af-border);
}
.af-tab-empty {
  margin-top: 24px;
  border: 1px solid var(--af-border);
  border-radius: 10px;
  padding: 32px;
  background: var(--af-surface);
  color: var(--af-text-secondary);
}

@media (max-width: 1439px) {
  .af-extensions { grid-template-columns: 1fr; }
}
.af-template-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:20px; margin-top:24px; }.af-template-grid article,.af-provider-panel { padding:24px; border:1px solid var(--af-border); border-radius:12px; background:var(--af-surface); }.af-template-grid h2 { font-size:22px; margin:0 0 12px; }.af-template-grid p,.af-provider-panel p { color:var(--af-text-secondary); line-height:1.8; }.af-template-grid span { display:block; font-size:14px; color:var(--af-accent-ink); margin:20px 0; }.af-provider-panel { margin-top:24px; }.af-provider-panel article { display:flex; align-items:center; justify-content:space-between; padding:24px 0; border-top:1px solid var(--af-border); gap:16px; }.af-extension-toggle { display:flex; gap:10px; align-items:center; margin:16px 0; }.af-permission-list { line-height:1.8; }
@media(max-width:767px) { .af-template-grid { grid-template-columns:1fr; } .af-provider-panel article { flex-direction:column; align-items:flex-start; } }
</style>
