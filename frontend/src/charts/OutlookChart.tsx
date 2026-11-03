import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipContentProps,
} from 'recharts'
import type { OutlookView } from '../api'
import { TooltipBox } from '../components/ui'
import { formatMW, formatUkDateTime, formatUkTime } from '../lib/format'
import { outlookRows, type OutlookRow } from '../lib/agent'

const THREE_HOURS = 3 * 3600_000

/** Recent actual wind, then the forecast ahead: as published, corrected, and its 80% range. */
export function OutlookChart(props: { outlook: OutlookView }) {
  const rows = outlookRows(props.outlook)
  const now = Date.parse(props.outlook.as_of)
  const first = rows.length ? rows[0].t : now
  const last = rows.length ? rows[rows.length - 1].t : now
  const ticks: number[] = []
  for (let t = Math.ceil(first / THREE_HOURS) * THREE_HOURS; t <= last; t += THREE_HOURS) ticks.push(t)

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
          />
          <YAxis
            width={64}
            stroke="var(--axis)"
            tickFormatter={(v: number) => v.toLocaleString('en-GB')}
            label={{ value: 'MW', angle: -90, position: 'insideLeft', offset: 6 }}
          />
          <Tooltip content={OutlookTooltip} />
          <ReferenceLine x={now} stroke="var(--text-muted)" strokeDasharray="3 3" label="now" />
          <Area
            dataKey="band"
            stroke="none"
            fill="var(--band)"
            connectNulls
            isAnimationActive={false}
            activeDot={false}
          />
          <Line
            dataKey="forecast"
            stroke="var(--series-official)"
            strokeWidth={2}
            dot={false}
            connectNulls
            isAnimationActive={false}
          />
          <Line
            dataKey="expected"
            stroke="var(--series-expected)"
            strokeWidth={3}
            dot={false}
            connectNulls
            isAnimationActive={false}
          />
          <Line
            dataKey="actual"
            stroke="var(--series-actual)"
            strokeWidth={2}
            dot={false}
            connectNulls
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}

function OutlookTooltip({ active, payload }: TooltipContentProps) {
  const row = payload?.find((item) => item.payload && 't' in item.payload)?.payload as
    | OutlookRow
    | undefined
  if (!active || !row) return null
  const rows: [string, string][] = []
  if (row.actual !== null) rows.push(['Actual', formatMW(row.actual)])
  if (row.forecast !== null) rows.push(['Forecast', formatMW(row.forecast)])
  if (row.expected !== null) rows.push(['Expected (corrected)', formatMW(row.expected)])
  if (row.band) rows.push(['Likely range (80%)', `${formatMW(row.band[0])} – ${formatMW(row.band[1])}`])
  return <TooltipBox title={formatUkDateTime(row.t)} rows={rows} />
}
