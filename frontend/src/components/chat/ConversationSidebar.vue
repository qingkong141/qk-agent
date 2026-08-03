<template>
  <div class="conv-sidebar">
    <div class="conv-header">
      <span>历史会话</span>
      <n-button size="tiny" type="primary" :disabled="loading" @click="emit('new')">
        新建
      </n-button>
    </div>
    <n-spin :show="loading">
      <div v-if="conversations.length === 0" class="conv-empty">暂无会话</div>
      <div
        v-for="item in conversations"
        :key="item.id"
        class="conv-item"
        :class="{ active: item.id === currentId }"
        @click="emit('select', item.id)"
      >
        <div class="conv-title">{{ item.title }}</div>
        <div class="conv-meta">{{ formatTime(item.updated_at) }}</div>
        <n-button
          class="conv-delete"
          text
          type="error"
          size="tiny"
          @click.stop="emit('delete', item.id)"
        >
          删除
        </n-button>
      </div>
    </n-spin>
  </div>
</template>

<script setup lang="ts">
import type { Conversation } from '@/stores/conversation'

defineProps<{
  conversations: Conversation[]
  currentId: string
  loading?: boolean
}>()

const emit = defineEmits<{
  select: [id: string]
  new: []
  delete: [id: string]
}>()

function formatTime(value: string) {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}
</script>

<style scoped>
.conv-sidebar {
  width: 220px;
  flex-shrink: 0;
  border-right: 1px solid #333;
  background: #141418;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.conv-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px;
  font-size: 13px;
  font-weight: 600;
  color: #ccc;
  border-bottom: 1px solid #333;
}
.conv-empty {
  padding: 24px 12px;
  text-align: center;
  color: #666;
  font-size: 13px;
}
.conv-item {
  position: relative;
  padding: 10px 12px;
  cursor: pointer;
  border-bottom: 1px solid #222;
}
.conv-item:hover {
  background: #1e1e24;
}
.conv-item.active {
  background: #243028;
  border-left: 3px solid #63e2b7;
}
.conv-title {
  font-size: 13px;
  color: #eee;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding-right: 36px;
}
.conv-meta {
  font-size: 11px;
  color: #666;
  margin-top: 4px;
}
.conv-delete {
  position: absolute;
  right: 8px;
  top: 10px;
  opacity: 0;
}
.conv-item:hover .conv-delete {
  opacity: 1;
}
</style>
