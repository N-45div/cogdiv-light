import { useEffect, useRef, useState, type ReactNode } from 'react'
import {
  api,
  type AgentRun,
  type AgentRunSummary,
  type ClockState,
  type ClockUpdate,
  type DeviceSummary,
  type OutlookView,
  type WatchFinding,
} from '../api'
import { OutlookChart } from '../charts/OutlookChart'
import { Card, LegendItem, Notice, StatTile } from '../components/ui'
import {
  loadToken,
  modelName,
  outcomeLabel,
  planWindows,
  saveToken,
} from '../lib/agent'
import { conversation, latestDevice } from '../lib/conversation'
import { formatMW, formatUkDateTime, formatUkTime } from '../lib/format'
import { useLoad, useNow, usePolling } from '../lib/hooks'

const POLL_MS = 5000
const DEFAULT_QUESTION = 'My EV needs 21 kWh at 7 kW, ready by 07:00. When should it charge?'
const EXAMPLES: [string, string][] = [
  ['EV charge', DEFAULT_QUESTION],
  ['Cleanest charge', 'Charge 14 kWh at 7 kW by 07:00, at the lowest carbon.'],
  ['AI compute job', 'I need to run 64 GPUs for 6 hours, done within 24 hours. Where and when is cleanest?'],
  ['Grid now', 'Where in Britain is the grid cleanest right now, and when is it cleanest tonight?'],
]

/** Live planning: the outlook, the agent and its fact checks, the devices, and the watcher. */
export function AgentView() {
  const clock = usePolling(api.clock, 'clock', POLL_MS)
  // Reload the outlook whenever the planning clock moves on by ten minutes (fast in replay).
  const outlookKey = clock.data ? `${clock.data.mode}|${clock.data.now.slice(0, 15)}` : 'start'
  const outlook = useLoad(() => api.outlook(24), outlookKey)
  const runs = usePolling(api.runs, 'runs', POLL_MS)
  const devices = usePolling(api.devices, 'devices', POLL_MS)
  const watch = usePolling(api.watch, 'watch', POLL_MS)
  const [token, setToken] = useState(loadToken)

  return (
    <div>
      <StatusTiles clock={clock.data} outlook={outlook.data} devices={devices.data} />

      <div className="grid-main">
        <Card
          title="Wind outlook"
          subtitle={outlook.data?.band_note}
          foot={
            outlook.data &&
            `Newest forecast published ${
              outlook.data.latest_run ? formatUkDateTime(outlook.data.latest_run) : 'n/a'
            } · ${outlook.data.data_source} data · UK time`
          }
        >
          <div className="legend">
            <LegendItem color="var(--series-actual)" label="Actual" />
            <LegendItem color="var(--series-official)" label="Official forecast" />
            <LegendItem color="var(--series-expected)" label="Expected, after correction" />
            <LegendItem color="var(--band)" label="Likely range (80%)" area />
          </div>
          {outlook.error && <Notice error>Could not load the outlook: {outlook.error}</Notice>}
          {outlook.data ? <OutlookChart outlook={outlook.data} /> : <Notice>Loading the outlook…</Notice>}
        </Card>
        <ConversationCard devices={devices.data} />
      </div>

      <div className="grid-2">
        <AskCard token={token} />
        <ClockCard clock={clock.data} token={token} setToken={setToken} />
      </div>
      <div className="grid-2">
        <RunsCard runs={runs.data} error={runs.error} />
        <div>
          <DevicesCard devices={devices.data} />
          <WatchCard findings={watch.data} />
        </div>
      </div>
    </div>
  )
}

function StatusTiles(props: {
  clock?: ClockState
  outlook?: OutlookView
  devices?: DeviceSummary[]
}) {
  const { clock, outlook, devices } = props
  const share = outlook?.history.by_lead.find((b) => b.lead_bucket === '0-6h')?.actual_share_pct
  const next = outlook?.points.find((p) => p.expected_mw !== null)
  const device = latestDevice(devices)
  return (
    <div className="tiles">
      <StatTile
        label="Planning clock"
        value={clock ? formatUkDateTime(clock.now) : '…'}
        note={
          clock &&
          (clock.mode === 'live'
            ? 'Live: real time, Elexon data'
            : `Replay of January 2026 · ${clock.speed ? `${clock.speed}x` : 'paused'}`)
        }
      />
      <StatTile
        label="Wind now (actual)"
        value={formatMW(outlook?.latest_actual?.actual_mw)}
        note={outlook?.latest_actual && `half-hour from ${formatUkDateTime(outlook.latest_actual.time)}`}
      />
      <StatTile
        label="Expected next hour"
        value={formatMW(next?.expected_mw)}
        note={next && `forecast says ${formatMW(next.forecast_mw)}`}
      />
      <StatTile
        label="Forecast track record"
        value={share !== null && share !== undefined ? `${Math.round(share)}%` : '–'}
        note={`metered wind as a share of the forecast, last ${outlook?.history.days ?? 28} days`}
      />
      <StatTile
        label="Device"
        value={device ? device.device_id : 'none yet'}
        note={
          device
            ? `${device.proposal_id ? 'plan waiting for the button' : device.active_id ? 'plan approved' : 'idle'}${
                device.last_seen ? ` · seen ${formatUkDateTime(device.last_seen)}` : ''
              }`
            : 'appears once a device polls'
        }
      />
    </div>
  )
}

const ONLINE_MS = 2 * 60_000

/** What the board heard and said, as a chat: the voice loop, seen from the web. */
function ConversationCard(props: { devices?: DeviceSummary[] }) {
  const device = latestDevice(props.devices)
  const turns = device ? conversation(device.events) : []
  const now = useNow(POLL_MS)
  const online = device?.last_seen ? now - Date.parse(device.last_seen) < ONLINE_MS : false
  const chat = useRef<HTMLDivElement>(null)
  const newest = device?.events[0]?.at ?? ''

  // Keep the newest line in view as the conversation grows.
  useEffect(() => {
    if (chat.current) chat.current.scrollTop = chat.current.scrollHeight
  }, [newest, turns.length])

  return (
    <section className="card convo">
      <div className="card-head">
        <div>
          <h2>Talking to {device?.device_id ?? 'the board'}</h2>
          <p>What the board heard and said</p>
        </div>
        {device && (
          <span className={online ? 'status-pill good' : 'status-pill'}>
            <span className={online ? 'dot' : 'dot off'} /> {online ? 'online' : 'offline'}
          </span>
        )}
      </div>
      <div className="convo-chat" ref={chat} aria-live="polite">
        {turns.length === 0 && (
          <p className="convo-empty">
            Nothing said yet. Hold the board's button and ask: “When should I charge 20 kWh by 7 am?”
          </p>
        )}
        {turns.map((t, i) =>
          t.kind === 'note' ? (
            <div key={`${t.at}-${i}`} className={`convo-note ${t.tone}`}>
              {t.text} <time>{formatUkTime(t.at)}</time>
            </div>
          ) : (
            <div key={`${t.at}-${i}`} className={`convo-bubble ${t.kind}`}>
              <span className="convo-who">
                {t.kind === 'you' ? 'You' : 'Wind Buddy'} · {formatUkTime(t.at)}
              </span>
              {t.text}
            </div>
          ),
        )}
      </div>
    </section>
  )
}

function AskCard(props: { token: string }) {
  const [question, setQuestion] = useState(DEFAULT_QUESTION)
  const [deviceId, setDeviceId] = useState('desk-1')
  const [busy, setBusy] = useState(false)
  const [run, setRun] = useState<AgentRun>()
  const [error, setError] = useState<string>()

  const ask = async () => {
    setBusy(true)
    setError(undefined)
    try {
      setRun(await api.ask(question, deviceId.trim(), props.token))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title="Ask the agent" subtitle="Muse Spark plans with the tools; every figure is checked.">
      <div className="form">
        <div className="chips" role="group" aria-label="Example questions">
          {EXAMPLES.map(([label, text]) => (
            <button
              key={label}
              className={question === text ? 'chip on' : 'chip'}
              onClick={() => setQuestion(text)}
              type="button"
            >
              {label}
            </button>
          ))}
        </div>
        <textarea
          rows={3}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          aria-label="Question"
        />
        <div className="form-row">
          <label className="field">
            <span>Send the plan to device</span>
            <input value={deviceId} onChange={(e) => setDeviceId(e.target.value)} placeholder="none" />
          </label>
          <button className="button primary" onClick={ask} disabled={busy || !question.trim()}>
            {busy ? 'Thinking…' : 'Ask'}
          </button>
        </div>
      </div>
      {error && <Notice error>{error.startsWith('401') ? 'Wrong or missing app token (set it under Clock & demo).' : error}</Notice>}
      {run && <RunDetail run={run} />}
    </Card>
  )
}

function RunDetail(props: { run: AgentRun }) {
  const { run } = props
  const windows = planWindows(run)
  return (
    <div className="run">
      <p className="answer">{run.answer ?? run.error}</p>
      <div className="meta-row">
        <FactBadge verified={run.verified} summary={run.factcheck?.summary} />
        <span>{[...new Set(run.models)].map(modelName).join(', ')}</span>
        <span>{run.seconds.toFixed(1)} s</span>
        {run.rewrites > 0 && <span>rewritten once after the check</span>}
      </div>
      {windows.length > 0 && (
        <ul className="windows">
          {windows.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
      {run.proposed_to && (
        <p className="hint">
          Sent to <strong>{run.proposed_to}</strong>: hold the button on the device to approve.
        </p>
      )}
      {run.factcheck && run.factcheck.claims.length > 0 && (
        <details>
          <summary>Every figure checked ({run.factcheck.claims.length})</summary>
          <table className="claims">
            <tbody>
              {run.factcheck.claims.map((c, i) => (
                <tr key={`${c.text}-${i}`}>
                  <td>{c.supported ? '✓' : '✗'}</td>
                  <td>{c.text}</td>
                  <td className="muted">{c.source ?? 'not in any tool result'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
      {run.tool_calls.length > 0 && (
        <details>
          <summary>Tool calls ({run.tool_calls.map((t) => t.name).join(', ')})</summary>
          {run.tool_calls.map((t, i) => (
            <pre key={i} className="json">
              {t.name}({typeof t.arguments === 'string' ? t.arguments : JSON.stringify(t.arguments)})
              {'\n→ '}
              {JSON.stringify(t.result, null, 1)}
            </pre>
          ))}
        </details>
      )}
    </div>
  )
}

function FactBadge(props: { verified: boolean; summary?: string | null }) {
  return (
    <span className={props.verified ? 'badge good' : 'badge bad'}>
      {props.summary ?? (props.verified ? 'verified' : 'not verified')}
    </span>
  )
}

function ClockCard(props: {
  clock?: ClockState
  token: string
  setToken: (token: string) => void
}) {
  const [error, setError] = useState<string>()
  const [found, setFound] = useState<string>()

  const act = async (work: () => Promise<unknown>) => {
    setError(undefined)
    try {
      await work()
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      setError(message.startsWith('401') ? 'Wrong or missing app token.' : message)
    }
  }
  const set = (update: ClockUpdate) => act(() => api.setClock(update, props.token))
  const replay = props.clock?.mode === 'replay'

  return (
    <Card title="Clock & demo" subtitle="Live uses real time; replay runs January 2026 as if it were now.">
      <div className="form">
        <label className="field">
          <span>App token (kept in this browser)</span>
          <input
            type="password"
            value={props.token}
            onChange={(e) => {
              props.setToken(e.target.value)
              saveToken(e.target.value)
            }}
            placeholder="needed to ask, move the clock, run the watcher"
          />
        </label>
        <div className="button-row">
          <button className="button" aria-pressed={!replay} onClick={() => set({ mode: 'live' })}>
            Live
          </button>
          <button
            className="button"
            aria-pressed={replay}
            onClick={() => set({ mode: 'replay', at: '2026-01-20T18:00', speed: 0 })}
          >
            Replay 20 Jan, 18:00
          </button>
        </div>
        {replay && (
          <div className="button-row">
            <button className="button" onClick={() => set({ speed: 0 })}>
              Pause
            </button>
            <button className="button" onClick={() => set({ speed: 60 })}>
              1 min/s
            </button>
            <button className="button" onClick={() => set({ speed: 900 })}>
              15 min/s
            </button>
            <button className="button" onClick={() => set({ advance_minutes: 60 })}>
              +1 hour
            </button>
          </div>
        )}
        <div className="button-row">
          <button
            className="button"
            onClick={() =>
              act(async () => {
                const findings = await api.runWatch(props.token)
                setFound(
                  findings.length
                    ? findings.map((f) => `${f.device_id}: ${outcomeLabel(f.outcome).label}`).join('; ')
                    : 'No approved plans to check.',
                )
              })
            }
          >
            Run the watcher now
          </button>
        </div>
      </div>
      {found && <p className="hint">{found}</p>}
      {error && <Notice error>{error}</Notice>}
    </Card>
  )
}

function RunsCard(props: { runs?: AgentRunSummary[]; error?: string }) {
  return (
    <Card title="Agent log" subtitle="Every question, the model that answered, and its fact check.">
      {props.error && <Notice error>{props.error}</Notice>}
      {props.runs && props.runs.length === 0 && <p className="hint">No questions yet.</p>}
      <ul className="log">
        {props.runs?.map((r) => (
          <RunItem key={r.id} run={r} />
        ))}
      </ul>
    </Card>
  )
}

function RunItem(props: { run: AgentRunSummary }) {
  const [open, setOpen] = useState(false)
  const r = props.run
  return (
    <li>
      <button className="log-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="log-q">{r.question}</span>
        <span className="meta-row">
          <FactBadge verified={r.verified} summary={r.summary} />
          <span>{[...new Set(r.models)].map(modelName).join(', ') || 'watcher'}</span>
          <span>{formatUkDateTime(r.as_of)}</span>
        </span>
      </button>
      {open && <RunLoader id={r.id} />}
    </li>
  )
}

function RunLoader(props: { id: string }) {
  const run = useLoad(() => api.run(props.id), props.id)
  if (run.error) return <Notice error>{run.error}</Notice>
  return run.data ? <RunDetail run={run.data} /> : <p className="hint">Loading…</p>
}

function DevicesCard(props: { devices?: DeviceSummary[] }) {
  return (
    <Card title="Devices" subtitle="ESP32 boards polling the server.">
      {props.devices?.length === 0 && <p className="hint">No device has polled yet.</p>}
      {props.devices?.map((d) => (
        <div key={d.device_id} className="device">
          <div className="meta-row">
            <strong>{d.device_id}</strong>
            <span className={d.proposal_id ? 'badge warn' : d.active_id ? 'badge good' : 'badge quiet'}>
              {d.proposal_id ? 'waiting for button' : d.active_id ? 'plan approved' : 'idle'}
            </span>
            {d.last_seen && <span>seen {formatUkDateTime(d.last_seen)}</span>}
            {typeof d.telemetry.rssi === 'number' && <span>Wi-Fi {d.telemetry.rssi} dBm</span>}
            {typeof d.telemetry.firmware === 'string' && <span>firmware {d.telemetry.firmware}</span>}
            {d.update_to && <span className="badge warn">updating to {d.update_to}</span>}
          </div>
          <ul className="events">
            {d.events.slice(0, 5).map((e, i) => (
              <li key={`${e.at}-${i}`}>
                <span className="muted">{formatUkDateTime(e.at)}</span> {e.event}
              </li>
            ))}
          </ul>
          {d.logs && d.logs.length > 0 && (
            <details className="device-log">
              <summary>Board log ({d.logs.length} latest lines)</summary>
              <pre>{d.logs.map((l) => l.line).join('\n')}</pre>
            </details>
          )}
        </div>
      ))}
    </Card>
  )
}

function WatchCard(props: { findings?: WatchFinding[] }) {
  return (
    <Card title="Watcher" subtitle="Re-checks approved plans every 30 minutes as forecasts update.">
      {props.findings?.length === 0 && <p className="hint">Nothing checked yet.</p>}
      <ul className="log">
        {props.findings?.slice(0, 8).map((f) => {
          const { label, tone } = outcomeLabel(f.outcome)
          return (
            <li key={f.id}>
              <div className="meta-row">
                <span className={`badge ${tone}`}>{label}</span>
                <span>{f.device_id}</span>
                <span>{formatUkDateTime(f.checked_at)}</span>
              </div>
              <p>{f.note ?? f.message}</p>
              <Small>
                {f.note ? `${modelName(f.model ?? '')} · ${f.note_check}` : 'written by code'}
              </Small>
            </li>
          )
        })}
      </ul>
    </Card>
  )
}

function Small(props: { children: ReactNode }) {
  return <p className="muted small">{props.children}</p>
}
