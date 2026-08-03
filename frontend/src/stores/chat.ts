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
  sources?: SourceCitation[]
}

export const useChatStore = defineStore('chat', () => {
  const messages = ref<ChatMessage[]>([])
  const conversationId = ref('')
  const isStreaming = ref(false)
  const useWorkflow = ref(true)
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

  function finishLastAssistant(output: string) {
    const last = [...messages.value].reverse().find((m) => m.role === 'assistant')
    if (last) {
      if (output?.trim()) {
        last.content = output
      }
      if (pendingSources.value.length) {
        last.sources = pendingSources.value
        pendingSources.value = []
      }
      last.streaming = false
    }
    isStreaming.value = false
  }

  function clear() {
    messages.value = []
    conversationId.value = ''
    pendingSources.value = []
  }

  function loadMessages(
    items: Array<{ id: string; role: string; content: string; sources?: unknown }>,
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
    useWorkflow,
    addMessage,
    appendToLast,
    stageSources,
    attachSourcesToLastAssistant,
    finishLastAssistant,
    clear,
    loadMessages,
  }
})
