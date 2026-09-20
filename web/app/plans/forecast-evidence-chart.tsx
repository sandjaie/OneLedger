"use client";

import { CartesianGrid, Line, LineChart, XAxis } from "recharts";

import { ChartContainer } from "@/components/ui/chart";
import { moneyText } from "@/lib/money/format";
import type { CurrencyRow, ForecastPoint } from "@/types/api";

export function forecastChartRows(points: readonly ForecastPoint[]) {
  let previousDays = 0;
  return points.map((point) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(point.date) || point.days <= previousDays) {
      throw new Error("Forecast chart points must be chronological dated horizons");
    }
    previousDays = point.days;
    for (const value of [point.chartBalanceCoordinate, point.chartConfirmedCoordinate]) {
      if (value !== null && value !== undefined && (!Number.isSafeInteger(value) || Math.abs(value) > 10000)) {
        throw new Error("Forecast chart coordinates must be bounded integers");
      }
    }
    return {
      date: point.date,
      days: point.days,
      projected: point.chartBalanceCoordinate ?? null,
      confirmed: point.chartConfirmedCoordinate ?? null,
      projectedExact: point.balance,
      confirmedExact: point.confirmedBalance,
    };
  });
}

export function ForecastEvidenceChart({ title, currency, currencies, points }: {
  title: string;
  currency: string;
  currencies: CurrencyRow[];
  points: ForecastPoint[];
}) {
  const rows = forecastChartRows(points);
  return (
    <section aria-label={title} className="forecast-evidence-chart">
      <div className="evidence-chart-head">
        <h3>{title}</h3>
        <p>Two forecasts in {currency}, not actual balances. The solid line uses confirmed commitments only; the dashed line also includes estimated commitments. Missing points stay blank.</p>
      </div>
      {rows.length === 0 ? <p>No forecast points are available for this context.</p> : <>
        <ul aria-label="Forecast series" className="evidence-chart-legend">
          <li><span aria-hidden="true" className="evidence-chart-legend-swatch" style={{ borderColor: "var(--console-blue)" }} />Confirmed-input forecast</li>
          <li><span aria-hidden="true" className="evidence-chart-legend-swatch" data-style="dashed" style={{ borderColor: "var(--category-2)" }} />All planned (includes estimates)</li>
        </ul>
        <ChartContainer aria-hidden="true" className="forecast-evidence-plot" config={{
          confirmed: { label: "Confirmed-input forecast", color: "var(--console-blue)" },
          projected: { label: "All planned (includes estimates)", color: "var(--category-2)" },
        }}>
          <LineChart data={rows} accessibilityLayer>
            <CartesianGrid vertical={false} />
            <XAxis dataKey="date" tickLine={false} axisLine={false} />
            <Line dataKey="confirmed" stroke="var(--color-confirmed)" strokeWidth={2.5} dot={{ r: 4 }} connectNulls={false} isAnimationActive={false} />
            <Line dataKey="projected" stroke="var(--color-projected)" strokeDasharray="6 3" strokeWidth={2.5} dot={{ r: 4 }} connectNulls={false} isAnimationActive={false} />
          </LineChart>
        </ChartContainer>
        <div className="evidence-chart-table-wrap">
          <table aria-label={`${title} exact values`}>
            <thead><tr><th scope="col">Horizon</th><th scope="col">Confirmed-input forecast</th><th scope="col">All planned (includes estimates)</th></tr></thead>
            <tbody>{rows.map((row) => <tr key={row.date}>
              <th scope="row">{row.days}d · {row.date}</th>
              <td>{row.confirmed === null ? "Not available" : moneyText(row.confirmedExact, currency, currencies)}</td>
              <td>{row.projected === null ? "Not available" : moneyText(row.projectedExact, currency, currencies)}</td>
            </tr>)}</tbody>
          </table>
        </div>
      </>}
    </section>
  );
}
