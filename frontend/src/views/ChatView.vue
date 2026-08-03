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
        <n-switch v-model:value="chatStore.useWorkflow" size="small">
          <template #checked>多 Agent 工作流</template>
          <template #unchecked>单 Agent</template>
        </n-switch>
      </div>
      <ChatStream />
      <div class="chat-input">
        <n-input
          v-model:value="input"
          type="textarea"
          placeholder="输入消息，Enter 发送，Shift+Enter 换行"
          :autosize="{ minRows: 1, maxRows: 4 }"
          :disabled="chatStore.isStreaming"
          @keydown="handleKeydown"
        />
        <n-button
          type="primary"
          :loading="chatStore.isStreaming"
          :disabled="!input.trim()"
          @click="handleSend"
        >
          发送
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

const chatStore = useChatStore()
const convStore = useConversationStore()
const message = useMessage()
const input = ref('')
const listLoading = ref(false)

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
  if (!text || chatStore.isStreaming) return
  input.value = ''
  window.dispatchEvent(new CustomEvent('chat-send', { detail: { message: text } }))
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
