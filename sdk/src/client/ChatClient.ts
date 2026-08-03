import { createStreamHandler } from '../stream/StreamHandler'
import type { ChatOptions, ChatResponse, SDKConfig, StreamSubscription } from '../types'

export class ChatClient {
  constructor(
    private config: SDKConfig,
    private request: <T>(path: string, options?: RequestInit) => Promise<T>,
  ) {}

  async send(options: ChatOptions): Promise<ChatResponse> {
    return this.request<ChatResponse>('/chat', {
      method: 'POST',
      body: JSON.stringify({
        message: options.message,
        conversation_id: options.conversationId,
        context: options.context,
        use_workflow: options.useWorkflow ?? true,
      }),
    })
  }

  stream(options: ChatOptions): StreamSubscription {
    const conversationId = options.conversationId || crypto.randomUUID()
    return createStreamHandler(
      this.config.baseUrl,
      conversationId,
      {
        message: options.message,
        context: options.context,
        workspace: this.config.workspace,
        useWorkflow: options.useWorkflow,
        maxRetries: options.maxRetries ?? this.config.maxRetries,
        timeoutMs: options.timeoutMs,
      },
      { apiKey: this.config.apiKey, token: this.config.token },
    )
  }

  async executeWorkflow(workflowId: string, input: Record<string, unknown>): Promise<unknown> {
    return this.request(`/workflows/${workflowId}/execute`, {
      method: 'POST',
      body: JSON.stringify({ input }),
    })
  }
}
