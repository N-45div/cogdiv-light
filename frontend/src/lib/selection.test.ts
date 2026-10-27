import { describe, expect, it } from 'vitest'
import { forecastAgeHours, isValidCutoff, isValidDate, resolveSelection } from './selection'

const scope = { delivery_first: '2026-01-02', delivery_last: '2026-02-01', default_cutoff: '09:20' }

describe('isValidCutoff', () => {
  it('accepts HH:MM from 00:00 to 23:59', () => {
    expect(['00:00', '09:20', '23:59'].every(isValidCutoff)).toBe(true)
  })

  it('rejects anything else', () => {
    expect(['9:20', '24:00', '09:60', '09:20:00', 'abc', '', null].some(isValidCutoff)).toBe(false)
  })
})

describe('isValidDate', () => {
  it('accepts real dates only', () => {
    expect(isValidDate('2026-01-15')).toBe(true)
    expect(isValidDate('2026-02-30')).toBe(false)
    expect(isValidDate('2026-1-5')).toBe(false)
    expect(isValidDate('15/01/2026')).toBe(false)
    expect(isValidDate(null)).toBe(false)
  })
})

describe('resolveSelection', () => {
  it('keeps valid, in-scope values from the URL', () => {
    expect(resolveSelection({ date: '2026-01-15', cutoff: '09:50' }, scope)).toEqual({
      date: '2026-01-15',
      cutoff: '09:50',
    })
  })

  it('falls back to defaults for missing, malformed or out-of-scope values', () => {
    const fallback = { date: '2026-01-02', cutoff: '09:20' }
    expect(resolveSelection({ date: null, cutoff: null }, scope)).toEqual(fallback)
    expect(resolveSelection({ date: '2026-1-5', cutoff: 'abc' }, scope)).toEqual(fallback)
    expect(resolveSelection({ date: '2026-01-01', cutoff: '24:00' }, scope)).toEqual(fallback)
    expect(resolveSelection({ date: '2026-02-02', cutoff: '9:20' }, scope)).toEqual(fallback)
  })

  it('accepts the first and last delivery days', () => {
    expect(resolveSelection({ date: '2026-02-01', cutoff: null }, scope).date).toBe('2026-02-01')
  })
})

describe('forecastAgeHours', () => {
  it('measures how old the forecast was at the cut-off', () => {
    expect(forecastAgeHours('2026-01-14T09:20:00Z', '2026-01-14T07:30:00Z')).toBeCloseTo(1.833, 3)
  })
})
