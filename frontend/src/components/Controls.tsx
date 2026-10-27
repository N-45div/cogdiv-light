import { useState } from 'react'
import type { CutoffPreset } from '../api'
import { addDays, formatDate } from '../lib/format'

const CUSTOM = 'custom'

/** Decision cut-off: an auction gate closure preset, or any custom time. */
export function CutoffPicker(props: {
  presets: CutoffPreset[]
  value: string
  onChange: (value: string) => void
}) {
  const matchesPreset = props.presets.some((p) => p.time === props.value)
  const [custom, setCustom] = useState(!matchesPreset)

  return (
    <label className="field">
      Decision cut-off (UK time, day before delivery)
      <span className="field-row">
        <select
          value={custom ? CUSTOM : props.value}
          onChange={(event) => {
            if (event.target.value === CUSTOM) {
              setCustom(true)
            } else {
              setCustom(false)
              props.onChange(event.target.value)
            }
          }}
        >
          {props.presets.map((preset) => (
            <option key={preset.key} value={preset.time}>
              {preset.label}
            </option>
          ))}
          <option value={CUSTOM}>Custom time…</option>
        </select>
        {custom && (
          <input
            type="time"
            aria-label="Custom cut-off time"
            value={props.value}
            onChange={(event) => event.target.value && props.onChange(event.target.value)}
          />
        )}
      </span>
    </label>
  )
}

/** Delivery day picker limited to the days in scope, with previous/next buttons. */
export function DayPicker(props: {
  value: string
  min: string
  max: string
  onChange: (value: string) => void
}) {
  const step = (days: number) => props.onChange(addDays(props.value, days))
  return (
    <label className="field">
      Delivery day
      <span className="field-row">
        <button
          type="button"
          className="icon-button"
          aria-label="Previous day"
          disabled={props.value <= props.min}
          onClick={() => step(-1)}
        >
          ‹
        </button>
        <input
          type="date"
          value={props.value}
          min={props.min}
          max={props.max}
          aria-label={`Delivery day, ${formatDate(props.value)}`}
          onChange={(event) => {
            const value = event.target.value
            if (value >= props.min && value <= props.max) props.onChange(value)
          }}
        />
        <button
          type="button"
          className="icon-button"
          aria-label="Next day"
          disabled={props.value >= props.max}
          onClick={() => step(1)}
        >
          ›
        </button>
      </span>
    </label>
  )
}
