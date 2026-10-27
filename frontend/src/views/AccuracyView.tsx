import { api, type AccuracyView as Accuracy } from '../api'
import { DailyBiasChart, HistogramChart, HourProfileChart, LeadTimeChart } from '../charts/AccuracyCharts'
import { Card, LegendItem, Notice, StatTile } from '../components/ui'
import { downloadCsv, toCsv } from '../lib/csv'
import { formatGWh, formatHours, formatMW, formatPct, formatShortDate, formatSignedMW } from '../lib/format'
import { useLoad } from '../lib/hooks'
import { biasDirection, compareLeadTimes } from '../lib/insights'

export function AccuracyView(props: { cutoff: string; onSelectDay: (date: string) => void }) {
  const accuracy = useLoad(() => api.accuracy(props.cutoff), props.cutoff)

  if (accuracy.error) return <Notice error>Could not load accuracy: {accuracy.error}</Notice>
  if (!accuracy.data) return <Notice>Loading accuracy…</Notice>

  const a = accuracy.data
  const s = a.summary
  if (s.n === 0) return <Notice>No forecast and actual pairs are available for this cut-off.</Notice>

  return (
    <div className={accuracy.loading ? 'loading' : undefined} aria-busy={accuracy.loading}>
      <p className="context">
        How accurate was the wind forecast the desk had at <strong>{a.cutoff}</strong> the day before
        delivery? Measured over <strong>{a.daily.length} delivery days</strong> ({s.n.toLocaleString('en-GB')}{' '}
        hourly comparisons). Error = forecast − actual: positive means less wind came than
        forecast, so the desk was <strong>short</strong>.
      </p>

      <Insights accuracy={a} />

      <div className="tiles">
        <StatTile
          label="Average hourly error (MAE)"
          value={formatMW(s.mae_mw)}
          note={`${formatPct(s.nmae_pct)} of average wind output (${formatMW(s.mean_actual_mw)})`}
        />
        <StatTile label="9 in 10 hours, error within" value={formatMW(s.p90_abs_error_mw)} note="90th percentile of absolute error" />
        <StatTile
          label="Bias (mean error)"
          value={formatSignedMW(s.bias_mw)}
          note={`${biasNote(s.bias_mw, s.mae_mw)} (too high in ${formatPct(s.over_forecast_share_pct, 0)} of hours)`}
        />
        <StatTile label="RMSE" value={formatMW(s.rmse_mw)} note="Weights large misses more heavily" />
        <StatTile
          label="Largest single-hour miss"
          value={formatMW(Math.max(s.max_over_forecast_mw ?? 0, s.max_under_forecast_mw ?? 0))}
          note={`Over ${formatMW(s.max_over_forecast_mw)} · under ${formatMW(s.max_under_forecast_mw)}`}
        />
      </div>

      <Card
        title="Error by delivery day"
        subtitle="Mean error of each day's decision forecast. Click a bar to open that day."
        actions={
          <button className="button" onClick={() => downloadCsv(`wind-accuracy-${a.cutoff.replace(':', '')}.csv`, dailyCsv(a))}>
            Download CSV
          </button>
        }
        table={<DailyTable accuracy={a} />}
      >
        <div className="legend">
          <LegendItem color="var(--short)" label="Forecast too high (desk short)" area />
          <LegendItem color="var(--long)" label="Forecast too low (desk long)" area />
        </div>
        <DailyBiasChart daily={a.daily} onSelectDay={props.onSelectDay} />
      </Card>

      <div className="grid-2">
        <Card
          title="Error by hour of delivery day"
          subtitle="Median error, with the range holding 80% of days. Later hours were forecast further ahead."
          table={<HourTable accuracy={a} />}
        >
          <div className="legend">
            <LegendItem color="var(--series-forecast)" label="Median error" />
            <LegendItem color="var(--band)" label="10th–90th percentile" area />
          </div>
          <HourProfileChart byHour={a.by_hour} />
        </Card>

        <Card
          title="Accuracy vs forecast lead time"
          subtitle="Every forecast run, by how many hours before delivery it was made."
          table={<LeadTable accuracy={a} />}
          foot={`Day-ahead decision forecasts were made ${formatHours(a.decision_lead_hours.min)} to ${formatHours(a.decision_lead_hours.max)} ahead.`}
        >
          <div className="legend">
            <LegendItem color="var(--series-forecast)" label="Average error (MAE)" />
            <LegendItem color="var(--short)" label="Bias (forecast − actual)" />
          </div>
          <LeadTimeChart byLead={a.by_lead_time} window={a.decision_lead_hours} />
        </Card>
      </div>

      <Card
        title="How big were the errors?"
        subtitle="Number of hours by size of error at decision time, in 500 MW bins labelled by their centre."
        table={<HistogramTable accuracy={a} />}
      >
        <HistogramChart bins={a.histogram} />
      </Card>
    </div>
  )
}

function biasNote(bias: number | null | undefined, mae: number | null | undefined): string {
  switch (biasDirection(bias, mae)) {
    case 'short':
      return 'Forecast ran high: the desk tended to be short'
    case 'long':
      return 'Forecast ran low: the desk tended to be long'
    default:
      return 'No clear tendency to over- or under-forecast'
  }
}

function Insights({ accuracy }: { accuracy: Accuracy }) {
  const s = accuracy.summary
  const lead = compareLeadTimes(accuracy.by_lead_time, accuracy.decision_lead_hours)
  const direction = biasDirection(s.bias_mw, s.mae_mw)
  const nearest = accuracy.by_lead_time.find((l) => l.lead_hours === 0)
  return (
    <div className="insight">
      {direction !== 'neutral' && (
        <p>
          <strong>
            The forecast ran {direction === 'short' ? 'high' : 'low'}: by {formatSignedMW(s.bias_mw)} on average
          </strong>
          , and it was too {direction === 'short' ? 'high' : 'low'} in{' '}
          {formatPct(direction === 'short' ? s.over_forecast_share_pct : 100 - (s.over_forecast_share_pct ?? 0), 0)} of
          hours.
          {nearest && biasDirection(nearest.bias_mw, nearest.mae_mw) === direction && (
            <>
              {' '}Even forecasts made under an hour ahead were off by {formatSignedMW(nearest.bias_mw)}, so this is a
              consistent gap between the forecast and metered output, not just weather uncertainty (a likely cause is
              wind farms being turned down for grid constraints in high wind).
            </>
          )}{' '}
          Taken at face value, the forecast would have left the desk{' '}
          <strong>{direction === 'short' ? 'short' : 'long'}</strong>. With the average offset removed, the remaining
          error is {formatMW(s.debiased_mae_mw)} per hour.
        </p>
      )}
      <p>
        At the {accuracy.cutoff} cut-off the forecast was off by <strong>{formatMW(s.mae_mw)}</strong> per hour on
        average ({formatPct(s.nmae_pct)} of typical wind output), and within{' '}
        <strong>{formatMW(s.p90_abs_error_mw)}</strong> in 9 hours out of 10. That is the margin to hold when buying
        day-ahead.
      </p>
      {lead && (
        <p>
          Forecasts made under 6 hours ahead were off by {formatMW(lead.shortTermMae)} on average, against{' '}
          {formatMW(lead.dayAheadMae)} for day-ahead ones: <strong>{Math.round(lead.improvementPct)}% smaller</strong>.
          That gap is the price of committing a day early, and the opportunity for intraday trading.
        </p>
      )}
    </div>
  )
}

function DailyTable({ accuracy }: { accuracy: Accuracy }) {
  return (
    <table>
      <thead>
        <tr>
          <th>Delivery day</th>
          <th>MAE</th>
          <th>Bias</th>
          <th>Worst hour</th>
          <th>Forecast energy</th>
          <th>Actual energy</th>
        </tr>
      </thead>
      <tbody>
        {accuracy.daily.map((d) => (
          <tr key={d.delivery_date}>
            <td>{formatShortDate(d.delivery_date)}</td>
            <td>{formatMW(d.mae_mw)}</td>
            <td>{formatSignedMW(d.bias_mw)}</td>
            <td>{formatMW(d.max_abs_error_mw)}</td>
            <td>{formatGWh(d.forecast_energy_mwh)}</td>
            <td>{formatGWh(d.actual_energy_mwh)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function HourTable({ accuracy }: { accuracy: Accuracy }) {
  return (
    <table>
      <thead>
        <tr>
          <th>Hour (UK)</th>
          <th>Days</th>
          <th>MAE</th>
          <th>P10 error</th>
          <th>Median</th>
          <th>P90 error</th>
        </tr>
      </thead>
      <tbody>
        {accuracy.by_hour.map((h) => (
          <tr key={h.hour}>
            <td>{String(h.hour).padStart(2, '0')}:00</td>
            <td>{h.n}</td>
            <td>{formatMW(h.mae_mw)}</td>
            <td>{formatSignedMW(h.p10_error_mw)}</td>
            <td>{formatSignedMW(h.p50_error_mw)}</td>
            <td>{formatSignedMW(h.p90_error_mw)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function LeadTable({ accuracy }: { accuracy: Accuracy }) {
  return (
    <table>
      <thead>
        <tr>
          <th>Lead time</th>
          <th>Comparisons</th>
          <th>MAE</th>
          <th>P90 abs error</th>
          <th>Bias</th>
        </tr>
      </thead>
      <tbody>
        {accuracy.by_lead_time.map((l) => (
          <tr key={l.lead_hours}>
            <td>
              {l.lead_hours}–{l.lead_hours + 1} h
            </td>
            <td>{l.n}</td>
            <td>{formatMW(l.mae_mw)}</td>
            <td>{formatMW(l.p90_abs_error_mw)}</td>
            <td>{formatSignedMW(l.bias_mw)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function HistogramTable({ accuracy }: { accuracy: Accuracy }) {
  return (
    <table>
      <thead>
        <tr>
          <th>Error range</th>
          <th>Hours</th>
        </tr>
      </thead>
      <tbody>
        {accuracy.histogram.map((b) => (
          <tr key={b.lower_mw}>
            <td>
              {formatSignedMW(b.lower_mw)} to {formatSignedMW(b.upper_mw)}
            </td>
            <td>{b.count}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function dailyCsv(a: Accuracy): string {
  return toCsv(
    ['delivery_date', 'cutoff_uk', 'hours_compared', 'mae_mw', 'bias_mw', 'max_abs_error_mw', 'forecast_energy_mwh', 'actual_energy_mwh'],
    a.daily.map((d) => [d.delivery_date, a.cutoff, d.n, d.mae_mw, d.bias_mw, d.max_abs_error_mw, d.forecast_energy_mwh, d.actual_energy_mwh]),
  )
}
