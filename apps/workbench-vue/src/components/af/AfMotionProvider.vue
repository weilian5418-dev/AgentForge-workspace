<script setup lang="ts">
import { AnimateUIProvider } from '@animate-ui/vue/provider'
import { usePreferencesStore } from '@/stores/preferences'
import { computed } from 'vue'

const preferences = usePreferencesStore()
const reducedMotion = computed(() => (preferences.reducedMotion ? 'user' : undefined))
</script>

<template>
  <AnimateUIProvider :reduced-motion="reducedMotion">
    <slot />
  </AnimateUIProvider>
</template>
