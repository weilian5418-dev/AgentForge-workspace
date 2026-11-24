<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useRoute, useRouter } from 'vue-router'
import { Check, Code2 } from 'lucide-vue-next'
import { useLibraryStore } from '@/stores/library'
import { useFeedbackStore } from '@/stores/feedback'
import AfTabs from '@/components/af/AfTabs.vue'
import AfField from '@/components/af/AfField.vue'
import AfButton from '@/components/af/AfButton.vue'
import AfErrorState from '@/components/af/AfErrorState.vue'
import { useWorkspaceRepository, shouldUseReferenceFromUrl } from '@/app/runtime'
import { useVisualReady } from '@/composables/useVisualReady'

const route = useRoute()
const router = useRouter()
const repository = useWorkspaceRepository()
const isReference = shouldUseReferenceFromUrl(window.location.search)
const profileId = computed(() => String(route.params.profileId))
const tab = ref('basic')
const local = useLibraryStore()
const feedback = useFeedbackStore()
const form = ref({name:'Frontend',harness:'Codex CLI',environment:'本机',workspace:'独立工作区',provider:'工作区默认提供方',model:'继承 Harness 默认模型',endpoint:'使用默认连接',concurrency:'1',timeout:'30 分钟'})
const network = ref(false)
const codeEditing = ref(true)
const terminal = ref(true)
const saving = ref(false)
async function save() {
  if (isReference) {
    local.profiles[profileId.value]={...form.value,codeEditing:String(codeEditing.value),terminal:String(terminal.value),network:String(network.value)}
    feedback.notify('配置已保存在此浏览器')
    return
  }
  if (saving.value || !repository.updateProfile) return
  saving.value = true
  try {
    await repository.updateProfile(profileId.value, {
      label: form.value.name,
      concurrency: form.value.concurrency,
      timeout: form.value.timeout,
    })
    await refetch()
    feedback.notify('配置已保存到后端', 'success')
  } catch (error) {
    feedback.notify(error instanceof Error ? error.message : '保存失败', 'error')
  } finally {
    saving.value = false
  }
}


const { data, isPending, isError, error, refetch, isSuccess } = useQuery({
  queryKey: computed(() => ['profile', profileId.value]),
  queryFn: ({ signal }) => repository.loadProfile(profileId.value, signal),
})

watch(data, value => { if(!value) return; if (!isReference) { form.value={name:value.name,harness:value.harness,environment:value.environment,workspace:'按任务绑定',provider:'由执行环境管理',model:value.model,endpoint:'由执行环境管理',concurrency:value.concurrency,timeout:value.timeout}; return }; form.value={...form.value,name:profileId.value.includes('backend')?'Backend':profileId.value.includes('reviewer')?'Reviewer':profileId.value.includes('planner')?'Planner':'Frontend',harness:value.harness,model:value.model,...local.profiles[profileId.value]}; const saved=local.profiles[profileId.value]; codeEditing.value=saved?.codeEditing!=='false'; terminal.value=saved?.terminal!=='false'; network.value=saved?.network==='true' }, {immediate:true})

useVisualReady(computed(() => isSuccess.value && Boolean(data.value)))

const permissions = [
  { label: '修改项目文件', status: '允许', tone: 'ok' },
  { label: '执行终端命令', status: '按任务授权', tone: 'warn' },
  { label: '访问外部网络', status: '需要批准', tone: 'attention' },
] as const
</script>

<template>
  <main class="af-page af-profile-page" data-page-id="08-harness-profile" data-region="content">
    <AfErrorState v-if="isError" :message="error?.message" @retry="refetch()" />
    <div v-else-if="isPending">加载配置…</div>
    <template v-else-if="data">
      <p class="af-crumb">执行者 / {{ data.harness }} / {{form.name}}</p>
      <header class="af-profile-head">
        <div class="af-profile-title">
          <span class="af-code-mark" aria-hidden="true"><Code2 :size="22" :stroke-width="1.75" /></span>
          <div>
            <div class="af-title-row">
              <h1>{{form.name}} 配置</h1>
              <span class="af-available"><Check v-if="isReference" :size="14" :stroke-width="2" />{{ isReference ? '可用' : data.authStatus ?? 'unknown' }}</span>
            </div>
            <p>{{ isReference ? '用于前端实现任务的可调度执行配置' : `访问级别：${data.accessLevel}；具体调度资格需按任务校验` }}</p>
          </div>
        </div>
        <AfButton v-if="!isReference" @click="refetch()">刷新配置</AfButton><AfButton v-else @click="feedback.notify('当前为设计预览','info')">测试连接</AfButton>
      </header>

      <AfTabs
        v-model="tab"
        :tabs="[
          { id: 'basic', label: '基本配置' },
          { id: 'caps', label: '能力与权限' },
          { id: 'logs', label: '运行记录' },
        ]"
      />

      <div class="af-profile">
        <form class="af-form" @submit.prevent>
          <template v-if="tab === 'basic'">
            <section>
              <h2>执行环境</h2>
              <div class="af-two">
                <AfField label="配置名称">
                  <input v-model="form.name" />
                </AfField>
                <AfField label="Harness">
                  <div class="af-inline">
                    <input v-model="form.harness" :readonly="!isReference" />
                    <span class="af-connected"><Check :size="14" :stroke-width="2" /> {{ isReference ? '已连接' : (data.authStatus ?? 'unknown') }}</span>
                  </div>
                </AfField>
                <AfField label="环境">
                  <input v-model="form.environment" :readonly="!isReference" />
                </AfField>
                <AfField label="工作区方式">
                  <input v-model="form.workspace" :readonly="!isReference" />
                </AfField>
              </div>
              <p v-if="!isReference" class="af-note">配置 ID：{{ data.id }} · 访问级别：{{ data.accessLevel }} · 校验：{{ data.validationId ?? '无' }}。Adapter 绑定不可通过此表单修改。</p>
            </section>

            <section>
              <h2>模型与连接</h2>
              <div class="af-two">
                <AfField label="提供方">
                  <input v-model="form.provider" :readonly="!isReference" />
                </AfField>
                <AfField label="模型" >
                  <input v-model="form.model" :readonly="!isReference" />
                </AfField>
                <AfField label="API Key">
                  <input type="password" value="************" readonly aria-label="API Key 由执行环境管理" />
                </AfField>
                <AfField label="Endpoint">
                  <input v-model="form.endpoint" :readonly="!isReference" />
                </AfField>
              </div>
              <p class="af-note">模型连接沿用执行环境配置。</p>
            </section>

            <section>
              <h2>调度偏好</h2>
              <div class="af-caps">
                <label><input type="checkbox" v-model="codeEditing" :disabled="!isReference" /> 代码编辑</label>
                <label><input type="checkbox" v-model="terminal" :disabled="!isReference" /> 终端</label>
                <label><input type="checkbox" v-model="network" :disabled="!isReference" /> 网络访问</label>
              </div>
              <div class="af-two">
                <AfField label="最大并发任务数">
                  <input v-model="form.concurrency" type="number" min="1" max="20" />
                </AfField>
                <AfField label="超时时间">
                  <input v-model="form.timeout" />
                </AfField>
              </div>
              <details class="af-advanced">
                <summary>高级参数</summary>
                <label class="af-advanced-field">上下文窗口<select aria-label="上下文窗口"><option>自动</option><option>128k</option><option>256k</option></select></label>
              </details>
            </section>
          </template>

          <template v-else-if="tab === 'caps'">
            <section><h2>能力与权限</h2><p class="af-note">为当前角色选择可以使用的能力。</p><div class="af-permission-controls"><label><input v-model="codeEditing" type="checkbox" :disabled="!isReference"/> 代码编辑</label><label><input v-model="terminal" type="checkbox" :disabled="!isReference"/> 终端执行</label><label><input v-model="network" type="checkbox" :disabled="!isReference"/> 网络访问（每次批准）</label></div><ul class="af-perm"><li v-for="item in permissions" :key="item.label"><span>{{item.label}}</span><em :data-tone="item.tone">{{item.status}}</em></li></ul></section>
          </template>
          <template v-else>
            <section><h2>最近运行</h2><p v-if="!isReference" class="af-note">运行记录按任务实例查看，请从任务页进入。</p><template v-else><RouterLink to="/tasks/demo-order-export" class="af-run-record"><strong>实现订单导出功能</strong><span>今天 16:20 · 进行中 →</span></RouterLink><RouterLink to="/tasks/demo-search" class="af-run-record"><strong>优化商品搜索</strong><span>今天 14:30 · 已完成 →</span></RouterLink></template></section>
          </template>

          <div class="af-actions">
            <AfButton @click="router.push('/executors')">取消</AfButton>
            <AfButton tone="primary" :pending="saving" @click="save">保存配置</AfButton>
          </div>
        </form>

        <aside class="af-aside">
          <section>
            <h2>调度预览</h2>
            <div class="af-preview-card">
              <strong>{{form.name}}</strong>
              <span>{{ form.harness }}</span>
              <div class="af-chips">
                <em>代码实现</em>
                <em>独立工作区</em>
                <em>单任务执行</em>
              </div>
            </div>
          </section>
          <section>
            <h2>权限边界</h2>
            <ul class="af-perm">
              <li v-for="item in permissions" :key="item.label">
                <span>{{ item.label }}</span>
                <em :data-tone="item.tone">{{ item.status }}</em>
              </li>
            </ul>
          </section>
          <section>
            <h2>连接检查</h2>
            <ul class="af-checks">
              <li><Check :size="14" :stroke-width="2" /> 产品已发现</li>
              <li><Check :size="14" :stroke-width="2" /> 凭据状态可用</li>
            </ul>
            <p class="af-muted">上次检测：刚刚</p>
          </section>
        </aside>
      </div>
    </template>
  </main>
</template>

<style scoped>
.af-crumb { margin: 0 0 12px; color: var(--af-text-muted); font-size: 18px; }
.af-profile-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}
.af-profile-title { display: flex; gap: 14px; min-width: 0; }
.af-code-mark {
  width: 48px;
  height: 48px;
  border-radius: 10px;
  background: var(--af-selected);
  color: var(--af-accent-ink);
  display: grid;
  place-items: center;
  flex-shrink: 0;
}
.af-title-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.af-profile-head h1 { margin: 0; font-size: 32px; line-height: 40px; font-weight: 700; }
.af-profile-head p { margin: 6px 0 0; color: var(--af-text-secondary); font-size: 18px; }
.af-available {
  height: 28px;
  padding: 0 10px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: var(--af-success-bg);
  color: var(--af-success);
  font-size: 16px;
  font-weight: 500;
}
.af-profile {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 510px;
  gap: 32px;
  margin-top: 20px;
}
.af-form {
  border: 1px solid var(--af-border);
  border-radius: 10px;
  padding: 24px;
  background: var(--af-surface);
  display: grid;
  gap: 28px;
}
.af-form h2 { margin: 0 0 14px; font-size: 22px; font-weight: 650; }
.af-two { display: grid; grid-template-columns: 1fr 1fr; gap: 20px 24px; }
.af-inline { display: grid; gap: 8px; }
.af-connected {
  width: fit-content;
  height: 28px;
  padding: 0 10px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: var(--af-success-bg);
  color: var(--af-success);
  font-size: 16px;
}
.af-caps { display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 16px; }
.af-caps label { display: inline-flex; align-items: center; gap: 8px; font-size: 18px; }
.af-advanced summary { cursor: pointer; color: var(--af-text-secondary); font-size: 18px; }
.af-advanced p { margin: 10px 0 0; color: var(--af-text-muted); font-size: 18px; }
.af-note { margin: 12px 0 0; color: var(--af-text-muted); font-size: 16px; line-height: 20px; }
.af-actions { display: flex; justify-content: flex-end; gap: 8px; }
.af-aside {
  border: 1px solid var(--af-border);
  border-radius: 10px;
  padding: 20px;
  background: var(--af-surface);
  align-self: start;
  display: grid;
  gap: 24px;
}
.af-aside h2 { margin: 0 0 12px; font-size: 18px; font-weight: 650; }
.af-preview-card {
  border: 1px solid var(--af-border);
  border-radius: 10px;
  padding: 14px;
  background: var(--af-surface-subtle);
}
.af-preview-card strong { display: block; font-size: 16px; }
.af-preview-card > span { display: block; margin-top: 4px; color: var(--af-text-secondary); font-size: 16px; }
.af-chips { margin-top: 12px; display: flex; flex-wrap: wrap; gap: 8px; }
.af-chips em {
  font-style: normal;
  height: 28px;
  padding: 0 10px;
  border-radius: 8px;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
  display: inline-flex;
  align-items: center;
  font-size: 14px;
  color: var(--af-text-secondary);
}
.af-perm, .af-checks { margin: 0; padding: 0; list-style: none; display: grid; gap: 10px; }
.af-perm li { display: flex; justify-content: space-between; gap: 12px; font-size: 18px; }
.af-perm em {
  font-style: normal;
  height: 26px;
  padding: 0 8px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  font-size: 14px;
  font-weight: 500;
}
.af-perm em[data-tone='ok'] { background: var(--af-success-bg); color: var(--af-success); }
.af-perm em[data-tone='warn'] { background: #f1eff4; color: var(--af-text-secondary); }
.af-perm em[data-tone='attention'] { background: var(--af-attention-bg); color: var(--af-attention); }
.af-checks li {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--af-success);
  font-size: 18px;
}
.af-muted { margin: 8px 0 0; color: var(--af-text-muted); font-size: 16px; }

@media (max-width: 1439px) {
  .af-profile { grid-template-columns: 1fr; }
  .af-two { grid-template-columns: 1fr; }
}
.af-permission-controls { display:grid; gap:16px; margin:24px 0; }.af-permission-controls label { display:flex; gap:10px; align-items:center; }.af-run-record { display:grid; gap:12px; padding:20px 0; border-bottom:1px solid var(--af-border); }.af-run-record span { color:var(--af-text-secondary); font-size:15px; }.af-advanced-field { display:grid; gap:10px; margin-top:16px; }
</style>
