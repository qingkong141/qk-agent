import type {
  DoneData,
  ErrorData,
  StreamEvent,
  StreamEventHandler,
  StreamEventType,
  StreamSubscription,
} from '../types'

export interface StreamConnectOptions {
  maxRetries?: number
  timeoutMs?: number
  retryDelayMs?: number
}

export class StreamHandler implements StreamSubscription {
  private handlers: Map<StreamEventType, StreamEventHandler[]> = new Map()
  private ws: WebSocket | null = null
  private cancelled = false
  private retryCount = 0
  private finished = false
  private timeoutId: ReturnType<typeof setTimeout> | null = null

  constructor(
    private wsUrl: string,
    private payload: Record<string, unknown>,
    private connectOptions: StreamConnectOptions = {},
  ) {}

  on(event: StreamEventType, handler: StreamEventHandler): StreamSubscription {
    if (!this.handlers.has(event)) {
      this.handlers.set(event, [])
    }
    this.handlers.get(event)!.push(handler)
    return this
  }

  connect(): void {
    if (this.cancelled || this.finished) return

    const { maxRetries = 3, timeoutMs = 120_000, retryDelayMs = 1000 } = this.connectOptions

    this.clearTimeout()
    this.timeoutId = setTimeout(() => {
      if (!this.finished && !this.cancelled) {
        this.ws?.close()
        this.tryReconnect(maxRetries, retryDelayMs, '响应超时')
      }
    }, timeoutMs)

    this.ws = new WebSocket(this.wsUrl)

    this.ws.onopen = () => {
      if (this.cancelled) {
        this.ws?.close()
        return
      }
      this.retryCount = 0
      this.ws?.send(JSON.stringify(this.payload))
    }

    this.ws.onmessage = (event) => {
      try {
        const msg: StreamEvent = JSON.parse(event.data as string)
        this.emit(msg.type, msg.data)
        if (msg.type === 'done') {
          this.finished = true
          this.clearTimeout()
          this.ws?.close()
        } else if (msg.type === 'error') {
          this.finished = true
          this.clearTimeout()
          this.ws?.close()
        }
      } catch {
        this.emit('error', { code: 'PARSE_ERROR', message: '消息解析失败' })
        this.finished = true
        this.clearTimeout()
      }
    }

    this.ws.onerror = () => {
      if (!this.finished && !this.cancelled) {
        this.tryReconnect(maxRetries, retryDelayMs, 'WebSocket 连接错误')
      }
    }

    this.ws.onclose = () => {
      this.ws = null
      if (!this.finished && !this.cancelled) {
        this.tryReconnect(maxRetries, retryDelayMs, '连接意外断开')
      }
    }
  }

  cancel(): void {
    this.cancelled = true
    this.finished = true
    this.clearTimeout()
    this.ws?.close()
    this.ws = null
  }

  private tryReconnect(maxRetries: number, retryDelayMs: number, reason: string): void {
    this.clearTimeout()
    if (this.finished || this.cancelled) return

    if (this.retryCount >= maxRetries) {
      this.finished = true
      this.emit('error', { code: 'WS_RETRY_EXHAUSTED', message: `${reason}，已重试 ${maxRetries} 次` })
      return
    }

    this.retryCount += 1
    setTimeout(() => this.connect(), retryDelayMs * this.retryCount)
  }

  private clearTimeout(): void {
    if (this.timeoutId) {
      clearTimeout(this.timeoutId)
      this.timeoutId = null
    }
  }

  private emit(type: StreamEventType, data: unknown): void {
    const handlers = this.handlers.get(type) || []
    handlers.forEach((h) => h(data))
  }
}

export function createStreamHandler(
  baseUrl: string,
  conversationId: string,
  options: {
    message: string
    context?: Record<string, unknown>
    workspace?: string
    useWorkflow?: boolean
    userId?: string
    maxRetries?: number
    timeoutMs?: number
  },
  auth: { apiKey?: string; token?: string },
): StreamHandler {
  const wsBase = baseUrl.replace(/^http/, 'ws').replace(/\/api\/v1$/, '')
  const wsUrl = `${wsBase}/api/v1/ws/${conversationId}`

  const payload: Record<string, unknown> = {
    type: 'chat',
    session_id: conversationId,
    message: options.message,
    context: options.context,
    workspace: options.workspace,
    use_workflow: options.useWorkflow ?? true,
    user_id: options.userId,
  }

  const handler = new StreamHandler(wsUrl, payload, {
    maxRetries: options.maxRetries,
    timeoutMs: options.timeoutMs,
  })
  handler.connect()
  return handler
}
