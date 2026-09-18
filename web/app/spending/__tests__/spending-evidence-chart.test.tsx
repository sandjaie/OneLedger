// @vitest-environment jsdom

import { describe, expect, it } from "vitest";

import { spendingChartRows } from "@/app/spending/spending-evidence-chart";
import type { CategorySpending } from "@/types/api";

function row(id: string, coordinate: number): CategorySpending {
  return {
    categoryId: id, categoryName: `Synthetic ${id}`, parentCategoryId: null,
    parentCategoryName: null, bucket: "need", amountBase: "10.0000",
    amountSecondary: "2.0000", previousAmountBase: "0.0000",
    transactionCount: 1, chartRatio: "1.0000", chartCoordinate: coordinate,
  };
}

describe("spending chart evidence", () => {
  it("keeps a category's color independent of row order and preserves negative net refunds", () => {
    const first = spendingChartRows([row("category-a", 10000), row("category-b", -2500)], "category");
    const reordered = spendingChartRows([row("category-b", -2500), row("category-a", 10000)], "category");
    expect(first.rows.find((item) => item.identity === "category-b")?.color)
      .toBe(reordered.rows.find((item) => item.identity === "category-b")?.color);
    expect(first.rows.find((item) => item.identity === "category-b")?.coordinate).toBe(-2500);
  });

  it("identifies overflow without fabricating an Other money total", () => {
    const result = spendingChartRows(Array.from({ length: 13 }, (_, index) => row(`category-${index}`, 500)), "category");
    expect(result.rows).toHaveLength(11);
    expect(result.overflowCount).toBe(2);
  });

  it("refuses an unbounded or fractional money plotting coordinate", () => {
    expect(() => spendingChartRows([row("category-a", 10001)], "category")).toThrow(/bounded integers/);
    expect(() => spendingChartRows([row("category-a", 0.5)], "category")).toThrow(/bounded integers/);
  });
});
