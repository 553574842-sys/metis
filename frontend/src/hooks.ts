import { useEffect, useState } from 'react'
import { api, ApiError } from './api'

export function useData<T>(path: string, interval = 0, reconnectOnWake = true, keepPreviousData = true) {
  const [result, setResult] = useState<{ path: string; data: T | null; error: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    let active = true, inFlight = false, failures = 0
    let timer: ReturnType<typeof setTimeout> | undefined
    let timeout: ReturnType<typeof setTimeout> | undefined
    let controller: AbortController | undefined
    async function fetchData() {
      if (!active || inFlight) return
      clearTimeout(timer)
      inFlight = true
      controller = new AbortController()
      timeout = setTimeout(() => controller?.abort(), 20000)
      let retry = false
      try {
        const value = await api<T>(path, { signal: controller.signal })
        if (active) { setResult({ path, data: value, error: '' }); failures = 0 }
      } catch (e) {
        if (active) {
          const error = e as Error
          setResult(previous => ({ path, data: previous && (keepPreviousData || previous.path === path) ? previous.data : null,
            error: error.name === 'AbortError' ? '读取超时，正在自动重连' : error.message }))
          retry = !(e instanceof ApiError) || e.status >= 500 || e.status === 429
          failures++
        }
      } finally {
        clearTimeout(timeout)
        inFlight = false
        if (active) {
          setLoading(false)
          // Wait for completion before scheduling: slow reads cannot pile up or overwrite newer results.
          if (retry && reconnectOnWake || failures === 0 && interval > 0)
            timer = setTimeout(() => void fetchData(), retry ? Math.min(30000, 5000 * failures) : interval)
        }
      }
    }
    const reconnect = () => { if (reconnectOnWake && document.visibilityState !== 'hidden') void fetchData() }
    const restored = (event: PageTransitionEvent) => { if (event.persisted) reconnect() }
    setLoading(true)
    void fetchData()
    window.addEventListener('online', reconnect)
    window.addEventListener('focus', reconnect)
    window.addEventListener('pageshow', restored)
    window.addEventListener('atlas:session-restored', reconnect)
    document.addEventListener('visibilitychange', reconnect)
    return () => {
      active = false; clearTimeout(timer); clearTimeout(timeout); controller?.abort()
      window.removeEventListener('online', reconnect)
      window.removeEventListener('focus', reconnect)
      window.removeEventListener('pageshow', restored)
      window.removeEventListener('atlas:session-restored', reconnect)
      document.removeEventListener('visibilitychange', reconnect)
    }
  }, [path, interval, revision, reconnectOnWake, keepPreviousData])
  const current = keepPreviousData || result?.path === path ? result : null
  return { data: current?.data ?? null, error: current?.error ?? '', loading: !current || loading, reload: () => setRevision(x => x + 1) }
}
