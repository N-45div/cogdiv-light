import { describe, expect, it } from 'vitest'
import { HOUR, SNAPSHOT, type Series, windiestWindow } from './wind'

describe('windiestWindow', () => {
  it('finds the windiest two hours before 7 am (3 Nov 2026 snapshot)', () => {
    const win = windiestWindow(SNAPSHOT)
    // Expected output peaks 20:00-21:59 UK time (GMT in November).
    expect(win).toEqual({ start: Date.UTC(2026, 10, 3, 20), end: Date.UTC(2026, 10, 3, 22) })
  })

  it('ignores hours after the next 7 am', () => {
    const start = Date.UTC(2026, 10, 3, 22)
    const s: Series = {
      actual: [],
      published: null,
      // Calm overnight, very windy after 07:00.
      outlook: Array.from({ length: 14 }, (_, i) => {
        const expected = i >= 9 ? 9000 : 1000 + i * 10
        return { t: start + i * HOUR, forecast: expected, expected, low: expected, high: expected }
      }),
    }
    const win = windiestWindow(s)!
    expect(win.end).toBeLessThanOrEqual(Date.UTC(2026, 10, 4, 7))
  })

  it('has nothing to say without a forecast', () => {
    expect(windiestWindow({ actual: [], outlook: [], published: null })).toBeNull()
  })
})
