import { defineStore } from 'pinia'
import { ref } from 'vue'
import client from '@/api/client'

export interface Conversation {
  id: string
  title: string
  workspace: string
  created_at: string
  updated_at: string
}

export interface StoredMessage {
  id: string
  role: string
  content: string
  sources?: Array<{ source: string; score: number; content: string }> | null
  created_at: string
}

export const useConversationStore = defineStore('conversation', () => {
  const list = ref<Conversation[]>([])
  const currentId = ref('')

  async function fetchList() {
    const { data } = await client.get<Conversation[]>('/conversations')
    list.value = data
  }

  async function create(title = '新对话') {
    const { data } = await client.post<Conversation>('/conversations', { title })
    list.value.unshift(data)
    currentId.value = data.id
    return data
  }

  function updateLocalTitle(id: string, title: string) {
    const item = list.value.find((c) => c.id === id)
    if (item) item.title = title
  }

  async function remove(id: string) {
    await client.delete(`/conversations/${id}`)
    list.value = list.value.filter((c) => c.id !== id)
    if (currentId.value === id) currentId.value = ''
  }

  async function fetchMessages(conversationId: string): Promise<StoredMessage[]> {
    const { data } = await client.get<StoredMessage[]>(`/conversations/${conversationId}/messages`)
    return data
  }

  return { list, currentId, fetchList, create, remove, fetchMessages, updateLocalTitle }
})
