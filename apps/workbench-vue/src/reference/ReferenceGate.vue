<script setup lang="ts">
import { isReferenceBuildEnabled } from '@/app/runtime'
</script>

<template>
  <div v-if="isReferenceBuildEnabled()" class="af-reference-gate" aria-hidden="true" />
</template>
