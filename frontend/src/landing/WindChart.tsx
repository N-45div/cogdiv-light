import { formatUkDateTime, formatUkTime } from '../lib/format'
import { HOUR, type Series, windiestWindow } from '../lib/wind'

// Plot area in viewBox units.
const W = 1200
const TOP = 20
const BOTTOM = 250

/** Wind across Britain: actual, the official forecast, our corrected expectation and its range. */
export function WindChart(props: { series: Series; live: boolean }) {
  const { actual, outlook } = props.series
  const t0 = actual[0]?.t ?? outlook[0].t
  const t1 = outlook[outlook.length - 1].t
  const peak = Math.max(...outlook.map((p) => Math.max(p.high, p.forecast)), ...actual.map((p) => p.mw))
  const yMax = Math.ceil(peak / 2500) * 2500 || 10000
  const x = (t: number) => ((t - t0) / (t1 - t0)) * W
  const y = (mw: number) => BOTTOM - (mw / yMax) * (BOTTOM - TOP)
  const line = (pts: [number, number][]) => pts.map(([t, v], i) => `${i ? 'L' : 'M'}${x(t).toFixed(1)} ${y(v).toFixed(1)}`).join(' ')
  const band =
    line(outlook.map((p) => [p.t, p.high])) +
    ' ' +
    [...outlook]
      .reverse()
      .map((p) => `L${x(p.t).toFixed(1)} ${y(p.low).toFixed(1)}`)
      .join(' ') +
    ' Z'
  const win = windiestWindow(props.series)
  const now = outlook[0].t
  const ticks: number[] = []
  for (let t = Math.ceil(t0 / HOUR) * HOUR; t <= t1; t += HOUR) {
    if (['00:00', '06:00', '12:00', '18:00'].includes(formatUkTime(t))) ticks.push(t)
  }
  const gridValues = [0, 0.25, 0.5, 0.75, 1].map((f) => f * yMax)

  return (
    <figure className="wind-chart">
      <div className="wind-chart-head">
        <h2>Tonight's wind across Britain</h2>
        <div className="legend">
          <span>
            <i className="swatch actual" />
            Actual
          </span>
          <span>
            <i className="swatch forecast" />
            Official forecast
          </span>
          <span>
            <i className="swatch expected" />
            Expected, after correction
          </span>
          <span>
            <i className="swatch band" />
            Likely range (80%)
          </span>
        </div>
      </div>
      <svg
        viewBox="-60 0 1280 300"
        role="img"
        aria-label={`GB wind generation${
          win ? `; the windiest two hours before 7 am are ${formatUkTime(win.start)} to ${formatUkTime(win.end)}` : ''
        }`}
      >
        {gridValues.map((v) => (
          <g key={v}>
            <line x1="0" x2={W} y1={y(v)} y2={y(v)} className={v === 0 ? 'axis' : 'grid'} />
            <text x="-10" y={y(v) + 5} textAnchor="end" className="tick">
              {v === yMax ? `${v / 1000} GW` : `${v / 1000}`}
            </text>
          </g>
        ))}
        {win && (
          <g>
            <rect x={x(win.start)} y={TOP} width={x(win.end) - x(win.start)} height={BOTTOM - TOP} className="window" />
            <text x={(x(win.start) + x(win.end)) / 2} y="14" textAnchor="middle" className="window-label">
              Windiest 2 h: {formatUkTime(win.start)}–{formatUkTime(win.end)}
            </text>
          </g>
        )}
        <path d={band} className="band" />
        <path d={line(outlook.map((p) => [p.t, p.forecast]))} className="forecast" />
        <path d={line(outlook.map((p) => [p.t, p.expected]))} className="expected" />
        {actual.length > 1 && <path d={line(actual.map((p) => [p.t, p.mw]))} className="actual" />}
        <line x1={x(now)} x2={x(now)} y1={TOP} y2={BOTTOM} className="now" />
        <text x={x(now) + 6} y={BOTTOM - 10} className="now-label">
          now
        </text>
        {ticks.map((t) => (
          <text key={t} x={x(t)} y="276" textAnchor="middle" className="tick">
            {formatUkTime(t)}
          </text>
        ))}
      </svg>
      <figcaption>
        GB wind generation, UK time. {props.live ? 'Live from' : 'Snapshot from'} Elexon BMRS
        {props.series.published ? `, forecast published ${formatUkDateTime(props.series.published)}` : ''}.
        “Expected” scales the official forecast by how much it has really delivered over the last 28 days.
      </figcaption>
    </figure>
  )
}
