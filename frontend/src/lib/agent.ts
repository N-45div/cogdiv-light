// Helpers for the Agent & device tab: chart rows, plan windows, labels, the saved app token.
import type { AgentRun, OutlookView, WatchFinding } from '../api'

export interface OutlookRow {
  t: number
  forecast: number | null
  expected: number | null
  band: [number, number] | null
  actual: number | null
}

/** One row per time: the outlook's hourly forecast and range, plus recent actuals. */
export function outlookRows(outlook: OutlookView): OutlookRow[] {
  const rows = new Map<number, OutlookRow>()
  const row = (t: number) => {
    let r = rows.get(t)
    if (!r) {
      r = { t, forecast: null, expected: null, band: null, actual: null }
      rows.set(t, r)
    }
    return r
  }
  for (const p of outlook.recent.points) row(Date.parse(p.time)).actual = p.actual_mw
  for (const p of outlook.points) {
    const r = row(Date.parse(p.time))
    r.forecast = p.forecast_mw
    r.expected = p.expected_mw
    r.band = p.low_mw !== null && p.high_mw !== null ? [p.low_mw, p.high_mw] : null
  }
  return [...rows.values()].sort((a, b) => a.t - b.t)
}

/** "Fri 02:00 to Fri 04:00, 14 kWh" for each charging window in an agent's plan, or
 * "Fri 01:00 to Fri 03:00 in North Scotland" for each run window of a compute job. */
export function planWindows(run: Pick<AgentRun, 'plan'>): string[] {
  const best = run.plan?.best as { region?: string; run_windows?: { from?: string; to?: string }[] } | undefined
  if (best && Array.isArray(best.run_windows)) {
    return best.run_windows.map((w) => `${w.from ?? '?'} to ${w.to ?? '?'} in ${best.region ?? '?'}`)
  }
  const windows = run.plan?.charge_windows
  if (!Array.isArray(windows)) return []
  return windows.map((w: { from?: string; to?: string; kwh?: number }) => {
    const kwh = typeof w.kwh === 'number' ? `, ${w.kwh} kWh` : ''
    return `${w.from ?? '?'} to ${w.to ?? '?'}${kwh}`
  })
}

const OUTCOMES: Record<WatchFinding['outcome'], { label: string; tone: 'good' | 'warn' | 'quiet' }> =
  {
    better_window: { label: 'Better window proposed', tone: 'good' },
    below_plan: { label: 'Wind below plan', tone: 'warn' },
    on_track: { label: 'On track', tone: 'quiet' },
    no_forecast: { label: 'No forecast yet', tone: 'quiet' },
    done: { label: 'Plan finished', tone: 'quiet' },
  }

export function outcomeLabel(outcome: WatchFinding['outcome']) {
  return OUTCOMES[outcome] ?? { label: outcome, tone: 'quiet' as const }
}

/** "meta:muse-spark-1.3" -> "Muse Spark 1.3 (Meta)" for people reading the log. */
export function modelName(model: string): string {
  const [provider, name = ''] = model.split(':', 2)
  const pretty = name
    .replace(/^meta-models\//, '')
    .replace(/-\d{4}$/, '')
    .split('-')
    .map((part) => (/^\d/.test(part) ? part : part.charAt(0).toUpperCase() + part.slice(1)))
    .join(' ')
  const host = { meta: 'Meta', baseten: 'Baseten', fireworks: 'Fireworks', modal: 'Modal' }[provider]
  return host ? `${pretty} (${host})` : model
}

const TOKEN_KEY = 'wind-buddy-app-token'

/** The app token is a per-browser convenience; storage can be blocked, so never rely on it. */
export function loadToken(): string {
  try {
    return window.localStorage.getItem(TOKEN_KEY) ?? ''
  } catch {
    return ''
  }
}

export function saveToken(token: string): void {
  try {
    window.localStorage.setItem(TOKEN_KEY, token)
  } catch {
    // storage blocked: the token just isn't remembered
  }
}
