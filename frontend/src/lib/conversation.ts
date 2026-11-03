import type { DeviceSummary } from '../api'

export type Turn =
  | { kind: 'you' | 'board'; at: string; text: string }
  | { kind: 'note'; at: string; text: string; tone: 'good' | 'warn' | 'quiet' }

type DeviceEvent = DeviceSummary['events'][number]

function rest(event: DeviceEvent, prefix: string) {
  return event.event.slice(prefix.length)
}

/** One board event as a line of the conversation. */
function turn(e: DeviceEvent): Turn {
  const at = e.at
  const note = (text: string, tone: 'good' | 'warn' | 'quiet' = 'quiet'): Turn => ({ kind: 'note', at, text, tone })
  const event = e.event
  if (event.startsWith('heard: ')) {
    const text = e.text ?? rest(e, 'heard: ')
    return text && text !== '(nothing)' ? { kind: 'you', at, text } : note('Heard nothing clear')
  }
  if (event.startsWith('said: ')) return { kind: 'board', at, text: e.text ?? rest(e, 'said: ') }
  if (event.startsWith('proposed ')) return note('Checked plan sent to the board')
  if (event.startsWith('approved ')) return note('Approved with the button', 'good')
  if (event.startsWith('rejected ')) return note('Turned down with a tap', 'warn')
  if (event.startsWith('stopped ')) return note('Charging stopped', 'warn')
  if (event.startsWith('alert: ')) return note(rest(e, 'alert: '), 'warn')
  if (event.startsWith('updated to ')) return note(`Firmware updated to ${rest(e, 'updated to ')}`, 'good')
  return note(event.charAt(0).toUpperCase() + event.slice(1))
}

/** The board's latest events (newest first) as a conversation, oldest first. */
export function conversation(events: DeviceEvent[], limit = 16): Turn[] {
  return events.slice(0, limit).reverse().map(turn)
}

/** The device seen most recently. */
export function latestDevice(devices: DeviceSummary[] | undefined): DeviceSummary | undefined {
  return devices?.reduce<DeviceSummary | undefined>(
    (best, d) => (!best || (d.last_seen ?? '') > (best.last_seen ?? '') ? d : best),
    undefined,
  )
}
