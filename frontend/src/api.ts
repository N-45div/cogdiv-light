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

// ---- planning, agent, devices, watcher (backend/wind_monitor/schemas.py, second half) ----

export interface ClockState {
  mode: 'live' | 'replay'
  now: string
  speed: number // simulated seconds per real second (0 = paused)
  first: string | null
  last: string | null
}

export interface ClockUpdate {
  mode?: 'live' | 'replay'
  at?: string // "YYYY-MM-DDTHH:MM", UK time
  speed?: number
  advance_minutes?: number
}

export interface LeadStats {
  lead_bucket: string // e.g. "0-6h"
  n: number
  bias_mw: number | null
  mae_mw: number | null
  over_forecast_share_pct: number | null
  actual_share_pct: number | null // metered wind as % of the forecast (median)
}

export interface OutlookPoint {
  time: string
  forecast_mw: number | null
  expected_mw: number | null // forecast scaled by its track record
  low_mw: number | null
  high_mw: number | null
  lead_hours: number
  publish_time: string
}

export interface OutlookView {
  as_of: string
  mode: string
  data_source: string
  latest_run: string | null
  latest_actual: { time: string; actual_mw: number | null } | null
  history: { days: number; n: number; by_lead: LeadStats[] }
  band_note: string
  points: OutlookPoint[]
  recent: {
    hours: number
    mean_error_mw: number | null
    points: { time: string; actual_mw: number | null; forecast_mw: number | null }[]
  }
}

export interface FactClaim {
  text: string
  kind: 'time' | 'number'
  supported: boolean
  source: string | null
}

export interface ToolCall {
  name: string
  arguments: Record<string, unknown> | string | null
  result: Record<string, unknown>
}

export interface AgentRun {
  id: string
  question: string
  as_of: string
  mode: string
  answer: string | null
  verified: boolean
  factcheck: { passed: boolean; summary: string; claims: FactClaim[] } | null
  models: string[]
  tool_calls: ToolCall[]
  plan: Record<string, unknown> | null
  kind?: 'charge' | 'compute'
  schedule: { start: string; end: string; energy_kwh: number }[]
  rewrites: number
  seconds: number
  error: string | null
  proposed_to?: string | null
}

export interface AgentRunSummary {
  id: string
  question: string
  as_of: string
  answer: string | null
  verified: boolean
  summary: string | null
  models: string[]
  seconds: number
}

export interface DeviceSummary {
  device_id: string
  last_seen: string | null
  version: number
  proposal_id: string | null
  active_id: string | null
  telemetry: Record<string, unknown>
  /** Newest first. Heard and said events carry the whole sentence in `text`. */
  events: { at: string; event: string; text?: string }[]
  /** Firmware offered over the air and not yet running. */
  update_to?: string | null
  /** The board's own log lines (last 20, oldest first). */
  logs?: { at: string; line: string }[]
}

export interface WatchFinding {
  id: string
  device_id: string
  checked_at: string
  outcome: 'better_window' | 'below_plan' | 'on_track' | 'no_forecast' | 'done'
  facts: Record<string, unknown>
  message: string
  note: string | null
  note_check: string | null
  model: string | null
  proposal_id: string | null
}

// ---- carbon intensity and compute jobs ----

export interface CarbonRegion {
  id: number
  name: string
  now_g: number | null // g CO2/kWh in the current half-hour
  index: string | null // NESO's band: very low .. very high
  mix: Record<string, number> // forecast generation mix now, % by fuel
  g: (number | null)[] // one per CarbonView.times
}

export interface CarbonView {
  as_of: string
  fetched_at: string
  source: string
  times: string[] // half-hour starts, UTC
  national: (number | null)[]
  gb_now_g: number | null
  regions: CarbonRegion[] // cleanest first right now
}

export interface ComputeRequest {
  gpus: number
  hours: number
  earliest?: string
  deadline?: string
  interruptible: boolean
  regions?: number[]
  baseline?: number
}

export interface SitePlan {
  region_id: number
  region: string
  feasible: boolean
  windows: { start: string; end: string; energy_kwh: number }[]
  average_g: number | null
  kg_co2: number | null
}

export interface ComputePlan {
  as_of: string
  job: {
    gpus: number
    hours: number
    kw_per_gpu: number
    power_kw: number
    energy_kwh: number
    earliest: string
    deadline: string
    interruptible: boolean
  }
  feasible: boolean
  best: SitePlan | null
  baseline: SitePlan // London, started straight away
  time_only: SitePlan // London at its cleanest time
  place_only: SitePlan | null // the best region, started straight away
  pct_less_co2: number | null
  kg_co2_saved: number | null
  sites: SitePlan[]
}

export interface GridBrief {
  at: string
  text: string
  by: string // model, or "code"
  check: string
  facts: Record<string, unknown>
}

export class ApiError extends Error {}

async function getJson<T>(path: string, params: Record<string, string> = {}): Promise<T> {
  const query = new URLSearchParams(params).toString()
  return readJson<T>(await fetch(query ? `${path}?${query}` : path))
}

/** POST with the app token (needed to ask the agent, move the clock, run the watcher). */
async function postJson<T>(path: string, body: unknown, appToken: string): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (appToken) headers['X-App-Token'] = appToken
  return readJson<T>(await fetch(path, { method: 'POST', headers, body: JSON.stringify(body) }))
}

async function readJson<T>(response: Response): Promise<T> {
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
  clock: () => getJson<ClockState>('/api/clock'),
  outlook: (hours = 24) => getJson<OutlookView>('/api/outlook', { hours: String(hours) }),
  runs: () => getJson<AgentRunSummary[]>('/api/agent/runs'),
  run: (id: string) => getJson<AgentRun>(`/api/agent/runs/${encodeURIComponent(id)}`),
  devices: () => getJson<DeviceSummary[]>('/api/devices'),
  watch: () => getJson<WatchFinding[]>('/api/watch'),
  ask: (question: string, deviceId: string, appToken: string) =>
    postJson<AgentRun>(
      '/api/agent/ask',
      deviceId ? { question, device_id: deviceId } : { question },
      appToken,
    ),
  setClock: (update: ClockUpdate, appToken: string) =>
    postJson<ClockState>('/api/clock', update, appToken),
  runWatch: (appToken: string) => postJson<WatchFinding[]>('/api/watch/run', {}, appToken),
  carbon: (hours = 24) => getJson<CarbonView>('/api/carbon', { hours: String(hours) }),
  brief: () => getJson<GridBrief>('/api/brief'),
  computePlan: (request: ComputeRequest) => postJson<ComputePlan>('/api/compute/plan', request, ''),
}
