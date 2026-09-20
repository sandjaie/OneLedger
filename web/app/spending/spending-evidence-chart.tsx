"use client";

import { Bar, BarChart, CartesianGrid, Cell, XAxis, YAxis } from "recharts";

import { ChartContainer } from "@/components/ui/chart";
import { categoryPalette } from "@/lib/charts/category-colors";
import type { BucketSpending, CategorySpending } from "@/types/api";

type SpendingRow = CategorySpending | BucketSpending;

function coordinate(value: number): number {
  if (!Number.isSafeInteger(value) || Math.abs(value) > 10000) {
    throw new Error("Spending chart coordinates must be bounded integers");
  }
  return value;
}

export function spendingChartRows(rows: readonly SpendingRow[], kind: "category" | "bucket") {
  const identities = rows.map((row) => kind === "category"
    ? (row as CategorySpending).categoryId ?? "uncategorised"
    : `bucket:${(row as BucketSpending).bucket}`);
  const palette = categoryPalette(identities);
  return {
    rows: rows.slice(0, palette.visible.length).map((row, index) => ({
      identity: palette.visible[index].identity,
      label: kind === "category"
        ? [(row as CategorySpending).parentCategoryName, (row as CategorySpending).categoryName].filter(Boolean).join(" · ")
        : (row as BucketSpending).bucket,
      coordinate: coordinate(row.chartCoordinate),
      color: palette.visible[index].color,
    })),
    overflowCount: palette.overflow.length,
  };
}

export function SpendingEvidenceChart({ rows, kind, currency, from, to, dense = false }: {
  rows: SpendingRow[];
  kind: "category" | "bucket";
  currency: string;
  from: string;
  to: string;
  dense?: boolean;
}) {
  const chart = spendingChartRows(rows, kind);
  const hasNegative = chart.rows.some((row) => row.coordinate < 0);
  const title = `Spending by ${kind} · ${from} to ${to}`;
  return (
    <section aria-label={title} className="spending-evidence-chart" data-dense={dense ? "true" : undefined}>
      <div className="evidence-chart-head">
        <h3>{title}</h3>
        <p>Net spending in {currency}. Bars compare the server&apos;s figures; negative bars mean refunds exceeded spending. The exact values are in the table below.</p>
      </div>
      <ChartContainer aria-hidden="true" className="spending-evidence-plot"
        config={{ spending: { label: "Net spending", color: "var(--category-1)" } }}>
        <BarChart data={chart.rows} layout="vertical" margin={{ left: 4, right: 12 }} accessibilityLayer>
          <CartesianGrid horizontal={false} />
          <XAxis domain={hasNegative ? [-10000, 10000] : [0, 10000]} hide type="number" />
          <YAxis dataKey="label" type="category" width={94} tickLine={false} axisLine={false} />
          <Bar dataKey="coordinate" isAnimationActive={false} radius={3}>
            {chart.rows.map((row) => <Cell key={row.identity} fill={row.color}
              stroke={row.coordinate < 0 ? "var(--foreground)" : undefined}
              strokeDasharray={row.coordinate < 0 ? "3 2" : undefined} />)}
          </Bar>
        </BarChart>
      </ChartContainer>
      {chart.overflowCount > 0 && (
        <p className="chart-overflow-note">{chart.overflowCount} more {kind}{chart.overflowCount === 1 ? "" : "s"} are listed in the exact-value table; they are not combined into an invented total.</p>
      )}
    </section>
  );
}
