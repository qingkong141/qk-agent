import type { AgentInfo, SDKConfig } from '../types'

export class AgentClient {
  constructor(
    private config: SDKConfig,
    private request: <T>(path: string, options?: RequestInit) => Promise<T>,
  ) {}

  async list(): Promise<AgentInfo[]> {
    return this.request<AgentInfo[]>('/agents')
  }

  async get(id: string): Promise<AgentInfo> {
    return this.request<AgentInfo>(`/agents/${id}`)
  }

  async create(data: { name: string; role?: string; capabilities?: string }): Promise<AgentInfo> {
    return this.request<AgentInfo>('/agents', {
      method: 'POST',
      body: JSON.stringify(data),
    })
  }
}
