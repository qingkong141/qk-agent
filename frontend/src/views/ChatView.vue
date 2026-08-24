<template>
  <div class="chat-view">
    <ConversationSidebar
      :conversations="convStore.list"
      :current-id="convStore.currentId"
      :loading="listLoading"
      @select="handleSelect"
      @new="handleNewChat"
      @delete="handleDelete"
    />
    <div class="chat-main">
      <div class="chat-header">
        <span class="title">{{ currentTitle }}</span>
        <n-select
          v-model:value="chatStore.agentId"
          class="agent-select"
          size="small"
          clearable
          :options="agentOptions"
          :loading="agentsLoading"
          :disabled="chatStore.isStreaming"
          placeholder="默认 Agent"
          @update:value="handleAgentChange"
        />
      </div>
      <ChatStream />
      <div class="chat-input">
        <n-input
          v-model:value="input"
          type="textarea"
          placeholder="输入消息，Enter 发送，Shift+Enter 换行"
          :autosize="{ minRows: 1, maxRows: 4 }"
          @keydown="handleKeydown"
        />
        <n-button
          :type="buttonType"
          :disabled="!inputText && !chatStore.isStreaming"
          @click="buttonLabel === '停止' ? handleCancel() : handleSend()"
        >
          {{ buttonLabel }}
        </n-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { useChatStore } from '@/stores/chat'
import { useConversationStore } from '@/stores/conversation'
import ChatStream from '@/components/chat/ChatStream.vue'
import ConversationSidebar from '@/components/chat/ConversationSidebar.vue'
import client from '@/api/client'

interface Agent {
  id: string
  name: string
  is_default: boolean
}

const chatStore = useChatStore()
const convStore = useConversationStore()
const message = useMessage()
const input = ref('')
const listLoading = ref(false)
const agents = ref<Agent[]>([])
const agentsLoading = ref(false)

const agentOptions = computed(() => agents.value.map((agent) => ({
  label: agent.is_default ? `${agent.name}（默认）` : agent.name,
  value: agent.id,
})))
const stopCommands = new Set(['停止', '停', '取消', '中止', 'stop', 'cancel'])
const inputText = computed(() => input.value.trim())
const isStopCommand = computed(() => stopCommands.has(inputText.value.toLowerCase()))
const buttonLabel = computed(() => {
  if (chatStore.isStreaming && (!inputText.value || isStopCommand.value)) return '停止'
  return '发送'
})
const buttonType = computed(() => buttonLabel.value === '停止' ? 'warning' : 'primary')

const currentTitle = computed(() => {
  const conv = convStore.list.find((c) => c.id === convStore.currentId)
  return conv?.title || 'AI 对话'
})

async function loadConversation(id: string) {
  convStore.currentId = id
  const rows = await convStore.fetchMessages(id)
  chatStore.loadMessages(rows)
}

async function handleSelect(id: string) {
  if (chatStore.isStreaming) {
    message.warning('请等待当前回复完成')
    return
  }
  if (id === convStore.currentId) return
  try {
    await loadConversation(id)
  } catch {
    message.error('加载会话失败')
  }
}

async function handleNewChat() {
  if (chatStore.isStreaming) {
    message.warning('请等待当前回复完成')
    return
  }
  chatStore.clear()
  await convStore.create()
}

async function handleDelete(id: string) {
  if (chatStore.isStreaming) {
    message.warning('请等待当前回复完成')
    return
  }
  await convStore.remove(id)
  if (convStore.currentId === id) {
    chatStore.clear()
  }
  if (convStore.list.length === 0) {
    await convStore.create()
  } else if (!convStore.currentId) {
    await loadConversation(convStore.list[0].id)
  }
}

function handleSend() {
  const text = input.value.trim()
  if (!text) return
  if (isStopCommand.value) {
    if (chatStore.isStreaming) {
      handleCancel()
    } else {
      input.value = ''
      message.info('当前没有正在生成的回复')
    }
    return
  }
  input.value = ''
  window.dispatchEvent(new CustomEvent('chat-send', {
    detail: {
      message: text,
      agentId: chatStore.agentId,
    },
  }))
}

function handleAgentChange(value: string | null) {
  chatStore.agentId = value
}

function handleCancel() {
  input.value = ''
  window.dispatchEvent(new CustomEvent('chat-cancel'))
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}

onMounted(async () => {
  listLoading.value = true
  try {
    await fetchAgents()
    await convStore.fetchList()
    if (convStore.list.length === 0) {
      await convStore.create()
    } else {
      await loadConversation(convStore.list[0].id)
    }
  } catch {
    message.error('加载会话列表失败')
  } finally {
    listLoading.value = false
  }
  window.addEventListener('conversation-updated', refreshConversationList)
})

async function fetchAgents() {
  agentsLoading.value = true
  try {
    const { data } = await client.get<Agent[]>('/agents')
    agents.value = data
    const defaultAgent = data.find((agent) => agent.is_default)
    if (!chatStore.agentId && defaultAgent) {
      chatStore.agentId = defaultAgent.id
    }
  } catch {
    message.warning('Agent 配置加载失败，将使用系统默认配置')
  } finally {
    agentsLoading.value = false
  }
}

onUnmounted(() => {
  window.removeEventListener('conversation-updated', refreshConversationList)
})

async function refreshConversationList() {
  try {
    await convStore.fetchList()
  } catch {
    // 静默失败，不影响聊天
  }
}
</script>

<style scoped>
.chat-view {
  display: flex;
  height: calc(100vh - 48px);
  margin: -24px;
  overflow: hidden;
}
.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 16px 24px;
}
.chat-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}
.title {
  font-size: 16px;
  font-weight: 600;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.agent-select {
  width: 220px;
}
.chat-input {
  display: flex;
  gap: 12px;
  margin-top: 16px;
  align-items: flex-end;
}
.chat-input .n-input {
  flex: 1;
}
</style>
