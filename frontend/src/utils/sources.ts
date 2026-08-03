import type { SourceCitation } from '@/stores/chat'

export function normalizeSources(raw: unknown): SourceCitation[] {
  if (!raw || raw === 'null') return []

  if (typeof raw === 'string') {
    try {
      return normalizeSources(JSON.parse(raw))
    } catch {
      return []
    }
  }

  if (!Array.isArray(raw)) return []

  return raw
    .filter((item): item is SourceCitation => {
      return Boolean(item && typeof item === 'object' && 'source' in item)
    })
    .map((item) => ({
      source: String(item.source),
      score: Number(item.score) || 0,
      content: String(item.content ?? ''),
    }))
}
