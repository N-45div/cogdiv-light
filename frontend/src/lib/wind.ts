import type { OutlookView } from '../api'
import { formatUkTime } from './format'

export interface Series {
  actual: { t: number; mw: number }[]
  outlook: { t: number; forecast: number; expected: number; low: number; high: number }[]
  published: string | null
}

export const HOUR = 3_600_000

/** The chart's data from the live outlook (null when it has no forecast to draw). */
export function seriesFrom(o: OutlookView): Series | null {
  const actual = o.recent.points
    .filter((p) => p.actual_mw !== null)
    .map((p) => ({ t: Date.parse(p.time), mw: p.actual_mw as number }))
  if (o.latest_actual?.actual_mw != null) {
    const t = Date.parse(o.latest_actual.time)
    if (!actual.length || actual[actual.length - 1].t < t) actual.push({ t, mw: o.latest_actual.actual_mw })
  }
  const outlook = o.points
    .filter((p) => p.forecast_mw !== null && p.expected_mw !== null && p.low_mw !== null && p.high_mw !== null)
    .map((p) => ({
      t: Date.parse(p.time),
      forecast: p.forecast_mw as number,
      expected: p.expected_mw as number,
      low: p.low_mw as number,
      high: p.high_mw as number,
    }))
  return outlook.length ? { actual, outlook, published: o.latest_run } : null
}

/** The windiest run of `hours` consecutive hours before the next 07:00 UK time. */
export function windiestWindow(s: Series, hours = 2): { start: number; end: number } | null {
  const pts = s.outlook
  const first = pts[0]?.t
  if (first === undefined) return null
  // The next 07:00 in the UK after the first point (UK is UTC or UTC+1).
  let deadline = first
  while (formatUkTime(deadline) !== '07:00' && deadline - first < 36 * HOUR) deadline += HOUR / 2
  let best: { start: number; end: number; mw: number } | null = null
  for (let i = 0; i + hours <= pts.length; i++) {
    const run = pts.slice(i, i + hours)
    if (run[run.length - 1].t + HOUR > deadline) break
    const mw = run.reduce((sum, p) => sum + p.expected, 0)
    if (!best || mw > best.mw) best = { start: run[0].t, end: run[run.length - 1].t + HOUR, mw }
  }
  return best && { start: best.start, end: best.end }
}

const iso = (day: number, hour: number) => Date.UTC(2026, 10, day, hour)
const FORECAST = [6590, 6924, 7351, 7702, 8074, 8297, 8418, 8426, 8240, 7772, 7255, 6786, 6595, 6585, 6764, 7012, 7330, 7604, 7790, 7906, 7841, 7628, 7357, 7236]
const EXPECTED = [5557, 5838, 6198, 6494, 6707, 6892, 6993, 6999, 6845, 6456, 6003, 5615, 5457, 5449, 5597, 5802, 6065, 6292, 6446, 6542, 6488, 6312, 6124, 6024]
const LOW = [4432, 4657, 4944, 5180, 5366, 5514, 5595, 5600, 5476, 5165, 4736, 4430, 4305, 4299, 4416, 4577, 4785, 4964, 5085, 5161, 5119, 4980, 4672, 4595]
const HIGH = [7094, 7454, 7914, 8292, 8591, 8828, 8957, 8966, 8768, 8270, 7575, 7086, 6886, 6876, 7063, 7322, 7654, 7940, 8134, 8255, 8187, 7965, 7835, 7706]

/** The outlook on 3 Nov 2026 at 14:37 UK time, shown when the live server can't be reached. */
export const SNAPSHOT: Series = {
  actual: [
    { t: iso(3, 9), mw: 7471 },
    { t: iso(3, 10), mw: 7382 },
    { t: iso(3, 11), mw: 7844 },
    { t: iso(3, 12), mw: 7338 },
    { t: iso(3, 13), mw: 7554 },
    { t: iso(3, 13) + HOUR / 2, mw: 7719 },
  ],
  outlook: FORECAST.map((forecast, i) => ({
    t: iso(3, 14 + i),
    forecast,
    expected: EXPECTED[i],
    low: LOW[i],
    high: HIGH[i],
  })),
  published: '2026-11-03T11:30:00Z',
}
