import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/workspace' },
    {
      path: '/workspace',
      component: () => import('@/pages/OverviewPage.vue'),
      meta: { shell: 'management', pageId: '01-overview' },
    },
    {
      path: '/tasks/new',
      component: () => import('@/pages/NewTaskPage.vue'),
      meta: { shell: 'taskManage', pageId: '02-new-task' },
    },
    {
      path: '/tasks',
      component: () => import('@/pages/TaskLibraryPage.vue'),
      meta: { shell: 'management', pageId: '05-task-library' },
    },
    {
      path: '/tasks/:runId',
      component: () => import('@/pages/ExecutionPage.vue'),
      meta: { shell: 'execution', pageId: '03-execution' },
    },
    {
      path: '/tasks/:runId/delivery',
      component: () => import('@/pages/DeliveryPage.vue'),
      meta: { shell: 'task', pageId: '04-delivery-review' },
    },
    {
      path: '/inbox',
      component: () => import('@/pages/InboxPage.vue'),
      meta: { shell: 'management', pageId: '06-inbox' },
    },
    {
      path: '/executors',
      component: () => import('@/pages/ExecutorsPage.vue'),
      meta: { shell: 'management', pageId: '07-executors' },
    },
    {
      path: '/executors/profiles/:profileId',
      component: () => import('@/pages/ProfilePage.vue'),
      meta: { shell: 'management', pageId: '08-harness-profile' },
    },
    {
      path: '/knowledge',
      component: () => import('@/pages/KnowledgePage.vue'),
      meta: { shell: 'management', pageId: '09-knowledge-library' },
    },
    {
      path: '/knowledge/:documentId',
      component: () => import('@/pages/DocumentPage.vue'),
      meta: { shell: 'knowledgeDetail', pageId: '10-knowledge-document' },
    },
    {
      path: '/extensions',
      component: () => import('@/pages/ExtensionsPage.vue'),
      meta: { shell: 'management', pageId: '11-extensions' },
    },
    {
      path: '/settings/connection',
      component: () => import('@/pages/SettingsPage.vue'),
      meta: { shell: 'management', pageId: '12-settings-system' },
    },
    {
      path: '/:pathMatch(.*)*',
      component: () => import('@/pages/NotFoundPage.vue'),
      meta: { shell: 'management', pageId: 'not-found' },
    },
  ],
})

router.beforeEach(() => {
  delete document.documentElement.dataset.visualReady
})
