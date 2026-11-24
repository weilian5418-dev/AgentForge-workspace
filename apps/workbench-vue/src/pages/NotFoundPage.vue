<script setup lang="ts">
import { computed } from 'vue'
import PageHeader from '@/components/af/PageHeader.vue'
import AfButton from '@/components/af/AfButton.vue'
import { useVisualReady } from '@/composables/useVisualReady'

useVisualReady(computed(() => true))
</script>

<template>
  <main class="af-page" data-page-id="not-found" data-region="content">
    <PageHeader title="未找到页面" description="该路由不存在，或 live 模式下无法访问 demo 任务。" />
    <AfButton tone="primary" @click="$router.push('/workspace')">返回工作台</AfButton>
  </main>
</template>
