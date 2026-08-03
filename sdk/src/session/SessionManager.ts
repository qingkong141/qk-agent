const STORAGE_KEY = 'ai-agent-sessions'

export interface Session {
  id: string
  title: string
  createdAt: string
  updatedAt: string
}

export class SessionManager {
  private sessions: Session[] = []

  constructor() {
    this.load()
  }

  create(title = '新对话'): Session {
    const session: Session = {
      id: crypto.randomUUID(),
      title,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    }
    this.sessions.unshift(session)
    this.save()
    return session
  }

  list(): Session[] {
    return [...this.sessions]
  }

  get(id: string): Session | undefined {
    return this.sessions.find((s) => s.id === id)
  }

  updateTitle(id: string, title: string): void {
    const session = this.sessions.find((s) => s.id === id)
    if (session) {
      session.title = title
      session.updatedAt = new Date().toISOString()
      this.save()
    }
  }

  remove(id: string): void {
    this.sessions = this.sessions.filter((s) => s.id !== id)
    this.save()
  }

  private load(): void {
    try {
      const raw = localStorage.getItem(STORAGE_KEY)
      if (raw) this.sessions = JSON.parse(raw)
    } catch {
      this.sessions = []
    }
  }

  private save(): void {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(this.sessions))
    } catch {
      // localStorage 不可用时忽略
    }
  }
}
