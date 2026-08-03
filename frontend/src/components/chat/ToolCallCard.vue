<template>
  <n-card size="small" :title="`工具: ${tool}`" class="tool-card">
    <n-code :code="result" language="json" word-wrap />
  </n-card>
</template>

<script setup lang="ts">
defineProps<{ tool: string; result: string }>()
</script>

<style scoped>
.tool-card {
  margin: 8px 0;
  max-width: 500px;
}
</style>
