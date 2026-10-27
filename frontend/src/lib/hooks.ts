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

export type View = 'day' | 'accuracy'

export interface UrlState {
  view: View
  date: string | null
  cutoff: string | null
}

function readUrl(): UrlState {
  const params = new URLSearchParams(window.location.search)
  return {
    view: params.get('view') === 'accuracy' ? 'accuracy' : 'day',
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
