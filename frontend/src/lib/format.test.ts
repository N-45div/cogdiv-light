import { describe, expect, it } from 'vitest'
import {
  addDays,
  formatDate,
  formatGWh,
  formatHours,
  formatMW,
  formatSignedMW,
  formatUkDateTime,
  formatUkTime,
} from './format'

describe('number formatting', () => {
  it('formats MW with thousands separators', () => {
    expect(formatMW(12345.6)).toBe('12,346 MW')
    expect(formatMW(null)).toBe('–')
  })

  it('shows the sign of an error explicitly', () => {
    expect(formatSignedMW(1250)).toBe('+1,250 MW')
    expect(formatSignedMW(-300)).toBe('−300 MW')
    expect(formatSignedMW(0.2)).toBe('0 MW')
  })

  it('converts MWh to GWh with one decimal', () => {
    expect(formatGWh(234_560)).toBe('234.6 GWh')
  })

  it('trims trailing zeros on hours', () => {
    expect(formatHours(15.5)).toBe('15.5 h')
    expect(formatHours(24)).toBe('24 h')
  })
})

describe('UK time formatting', () => {
  it('shows GMT times unchanged in winter', () => {
    expect(formatUkTime('2026-01-14T09:20:00Z')).toBe('09:20')
  })

  it('shifts to BST in summer', () => {
    expect(formatUkTime('2026-07-14T08:20:00Z')).toBe('09:20')
  })

  it('includes the weekday and date', () => {
    expect(formatUkDateTime('2026-01-14T08:30:00Z')).toBe('Wed 14 Jan, 08:30')
  })
})

describe('calendar dates', () => {
  it('formats without shifting the day', () => {
    expect(formatDate('2026-01-15')).toBe('Thu 15 Jan 2026')
  })

  it('adds days across month ends', () => {
    expect(addDays('2026-01-31', 1)).toBe('2026-02-01')
    expect(addDays('2026-01-02', -1)).toBe('2026-01-01')
  })
})
