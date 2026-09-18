// @vitest-environment jsdom

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { EvidenceChart, chartRows } from "@/app/ui/evidence-chart";

afterEach(cleanup);

const series = [{
  id: "actual",
  label: "Actual",
  color: "var(--category-1)",
  points: [
    { period: "Jan", coordinate: 450, exact: "45.0000" },
    { period: "Feb", coordinate: null, exact: null },
    { period: "Mar", coordinate: 700, exact: "70.0000" },
  ],
}];

describe("EvidenceChart", () => {
  it("leaves unsupported months as gaps and keeps exact values in its fallback", () => {
    expect(chartRows(series)).toEqual([
      { period: "Jan", actual: 450 },
      { period: "Feb", actual: null },
      { period: "Mar", actual: 700 },
    ]);

    render(<EvidenceChart title="Confirmed trend" description="Synthetic series" series={series} />);
    const table = screen.getByRole("table", { name: "Confirmed trend exact values" });
    expect(within(table).getByRole("row", { name: "Jan 45.0000" })).toBeTruthy();
    expect(within(table).getByRole("row", { name: "Feb Not available" })).toBeTruthy();
    expect(within(table).getByRole("row", { name: "Mar 70.0000" })).toBeTruthy();
  });

  it("refuses unbounded or non-integer plotting coordinates", () => {
    expect(() => chartRows([{
      ...series[0], points: [{ period: "Jan", coordinate: 1.5, exact: "1.5" }],
    }])).toThrow("bounded integer");
  });

  it("refuses ambiguous duplicate period evidence", () => {
    expect(() => chartRows([{
      ...series[0], points: [series[0].points[0], series[0].points[0]],
    }])).toThrow("duplicate period");
  });

  it("explains absent evidence without drawing a fabricated chart", () => {
    render(<EvidenceChart title="Confirmed trend" description="Synthetic series" series={[]} />);
    expect(screen.getByText("No evidenced periods yet")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });
});
