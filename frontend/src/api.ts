// Typed client for the Python API. These types mirror backend/wind_monitor/schemas.py.
// Times arrive as ISO strings in UTC; the UI converts them to UK time for display.

export interface CutoffPreset {
  key: string
  label: string
  time: string // "HH:MM", UK local time on the day before delivery
}

export interface Meta {
  timezone: string
  decision_first: string
  decision_last: string
  delivery_first: string
  delivery_last: string
  default_cutoff: string
  cutoff_presets: CutoffPreset[]
  data: {
    source: 'snapshot' | 'live'
    retrieved_at: string
    actuals_rows: number
    forecast_rows: number
    forecast_runs: number
    actuals_first: string | null
    actuals_last: string | null
  }
}

export interface ErrorSummary {
  n: number
  mae_mw?: number | null
  rmse_mw?: number | null
  bias_mw?: number | null // mean of forecast - actual; positive = desk short
  median_abs_error_mw?: number | null
  p90_abs_error_mw?: number | null
  max_over_forecast_mw?: number | null
  max_under_forecast_mw?: number | null
  mean_actual_mw?: number | null
  nmae_pct?: number | null
  over_forecast_share_pct?: number | null // % of hours the forecast was too high
  debiased_mae_mw?: number | null // error left after removing the average offset
}

export interface DayPoint {
  time: string
  actual_mw: number | null
  forecast_mw: number | null
  expected_mw: number | null // forecast adjusted by its typical (median) error
  low_mw: number | null
  high_mw: number | null
  error_mw: number | null
}

export interface ForecastRun {
  publish_time: string
  after_cutoff: boolean
  points: { time: string; forecast_mw: number | null }[]
}

export interface DayView {
  delivery_date: string
  decision_date: string
  cutoff: string
  cutoff_time: string
  decision_run: { publish_time: string; lead_hours_min: number; lead_hours_max: number } | null
  summary: {
    forecast_energy_mwh: number | null
    expected_energy_mwh: number | null
    low_energy_mwh: number | null
    high_energy_mwh: number | null
    actual_energy_mwh: number | null
    peak_forecast_mw: number | null
    min_forecast_mw: number | null
    // Coverage: daily totals are null unless the day is complete.
    forecast_hours: number
    expected_hours: number
    actual_periods: number
    expected_periods: number
    accuracy: ErrorSummary
  }
  band_note: string
  points: DayPoint[]
  runs: ForecastRun[]
}

export interface DailyAccuracy {
  delivery_date: string
  n: number
  mae_mw: number | null
  bias_mw: number | null
  max_abs_error_mw: number | null
  forecast_energy_mwh: number | null
  actual_energy_mwh: number | null
}

export interface HourlyAccuracy {
  hour: number
  n: number
  mae_mw: number | null
  mean_error_mw: number | null
  p10_error_mw: number | null
  p50_error_mw: number | null
  p90_error_mw: number | null
}

export interface LeadTimeAccuracy {
  lead_hours: number
  n: number
  mae_mw: number | null
  rmse_mw: number | null
  bias_mw: number | null
  p90_abs_error_mw: number | null
}

export interface AccuracyView {
  cutoff: string
  summary: ErrorSummary
  daily: DailyAccuracy[]
  by_hour: HourlyAccuracy[]
  by_lead_time: LeadTimeAccuracy[]
  decision_lead_hours: { min: number | null; max: number | null }
  histogram: { lower_mw: number; upper_mw: number; count: number }[]
}

export class ApiError extends Error {}

async function getJson<T>(path: string, params: Record<string, string> = {}): Promise<T> {
  const query = new URLSearchParams(params).toString()
  const response = await fetch(query ? `${path}?${query}` : path)
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      // body was not JSON; keep the status text
    }
    throw new ApiError(`${response.status}: ${detail}`)
  }
  return response.json() as Promise<T>
}

export const api = {
  meta: () => getJson<Meta>('/api/meta'),
  day: (deliveryDate: string, cutoff: string) =>
    getJson<DayView>('/api/day', { delivery_date: deliveryDate, cutoff }),
  accuracy: (cutoff: string) => getJson<AccuracyView>('/api/accuracy', { cutoff }),
}
