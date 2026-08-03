export interface FetchRetryOptions {
  maxRetries?: number
  retryDelayMs?: number
  timeoutMs?: number
}

export async function fetchWithRetry(
  url: string,
  options: RequestInit = {},
  retryOptions: FetchRetryOptions = {},
): Promise<Response> {
  const { maxRetries = 2, retryDelayMs = 800, timeoutMs = 30_000 } = retryOptions
  let lastError: Error | null = null

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), timeoutMs)

    try {
      const response = await fetch(url, { ...options, signal: controller.signal })
      clearTimeout(timer)

      if (response.status >= 500 && attempt < maxRetries) {
        await sleep(retryDelayMs * (attempt + 1))
        continue
      }
      return response
    } catch (err) {
      clearTimeout(timer)
      lastError = err instanceof Error ? err : new Error(String(err))
      if (attempt < maxRetries) {
        await sleep(retryDelayMs * (attempt + 1))
      }
    }
  }

  throw lastError ?? new Error('请求失败')
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}
