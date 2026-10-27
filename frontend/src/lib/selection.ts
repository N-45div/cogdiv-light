// Rules for turning URL parameters into a valid selection, and for flagging stale forecasts.
// Kept free of React so they are easy to test.

const CUTOFF_PATTERN = /^([01]\d|2[0-3]):[0-5]\d$/
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/

/** "HH:MM", 00:00-23:59. */
export function isValidCutoff(value: string | null | undefined): value is string {
  return typeof value === 'string' && CUTOFF_PATTERN.test(value)
}

/** A real calendar date in "YYYY-MM-DD" form (rejects 2026-02-30, 2026-1-5, ...). */
export function isValidDate(value: string | null | undefined): value is string {
  if (typeof value !== 'string' || !DATE_PATTERN.test(value)) return false
  const parsed = new Date(`${value}T00:00:00Z`)
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value
}

export interface Scope {
  delivery_first: string
  delivery_last: string
  default_cutoff: string
}

/** Use the URL's date and cut-off when they are valid and in scope, otherwise the defaults. */
export function resolveSelection(
  url: { date: string | null; cutoff: string | null },
  scope: Scope,
): { date: string; cutoff: string } {
  const dateOk = isValidDate(url.date) && url.date >= scope.delivery_first && url.date <= scope.delivery_last
  return {
    date: dateOk ? url.date! : scope.delivery_first,
    cutoff: isValidCutoff(url.cutoff) ? url.cutoff : scope.default_cutoff,
  }
}

/** Forecasts are published every 2-4 hours; older than this at the cut-off is unusual. */
export const STALE_FORECAST_HOURS = 6

/** How old the forecast was when the decision was made, in hours. */
export function forecastAgeHours(cutoffTime: string, publishTime: string): number {
  return (Date.parse(cutoffTime) - Date.parse(publishTime)) / 3_600_000
}
