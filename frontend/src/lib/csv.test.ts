import { describe, expect, it } from 'vitest'
import { toCsv } from './csv'

describe('toCsv', () => {
  it('writes a header and rows, leaving missing values empty', () => {
    expect(toCsv(['time', 'mw'], [['09:00', 1200], ['09:30', null]])).toBe(
      'time,mw\n09:00,1200\n09:30,\n',
    )
  })

  it('quotes cells containing commas or quotes', () => {
    expect(toCsv(['note'], [['a, "b"']])).toBe('note\n"a, ""b"""\n')
  })
})
