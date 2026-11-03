import { describe, expect, it } from 'vitest'
import { CARBON_BINS, carbonColor, formatG, formatKg, lessPct, topFuel } from './carbon'
import { planWindows } from './agent'

describe('carbon helpers', () => {
  it('bins carbon intensity from light (clean) to dark (dirty)', () => {
    expect(carbonColor(0)).toBe(CARBON_BINS[0].color)
    expect(carbonColor(24.9)).toBe(CARBON_BINS[0].color)
    expect(carbonColor(25)).toBe(CARBON_BINS[1].color)
    expect(carbonColor(380)).toBe(CARBON_BINS[7].color)
    expect(carbonColor(null)).toBe('transparent')
  })

  it('formats figures for people', () => {
    expect(formatKg(4.576)).toBe('4.6 kg')
    expect(formatKg(0.208)).toBe('0.21 kg')
    expect(formatKg(31.2)).toBe('31 kg')
    expect(formatKg(6240)).toBe('6.2 t')
    expect(formatKg(0)).toBe('0 kg')
    expect(formatG(181.6)).toBe('182 g/kWh')
    expect(formatG(null)).toBe('n/a')
  })

  it('says how much less', () => {
    expect(lessPct(36, 75)).toBe(52)
    expect(lessPct(0, 75)).toBe(100)
    expect(lessPct(5, 0)).toBeNull()
  })

  it('finds the biggest fuel in a mix', () => {
    expect(topFuel({ wind: 62, gas: 20, solar: 0 })).toEqual(['wind', 62])
    expect(topFuel({})).toBeNull()
  })

  it('lists a compute plan by region', () => {
    const plan = { best: { region: 'North Scotland', run_windows: [{ from: 'Fri 01:00', to: 'Fri 03:00' }] } }
    expect(planWindows({ plan })).toEqual(['Fri 01:00 to Fri 03:00 in North Scotland'])
  })
})
