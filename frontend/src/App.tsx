import type { ReactNode } from 'react'
import { api, type Meta } from './api'
import { CutoffPicker, DayPicker } from './components/Controls'
import { Notice } from './components/ui'
import { formatUkDateTime } from './lib/format'
import { useLoad, useUrlState, type View } from './lib/hooks'
import { resolveSelection } from './lib/selection'
import { AccuracyView } from './views/AccuracyView'
import { AgentView } from './views/AgentView'
import { DayView } from './views/DayView'
import { GridView } from './views/GridView'

const TABS: { view: View; label: string; title: string; intro: string }[] = [
  {
    view: 'agent',
    label: 'Live',
    title: 'Live console',
    intro:
      "Muse Spark plans flexible demand into the windiest hours. Every figure is fact-checked before a plan reaches the board, where it waits for a button press.",
  },
  {
    view: 'grid',
    label: 'Grid & compute',
    title: 'Where and when the grid is cleanest',
    intro:
      "NESO's carbon forecast for Britain's 14 regions, half-hour by half-hour. Work that can wait or move, from an EV charge to a GPU training run, can follow the wind to the cleanest hours and places.",
  },
  {
    view: 'day',
    label: 'Day-ahead',
    title: 'What the desk knew',
    intro:
      'The latest wind forecast published before the day-ahead auction closed, with its likely range and what actually blew.',
  },
  {
    view: 'accuracy',
    label: 'Accuracy',
    title: 'How far to trust the forecast',
    intro: 'January 2026, scored at decision time: how big the misses were, which way they leaned, and when.',
  },
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

  const tab = TABS.find((t) => t.view === url.view) ?? TABS[0]

  return (
    <Shell
      tabs={
        <div className="tabs" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.view}
              role="tab"
              aria-selected={url.view === t.view}
              onClick={() => setUrl({ view: t.view })}
            >
              {t.label}
            </button>
          ))}
        </div>
      }
      footer={<DataFooter meta={m} />}
    >
      <div className="page-head">
        <h1>{tab.title}</h1>
        <p>{tab.intro}</p>
      </div>
      {url.view === 'agent' ? (
        <AgentView />
      ) : url.view === 'grid' ? (
        <GridView />
      ) : (
        <>
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
        </>
      )}
    </Shell>
  )
}

function Shell(props: { children: ReactNode; tabs?: ReactNode; footer?: ReactNode }) {
  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-inner">
          <a className="logo" href="/">
            <img src="/favicon.svg" alt="" />
            Wind Buddy
          </a>
          <span className="topbar-page">Console</span>
          {props.tabs}
          <a className="topbar-link" href="/">
            About
          </a>
        </div>
      </header>
      <main className="main">{props.children}</main>
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
