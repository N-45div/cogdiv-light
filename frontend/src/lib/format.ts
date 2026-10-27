// Display formatting. All times are shown in UK local time, the time the desk trades in.

const UK_TZ = 'Europe/London'
const DASH = '–'

const ukTimeFormat = new Intl.DateTimeFormat('en-GB', {
  timeZone: UK_TZ,
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})
const ukDateTimeFormat = new Intl.DateTimeFormat('en-GB', {
  timeZone: UK_TZ,
  weekday: 'short',
  day: 'numeric',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})
// Calendar dates ("2026-01-15") carry no time zone, so format them as UTC to avoid day shifts.
const dateFormat = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'UTC',
  weekday: 'short',
  day: 'numeric',
  month: 'short',
  year: 'numeric',
})
const shortDateFormat = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'UTC',
  day: 'numeric',
  month: 'short',
})

const isMissing = (value: number | null | undefined): value is null | undefined =>
  value === null || value === undefined || Number.isNaN(value)

export function formatMW(value: number | null | undefined): string {
  return isMissing(value) ? DASH : `${Math.round(value).toLocaleString('en-GB')} MW`
}

/** Signed MW, e.g. "+1,250 MW" / "−300 MW" (true minus sign for readability). */
export function formatSignedMW(value: number | null | undefined): string {
  if (isMissing(value)) return DASH
  const rounded = Math.round(value)
  const sign = rounded > 0 ? '+' : rounded < 0 ? '−' : ''
  return `${sign}${Math.abs(rounded).toLocaleString('en-GB')} MW`
}

export function formatGWh(mwh: number | null | undefined): string {
  return isMissing(mwh) ? DASH : `${(mwh / 1000).toLocaleString('en-GB', { maximumFractionDigits: 1, minimumFractionDigits: 1 })} GWh`
}

export function formatPct(value: number | null | undefined, digits = 1): string {
  return isMissing(value) ? DASH : `${value.toFixed(digits)}%`
}

export function formatHours(value: number | null | undefined): string {
  return isMissing(value) ? DASH : `${Number(value.toFixed(1))} h`
}

/** "14:30" in UK time, from an ISO timestamp or epoch milliseconds. */
export function formatUkTime(value: string | number): string {
  return ukTimeFormat.format(new Date(value))
}

// Browsers differ on punctuation (e.g. "Thu, 15 Jan"), so assemble strings from parts.
function parts(format: Intl.DateTimeFormat, date: Date): Record<string, string> {
  return Object.fromEntries(format.formatToParts(date).map((p) => [p.type, p.value]))
}

/** "Wed 14 Jan, 08:30" in UK time. */
export function formatUkDateTime(value: string | number): string {
  const p = parts(ukDateTimeFormat, new Date(value))
  return `${p.weekday} ${p.day} ${p.month}, ${p.hour}:${p.minute}`
}

/** "Thu 15 Jan 2026" from a calendar date "2026-01-15". */
export function formatDate(isoDate: string): string {
  const p = parts(dateFormat, new Date(`${isoDate}T00:00:00Z`))
  return `${p.weekday} ${p.day} ${p.month} ${p.year}`
}

/** "15 Jan" from a calendar date. */
export function formatShortDate(isoDate: string): string {
  return shortDateFormat.format(new Date(`${isoDate}T00:00:00Z`))
}

export function addDays(isoDate: string, days: number): string {
  const date = new Date(`${isoDate}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() + days)
  return date.toISOString().slice(0, 10)
}
