// Helpers for the grid view: the carbon colour scale and plain-English figures.

/** Carbon intensity is a magnitude, so one hue from light (clean) to dark (dirty), in bins
 * people can read off the legend. Darker means more CO2, like smoke. */
export const CARBON_BINS: { below: number; color: string; label: string }[] = [
  { below: 25, color: '#eef3f9', label: '0' },
  { below: 50, color: '#d6e1ee', label: '25' },
  { below: 100, color: '#b9cbe0', label: '50' },
  { below: 150, color: '#96aecb', label: '100' },
  { below: 200, color: '#7290b4', label: '150' },
  { below: 250, color: '#50719a', label: '200' },
  { below: 300, color: '#33527c', label: '250' },
  { below: Infinity, color: '#16305a', label: '300+' },
]

export function carbonColor(g: number | null | undefined): string {
  if (g === null || g === undefined || Number.isNaN(g)) return 'transparent'
  return (CARBON_BINS.find((b) => g < b.below) ?? CARBON_BINS[CARBON_BINS.length - 1]).color
}

/** "4.6 kg", "0.21 kg", "1.2 t" */
export function formatKg(kg: number | null | undefined): string {
  if (kg === null || kg === undefined) return 'n/a'
  if (kg === 0) return '0 kg'
  if (kg >= 1000) return `${(kg / 1000).toLocaleString('en-GB', { maximumFractionDigits: 1 })} t`
  if (kg >= 10) return `${kg.toFixed(0)} kg`
  if (kg >= 1) return `${kg.toFixed(1)} kg`
  return `${kg.toFixed(2)} kg`
}

/** "182 g/kWh" */
export function formatG(g: number | null | undefined): string {
  return g === null || g === undefined ? 'n/a' : `${Math.round(g)} g/kWh`
}

/** The fuel with the biggest share in a generation mix: ["wind", 62]. */
export function topFuel(mix: Record<string, number>): [string, number] | null {
  const entries = Object.entries(mix).filter(([, v]) => v > 0)
  if (!entries.length) return null
  return entries.reduce((a, b) => (b[1] > a[1] ? b : a))
}

/** How much less `kg` is than `base`, in whole percent ("52%"), or null. */
export function lessPct(kg: number | null | undefined, base: number | null | undefined): number | null {
  if (kg === null || kg === undefined || !base) return null
  return Math.round((1 - kg / base) * 100)
}
