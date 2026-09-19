// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { EvidenceChart, chartRows } from "@/app/ui/evidence-chart";
import { categoryPalette } from "@/lib/charts/category-colors";

afterEach(cleanup);

const series = [{
  id: "actual",
  label: "Actual",
  color: "var(--category-1)",
  lineStyle: "solid" as const,
  marker: "circle" as const,
  points: [
    { period: "2026-01", coordinate: 450, exact: "45.0000" },
    { period: "2026-02", coordinate: null, exact: null },
    { period: "2026-03", coordinate: 700, exact: "70.0000" },
  ],
}];
const periods = ["2026-01", "2026-02", "2026-03"];

describe("EvidenceChart", () => {
  it("leaves unsupported months as gaps and keeps exact values in its fallback", () => {
    expect(chartRows(periods, series)).toEqual([
      { period: "2026-01", actual: 450 },
      { period: "2026-02", actual: null },
      { period: "2026-03", actual: 700 },
    ]);

    render(<EvidenceChart title="Confirmed trend" description="Synthetic series" periods={periods} series={series} />);
    const table = screen.getByRole("table", { name: "Confirmed trend exact values" });
    expect(within(table).getByRole("row", { name: "2026-01 45.0000" })).toBeTruthy();
    expect(within(table).getByRole("row", { name: "2026-02 Not available" })).toBeTruthy();
    expect(within(table).getByRole("row", { name: "2026-03 70.0000" })).toBeTruthy();
  });

  it("refuses unbounded or non-integer plotting coordinates", () => {
    expect(() => chartRows(periods, [{
      ...series[0], points: [{ period: "2026-01", coordinate: 1.5, exact: "1.5" }],
    }])).toThrow("bounded integer");
  });

  it("refuses ambiguous duplicate period evidence", () => {
    expect(() => chartRows(periods, [{
      ...series[0], points: [series[0].points[0], series[0].points[0]],
    }])).toThrow("duplicate period");
  });

  it("requires exact evidence for every plotted coordinate", () => {
    expect(() => chartRows(periods, [{
      ...series[0], points: [{ period: "2026-01", coordinate: 100, exact: null }],
    }])).toThrow("exact value");
  });

  it("uses a canonical timeline for staggered series and rejects out-of-order months", () => {
    const staggered = [
      { ...series[0], points: [series[0].points[0], series[0].points[2]] },
      { ...series[0], id: "benchmark", points: [{ period: "2026-02", coordinate: 300, exact: "30.0000" }] },
    ];
    expect(chartRows(periods, staggered)).toEqual([
      { period: "2026-01", actual: 450, benchmark: null },
      { period: "2026-02", actual: null, benchmark: 300 },
      { period: "2026-03", actual: 700, benchmark: null },
    ]);
    expect(() => chartRows(["2026-03", "2026-02"], staggered)).toThrow("chronological");
    expect(() => chartRows(["2026-01", "2026-03"], staggered)).toThrow("contiguous");
    expect(chartRows(["2025-12", "2026-01"], [])).toEqual([
      { period: "2025-12" }, { period: "2026-01" },
    ]);
  });

  it("keeps the exact values reachable when a console module renders it densely", () => {
    render(<EvidenceChart dense title="Confirmed trend" description="Synthetic series" periods={periods} series={series} />);

    // The module owns the heading; the chart's accessible equivalent is collapsed, never dropped.
    expect(screen.queryByRole("heading", { name: "Confirmed trend" })).toBeNull();
    const disclosure = screen.getByText("Exact values");
    fireEvent.click(disclosure);
    const table = screen.getByRole("table", { name: "Confirmed trend exact values" });
    expect(within(table).getByRole("row", { name: "2026-02 Not available" })).toBeTruthy();
  });

  it("explains absent evidence without drawing a fabricated chart", () => {
    render(<EvidenceChart title="Confirmed trend" description="Synthetic series" periods={periods} series={[]} />);
    expect(screen.getByText("No evidenced periods yet")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("keeps secondary marks with category identity when colliding colors reorder", () => {
    const palette = categoryPalette(["category-03", "category-08"]);
    expect(palette.visible).toEqual([
      { identity: "category-03", color: "var(--category-2)", lineStyle: "dotted", marker: "circle" },
      { identity: "category-08", color: "var(--category-2)", lineStyle: "solid", marker: "square" },
    ]);
    const categories = palette.visible.map((style) => ({
      id: style.identity, label: style.identity, color: style.color,
      lineStyle: style.lineStyle, marker: style.marker,
      points: [{ period: "2026-01", coordinate: 100, exact: "10.0000" }],
    }));
    const { rerender } = render(<EvidenceChart title="Category trend" description="Synthetic series" periods={["2026-01"]} series={categories} />);
    const mark = (id: string) => {
      const row = within(screen.getByRole("list", { name: "Chart series" })).getByText(id).closest("li");
      return [row?.querySelector("[data-style]")?.getAttribute("data-style"), row?.querySelector("[data-marker]")?.getAttribute("data-marker")];
    };
    expect(mark("category-03")).toEqual(["dotted", "circle"]);
    expect(mark("category-08")).toEqual(["solid", "square"]);
    rerender(<EvidenceChart title="Category trend" description="Synthetic series" periods={["2026-01"]} series={[...categories].reverse()} />);
    expect(mark("category-03")).toEqual(["dotted", "circle"]);
    expect(mark("category-08")).toEqual(["solid", "square"]);
  });
});
