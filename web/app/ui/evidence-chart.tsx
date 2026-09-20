"use client";

import type { ReactNode } from "react";
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
  lineStyle: "solid" | "dashed" | "dotted";
  marker: "circle" | "square" | "diamond";
  points: EvidencePoint[];
};

function ChartPoint({ cx, cy, color, marker }: {
  cx?: number;
  cy?: number;
  color: string;
  marker: EvidenceSeries["marker"];
}) {
  if (cx === undefined || cy === undefined) return null;
  if (marker === "square") return <rect x={cx - 4} y={cy - 4} width="8" height="8" fill={color} />;
  if (marker === "diamond") return <path d={`M ${cx} ${cy - 5} L ${cx + 5} ${cy} L ${cx} ${cy + 5} L ${cx - 5} ${cy} Z`} fill={color} />;
  return <circle cx={cx} cy={cy} r="4" fill={color} />;
}

export function chartRows(periods: readonly string[], series: readonly EvidenceSeries[]): Array<Record<string, string | number | null>> {
  if (periods.some((period, index) => !/^\d{4}-(0[1-9]|1[0-2])$/.test(period) || (index > 0 && period <= periods[index - 1]))) {
    throw new Error("Chart periods must be chronological ISO months");
  }
  for (let index = 1; index < periods.length; index += 1) {
    const previous = periods[index - 1];
    const nextDate = new Date(`${previous}-01T00:00:00.000Z`);
    nextDate.setUTCMonth(nextDate.getUTCMonth() + 1);
    const next = nextDate.toISOString().slice(0, 7);
    if (periods[index] !== next) throw new Error("Chart periods must be contiguous months with null gaps");
  }
  const timeline = new Set(periods);
  const ids = new Set<string>();
  for (const item of series) {
    if (!/^[a-z][a-z0-9-]*$/.test(item.id) || ids.has(item.id)) throw new Error("Chart series need unique safe ids");
    ids.add(item.id);
    const seenPeriods = new Set<string>();
    for (const point of item.points) {
      if (!timeline.has(point.period)) throw new Error("Chart point is outside the canonical timeline");
      if (seenPeriods.has(point.period)) throw new Error("Chart series has a duplicate period");
      seenPeriods.add(point.period);
      if (point.coordinate !== null && (!Number.isSafeInteger(point.coordinate) || Math.abs(point.coordinate) > 10000)) {
        throw new Error("Chart coordinates must be bounded integers");
      }
      if (point.coordinate !== null && (!point.exact || !point.exact.trim())) {
        throw new Error("A plotted coordinate requires an exact value");
      }
    }
  }
  return periods.map((period) => Object.fromEntries([
    ["period", period],
    ...series.map((item) => [item.id, item.points.find((point) => point.period === period)?.coordinate ?? null]),
  ]));
}

export function EvidenceChart({ title, description, periods, series, dense = false }: {
  title: string;
  description: string;
  periods: string[];
  series: EvidenceSeries[];
  /** Inside a console module: the visible head is the module's own, and the exact values —
   * which stay the chart's accessible equivalent — collapse behind a disclosure instead of
   * doubling the module's height. Nothing is dropped: the description stays in the accessible
   * tree and a dense chart is still readable without the plot. */
  dense?: boolean;
}) {
  const rows = chartRows(periods, series);
  const hasSeries = rows.length > 0 && series.length > 0;
  const config = Object.fromEntries(series.map((item) => [item.id, { label: item.label, color: item.color }]));

  return (
    <section className="evidence-chart" aria-label={title} data-dense={dense ? "true" : undefined}>
      {dense ? (
        <p className="visually-hidden">{description}</p>
      ) : (
        <div className="evidence-chart-head">
          <h3>{title}</h3>
          <p>{description}</p>
        </div>
      )}
      {!hasSeries ? (
        <Empty>
          <EmptyHeader>
            <EmptyTitle>No evidenced periods yet</EmptyTitle>
            <EmptyDescription>The chart will appear when its source provides a supported series.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <>
          <ul aria-label="Chart series" className="evidence-chart-legend">
            {series.map((item) => (
              <li key={item.id}>
                <span aria-hidden="true" className="evidence-chart-legend-swatch" data-style={item.lineStyle} style={{ borderColor: item.color }} />
                <span aria-hidden="true" className="evidence-chart-legend-marker" data-marker={item.marker} style={{ backgroundColor: item.color }} />
                {item.label}
              </li>
            ))}
          </ul>
          <ChartContainer aria-hidden="true" className="evidence-chart-plot" config={config}>
            <LineChart data={rows} accessibilityLayer>
              <CartesianGrid vertical={false} />
              <XAxis dataKey="period" tickLine={false} axisLine={false} />
              {series.map((item) => (
                <Line
                  key={item.id}
                  dataKey={item.id}
                  stroke={`var(--color-${item.id})`}
                  strokeDasharray={item.lineStyle === "dashed" ? "6 3" : item.lineStyle === "dotted" ? "2 3" : undefined}
                  strokeWidth={2.5}
                  dot={(props) => <ChartPoint cx={props.cx} cy={props.cy} color={`var(--color-${item.id})`} marker={item.marker} />}
                  activeDot={false}
                  connectNulls={false}
                  isAnimationActive={false}
                />
              ))}
            </LineChart>
          </ChartContainer>
        </>
      )}
      {hasSeries && wrapTable(
        dense,
        <div className="evidence-chart-table-wrap">
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
      </div>,
      )}
    </section>
  );
}

function wrapTable(dense: boolean, table: ReactNode): ReactNode {
  if (!dense) return table;
  return (
    <details className="evidence-chart-exact">
      <summary>Exact values</summary>
      {table}
    </details>
  );
}
