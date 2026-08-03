<template>
  <div>
    <h2 style="margin-bottom: 24px">监控面板</h2>
    <n-grid :cols="4" :x-gap="16" :y-gap="16">
      <n-gi v-for="item in stats" :key="item.label">
        <n-card>
          <n-statistic :label="item.label" :value="item.value" />
        </n-card>
      </n-gi>
    </n-grid>

    <n-card v-if="toolUsage.length" title="今日工具调用分布" style="margin-top: 24px">
      <div v-for="item in toolUsage" :key="item.tool" class="tool-row">
        <span class="tool-name">{{ item.tool }}</span>
        <n-progress
          type="line"
          :percentage="toolPercent(item.count)"
          :show-indicator="false"
          style="flex: 1"
        />
        <span class="tool-count">{{ item.count }}</span>
      </div>
    </n-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import client from '@/api/client'

interface ToolUsage {
  tool: string
  count: number
}

const stats = ref([
  { label: '总对话数', value: 0 },
  { label: '总消息数', value: 0 },
  { label: '今日工具调用', value: 0 },
  { label: '今日 Token 消耗', value: 0 },
])

const toolUsage = ref<ToolUsage[]>([])
const maxToolCount = ref(1)

function toolPercent(count: number) {
  return Math.round((count / maxToolCount.value) * 100)
}

onMounted(async () => {
  try {
    const { data } = await client.get('/monitor/metrics')
    stats.value = [
      { label: '总对话数', value: data.total_conversations },
      { label: '总消息数', value: data.total_messages },
      { label: '今日工具调用', value: data.tool_calls_today },
      { label: '今日 Token 消耗', value: data.tokens_used_today },
    ]
    toolUsage.value = data.tool_usage || []
    maxToolCount.value = Math.max(...toolUsage.value.map((t) => t.count), 1)
  } catch {
    // 使用默认值
  }
})
</script>

<style scoped>
.tool-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.tool-name {
  width: 180px;
  font-size: 13px;
  color: #ccc;
}
.tool-count {
  width: 40px;
  text-align: right;
  font-size: 13px;
  color: #888;
}
</style>
