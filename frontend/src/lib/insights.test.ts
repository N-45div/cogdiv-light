import { describe, expect, it } from 'vitest'
import type { LeadTimeAccuracy } from '../api'
import { biasDirection, compareLeadTimes } from './insights'

const row = (lead_hours: number, mae_mw: number, n = 10): LeadTimeAccuracy => ({
  lead_hours,
  n,
  mae_mw,
  rmse_mw: null,
  bias_mw: null,
  p90_abs_error_mw: null,
})

describe('compareLeadTimes', () => {
  it('weights each lead-time bucket by its number of comparisons', () => {
    const rows = [row(1, 200, 10), row(3, 400, 30), row(20, 1000, 10), row(30, 1200, 10)]
    const result = compareLeadTimes(rows, { min: 15.5, max: 38.5 })
    expect(result).not.toBeNull()
    expect(result!.shortTermMae).toBe(350) // (200*10 + 400*30) / 40
    expect(result!.dayAheadMae).toBe(1100)
    expect(result!.improvementPct).toBeCloseTo(68.18, 1)
  })

  it('returns null without a decision window or data', () => {
    expect(compareLeadTimes([row(1, 200)], { min: null, max: null })).toBeNull()
    expect(compareLeadTimes([], { min: 15, max: 38 })).toBeNull()
  })
})

describe('biasDirection', () => {
  it('reads positive bias (forecast too high) as the desk being short', () => {
    expect(biasDirection(300, 1000)).toBe('short')
    expect(biasDirection(-300, 1000)).toBe('long')
  })

  it('ignores a bias that is small next to the typical error', () => {
    expect(biasDirection(50, 1000)).toBe('neutral')
    expect(biasDirection(null, 1000)).toBe('neutral')
  })
})
