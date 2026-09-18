"use client";

import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";

import { ChartContainer } from "@/components/ui/chart";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import type { AllocationGroupRow } from "@/types/api";

export function allocationChartRows(groups: readonly AllocationGroupRow[]) {
  return groups.map((group) => {
    const coordinate = group.chartCoordinate ?? null;
    if (coordinate !== null && (!Number.isSafeInteger(coordinate) || Math.abs(coordinate) > 10000)) {
      throw new Error("Allocation chart coordinates must be bounded integers");
    }
    return { label: group.label, coordinate };
  });
}

export function AllocationEvidenceChart({ currency, groups }: {
  currency: string;
  groups: AllocationGroupRow[];
}) {
  const rows = allocationChartRows(groups);
  const hasNegative = rows.some((row) => row.coordinate !== null && row.coordinate < 0);
  return (
    <section aria-label="Allocation by asset class" className="allocation-evidence-chart">
      <div>
        <h3>By asset class</h3>
        <p>Bar lengths compare recorded, valued holdings. Exact values and ratios are below.</p>
      </div>
      {rows.length === 0 ? (
        <Empty>
          <EmptyHeader>
            <EmptyTitle>No valued allocation yet</EmptyTitle>
            <EmptyDescription>Record holdings and dated prices to see their allocation.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <>
          <ChartContainer aria-hidden="true" className="allocation-evidence-plot"
            config={{ allocation: { label: "Valued holdings", color: "var(--category-1)" } }}>
            <BarChart data={rows} layout="vertical" margin={{ left: 8, right: 8 }}>
              <CartesianGrid horizontal={false} />
              <XAxis domain={hasNegative ? [-10000, 10000] : [0, 10000]} hide type="number" />
              <YAxis dataKey="label" type="category" width={105} />
              <Bar dataKey="coordinate" fill="var(--color-allocation)" isAnimationActive={false} radius={3} />
            </BarChart>
          </ChartContainer>
          <div className="evidence-chart-table-wrap">
            <table aria-label="Allocation exact values">
              <thead><tr><th scope="col">Asset class</th><th scope="col">Value</th><th scope="col">Ratio</th></tr></thead>
              <tbody>{groups.map((group) => (
                <tr key={group.label}>
                  <th scope="row">{group.label.replaceAll("_", " ")}</th>
                  <td>{currency} {group.valueBase}</td>
                  <td>{group.ratio ?? "Not available"}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
