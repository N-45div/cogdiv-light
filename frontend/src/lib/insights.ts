import type { LeadTimeAccuracy } from '../api'

function weightedMae(rows: LeadTimeAccuracy[]): number | null {
  const usable = rows.filter((r) => r.mae_mw !== null && r.n > 0)
  const total = usable.reduce((sum, r) => sum + r.n, 0)
  if (total === 0) return null
  return usable.reduce((sum, r) => sum + (r.mae_mw as number) * r.n, 0) / total
}

export interface LeadComparison {
  dayAheadMae: number
  shortTermMae: number
  /** How much smaller the short-term error is, as a % of the day-ahead error. */
  improvementPct: number
}

/**
 * Compare forecasts made in the day-ahead decision window with those made shortly before
 * delivery (under `shortTermHours` ahead). This gap is the price of deciding a day early.
 */
export function compareLeadTimes(
  byLead: LeadTimeAccuracy[],
  window: { min: number | null; max: number | null },
  shortTermHours = 6,
): LeadComparison | null {
  if (window.min === null || window.max === null) return null
  const dayAhead = weightedMae(
    byLead.filter((r) => r.lead_hours >= Math.floor(window.min!) && r.lead_hours <= Math.floor(window.max!)),
  )
  const shortTerm = weightedMae(byLead.filter((r) => r.lead_hours < shortTermHours))
  if (dayAhead === null || shortTerm === null || dayAhead === 0) return null
  return {
    dayAheadMae: dayAhead,
    shortTermMae: shortTerm,
    improvementPct: ((dayAhead - shortTerm) / dayAhead) * 100,
  }
}

export type BiasDirection = 'short' | 'long' | 'neutral'

/** A bias under 10% of the typical error is treated as "no clear tendency". */
export function biasDirection(biasMw: number | null | undefined, maeMw: number | null | undefined): BiasDirection {
  if (biasMw == null || maeMw == null || Math.abs(biasMw) < 0.1 * maeMw) return 'neutral'
  return biasMw > 0 ? 'short' : 'long'
}
