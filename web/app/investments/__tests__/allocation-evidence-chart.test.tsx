// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { AllocationEvidenceChart, allocationChartRows } from "@/app/investments/allocation-evidence-chart";
import type { AllocationGroupRow } from "@/types/api";

afterEach(cleanup);

const groups: AllocationGroupRow[] = [
  { label: "equity", valueBase: "150.0000", ratio: "1.5000000000", chartCoordinate: 10000 },
  { label: "cash", valueBase: "-50.0000", ratio: "-0.5000000000", chartCoordinate: -3333 },
];

describe("allocation evidence chart", () => {
  it("uses bounded server coordinates but preserves exact values and overflow ratios", () => {
    expect(allocationChartRows(groups)).toEqual([
      { label: "equity", coordinate: 10000 },
      { label: "cash", coordinate: -3333 },
    ]);
    render(<AllocationEvidenceChart currency="XAA" groups={groups} />);
    const table = screen.getByRole("table", { name: "Allocation exact values" });
    expect(table.textContent).toContain("XAA 150.0000");
    expect(table.textContent).toContain("1.5000000000");
    expect(table.textContent).toContain("XAA -50.0000");
    expect(table.textContent).toContain("-0.5000000000");
  });

  it("refuses invented or unsafe plotting coordinates", () => {
    expect(() => allocationChartRows([{ ...groups[0], chartCoordinate: 10001 }])).toThrow("bounded integer");
    expect(() => allocationChartRows([{ ...groups[0], chartCoordinate: 1.5 }])).toThrow("bounded integer");
  });

  it("explains when no valued allocation exists", () => {
    render(<AllocationEvidenceChart currency="XAA" groups={[]} />);
    expect(screen.getByText("No valued allocation yet")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });
});
