import type { DocumentInfo, SDKConfig } from '../types'

export class KnowledgeClient {
  constructor(
    private config: SDKConfig,
    private request: <T>(path: string, options?: RequestInit) => Promise<T>,
  ) {}

  async list(): Promise<DocumentInfo[]> {
    return this.request<DocumentInfo[]>('/documents')
  }

  async upload(file: File): Promise<{ document_id: string; status: string }> {
    const formData = new FormData()
    formData.append('file', file)

    const headers: Record<string, string> = {}
    if (this.config.apiKey) headers['X-API-Key'] = this.config.apiKey
    if (this.config.token) headers['Authorization'] = `Bearer ${this.config.token}`

    const response = await fetch(`${this.config.baseUrl}/documents/upload`, {
      method: 'POST',
      headers,
      body: formData,
    })

    if (!response.ok) {
      throw new Error(`上传失败: ${response.statusText}`)
    }
    return response.json()
  }

  async search(query: string): Promise<unknown> {
    return this.request('/documents/search', {
      method: 'POST',
      body: JSON.stringify({ query }),
    })
  }
}
