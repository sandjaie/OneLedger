// @vitest-environment jsdom

import { describe, expect, it } from "vitest";

import { forecastChartRows } from "@/app/plans/forecast-evidence-chart";
import type { ForecastPoint } from "@/types/api";

const points: ForecastPoint[] = [
  { days: 30, date: "2026-04-14", balance: "50.0000", confirmedBalance: "70.0000", chartBalanceCoordinate: 5000, chartConfirmedCoordinate: 7000 },
  { days: 60, date: "2026-05-14", balance: "60.0000", confirmedBalance: "80.0000", chartBalanceCoordinate: null, chartConfirmedCoordinate: 8000 },
];

describe("forecast chart evidence", () => {
  it("keeps missing points as gaps and exact values separate from geometry", () => {
    expect(forecastChartRows(points)).toEqual([
      { days: 30, date: "2026-04-14", projected: 5000, confirmed: 7000, projectedExact: "50.0000", confirmedExact: "70.0000" },
      { days: 60, date: "2026-05-14", projected: null, confirmed: 8000, projectedExact: "60.0000", confirmedExact: "80.0000" },
    ]);
  });

  it("refuses invalid geometry and out-of-order horizons", () => {
    expect(() => forecastChartRows([{ ...points[0], chartBalanceCoordinate: 10001 }])).toThrow(/bounded integers/);
    expect(() => forecastChartRows([...points].reverse())).toThrow(/chronological/);
  });
});
