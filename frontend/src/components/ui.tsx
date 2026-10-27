import { useState, type ReactNode } from 'react'

export function StatTile(props: {
  label: string
  value: string
  note?: ReactNode
  /** Outcome tiles show what happened after the decision (dashed border). */
  outcome?: boolean
}) {
  return (
    <div className={props.outcome ? 'tile outcome' : 'tile'}>
      <div className="tile-label">{props.label}</div>
      <div className="tile-value">{props.value}</div>
      {props.note && <div className="tile-note">{props.note}</div>}
    </div>
  )
}

/** A titled panel holding a chart, with a switch to the same data as a table. */
export function Card(props: {
  title: string
  subtitle?: ReactNode
  children: ReactNode
  table?: ReactNode
  actions?: ReactNode
  foot?: ReactNode
}) {
  const [showTable, setShowTable] = useState(false)
  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>{props.title}</h2>
          {props.subtitle && <p>{props.subtitle}</p>}
        </div>
        <div className="card-actions">
          {props.actions}
          {props.table && (
            <button className="button" onClick={() => setShowTable(!showTable)}>
              {showTable ? 'Show chart' : 'Show table'}
            </button>
          )}
        </div>
      </div>
      {showTable && props.table ? <div className="table-wrap">{props.table}</div> : props.children}
      {props.foot && <div className="card-foot">{props.foot}</div>}
    </section>
  )
}

export function LegendItem(props: { color: string; label: string; area?: boolean; dashed?: boolean }) {
  const kind = props.area ? 'swatch area' : props.dashed ? 'swatch dashed' : 'swatch'
  const style = props.dashed ? { color: props.color } : { background: props.color }
  return (
    <span>
      <i className={kind} style={style} />
      {props.label}
    </span>
  )
}

export function TooltipBox(props: { title: string; rows: [string, string][] }) {
  return (
    <div className="tooltip">
      <div className="tooltip-title">{props.title}</div>
      {props.rows.map(([label, value]) => (
        <div className="tooltip-row" key={label}>
          <span>{label}</span>
          <b>{value}</b>
        </div>
      ))}
    </div>
  )
}

export function Notice(props: { children: ReactNode; error?: boolean }) {
  return (
    <div className={props.error ? 'notice error' : 'notice'} role={props.error ? 'alert' : 'status'}>
      {props.children}
    </div>
  )
}
