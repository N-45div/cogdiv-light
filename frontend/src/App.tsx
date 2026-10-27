import type { ReactNode } from 'react'
import { api, type Meta } from './api'
import { CutoffPicker, DayPicker } from './components/Controls'
import { Notice } from './components/ui'
import { formatUkDateTime } from './lib/format'
import { useLoad, useUrlState, type View } from './lib/hooks'
import { resolveSelection } from './lib/selection'
import { AccuracyView } from './views/AccuracyView'
import { DayView } from './views/DayView'

const TABS: { view: View; label: string }[] = [
  { view: 'day', label: 'Day-ahead view' },
  { view: 'accuracy', label: 'Forecast accuracy' },
]

export default function App() {
  const meta = useLoad(api.meta, 'meta')
  const [url, setUrl] = useUrlState()

  if (meta.error) {
    return (
      <Shell>
        <Notice error>Could not load the wind data. {meta.error}</Notice>
      </Shell>
    )
  }
  if (!meta.data) {
    return (
      <Shell>
        <Notice>Loading wind data…</Notice>
      </Shell>
    )
  }

  const m = meta.data
  // Malformed or out-of-scope URL values fall back to the defaults.
  const { date, cutoff } = resolveSelection(url, m)

  return (
    <Shell
      tabs={
        <div className="tabs" role="tablist">
          {TABS.map((tab) => (
            <button
              key={tab.view}
              role="tab"
              aria-selected={url.view === tab.view}
              onClick={() => setUrl({ view: tab.view })}
            >
              {tab.label}
            </button>
          ))}
        </div>
      }
      footer={<DataFooter meta={m} />}
    >
      <div className="controls">
        {url.view === 'day' && (
          <DayPicker
            value={date}
            min={m.delivery_first}
            max={m.delivery_last}
            onChange={(value) => setUrl({ date: value })}
          />
        )}
        <CutoffPicker
          presets={m.cutoff_presets}
          value={cutoff}
          onChange={(value) => setUrl({ cutoff: value })}
        />
      </div>
      {url.view === 'day' ? (
        <DayView date={date} cutoff={cutoff} />
      ) : (
        <AccuracyView cutoff={cutoff} onSelectDay={(day) => setUrl({ view: 'day', date: day })} />
      )}
    </Shell>
  )
}

function Shell(props: { children: ReactNode; tabs?: ReactNode; footer?: ReactNode }) {
  return (
    <div className="app">
      <header className="header">
        <div className="brand">
          <img src="/favicon.svg" alt="" />
          <div>
            <h1>Wind Forecast Monitor</h1>
            <p>GB national wind generation · day-ahead purchase decisions · January 2026</p>
          </div>
        </div>
        {props.tabs}
      </header>
      <main>{props.children}</main>
      {props.footer}
    </div>
  )
}

function DataFooter({ meta }: { meta: Meta }) {
  const d = meta.data
  return (
    <footer className="footer">
      Data: Elexon BMRS —{' '}
      <a href="https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/FUELHH/stream">
        FUELHH
      </a>{' '}
      (actual wind outturn, half-hourly) and{' '}
      <a href="https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/WINDFOR/stream">
        WINDFOR
      </a>{' '}
      (wind forecasts, {d.forecast_runs.toLocaleString('en-GB')} runs). Retrieved{' '}
      {formatUkDateTime(d.retrieved_at)} UK time ({d.source}). All times are UK local time.{' '}
      <a href="/api/docs">API docs</a>
    </footer>
  )
}
