"use client";

import { CartesianGrid, Line, LineChart, XAxis } from "recharts";

import { ChartContainer } from "@/components/ui/chart";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";

export type EvidencePoint = {
  period: string;
  /** Bounded integer plotting coordinate supplied by the server, never a parsed amount. */
  coordinate: number | null;
  /** Exact server string, or null when evidence is incomplete. */
  exact: string | null;
};

export type EvidenceSeries = {
  id: string;
  label: string;
  color: string;
  points: EvidencePoint[];
};

export function chartRows(series: readonly EvidenceSeries[]): Array<Record<string, string | number | null>> {
  const periods = [...new Set(series.flatMap((item) => item.points.map((point) => point.period)))];
  const ids = new Set<string>();
  for (const item of series) {
    if (!/^[a-z][a-z0-9-]*$/.test(item.id) || ids.has(item.id)) throw new Error("Chart series need unique safe ids");
    ids.add(item.id);
    const seenPeriods = new Set<string>();
    for (const point of item.points) {
      if (seenPeriods.has(point.period)) throw new Error("Chart series has a duplicate period");
      seenPeriods.add(point.period);
      if (point.coordinate !== null && (!Number.isSafeInteger(point.coordinate) || Math.abs(point.coordinate) > 10000)) {
        throw new Error("Chart coordinates must be bounded integers");
      }
    }
  }
  return periods.map((period) => Object.fromEntries([
    ["period", period],
    ...series.map((item) => [item.id, item.points.find((point) => point.period === period)?.coordinate ?? null]),
  ]));
}

export function EvidenceChart({ title, description, series }: {
  title: string;
  description: string;
  series: EvidenceSeries[];
}) {
  const rows = chartRows(series);
  const config = Object.fromEntries(series.map((item) => {
    if (!/^var\(--(?:category-(?:[1-9]|1[0-2])|chart-[1-5])\)$/.test(item.color)) {
      throw new Error("Chart colors must use approved palette tokens");
    }
    return [item.id, { label: item.label, color: item.color }];
  }));

  return (
    <section className="evidence-chart" aria-label={title}>
      <div className="evidence-chart-head">
        <h3>{title}</h3>
        <p>{description}</p>
      </div>
      {rows.length === 0 ? (
        <Empty>
          <EmptyHeader>
            <EmptyTitle>No evidenced periods yet</EmptyTitle>
            <EmptyDescription>The chart will appear when its source provides a supported series.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <ChartContainer aria-hidden="true" className="evidence-chart-plot" config={config}>
          <LineChart data={rows} accessibilityLayer>
            <CartesianGrid vertical={false} />
            <XAxis dataKey="period" tickLine={false} axisLine={false} />
            {series.map((item) => (
              <Line
                key={item.id}
                dataKey={item.id}
                stroke={`var(--color-${item.id})`}
                strokeWidth={2.5}
                dot
                connectNulls={false}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ChartContainer>
      )}
      {rows.length > 0 && <div className="evidence-chart-table-wrap">
        <table aria-label={`${title} exact values`}>
          <thead><tr><th scope="col">Period</th>{series.map((item) => <th key={item.id} scope="col">{item.label}</th>)}</tr></thead>
          <tbody>
            {rows.map((row) => (
              <tr key={String(row.period)}>
                <th scope="row">{row.period}</th>
                {series.map((item) => <td key={item.id}>{item.points.find((point) => point.period === row.period)?.exact ?? "Not available"}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>}
    </section>
  );
}
