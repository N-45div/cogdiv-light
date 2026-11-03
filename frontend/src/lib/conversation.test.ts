import { describe, expect, it } from 'vitest'
import type { DeviceSummary } from '../api'
import { conversation, latestDevice } from './conversation'

const at = '2026-11-03T10:09:27+00:00'

describe('conversation', () => {
  it('turns board events into a conversation, oldest first', () => {
    const turns = conversation([
      { at, event: "said: Great, it's booked." },
      { at, event: 'approved 32ae1d1b7714' },
      { at, event: "said: Here's my plan. Charge 20:00 to 21:30", text: "Here's my plan. Charge 20:00 to 21:30 to get 10 kWh by 06:00." },
      { at, event: 'proposed 32ae1d1b7714' },
      { at, event: 'heard: I need ten kilowatt hours by six AM.' },
    ])
    expect(turns.map((t) => t.kind)).toEqual(['you', 'note', 'board', 'note', 'board'])
    // The whole sentence, not the event's shortened copy.
    expect(turns[2].text).toBe("Here's my plan. Charge 20:00 to 21:30 to get 10 kWh by 06:00.")
    expect(turns[3]).toMatchObject({ kind: 'note', tone: 'good', text: 'Approved with the button' })
  })

  it('treats an empty transcript as a note', () => {
    expect(conversation([{ at, event: 'heard: (nothing)' }])[0]).toMatchObject({ kind: 'note' })
  })

  it('keeps only the latest events', () => {
    const events = Array.from({ length: 30 }, (_, i) => ({ at, event: `said: line ${i}` }))
    const turns = conversation(events, 5)
    expect(turns).toHaveLength(5)
    expect(turns[4].text).toBe('line 0')
  })
})

describe('latestDevice', () => {
  const device = (id: string, seen: string | null) => ({ device_id: id, last_seen: seen }) as DeviceSummary

  it('picks the device seen most recently', () => {
    const devices = [device('a', '2026-11-03T10:00:00Z'), device('b', '2026-11-03T11:00:00Z'), device('c', null)]
    expect(latestDevice(devices)?.device_id).toBe('b')
    expect(latestDevice([])).toBeUndefined()
  })
})
