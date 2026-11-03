import { useCallback, useEffect, useEffectEvent, useState } from 'react'

export interface Loadable<T> {
  data: T | undefined
  error: string | undefined
  loading: boolean
}

interface Result<T> {
  key: string | null // which request this result belongs to
  data?: T
  error?: string
}

/**
 * Load data whenever `key` changes. While a new request is in flight the previous data is
 * kept (shown dimmed by the caller), so the page does not jump around between selections.
 */
export function useLoad<T>(load: () => Promise<T>, key: string): Loadable<T> {
  const [result, setResult] = useState<Result<T>>({ key: null })
  // An effect event always sees the latest `load` without re-running the effect.
  const runLoad = useEffectEvent(load)

  useEffect(() => {
    let cancelled = false
    runLoad().then(
      (data) => {
        if (!cancelled) setResult({ key, data })
      },
      (error: unknown) => {
        if (cancelled) return
        const message = error instanceof Error ? error.message : String(error)
        setResult((previous) => ({ key, data: previous.data, error: message }))
      },
    )
    return () => {
      cancelled = true
    }
  }, [key])

  const current = result.key === key
  return { data: result.data, error: current ? result.error : undefined, loading: !current }
}

/**
 * useLoad, refreshed every `everyMs`. Old data stays on screen between refreshes.
 */
export function usePolling<T>(load: () => Promise<T>, key: string, everyMs: number): Loadable<T> {
  const [tick, setTick] = useState(0)
  useEffect(() => {
    const id = window.setInterval(() => setTick((t) => t + 1), everyMs)
    return () => window.clearInterval(id)
  }, [everyMs])
  return useLoad(load, `${key}#${tick}`)
}

export type View = 'day' | 'accuracy' | 'agent' | 'grid'

export interface UrlState {
  view: View
  date: string | null
  cutoff: string | null
}

function readUrl(): UrlState {
  const params = new URLSearchParams(window.location.search)
  return {
    // The agent tab is the front page; the January study views are one click away.
    view: (['day', 'accuracy', 'grid'] as const).find((v) => v === params.get('view')) ?? 'agent',
    date: params.get('date'),
    cutoff: params.get('cutoff'),
  }
}

/** View, date and cut-off live in the URL, so any screen can be bookmarked or shared. */
export function useUrlState(): [UrlState, (patch: Partial<UrlState>) => void] {
  const [state, setState] = useState<UrlState>(readUrl)

  useEffect(() => {
    const onPop = () => setState(readUrl())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  const update = useCallback((patch: Partial<UrlState>) => {
    // The URL is the source of truth, so build the next state from it.
    const next = { ...readUrl(), ...patch }
    const params = new URLSearchParams()
    params.set('view', next.view)
    if (next.date) params.set('date', next.date)
    if (next.cutoff) params.set('cutoff', next.cutoff)
    window.history.pushState(null, '', `?${params.toString()}`)
    setState(next)
  }, [])

  return [state, update]
}

/** The current time, refreshed every `everyMs`, so labels like "seen 3 min ago" stay true. */
export function useNow(everyMs: number): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), everyMs)
    return () => window.clearInterval(id)
  }, [everyMs])
  return now
}
