import { defineStore } from 'pinia'
import { ref } from 'vue'
import { generateId } from '@/utils/id'
import { normalizeSources } from '@/utils/sources'

export interface SourceCitation {
  source: string
  score: number
  content: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'tool' | 'agent'
  content: string
  toolName?: string
  agentName?: string
  streaming?: boolean
  status?: 'completed' | 'needs_user_input' | 'interrupted' | 'cancelled'
  sources?: SourceCitation[]
}

export const useChatStore = defineStore('chat', () => {
  const messages = ref<ChatMessage[]>([])
  const conversationId = ref('')
  const isStreaming = ref(false)
  const agentId = ref<string | null>(null)
  const pendingSources = ref<SourceCitation[]>([])

  function addMessage(msg: Omit<ChatMessage, 'id'>) {
    messages.value.push({ ...msg, id: generateId() })
  }

  function appendToLast(content: string) {
    const last = [...messages.value].reverse().find((m) => m.role === 'assistant')
    if (last) {
      last.content += content
    }
  }

  function stageSources(sources: SourceCitation[]) {
    pendingSources.value = sources
  }

  function attachSourcesToLastAssistant(sources: SourceCitation[]) {
    const last = [...messages.value].reverse().find((m) => m.role === 'assistant')
    if (last && sources.length) {
      last.sources = sources
    }
  }

  function finishLastAssistant(
    output: string,
    status: ChatMessage['status'] = 'completed',
    sources?: SourceCitation[],
  ) {
    const last = [...messages.value].reverse().find((m) => m.role === 'assistant')
    if (last) {
      if (output?.trim()) {
        last.content = output
      }
      last.status = status
      if (sources !== undefined) {
        last.sources = sources.length ? sources : undefined
      } else if (pendingSources.value.length) {
        last.sources = pendingSources.value
      }
      pendingSources.value = []
      last.streaming = false
    }
    isStreaming.value = false
  }

  function interruptLastAssistant(partialOutput?: string) {
    const last = [...messages.value].reverse().find((m) => m.role === 'assistant')
    if (last) {
      if (partialOutput?.trim()) {
        last.content = partialOutput
      } else if (!last.content.trim()) {
        last.content = '已停止'
      }
      last.status = 'interrupted'
      last.streaming = false
    }
    pendingSources.value = []
    isStreaming.value = false
  }

  function cancelLastAssistant() {
    const last = [...messages.value].reverse().find((m) => m.role === 'assistant')
    if (last) {
      if (!last.content.trim()) {
        last.content = '已取消'
      }
      last.status = 'cancelled'
      last.streaming = false
    }
    pendingSources.value = []
    isStreaming.value = false
  }

  function clear() {
    messages.value = []
    conversationId.value = ''
    pendingSources.value = []
  }

  function loadMessages(
    items: Array<{ id: string; role: string; content: string; status?: ChatMessage['status']; sources?: unknown }>,
  ) {
    messages.value = items
      .filter((m) => m.role === 'user' || m.role === 'assistant')
      .map((m) => {
        const sources = m.role === 'assistant' ? normalizeSources(m.sources) : []
        return {
          id: m.id,
          role: m.role as 'user' | 'assistant',
          content: m.content,
          streaming: false,
          status: m.status ?? 'completed',
          sources: sources.length ? sources : undefined,
        }
      })
    pendingSources.value = []
    isStreaming.value = false
  }

  return {
    messages,
    conversationId,
    isStreaming,
    agentId,
    addMessage,
    appendToLast,
    stageSources,
    attachSourcesToLastAssistant,
    finishLastAssistant,
    interruptLastAssistant,
    cancelLastAssistant,
    clear,
    loadMessages,
  }
})
