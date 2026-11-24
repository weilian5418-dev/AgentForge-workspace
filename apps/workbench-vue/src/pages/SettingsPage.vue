<script setup lang="ts">
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { Check, Eye, EyeOff, Settings, Link, Monitor, Shield, Bell, Sun, Moon, Database, Server, Zap, Activity } from 'lucide-vue-next'
import PageHeader from '@/components/af/PageHeader.vue'
import AfField from '@/components/af/AfField.vue'
import AfButton from '@/components/af/AfButton.vue'
import AfErrorState from '@/components/af/AfErrorState.vue'
import { useWorkspaceRepository, shouldUseReferenceFromUrl } from '@/app/runtime'
import { useSessionStore } from '@/stores/session'
import { usePreferencesStore } from '@/stores/preferences'
import { useFeedbackStore } from '@/stores/feedback'
import { useVisualReady } from '@/composables/useVisualReady'
const repository = useWorkspaceRepository()
const preferences = usePreferencesStore()
const feedback = useFeedbackStore()
const session = useSessionStore()
const queryClient = useQueryClient()
const isReference = shouldUseReferenceFromUrl(window.location.search)
const { theme: themeChoice, accent } = storeToRefs(preferences)
const section = ref('connection')
const showKey = ref(false)
const autoReconnect = ref(true)
const endpoint = ref('http://127.0.0.1:8765')
const apiKey = ref(isReference ? 'preview-local-key' : '')
const workspaceName = ref('本地工作区')
const projectName = ref('电商平台')
const notifyApproval = ref(true)
const notifyDelivery = ref(true)
const notifySound = ref(false)
const policy = ref('confirm')
let saved = { theme: themeChoice.value, accent: accent.value, reducedMotion: preferences.reducedMotion }
const sections = [{ id:'basic', label:'基本设置', icon:Settings }, { id:'connection', label:'服务连接', icon:Link }, { id:'appearance', label:'外观与交互', icon:Monitor }, { id:'policy', label:'权限策略', icon:Shield }, { id:'notify', label:'通知', icon:Bell }]
const { data, isPending, isError, error, refetch, isSuccess } = useQuery({ queryKey:['settings'], queryFn:({signal}) => repository.loadSettings(signal) })
useVisualReady(computed(() => isSuccess.value && Boolean(data.value)))
const health = computed(() => (data.value?.services ?? []).map((item,index) => ({...item, icon:[Server,Database,Zap,Activity][index] ?? Activity})))
async function connect() {
 if (isReference) return feedback.notify('当前是设计预览；使用真实模式连接本机服务')
 const ok = await session.connect({ endpoint: endpoint.value, token: apiKey.value })
 if (ok) { apiKey.value = ''; await queryClient.invalidateQueries(); feedback.notify('本机服务已连接，凭据仅保留在内存中', 'success') }
 else feedback.notify(session.error, 'error')
}
function saveLocal() {
 const ok = preferences.save()
 try { localStorage.setItem('af-workspace-settings', JSON.stringify({ workspaceName:workspaceName.value, projectName:projectName.value, autoReconnect:autoReconnect.value, notifyApproval:notifyApproval.value, notifyDelivery:notifyDelivery.value, notifySound:notifySound.value, policy:policy.value })) } catch { feedback.notify('当前浏览器无法保存设置','error'); return }
 saved = { theme:themeChoice.value, accent:accent.value, reducedMotion:preferences.reducedMotion }
 feedback.notify(ok ? '本地设置已保存' : '当前浏览器无法保存外观设置', ok ? 'success' : 'error')
}
try { const local = JSON.parse(localStorage.getItem('af-workspace-settings') || '{}'); workspaceName.value=local.workspaceName ?? workspaceName.value; projectName.value=local.projectName ?? projectName.value; autoReconnect.value=local.autoReconnect ?? true; notifyApproval.value=local.notifyApproval ?? true; notifyDelivery.value=local.notifyDelivery ?? true; notifySound.value=local.notifySound ?? false; policy.value=local.policy ?? 'confirm' } catch {}
function cancel() { themeChoice.value=saved.theme; accent.value=saved.accent; preferences.reducedMotion=saved.reducedMotion; feedback.notify('已恢复上次保存的外观设置','info') }
</script>
<template>
 <main class="af-page af-settings-page" data-page-id="12-settings-system" data-region="content">
 <p class="af-crumb">工作区 / 设置</p><PageHeader title="设置" description="连接本地服务，调整工作区偏好。" />
 <AfErrorState v-if="isError" :message="error?.message" @retry="refetch()" /><div v-else-if="isPending">加载设置…</div>
 <div v-else-if="data" class="af-settings">
  <nav class="af-settings-nav" aria-label="设置分区"><button v-for="item in sections" :key="item.id" type="button" :data-active="section===item.id" @click="section=item.id"><component :is="item.icon" :size="22" />{{item.label}}</button></nav>
  <div class="af-settings-center">
  <section v-if="section==='connection'" class="af-settings-form">
   <h2>服务连接</h2><AfField label="后端服务地址"><div class="af-endpoint-row"><input v-model="endpoint" type="url" aria-label="后端服务地址" /><AfButton :pending="session.connecting" @click="connect">测试连接</AfButton></div></AfField>
   <p v-if="isReference || session.connected" class="af-ok-line"><Check :size="18" />{{ isReference ? '设计预览 · 演示连接状态' : '连接正常，已完成认证。' }}</p>
   <p v-else role="alert">{{ session.error || '尚未连接本机服务' }}</p>
   <AfField label="API 密钥"><div class="af-key-row"><input v-model="apiKey" :type="showKey?'text':'password'" aria-label="API 密钥" :placeholder="isReference ? '' : '留空时自动获取本机凭据'" autocomplete="off" /><button class="af-eye" type="button" :aria-label="showKey?'隐藏密钥':'显示密钥'" @click="showKey=!showKey"><EyeOff v-if="showKey" :size="20"/><Eye v-else :size="20"/></button></div></AfField>
   <div class="af-setting-two"><AfField label="运行方式"><select aria-label="运行方式" :disabled="!isReference"><option>本地工作区</option><option v-if="isReference">远程工作区</option></select></AfField><label v-if="isReference" class="af-toggle-row"><span>自动重连</span><input v-model="autoReconnect" type="checkbox" class="af-switch" /></label><p v-else class="af-note">任务事件断开后会自动重试，离开任务页会停止订阅。</p></div>
   <div class="af-events"><h3>消息与事件</h3><div><strong>实时事件</strong><span class="af-pill" :data-ok="isReference || session.streamStatus === 'connected'">{{ isReference ? '✓ 已连接' : session.streamLabel }}</span><span>最后同步　{{ isReference ? '刚刚' : session.lastSync ?? '尚未同步' }}</span></div></div>
  </section>
  <section v-if="section==='appearance' || section==='connection'" class="af-settings-form">
   <h2>外观与交互</h2><div class="af-appearance-row"><span class="af-label">主题</span><div class="af-theme"><button v-for="item in [{id:'light',label:'浅色',icon:Sun},{id:'dark',label:'深色',icon:Moon},{id:'system',label:'跟随系统',icon:Monitor}] as const" :key="item.id" type="button" :data-active="themeChoice===item.id" @click="themeChoice=item.id"><component :is="item.icon" :size="20"/>{{item.label}}</button></div></div>
   <div class="af-appearance-row"><span class="af-label">主题色（强调色）</span><div class="af-accents"><button v-for="item in ['lavender','gray','amber'] as const" :key="item" type="button" class="af-swatch" :data-color="item" :data-active="accent===item" :aria-label="{lavender:'紫色',gray:'灰色',amber:'黄色'}[item]" :aria-pressed="accent===item" @click="accent=item" /></div></div>
   <label class="af-toggle-row"><span>减少动态效果</span><input v-model="preferences.reducedMotion" type="checkbox" class="af-switch" /><span class="af-note">遵循系统的动态效果偏好。</span></label>
   <div class="af-shortcuts"><div><span>发送消息</span><em>Enter</em></div><div><span>打开命令面板</span><em>Ctrl + K</em></div></div>
   <div class="af-form-actions"><AfButton @click="cancel">取消</AfButton><AfButton tone="primary" @click="saveLocal">保存设置</AfButton></div>
  </section>
  <section v-if="section==='basic'" class="af-settings-form"><h2>基本设置</h2><AfField label="工作区名称"><input v-model="workspaceName" /></AfField><AfField label="默认项目"><input v-model="projectName" /></AfField><p class="af-note">用于当前浏览器的工作区显示偏好。</p><div class="af-form-actions"><AfButton tone="primary" @click="saveLocal">保存设置</AfButton></div></section>
  <section v-if="section==='policy'" class="af-settings-form"><h2>权限策略</h2><AfField label="任务授权方式"><select v-model="policy"><option value="confirm">敏感操作逐次确认</option><option value="task">按任务确认授权范围</option><option value="readonly">只读预览</option></select></AfField><p class="af-note">项目写入、终端命令和网络访问将分别显示授权请求。</p><div class="af-form-actions"><AfButton tone="primary" @click="saveLocal">保存偏好</AfButton></div></section>
  <section v-if="section==='notify'" class="af-settings-form"><h2>通知</h2><label class="af-toggle-row"><span>有待批准事项时提醒</span><input v-model="notifyApproval" type="checkbox" class="af-switch"/></label><label class="af-toggle-row"><span>任务交付时提醒</span><input v-model="notifyDelivery" type="checkbox" class="af-switch"/></label><label class="af-toggle-row"><span>提示音</span><input v-model="notifySound" type="checkbox" class="af-switch"/></label><div class="af-form-actions"><AfButton tone="primary" @click="saveLocal">保存设置</AfButton></div></section>
  </div>
  <aside class="af-aside"><section><h2>服务状态</h2><ul><li v-for="item in health" :key="item.id"><component :is="item.icon" :size="22"/><span>{{item.name}}</span><em :data-ok="item.status==='ok'"><Check v-if="item.status==='ok'" :size="16"/>{{item.detail}}</em></li></ul></section><section><h2>告警</h2><div class="af-empty-alert"><Check v-if="!data.alerts.length" :size="34"/><p v-for="alert in data.alerts" :key="alert">{{ alert }}</p><p v-if="!data.alerts.length">暂无未处理告警</p></div><p class="af-note">状态以最近一次检测为准。</p></section></aside>
 </div></main>
</template>
<style scoped>
.af-crumb { margin: 0 0 12px; color: var(--af-text-muted); font-size: 16px; }
.af-settings {
  display: grid;
  grid-template-columns: 254px minmax(0, 1fr) 440px;
  gap: 24px;
  align-items: start;
}
.af-settings-nav,
.af-settings-form,
.af-aside {
  border: 1px solid var(--af-border);
  border-radius: 10px;
  background: var(--af-surface);
  align-self: start;
}
.af-settings-nav {
  padding: 12px;
  display: grid;
  gap: 4px;
}
.af-settings-nav button {
  height: 44px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  text-align: left;
  padding: 0 12px;
  color: var(--af-text-secondary);
  font-size: 18px;
}
.af-settings-nav button[data-active='true'] {
  background: var(--af-selected);
  color: var(--af-accent-ink);
  font-weight: 500;
}
.af-settings-form {
  padding: 24px;
  display: grid;
  gap: 18px;
}
.af-settings-form h2 {
  margin: 0;
  font-size: 24px;
  font-weight: 650;
}
.af-endpoint-row,
.af-key-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
}
.af-eye {
  width: 46px;
  height: 46px;
  border: 1px solid var(--af-border);
  border-radius: 8px;
  background: var(--af-surface);
  color: var(--af-text-secondary);
  display: grid;
  place-items: center;
}
.af-ok-line {
  margin: -6px 0 0;
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--af-success);
  font-size: 13px;
}
.af-toggle-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 44px;
  font-size: 18px;
}
.af-switch {
  width: 44px;
  height: 24px;
  appearance: none;
  border-radius: 999px;
  background: #d9d4e0;
  position: relative;
  cursor: pointer;
}
.af-switch::after {
  content: '';
  position: absolute;
  top: 2px;
  left: 2px;
  width: 20px;
  height: 20px;
  border-radius: 999px;
  background: var(--af-surface);
  transition: transform 0.15s ease;
}
.af-switch:checked { background: var(--af-accent-strong); }
.af-switch:checked::after { transform: translateX(20px); }
.af-events {
  border: 1px solid var(--af-border);
  border-radius: 10px;
  padding: 14px;
  background: var(--af-surface-subtle);
}
.af-events > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.af-events p {
  margin: 8px 0 0;
  color: var(--af-text-muted);
  font-size: 13px;
}
.af-pill {
  height: 26px;
  padding: 0 10px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  font-size: 12px;
  background: #f1eff4;
  color: var(--af-text-secondary);
}
.af-pill[data-ok='true'] {
  background: var(--af-success-bg);
  color: var(--af-success);
}
.af-field-block { display: grid; gap: 10px; }
.af-label { font-size: 16px; font-weight: 500; }
.af-theme {
  display: inline-flex;
  gap: 4px;
  padding: 3px;
  border-radius: 8px;
  background: var(--af-surface-subtle);
}
.af-theme button {
  height: 36px;
  padding: 0 14px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--af-text-secondary);
  font-size: 16px;
}
.af-theme button[data-active='true'] {
  background: var(--af-surface);
  color: var(--af-text);
  box-shadow: 0 0 0 1px var(--af-border);
}
.af-accents { display: flex; gap: 10px; }
.af-swatch {
  width: 28px;
  height: 28px;
  border-radius: 999px;
  border: 2px solid transparent;
}
.af-swatch[data-color='lavender'] { background: #795cab; }
.af-swatch[data-color='gray'] { background: #8a8694; }
.af-swatch[data-color='amber'] { background: #d4a017; }
.af-swatch[data-active='true'] { box-shadow: 0 0 0 2px #fff, 0 0 0 4px var(--af-accent-strong); }
.af-shortcuts {
  display: grid;
  gap: 10px;
  border: 1px solid var(--af-border);
  border-radius: 10px;
  padding: 12px 14px;
}
.af-shortcuts div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 16px;
}
.af-shortcuts em {
  font-style: normal;
  color: var(--af-text-secondary);
  font-family: 'AF Mono', monospace;
  font-size: 13px;
}
.af-note { margin: 0; color: var(--af-text-muted); font-size: 13px; line-height: 20px; }
.af-form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--af-border);
}
.af-aside {
  padding: 20px;
  display: grid;
  gap: 24px;
}
.af-aside h2 {
  margin: 0 0 12px;
  font-size: 18px;
  font-weight: 650;
}
.af-aside ul {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 12px;
}
.af-aside li {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 16px;
}
.af-aside em {
  font-style: normal;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--af-text-muted);
}
.af-aside em[data-ok='true'] { color: var(--af-success); }
.af-empty-alert {
  border: 1px dashed var(--af-border);
  border-radius: 10px;
  padding: 20px;
  display: grid;
  justify-items: center;
  gap: 8px;
  color: var(--af-text-muted);
  text-align: center;
}
.af-empty-alert p { margin: 0; font-size: 13px; }

@media (max-width: 1439px) {
  .af-settings { grid-template-columns: 1fr; }
}

.af-settings-center { display:grid; gap:22px; min-width:0; }
.af-settings-nav { min-height:calc(100dvh - 250px); align-content:start; }
.af-settings-nav button { display:flex; gap:16px; align-items:center; height:58px; }
.af-setting-two { display:grid; grid-template-columns:1fr 1fr; gap:30px; }
.af-appearance-row { display:grid; grid-template-columns:150px minmax(0,1fr); gap:20px; align-items:center; }
.af-theme { display:flex; background:transparent; padding:0; gap:16px; }
.af-theme button { display:flex; align-items:center; justify-content:center; gap:12px; flex:1; border:1px solid var(--af-border); height:46px; white-space:nowrap; }
.af-theme button[data-active='true'] { background:var(--af-selected); color:var(--af-accent-ink); border-color:var(--af-accent); }
.af-ok-line { background:var(--af-success-bg); padding:8px 12px; border-radius:8px; }
.af-events { border:0; border-top:1px solid var(--af-border); border-radius:0; background:transparent; padding:16px 0 0; }
.af-events h3 { margin:0 0 16px; font-size:20px; }
.af-events > div { justify-content:flex-start; }
.af-events > div > :last-child { margin-left:auto; color:var(--af-text-secondary); }
.af-aside { padding:0; border:0; background:transparent; }
.af-aside section { border:1px solid var(--af-border); border-radius:12px; background:var(--af-surface); padding:22px; }
.af-aside h2 { font-size:24px; border-bottom:1px solid var(--af-border); padding-bottom:16px; }
.af-aside li { align-items:center; min-height:44px; border-bottom:1px solid var(--af-border); }
.af-aside em { margin-left:auto; white-space:nowrap; }
.af-empty-alert { border:0; min-height:180px; align-content:center; }
@media(max-width:1750px) { .af-appearance-row { grid-template-columns:1fr; gap:12px; } .af-theme { gap:8px; } .af-theme button { padding:0 8px; } }
@media(max-width:767px) { .af-settings-nav { min-height:0; } .af-setting-two { grid-template-columns:1fr; gap:12px; } .af-events > div { flex-wrap:wrap; } .af-theme { flex-wrap:wrap; } .af-toggle-row { flex-wrap:wrap; } }
@media(max-width:767px) { .af-settings { grid-template-columns:minmax(0,1fr); }.af-settings-nav { display:flex; overflow:auto; min-height:0; padding:6px; max-width:100%; }.af-settings-nav button { flex-shrink:0; height:46px; padding-inline:12px; font-size:15px; gap:8px; }.af-settings-form { padding:18px; }.af-settings > .af-aside { grid-column:auto; }.af-endpoint-row { grid-template-columns:1fr; }.af-aside section { padding:18px; }.af-appearance-row { grid-template-columns:1fr; } }
.af-settings-form { gap:14px; padding:20px; }.af-shortcuts { grid-template-columns:1fr 1fr; gap:20px; }.af-shortcuts div { align-items:center; font-size:15px; }.af-setting-two .af-toggle-row { justify-content:flex-start; gap:20px; }.af-settings-form > .af-toggle-row { justify-content:flex-start; }.af-settings-form > .af-toggle-row > span:first-child { min-width:150px; }.af-settings-form > .af-toggle-row .af-note { margin-left:12px; }
@media(max-width:767px) { .af-settings-form { padding:18px; }.af-shortcuts { grid-template-columns:1fr; gap:12px; }.af-settings-form > .af-toggle-row > span:first-child { min-width:0; }.af-settings-form > .af-toggle-row .af-note { width:100%; margin-left:0; } }
</style>
