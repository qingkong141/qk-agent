import { AgentClient } from './client/AgentClient'
import { ChatClient } from './client/ChatClient'
import { KnowledgeClient } from './client/KnowledgeClient'
import { ContextProvider } from './context/ContextProvider'
import { SessionManager } from './session/SessionManager'
import type { SDKConfig } from './types'
import { fetchWithRetry } from './utils/fetchWithRetry'

export class AgentSDK {
  public chat: ChatClient
  public agents: AgentClient
  public knowledge: KnowledgeClient
  public context: ContextProvider
  public sessions: SessionManager

  private config: SDKConfig

  constructor(config: SDKConfig) {
    this.config = config
    const request = this.createRequest()
    this.chat = new ChatClient(config, request)
    this.agents = new AgentClient(config, request)
    this.knowledge = new KnowledgeClient(config, request)
    this.context = new ContextProvider(config, request)
    this.sessions = new SessionManager()
  }

  private createRequest() {
    return async <T>(path: string, options: RequestInit = {}): Promise<T> => {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        ...(options.headers as Record<string, string>),
      }
      if (this.config.apiKey) headers['X-API-Key'] = this.config.apiKey
      if (this.config.token) headers['Authorization'] = `Bearer ${this.config.token}`

      const response = await fetchWithRetry(
        `${this.config.baseUrl}${path}`,
        { ...options, headers },
        {
          maxRetries: this.config.maxRetries ?? 2,
          timeoutMs: this.config.requestTimeoutMs ?? 30_000,
        },
      )

      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }))
        throw new Error((error as { detail?: string }).detail || '请求失败')
      }
      return response.json()
    }
  }
}

export { AgentSDK as default }
export * from './types'
export { StreamHandler } from './stream/StreamHandler'
export { ContextProvider } from './context/ContextProvider'
export { SessionManager } from './session/SessionManager'
