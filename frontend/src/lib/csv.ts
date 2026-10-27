// Small CSV export so the desk can take the numbers into their own spreadsheets.

type Cell = string | number | null | undefined

export function toCsv(header: string[], rows: Cell[][]): string {
  const escape = (cell: Cell) => {
    if (cell === null || cell === undefined) return ''
    const text = String(cell)
    return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text
  }
  return [header, ...rows].map((row) => row.map(escape).join(',')).join('\n') + '\n'
}

export function downloadCsv(filename: string, csv: string): void {
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}
