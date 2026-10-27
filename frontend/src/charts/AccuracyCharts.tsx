import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipContentProps,
} from 'recharts'
import type { AccuracyView, DailyAccuracy, HourlyAccuracy, LeadTimeAccuracy } from '../api'
import { TooltipBox } from '../components/ui'
import { formatGWh, formatMW, formatShortDate, formatSignedMW } from '../lib/format'

const mwTick = (value: number) => value.toLocaleString('en-GB')
const signColor = (value: number | null) => ((value ?? 0) > 0 ? 'var(--short)' : 'var(--long)')
const hourLabel = (hour: number) => `${String(hour).padStart(2, '0')}:00`

function firstPayload<T>(props: TooltipContentProps): T | undefined {
  return props.active ? (props.payload?.[0]?.payload as T | undefined) : undefined
}

/** Mean error per delivery day: red = forecast too high (desk short), blue = too low (long). */
export function DailyBiasChart(props: { daily: DailyAccuracy[]; onSelectDay: (date: string) => void }) {
  return (
    <div className="chart">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={props.daily} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis
            dataKey="delivery_date"
            tickFormatter={formatShortDate}
            stroke="var(--axis)"
            interval="preserveStartEnd"
            minTickGap={16}
          />
          <YAxis width={64} stroke="var(--axis)" tickFormatter={mwTick} />
          <ReferenceLine y={0} stroke="var(--axis)" />
          <Tooltip content={DailyTooltip} cursor={{ fill: 'var(--accent-soft)' }} />
          <Bar
            dataKey="bias_mw"
            radius={2}
            isAnimationActive={false}
            cursor="pointer"
            onClick={(entry) => {
              const date = (entry.payload as DailyAccuracy | undefined)?.delivery_date
              if (date) props.onSelectDay(date)
            }}
          >
            {props.daily.map((d) => (
              <Cell key={d.delivery_date} fill={signColor(d.bias_mw)} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

function DailyTooltip(props: TooltipContentProps) {
  const d = firstPayload<DailyAccuracy>(props)
  if (!d) return null
  return (
    <TooltipBox
      title={`Delivery ${formatShortDate(d.delivery_date)} (click to open)`}
      rows={[
        ['Mean error (bias)', formatSignedMW(d.bias_mw)],
        ['Average error (MAE)', formatMW(d.mae_mw)],
        ['Worst hour', formatMW(d.max_abs_error_mw)],
        ['Forecast energy', formatGWh(d.forecast_energy_mwh)],
        ['Actual energy', formatGWh(d.actual_energy_mwh)],
      ]}
    />
  )
}

/** Spread of errors for each hour of the delivery day (later hours = forecast made further ahead). */
export function HourProfileChart(props: { byHour: HourlyAccuracy[] }) {
  const rows = props.byHour.map((h) => ({
    ...h,
    band: h.p10_error_mw !== null && h.p90_error_mw !== null ? [h.p10_error_mw, h.p90_error_mw] : null,
  }))
  return (
    <div className="chart small">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="hour" tickFormatter={hourLabel} stroke="var(--axis)" interval={2} />
          <YAxis width={64} stroke="var(--axis)" tickFormatter={mwTick} />
          <ReferenceLine y={0} stroke="var(--axis)" />
          <Tooltip content={HourTooltip} />
          <Area dataKey="band" stroke="none" fill="var(--band)" isAnimationActive={false} activeDot={false} />
          <Line dataKey="p50_error_mw" stroke="var(--series-forecast)" strokeWidth={2} dot={false} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}

function HourTooltip(props: TooltipContentProps) {
  const h = firstPayload<HourlyAccuracy>(props)
  if (!h) return null
  return (
    <TooltipBox
      title={`${hourLabel(h.hour)} (${h.n} days)`}
      rows={[
        ['Median error', formatSignedMW(h.p50_error_mw)],
        ['80% of errors between', `${formatSignedMW(h.p10_error_mw)} and ${formatSignedMW(h.p90_error_mw)}`],
        ['Average error (MAE)', formatMW(h.mae_mw)],
      ]}
    />
  )
}

/** Average error of all forecast runs by how far ahead they were made. */
export function LeadTimeChart(props: { byLead: LeadTimeAccuracy[]; window: AccuracyView['decision_lead_hours'] }) {
  const { min, max } = props.window
  return (
    <div className="chart small">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={props.byLead} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis
            dataKey="lead_hours"
            type="number"
            domain={[0, 48]}
            ticks={[0, 6, 12, 18, 24, 30, 36, 42, 48]}
            tickFormatter={(h: number) => `${h} h`}
            stroke="var(--axis)"
          />
          <YAxis width={64} stroke="var(--axis)" tickFormatter={mwTick} domain={[(min: number) => Math.min(0, min), 'auto']} />
          {min !== null && max !== null && (
            <ReferenceArea
              x1={Math.floor(min)}
              x2={Math.min(48, Math.ceil(max))}
              fill="var(--window)"
              stroke="none"
              label={{ value: 'Day-ahead decisions', position: 'insideTop', fill: 'var(--text-secondary)', fontSize: 12 }}
            />
          )}
          <ReferenceLine y={0} stroke="var(--axis)" />
          <Tooltip content={LeadTooltip} />
          <Line dataKey="mae_mw" stroke="var(--series-forecast)" strokeWidth={2} dot={false} isAnimationActive={false} />
          <Line dataKey="bias_mw" stroke="var(--short)" strokeWidth={2} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

function LeadTooltip(props: TooltipContentProps) {
  const l = firstPayload<LeadTimeAccuracy>(props)
  if (!l) return null
  return (
    <TooltipBox
      title={`Made ${l.lead_hours}–${l.lead_hours + 1} h ahead`}
      rows={[
        ['Average error (MAE)', formatMW(l.mae_mw)],
        ['90% of errors within', formatMW(l.p90_abs_error_mw)],
        ['Mean error (bias)', formatSignedMW(l.bias_mw)],
        ['Comparisons', l.n.toLocaleString('en-GB')],
      ]}
    />
  )
}

/** How often errors of each size happened (500 MW bins). */
export function HistogramChart(props: { bins: AccuracyView['histogram'] }) {
  const rows = props.bins.map((b) => ({ ...b, mid: (b.lower_mw + b.upper_mw) / 2 }))
  return (
    <div className="chart small">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 4 }} barCategoryGap={2}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="mid" tickFormatter={(v: number) => formatSignedMW(v).replace(' MW', '')} stroke="var(--axis)" minTickGap={12} />
          <YAxis width={48} stroke="var(--axis)" allowDecimals={false} />
          <Tooltip content={HistogramTooltip} cursor={{ fill: 'var(--accent-soft)' }} />
          <Bar dataKey="count" radius={2} isAnimationActive={false}>
            {rows.map((b) => (
              <Cell key={b.lower_mw} fill={signColor(b.mid)} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

function HistogramTooltip(props: TooltipContentProps) {
  const b = firstPayload<{ lower_mw: number; upper_mw: number; count: number }>(props)
  if (!b) return null
  return (
    <TooltipBox
      title={`${formatSignedMW(b.lower_mw)} to ${formatSignedMW(b.upper_mw)}`}
      rows={[['Hours', b.count.toLocaleString('en-GB')]]}
    />
  )
}
