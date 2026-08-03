<template>
  <div v-if="sources.length" class="sources">
    <div class="sources-title">参考来源</div>
    <div v-for="(item, index) in sources" :key="index" class="source-item">
      <div class="source-header">
        <span class="source-name">{{ item.source }}</span>
        <span class="source-score">相关度 {{ Math.round(item.score * 100) }}%</span>
      </div>
      <p v-if="item.content" class="source-excerpt">{{ item.content }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { SourceCitation } from '@/stores/chat'

defineProps<{ sources: SourceCitation[] }>()
</script>

<style scoped>
.sources {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid #3a3a3e;
}
.sources-title {
  font-size: 12px;
  color: #63e2b7;
  margin-bottom: 8px;
}
.source-item {
  background: #1e1e22;
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 6px;
}
.source-header {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
}
.source-name {
  color: #ccc;
  font-weight: 500;
}
.source-score {
  color: #888;
  flex-shrink: 0;
}
.source-excerpt {
  margin: 6px 0 0;
  font-size: 11px;
  color: #777;
  line-height: 1.5;
}
</style>
