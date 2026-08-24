import { createStreamHandler } from '../stream/StreamHandler'
import type { ChatOptions, ChatResponse, SDKConfig, StreamSubscription } from '../types'

export class ChatClient {
  constructor(
    private config: SDKConfig,
    private request: <T>(path: string, options?: RequestInit) => Promise<T>,
  ) {}

  async send(options: ChatOptions): Promise<ChatResponse> {
    this.requireEndUserIdForApiKey()
    return this.request<ChatResponse>('/chat', {
      method: 'POST',
      body: JSON.stringify({
        message: options.message,
        conversation_id: options.conversationId,
        agent_id: options.agentId,
        context: options.context,
        execution_strategy: 'auto',
        approval_id: options.approvalId,
      }),
    })
  }

  stream(options: ChatOptions): StreamSubscription {
    this.requireEndUserIdForApiKey()
    const conversationId = options.conversationId || crypto.randomUUID()
    return createStreamHandler(
      this.config.baseUrl,
      conversationId,
      {
        message: options.message,
        agentId: options.agentId,
        context: options.context,
        maxRetries: options.maxRetries ?? this.config.maxRetries,
        timeoutMs: options.timeoutMs,
        approvalId: options.approvalId,
      },
      {
        apiKey: this.config.apiKey,
        token: this.config.token,
        endUserId: this.config.endUserId,
      },
    )
  }

  private requireEndUserIdForApiKey(): void {
    if (this.config.apiKey && !this.config.endUserId?.trim()) {
      throw new Error('API Key 调用聊天接口时必须配置 endUserId')
    }
  }

  async executeWorkflow(workflowId: string, input: Record<string, unknown>): Promise<unknown> {
    return this.request(`/workflows/${workflowId}/execute`, {
      method: 'POST',
      body: JSON.stringify({ input }),
    })
  }
}
