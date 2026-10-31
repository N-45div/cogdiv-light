import { describe, expect, it } from 'vitest'
import type { OutlookView } from '../api'
import { modelName, outcomeLabel, outlookRows, planWindows } from './agent'

const outlook = {
  recent: {
    hours: 6,
    mean_error_mw: 100,
    points: [{ time: '2026-01-08T17:00:00Z', actual_mw: 9_000, forecast_mw: 9_100 }],
  },
  points: [
    {
      time: '2026-01-08T18:00:00Z',
      forecast_mw: 10_000,
      expected_mw: 8_000,
      low_mw: 7_000,
      high_mw: 9_000,
      lead_hours: 1,
      publish_time: '2026-01-08T17:30:00Z',
    },
    {
      time: '2026-01-08T19:00:00Z',
      forecast_mw: 11_000,
      expected_mw: null,
      low_mw: null,
      high_mw: null,
      lead_hours: 2,
      publish_time: '2026-01-08T17:30:00Z',
    },
  ],
} as unknown as OutlookView

describe('outlookRows', () => {
  it('merges recent actuals with the forecast, in time order', () => {
    const rows = outlookRows(outlook)
    expect(rows.map((r) => new Date(r.t).toISOString().slice(11, 16))).toEqual(['17:00', '18:00', '19:00'])
    expect(rows[0]).toMatchObject({ actual: 9_000, forecast: null })
    expect(rows[1]).toMatchObject({ forecast: 10_000, expected: 8_000, band: [7_000, 9_000] })
    expect(rows[2].band).toBeNull()
  })
})

describe('planWindows', () => {
  it('describes each charging window', () => {
    const plan = { charge_windows: [{ from: 'Fri 02:00', to: 'Fri 04:00', kwh: 14 }] }
    expect(planWindows({ plan })).toEqual(['Fri 02:00 to Fri 04:00, 14 kWh'])
    expect(planWindows({ plan: null })).toEqual([])
  })
})

describe('labels', () => {
  it('names models and watcher outcomes for people', () => {
    expect(modelName('meta:muse-spark-1.3')).toBe('Muse Spark 1.3 (Meta)')
    expect(modelName('baseten:meta-models/muse-spark-open-1026')).toBe('Muse Spark Open (Baseten)')
    expect(modelName('fake')).toBe('fake')
    expect(outcomeLabel('better_window')).toEqual({ label: 'Better window proposed', tone: 'good' })
  })
})
