const rawApiBase = (import.meta.env.VITE_API_BASE_URL || '').trim().replace(/\/$/, '')

export function apiUrl(path: string): string {
  if (!rawApiBase || !path.startsWith('/api')) return path
  return `${rawApiBase}${path}`
}

export function installApiFetchRewrite(): void {
  if (!rawApiBase || typeof window === 'undefined') return
  const originalFetch = window.fetch.bind(window)
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    if (typeof input === 'string' && input.startsWith('/api')) {
      return originalFetch(apiUrl(input), init)
    }
    if (input instanceof URL && input.pathname.startsWith('/api')) {
      return originalFetch(apiUrl(`${input.pathname}${input.search}`), init)
    }
    if (input instanceof Request) {
      const u = new URL(input.url)
      if (u.origin === window.location.origin && u.pathname.startsWith('/api')) {
        return originalFetch(new Request(apiUrl(`${u.pathname}${u.search}`), input), init)
      }
    }
    return originalFetch(input, init)
  }
}
