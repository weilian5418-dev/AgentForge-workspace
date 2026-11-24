<script setup lang="ts">
import AfMotionProvider from '@/components/af/AfMotionProvider.vue'
import AppShell from '@/layouts/AppShell.vue'
import ReferenceGate from '@/reference/ReferenceGate.vue'
</script>

<template>
  <AfMotionProvider>
    <ReferenceGate />
    <AppShell />
  </AfMotionProvider>
</template>
