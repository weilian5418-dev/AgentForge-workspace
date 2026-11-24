import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { VueQueryPlugin } from '@tanstack/vue-query'
import App from './App.vue'
import { router } from './app/router'
import { usePreferencesStore } from './stores/preferences'
import { createAppQueryClient } from './app/query-client'
import { createWorkspaceRepository, workspaceRepositoryKey, shouldUseReferenceFromUrl } from './app/runtime'
import { useSessionStore } from './stores/session'
import './styles/tokens.css'
import './styles/base.css'
import './styles/layout.css'
import './styles/responsive.css'

const app = createApp(App)
const pinia = createPinia()
const queryClient = createAppQueryClient()

app.use(pinia)
usePreferencesStore(pinia)
app.provide(workspaceRepositoryKey, createWorkspaceRepository(window.location.search))
app.use(VueQueryPlugin, { queryClient })
app.use(router)
if (!shouldUseReferenceFromUrl(window.location.search)) await useSessionStore(pinia).connect()
app.mount('#app')
