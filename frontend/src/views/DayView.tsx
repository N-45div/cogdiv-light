import { useState } from 'react'
import { api, type DayView as Day } from '../api'
import { DayChart } from '../charts/DayChart'
import { Card, LegendItem, Notice, StatTile } from '../components/ui'
import { downloadCsv, toCsv } from '../lib/csv'
import {
  formatDate,
  formatGWh,
  formatHours,
  formatMW,
  formatSignedMW,
  formatUkDateTime,
  formatUkTime,
} from '../lib/format'
import { useLoad } from '../lib/hooks'

export function DayView(props: { date: string; cutoff: string }) {
  const { date, cutoff } = props
  const day = useLoad(() => api.day(date, cutoff), `${date}|${cutoff}`)
  const [showLaterRuns, setShowLaterRuns] = useState(false)

  if (day.error) return <Notice error>Could not load this day: {day.error}</Notice>
  if (!day.data) return <Notice>Loading {formatDate(date)}…</Notice>

  const d = day.data
  const laterCount = d.runs.filter((r) => r.after_cutoff).length

  return (
    <div className={day.loading ? 'loading' : undefined} aria-busy={day.loading}>
      <p className="context">
        Decision on <strong>{formatDate(d.decision_date)} at {d.cutoff}</strong> for delivery on{' '}
        <strong>{formatDate(d.delivery_date)}</strong>.{' '}
        {d.decision_run ? (
          <>
            Latest wind forecast available then: published{' '}
            <strong>{formatUkDateTime(d.decision_run.publish_time)}</strong>, covering{' '}
            {formatHours(d.decision_run.lead_hours_min)} to{' '}
            {formatHours(d.decision_run.lead_hours_max)} ahead.
          </>
        ) : (
          <>No wind forecast for this day had been published by the cut-off.</>
        )}
      </p>

      {d.decision_run && <DecisionTiles day={d} />}

      <Card
        title="Wind generation: forecast at decision time vs actual"
        subtitle={d.band_note}
        actions={
          <button className="button" onClick={() => downloadCsv(`wind-${d.delivery_date}.csv`, dayCsv(d))}>
            Download CSV
          </button>
        }
        table={<DayTable day={d} />}
        foot="Forecasts are hourly and actuals half-hourly; errors are measured on the hour."
      >
        <div className="legend">
          <LegendItem color="var(--series-forecast)" label="Forecast used for the decision" />
          <LegendItem color="var(--series-forecast)" label="Expected (adjusted for typical bias)" dashed />
          <LegendItem color="var(--band)" label="Likely range of actual (80%)" area />
          <LegendItem color="var(--series-actual)" label="Actual generation" />
          {showLaterRuns && <LegendItem color="var(--run-other)" label="Forecasts published after the cut-off" />}
        </div>
        <DayChart day={d} showLaterRuns={showLaterRuns} />
        {laterCount > 0 && (
          <label className="toggle">
            <input
              type="checkbox"
              checked={showLaterRuns}
              onChange={(event) => setShowLaterRuns(event.target.checked)}
            />
            Show the {laterCount} forecasts published after the cut-off (how the outlook changed)
          </label>
        )}
      </Card>
    </div>
  )
}

function DecisionTiles({ day }: { day: Day }) {
  const s = day.summary
  const acc = s.accuracy
  const energyError =
    s.forecast_energy_mwh !== null && s.actual_energy_mwh !== null
      ? s.forecast_energy_mwh - s.actual_energy_mwh
      : null
  return (
    <div className="tiles">
      <StatTile
        label="Forecast wind energy for the day"
        value={formatGWh(s.forecast_energy_mwh)}
        note={`Hourly ${formatMW(s.min_forecast_mw)} to ${formatMW(s.peak_forecast_mw)}`}
      />
      <StatTile
        label="Expected after bias adjustment"
        value={formatGWh(s.expected_energy_mwh)}
        note="Forecast minus its typical error at each hour"
      />
      <StatTile
        label="Low-wind case (cautious plan)"
        value={formatGWh(s.low_energy_mwh)}
        note="Every hour at its 10th percentile: a cautious volume to plan around"
      />
      <StatTile
        label="Outcome: actual wind energy"
        value={formatGWh(s.actual_energy_mwh)}
        note={energyError === null ? 'Not yet known' : `Forecast ${energyError > 0 ? 'over' : 'under'} by ${formatGWh(Math.abs(energyError))}`}
        outcome
      />
      <StatTile
        label="Outcome: average hourly error (MAE)"
        value={formatMW(acc.mae_mw)}
        note={`Bias ${formatSignedMW(acc.bias_mw)} · worst hour ${formatMW(Math.max(acc.max_over_forecast_mw ?? 0, acc.max_under_forecast_mw ?? 0))}`}
        outcome
      />
    </div>
  )
}

function DayTable({ day }: { day: Day }) {
  return (
    <table>
      <thead>
        <tr>
          <th>Time (UK)</th>
          <th>Forecast</th>
          <th>Expected</th>
          <th>Low (P10)</th>
          <th>High (P90)</th>
          <th>Actual</th>
          <th>Error</th>
        </tr>
      </thead>
      <tbody>
        {day.points.map((p) => (
          <tr key={p.time}>
            <td>{formatUkTime(p.time)}</td>
            <td>{formatMW(p.forecast_mw)}</td>
            <td>{formatMW(p.expected_mw)}</td>
            <td>{formatMW(p.low_mw)}</td>
            <td>{formatMW(p.high_mw)}</td>
            <td>{formatMW(p.actual_mw)}</td>
            <td>{formatSignedMW(p.error_mw)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function dayCsv(day: Day): string {
  return toCsv(
    ['time_utc', 'time_uk', 'forecast_mw', 'expected_mw', 'low_p10_mw', 'high_p90_mw', 'actual_mw', 'error_mw', 'forecast_published_utc'],
    day.points.map((p) => [
      p.time,
      formatUkTime(p.time),
      p.forecast_mw,
      p.expected_mw,
      p.low_mw,
      p.high_mw,
      p.actual_mw,
      p.error_mw,
      p.forecast_mw !== null ? day.decision_run?.publish_time : null,
    ]),
  )
}
