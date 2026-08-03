export interface SDKConfig {
  baseUrl: string
  apiKey?: string
  token?: string
  workspace?: string
  maxRetries?: number
  requestTimeoutMs?: number
}

export interface ChatOptions {
  message: string
  conversationId?: string
  context?: Record<string, unknown>
  useWorkflow?: boolean
  maxRetries?: number
  timeoutMs?: number
}

export interface ChatResponse {
  conversation_id: string
  output: string
  intermediate_steps?: Array<{ tool: string; result: string }>
}

export type StreamEventType =
  | 'token'
  | 'agent_start'
  | 'tool_call'
  | 'tool_result'
  | 'agent_end'
  | 'done'
  | 'error'

export interface StreamEvent {
  type: StreamEventType
  data: unknown
}

export interface ToolCallData {
  tool: string
  args: unknown
}

export interface ToolResultData {
  tool?: string
  result: string
}

export interface DoneData {
  session_id: string
  output: string
  intermediate_steps?: Array<{ tool: string; result: string }>
}

export interface ErrorData {
  code: string
  message: string
}

export type StreamEventHandler<T = unknown> = (data: T) => void

export interface StreamSubscription {
  on(event: StreamEventType, handler: StreamEventHandler): StreamSubscription
  cancel(): void
}

export interface AgentInfo {
  id: string
  name: string
  role: string
  capabilities: string
}

export interface DocumentInfo {
  id: string
  filename: string
  status: string
  chunk_count: number
}
