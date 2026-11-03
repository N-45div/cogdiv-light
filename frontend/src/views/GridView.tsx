import { useState } from 'react'
import { api, type CarbonView, type ComputePlan, type GridBrief, type SitePlan } from '../api'
import { Card, Notice, StatTile, TooltipBox } from '../components/ui'
import { CARBON_BINS, carbonColor, formatG, formatKg, lessPct, topFuel } from '../lib/carbon'
import { formatUkDateTime, formatUkTime } from '../lib/format'
import { modelName } from '../lib/agent'
import { useLoad, usePolling } from '../lib/hooks'

const POLL_MS = 5 * 60_000
const HOUR = 3_600_000

export function GridView() {
  const carbon = usePolling(() => api.carbon(24), 'carbon', POLL_MS)
  const brief = usePolling(api.brief, 'brief', POLL_MS)
  const [plan, setPlan] = useState<ComputePlan>()
  const c = carbon.data

  return (
    <div>
      <BriefCard brief={brief.data} />
      {carbon.error && <Notice error>Could not load the carbon forecast: {carbon.error}</Notice>}
      {c && (
        <div className="tiles">
          <StatTile label="Great Britain now" value={formatG(c.gb_now_g)} note="NESO forecast, this half-hour" />
          <StatTile
            label="Cleanest region now"
            value={formatG(c.regions[0]?.now_g)}
            note={c.regions[0]?.name}
          />
          <StatTile
            label="Dirtiest region now"
            value={formatG(c.regions[c.regions.length - 1]?.now_g)}
            note={c.regions[c.regions.length - 1]?.name}
          />
        </div>
      )}
      <Card
        title="Carbon intensity by region, next 24 hours"
        subtitle="Grams of CO2 per kWh, each half-hour. Darker is dirtier. Regions cleanest first, right now."
        table={c && <RegionTable carbon={c} />}
        foot={c && `${c.source}. Downloaded ${formatUkDateTime(c.fetched_at)} UK time. All times UK.`}
      >
        {c ? <Heatmap carbon={c} plan={plan} /> : !carbon.error && <Notice>Loading the carbon forecast…</Notice>}
      </Card>
      <div className="grid-2">
        <ComputeCard plan={plan} onPlan={setPlan} />
        {plan?.best ? (
          <SavingCard plan={plan} />
        ) : (
          <Card title="Where the saving comes from" subtitle="Plan a job to compare: moving it in time, in place, or both.">
            <p className="muted">
              A data centre can't follow the wind, but the jobs inside it can. A checkpointed training run only has
              to finish by its deadline, so it can wait for a cleaner hour, and run where the grid is cleanest.
            </p>
          </Card>
        )}
      </div>
      {plan?.best && <RegionsCard plan={plan} />}
    </div>
  )
}

function BriefCard(props: { brief?: GridBrief }) {
  const b = props.brief
  return (
    <section className="card brief">
      <div className="brief-label">Grid brief</div>
      <p className="brief-text">{b ? b.text : 'Reading the grid…'}</p>
      {b && (
        <div className="meta-row">
          <span className={b.by === 'code' ? 'badge' : 'badge good'}>
            {b.by === 'code' ? 'written by code' : b.check}
          </span>
          <span>
            {b.by === 'code'
              ? 'the open-weight model is offline or its note failed the check'
              : `written by ${modelName(b.by)}, open weights`}
          </span>
          <span>updated {formatUkTime(b.at)}</span>
        </div>
      )}
    </section>
  )
}

// ---- the heatmap ------------------------------------------------------------------------

const LABEL_W = 178
const CELL_H = 20
const GAP = 2

function Heatmap(props: { carbon: CarbonView; plan?: ComputePlan }) {
  const { carbon, plan } = props
  const [hover, setHover] = useState<{ r: number; c: number }>()
  const cols = carbon.times.length
  const width = 960
  const cellW = (width - LABEL_W) / cols
  const height = carbon.regions.length * CELL_H + 26
  const times = carbon.times.map((t) => Date.parse(t))
  // The plan's run windows, as columns in the best region's row.
  const best = plan?.best
  const running = new Set<number>()
  if (best) {
    for (const w of best.windows) {
      const [s, e] = [Date.parse(w.start), Date.parse(w.end)]
      times.forEach((t, i) => t >= s && t < e && running.add(i))
    }
  }
  const ticks = times
    .map((t, i) => ({ t, i }))
    .filter(({ t }) => new Date(t).getUTCMinutes() === 0 && Math.round(t / HOUR) % 3 === 0)
  const hovered = hover && carbon.regions[hover.r]

  return (
    <div className="heatmap" onMouseLeave={() => setHover(undefined)}>
      <div className="heatmap-scroll">
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Carbon intensity by region and half-hour">
          {carbon.regions.map((region, r) => {
            const y = r * CELL_H
            const isBest = best?.region_id === region.id
            return (
              <g key={region.id}>
                <text x={LABEL_W - 10} y={y + CELL_H / 2 + 4} textAnchor="end" className={isBest ? 'hm-label best' : 'hm-label'}>
                  {region.name}
                </text>
                {region.g.map((g, c) => (
                  <rect
                    key={c}
                    x={LABEL_W + c * cellW + GAP / 2}
                    y={y + GAP / 2}
                    width={cellW - GAP}
                    height={CELL_H - GAP}
                    rx={2}
                    fill={carbonColor(g)}
                    className={g === null ? 'hm-cell empty' : 'hm-cell'}
                    onMouseEnter={() => setHover({ r, c })}
                  />
                ))}
                {isBest &&
                  [...running].map((c) => (
                    <rect
                      key={`run-${c}`}
                      x={LABEL_W + c * cellW + GAP / 2}
                      y={y + GAP / 2}
                      width={cellW - GAP}
                      height={CELL_H - GAP}
                      rx={2}
                      className="hm-run"
                    />
                  ))}
              </g>
            )
          })}
          {ticks.map(({ t, i }) => (
            <text key={t} x={LABEL_W + i * cellW} y={height - 6} textAnchor="middle" className="hm-tick">
              {formatUkTime(t)}
            </text>
          ))}
        </svg>
        {hover && hovered && (
          <div
            className="hm-tooltip"
            style={{
              left: `${((LABEL_W + (hover.c + 0.5) * cellW) / width) * 100}%`,
              top: `${((hover.r * CELL_H) / height) * 100}%`,
            }}
          >
            <TooltipBox
              title={hovered.name}
              rows={[
                [
                  `${formatUkTime(times[hover.c])}–${formatUkTime(times[hover.c] + HOUR / 2)}`,
                  formatG(hovered.g[hover.c]),
                ],
                ['Great Britain', formatG(carbon.national[hover.c])],
              ]}
            />
          </div>
        )}
      </div>
      <div className="hm-legend" aria-label="Colour scale, g CO2 per kWh">
        {CARBON_BINS.map((b) => (
          <span key={b.label}>
            <i style={{ background: b.color }} />
            {b.label}
          </span>
        ))}
        <span className="muted">g/kWh</span>
        {best && (
          <span className="hm-legend-run">
            <i />
            your job's run windows
          </span>
        )}
      </div>
    </div>
  )
}

function RegionTable(props: { carbon: CarbonView }) {
  return (
    <table className="data">
      <thead>
        <tr>
          <th>Region</th>
          <th>Now</th>
          <th>Biggest source now</th>
          <th>Cleanest half-hour (24 h)</th>
        </tr>
      </thead>
      <tbody>
        {props.carbon.regions.map((r) => {
          const fuel = topFuel(r.mix)
          const known = r.g.map((g, i) => [g, i] as const).filter(([g]) => g !== null) as [number, number][]
          const low = known.length ? known.reduce((a, b) => (b[0] < a[0] ? b : a)) : null
          return (
            <tr key={r.id}>
              <td>{r.name}</td>
              <td>{formatG(r.now_g)}</td>
              <td>{fuel ? `${fuel[0]} ${Math.round(fuel[1])}%` : 'n/a'}</td>
              <td>{low ? `${formatUkTime(props.carbon.times[low[1]])}, ${formatG(low[0])}` : 'n/a'}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// ---- planning a compute job ---------------------------------------------------------------

const DEADLINES = [
  { key: '12', label: 'within 12 hours', hours: 12 },
  { key: '24', label: 'within 24 hours', hours: 24 },
  { key: '36', label: 'within 36 hours', hours: 36 },
]

function ComputeCard(props: { plan?: ComputePlan; onPlan: (plan: ComputePlan) => void }) {
  const clock = useLoad(api.clock, 'clock')
  const [gpus, setGpus] = useState(64)
  const [hours, setHours] = useState(6)
  const [within, setWithin] = useState('24')
  const [pause, setPause] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string>()
  const plan = props.plan

  const run = async () => {
    setBusy(true)
    setError(undefined)
    try {
      const now = clock.data ? Date.parse(clock.data.now) : Date.now()
      const span = DEADLINES.find((d) => d.key === within)?.hours ?? 24
      const deadline = new Date(now + span * HOUR).toISOString()
      props.onPlan(await api.computePlan({ gpus, hours, deadline, interruptible: pause }))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title="Plan a compute job" subtitle="A training run or batch that only has to finish by a deadline.">
      <div className="form">
        <div className="form-row">
          <label className="field">
            <span>GPUs</span>
            <input type="number" min={1} max={100000} value={gpus} onChange={(e) => setGpus(Number(e.target.value))} />
          </label>
          <label className="field">
            <span>Hours of running</span>
            <input type="number" min={0.5} max={36} step={0.5} value={hours} onChange={(e) => setHours(Number(e.target.value))} />
          </label>
        </div>
        <div className="form-row">
          <label className="field">
            <span>Done</span>
            <select value={within} onChange={(e) => setWithin(e.target.value)}>
              {DEADLINES.map((d) => (
                <option key={d.key} value={d.key}>
                  {d.label}
                </option>
              ))}
            </select>
          </label>
          <label className="check">
            <input type="checkbox" checked={pause} onChange={(e) => setPause(e.target.checked)} />
            Checkpointed (can pause)
          </label>
        </div>
        <button className="button primary" onClick={run} disabled={busy || !(gpus >= 1) || !(hours > 0)}>
          {busy ? 'Planning…' : 'Find the cleanest place and time'}
        </button>
      </div>
      {error && <Notice error>{error}</Notice>}
      {plan && !plan.feasible && <Notice error>No region can fit that job before the deadline.</Notice>}
      {plan?.best && (
        <div className="compute-result">
          <p className="compute-headline">
            Run it in <strong>{plan.best.region}</strong>, {windowsText(plan.best)}.
          </p>
          <div className="compute-figures">
            <div>
              <b>{plan.pct_less_co2 === null ? 'n/a' : `${Math.round(plan.pct_less_co2)}%`}</b>
              <span>less CO2 than starting now in London</span>
            </div>
            <div>
              <b>{formatKg(plan.kg_co2_saved)}</b>
              <span>of CO2 saved</span>
            </div>
            <div>
              <b>{formatG(plan.best.average_g)}</b>
              <span>while it runs</span>
            </div>
          </div>
          <p className="hint">
            {plan.job.energy_kwh.toLocaleString('en-GB')} kWh: {plan.job.gpus.toLocaleString('en-GB')} GPUs at{' '}
            {plan.job.kw_per_gpu} kW each (an 8-GPU H100 server draws about 10 kW), cooling not included.
            The planner alone: to book a job on the board, ask the agent on the Live tab.
          </p>
        </div>
      )}
    </Card>
  )
}

function windowsText(site: SitePlan): string {
  const parts = site.windows.map((w) => `${formatUkTime(w.start)}–${formatUkTime(w.end)}`)
  return parts.length > 3 ? `${parts.slice(0, 3).join(', ')} and ${parts.length - 3} more` : parts.join(', ')
}

function SavingCard(props: { plan: ComputePlan }) {
  const { plan } = props
  if (!plan.best) return null
  const rows: { label: string; site: SitePlan | null; us?: boolean }[] = [
    { label: `${plan.baseline.region}, straight away`, site: plan.baseline },
    { label: `${plan.baseline.region}, at its cleanest time`, site: plan.time_only },
    { label: `${plan.best.region}, straight away`, site: plan.place_only },
    { label: `${plan.best.region}, at its cleanest time`, site: plan.best, us: true },
  ]
  const max = Math.max(...rows.map((r) => r.site?.kg_co2 ?? 0), 1e-9)
  const base = plan.baseline.kg_co2
  const time = lessPct(plan.time_only.kg_co2, base)
  const place = lessPct(plan.place_only?.kg_co2, base)
  return (
    <Card title="Where the saving comes from" subtitle="CO2 for the same job: moving it in time, in place, or both.">
      <ul className="hbars">
        {rows.map((r) => (
          <li key={r.label} className={r.us ? 'us' : ''}>
            <span className="hbar-label">{r.label}</span>
            <span className="hbar-track">
              <span className="hbar" style={{ width: `${((r.site?.kg_co2 ?? 0) / max) * 100}%` }} />
            </span>
            <span className="hbar-value">{formatKg(r.site?.kg_co2)}</span>
          </li>
        ))}
      </ul>
      <p className="saving-note">
        Waiting for {plan.baseline.region}'s cleanest hours cuts {time ?? 'n/a'}%. Moving to {plan.best.region}{' '}
        straight away cuts {place ?? 'n/a'}%. Both together: {plan.pct_less_co2 === null ? 'n/a' : Math.round(plan.pct_less_co2)}%.
      </p>
    </Card>
  )
}

function RegionsCard(props: { plan: ComputePlan }) {
  const { plan } = props
  return (
    <Card title="Every region, least CO2 first" subtitle="Each region's cleanest half-hours before the deadline.">
      <div className="table-wrap short">
        <table className="data">
          <thead>
            <tr>
              <th>Region</th>
              <th>CO2</th>
              <th>g/kWh</th>
              <th>Runs (UK time)</th>
            </tr>
          </thead>
          <tbody>
            {plan.sites.map((s) => (
              <tr key={s.region_id} className={s.region_id === plan.best?.region_id ? 'best' : ''}>
                <td>{s.region}</td>
                <td>{s.feasible ? formatKg(s.kg_co2) : 'does not fit'}</td>
                <td>{s.average_g === null ? 'n/a' : Math.round(s.average_g)}</td>
                <td>{s.feasible ? windowsText(s) : ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}
