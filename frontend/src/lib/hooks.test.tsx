// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { useLoad, useUrlState } from './hooks'

/** A promise we resolve or reject by hand, to control the order responses arrive in. */
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('useLoad', () => {
  it('keeps the previous data on screen while the next request loads', async () => {
    const first = deferred<string>()
    const second = deferred<string>()
    const requests: Record<string, Promise<string>> = { a: first.promise, b: second.promise }
    const { result, rerender } = renderHook(({ key }) => useLoad(() => requests[key], key), {
      initialProps: { key: 'a' },
    })
    expect(result.current).toMatchObject({ data: undefined, loading: true })

    await act(async () => first.resolve('day A'))
    expect(result.current).toMatchObject({ data: 'day A', loading: false })

    rerender({ key: 'b' })
    expect(result.current).toMatchObject({ data: 'day A', loading: true })

    await act(async () => second.resolve('day B'))
    expect(result.current).toMatchObject({ data: 'day B', loading: false })
  })

  it('ignores a slow response for a selection the user has already left', async () => {
    const slow = deferred<string>()
    const fast = deferred<string>()
    const requests: Record<string, Promise<string>> = { a: slow.promise, b: fast.promise }
    const { result, rerender } = renderHook(({ key }) => useLoad(() => requests[key], key), {
      initialProps: { key: 'a' },
    })

    rerender({ key: 'b' }) // user moves on before "a" answers
    await act(async () => fast.resolve('day B'))
    await act(async () => slow.resolve('day A (stale)'))

    expect(result.current).toMatchObject({ data: 'day B', loading: false })
  })

  it('reports an error but keeps the last good data', async () => {
    const ok = deferred<string>()
    const failing = deferred<string>()
    const requests: Record<string, Promise<string>> = { a: ok.promise, b: failing.promise }
    const { result, rerender } = renderHook(({ key }) => useLoad(() => requests[key], key), {
      initialProps: { key: 'a' },
    })
    await act(async () => ok.resolve('day A'))

    rerender({ key: 'b' })
    await act(async () => failing.reject(new Error('503: unavailable')))

    await waitFor(() => expect(result.current.error).toBe('503: unavailable'))
    expect(result.current.data).toBe('day A')
  })
})

describe('useUrlState', () => {
  afterEach(() => window.history.replaceState(null, '', '/'))

  it('reads the selection from the URL and defaults the view', () => {
    window.history.replaceState(null, '', '/?date=2026-01-15&cutoff=09:50')
    const { result } = renderHook(() => useUrlState())
    expect(result.current[0]).toEqual({ view: 'day', date: '2026-01-15', cutoff: '09:50' })
  })

  it('writes changes to the URL so the screen can be shared', () => {
    const { result } = renderHook(() => useUrlState())
    act(() => result.current[1]({ view: 'accuracy', cutoff: '09:50' }))
    expect(window.location.search).toBe('?view=accuracy&cutoff=09%3A50')
    expect(result.current[0].view).toBe('accuracy')
  })

  it('follows the browser back button', () => {
    const { result } = renderHook(() => useUrlState())
    act(() => result.current[1]({ date: '2026-01-20' }))
    act(() => {
      window.history.replaceState(null, '', '/?date=2026-01-10')
      window.dispatchEvent(new PopStateEvent('popstate'))
    })
    expect(result.current[0].date).toBe('2026-01-10')
  })
})
