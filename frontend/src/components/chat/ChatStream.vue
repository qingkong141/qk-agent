<template>
  <div ref="containerRef" class="chat-stream">
    <div v-if="messages.length === 0" class="empty">
      <p>开始一段新对话</p>
    </div>
    <ChatMessage
      v-for="msg in messages"
      :key="msg.id"
      :message="msg"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { storeToRefs } from 'pinia'
import { useWebSocket } from '@vueuse/core'
import { useChatStore } from '@/stores/chat'
import { useConversationStore } from '@/stores/conversation'
import { useAuthStore } from '@/stores/auth'
import { normalizeSources } from '@/utils/sources'
import ChatMessage from './ChatMessage.vue'

const chatStore = useChatStore()
const { messages } = storeToRefs(chatStore)
const convStore = useConversationStore()
const auth = useAuthStore()
const containerRef = ref<HTMLElement>()
const pendingSend = ref<{ message: string; agentId?: string | null } | null>(null)
let cancelFallbackTimer: ReturnType<typeof setTimeout> | null = null

function scrollToBottom() {
  nextTick(() => {
    if (containerRef.value) {
      containerRef.value.scrollTop = containerRef.value.scrollHeight
    }
  })
}

watch(() => messages.value.length, scrollToBottom)

const wsUrl = ref('')
const { send, open, close } = useWebSocket(wsUrl, {
  immediate: false,
  autoReconnect: false,
  onMessage(_ws, event) {
      const msg = JSON.parse(event.data)
    if (msg.type === 'interrupted' || msg.type === 'cancelled' || msg.type === 'done' || msg.type === 'error') {
      clearCancelFallback()
    }
    if (msg.type === 'token') {
      chatStore.appendToLast(msg.data)
      scrollToBottom()
    } else if (msg.type === 'tool_result') {
      const sources = normalizeSources(msg.data?.sources)
      if (sources.length) {
        chatStore.stageSources(sources)
        chatStore.attachSourcesToLastAssistant(sources)
      }
    } else if (msg.type === 'done') {
      const output = msg.data?.output ?? ''
      const sources = normalizeSources(msg.data?.sources)
      if (sources.length) {
        chatStore.stageSources(sources)
        chatStore.attachSourcesToLastAssistant(sources)
      }
      chatStore.finishLastAssistant(output, msg.data?.status ?? 'completed', sources)
      window.dispatchEvent(new CustomEvent('conversation-updated'))
      close()
      scrollToBottom()
      flushPendingSend()
    } else if (msg.type === 'error') {
      chatStore.stageSources([])
      chatStore.finishLastAssistant(`错误: ${msg.data?.message ?? '未知错误'}`)
      close()
      flushPendingSend()
    } else if (msg.type === 'interrupted') {
      chatStore.interruptLastAssistant(msg.data?.partial_output)
      close()
      scrollToBottom()
      flushPendingSend()
    } else if (msg.type === 'cancelled') {
      chatStore.cancelLastAssistant()
      close()
      scrollToBottom()
      flushPendingSend()
    }
  },
})

watch(
  () => convStore.currentId,
  () => {
    close()
  },
)

async function handleSend(e: Event) {
  const { message, agentId } = (e as CustomEvent).detail
  if (chatStore.isStreaming) {
    pendingSend.value = { message, agentId }
    send(JSON.stringify({ type: 'cancel', session_id: convStore.currentId }))
    return
  }
  await startChat(message, agentId)
}

async function startChat(message: string, agentId?: string | null) {
  close()
  if (!convStore.currentId) {
    await convStore.create()
  }

  chatStore.addMessage({ role: 'user', content: message })
  if (convStore.currentId) {
    const conv = convStore.list.find((c) => c.id === convStore.currentId)
    if (conv && (conv.title === '新对话' || !conv.title?.trim())) {
      convStore.updateLocalTitle(convStore.currentId, message.trim().slice(0, 50))
    }
  }
  chatStore.stageSources([])
  chatStore.addMessage({ role: 'assistant', content: '', streaming: true })
  chatStore.isStreaming = true

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  wsUrl.value = `${protocol}//${window.location.host}/api/v1/ws/${convStore.currentId}`

  await open()
  send(JSON.stringify({
    type: 'chat',
    message,
    agent_id: agentId,
    session_id: convStore.currentId,
    execution_strategy: 'auto',
    access_token: auth.token,
  }))
}

function flushPendingSend() {
  const next = pendingSend.value
  if (!next) return
  pendingSend.value = null
  nextTick(() => {
    startChat(next.message, next.agentId)
  })
}

function handleCancel() {
  if (!chatStore.isStreaming) return
  pendingSend.value = null
  send(JSON.stringify({ type: 'cancel', session_id: convStore.currentId }))
  scheduleCancelFallback()
}

function scheduleCancelFallback() {
  clearCancelFallback()
  cancelFallbackTimer = setTimeout(() => {
    if (!chatStore.isStreaming) return
    chatStore.interruptLastAssistant()
    close()
  }, 1500)
}

function clearCancelFallback() {
  if (cancelFallbackTimer) {
    clearTimeout(cancelFallbackTimer)
    cancelFallbackTimer = null
  }
}

onMounted(() => {
  window.addEventListener('chat-send', handleSend)
  window.addEventListener('chat-cancel', handleCancel)
})

onUnmounted(() => {
  clearCancelFallback()
  window.removeEventListener('chat-send', handleSend)
  window.removeEventListener('chat-cancel', handleCancel)
})
</script>

<style scoped>
.chat-stream {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  background: #18181c;
  border-radius: 8px;
}
.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #666;
}
</style>
