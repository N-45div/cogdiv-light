import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipContentProps,
} from 'recharts'
import type { DayView } from '../api'
import { TooltipBox } from '../components/ui'
import { formatMW, formatSignedMW, formatUkTime } from '../lib/format'

interface Row {
  t: number
  forecast: number | null
  expected: number | null
  actual: number | null
  band: [number, number] | null
  error: number | null
}

const THREE_HOURS = 3 * 3600_000

export function DayChart(props: { day: DayView; showLaterRuns: boolean }) {
  const rows: Row[] = props.day.points.map((p) => ({
    t: Date.parse(p.time),
    forecast: p.forecast_mw,
    expected: p.expected_mw,
    actual: p.actual_mw,
    band: p.low_mw !== null && p.high_mw !== null ? [p.low_mw, p.high_mw] : null,
    error: p.error_mw,
  }))
  const laterRuns = props.showLaterRuns ? props.day.runs.filter((r) => r.after_cutoff) : []

  const start = Date.parse(`${props.day.delivery_date}T00:00:00Z`)
  const first = rows.length ? rows[0].t : start
  const last = rows.length ? rows[rows.length - 1].t : start + 23.5 * 3600_000
  const ticks: number[] = []
  for (let t = first; t <= last; t += THREE_HOURS) ticks.push(t)

  return (
    <div className="chart">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={[first, last]}
            ticks={ticks}
            tickFormatter={formatUkTime}
            stroke="var(--axis)"
            allowDuplicatedCategory={false}
          />
          <YAxis
            width={64}
            stroke="var(--axis)"
            tickFormatter={(v: number) => v.toLocaleString('en-GB')}
            label={{ value: 'MW', angle: -90, position: 'insideLeft', offset: 6 }}
          />
          <Tooltip content={DayTooltip} />
          <Area
            dataKey="band"
            stroke="none"
            fill="var(--band)"
            connectNulls
            isAnimationActive={false}
            activeDot={false}
          />
          {laterRuns.map((run) => (
            <Line
              key={run.publish_time}
              data={run.points.map((p) => ({ t: Date.parse(p.time), later: p.forecast_mw }))}
              dataKey="later"
              stroke="var(--run-other)"
              strokeWidth={1}
              dot={false}
              activeDot={false}
              isAnimationActive={false}
            />
          ))}
          <Line
            dataKey="forecast"
            stroke="var(--series-forecast)"
            strokeWidth={2}
            dot={false}
            connectNulls
            isAnimationActive={false}
          />
          <Line
            dataKey="expected"
            stroke="var(--series-forecast)"
            strokeWidth={2}
            strokeDasharray="5 4"
            dot={false}
            connectNulls
            isAnimationActive={false}
          />
          <Line
            dataKey="actual"
            stroke="var(--series-actual)"
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}

function DayTooltip({ active, payload }: TooltipContentProps) {
  const row = payload?.find((item) => item.payload && 'forecast' in item.payload)?.payload as
    | Row
    | undefined
  if (!active || !row) return null
  const rows: [string, string][] = []
  if (row.forecast !== null) rows.push(['Forecast', formatMW(row.forecast)])
  if (row.expected !== null) rows.push(['Expected (bias-adjusted)', formatMW(row.expected)])
  if (row.band) rows.push(['Likely range (80%)', `${formatMW(row.band[0])} – ${formatMW(row.band[1])}`])
  if (row.actual !== null) rows.push(['Actual', formatMW(row.actual)])
  if (row.error !== null) rows.push(['Error (forecast − actual)', formatSignedMW(row.error)])
  return <TooltipBox title={formatUkTime(row.t)} rows={rows} />
}
