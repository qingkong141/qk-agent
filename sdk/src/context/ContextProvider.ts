import type { SDKConfig } from '../types'

export class ContextProvider {
  private context: Record<string, unknown> = {}

  constructor(
    private config: SDKConfig,
    private request: <T>(path: string, options?: RequestInit) => Promise<T>,
  ) {}

  set(context: Record<string, unknown>): void {
    this.context = { ...context }
    const workspace = this.config.workspace || 'default'
    this.request(`/context/${workspace}`, {
      method: 'POST',
      body: JSON.stringify(context),
    }).catch(() => {
      // 静默失败，本地 context 仍可用
    })
  }

  get(): Record<string, unknown> {
    return { ...this.context }
  }

  clear(): void {
    this.context = {}
    const workspace = this.config.workspace || 'default'
    this.request(`/context/${workspace}`, { method: 'DELETE' }).catch(() => {})
  }
}
