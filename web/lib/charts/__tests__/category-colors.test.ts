import { describe, expect, it } from "vitest";

import { categoryPalette } from "@/lib/charts/category-colors";

describe("category chart colors", () => {
  it("keeps a category color when the complete taxonomy arrives in another order", () => {
    const identities = ["category-00", "category-01", "category-02"];
    const first = categoryPalette(identities);
    const second = categoryPalette([" CATEGORY-02 ", "category-00", "category-01"]);
    expect(first.visible.find((item) => item.identity === "category-02")?.color)
      .toBe(second.visible.find((item) => item.identity === "category-02")?.color);
  });

  it("gives the eleven visible categories distinct colors and separates overflow", () => {
    const identities = Array.from({ length: 14 }, (_, index) => `category-${String(index).padStart(2, "0")}`);
    const result = categoryPalette(identities);
    expect(result.visible.map((item) => item.identity)).toEqual(identities.slice(0, 11));
    expect(new Set(result.visible.map((item) => item.color)).size).toBe(11);
    expect(result.overflow).toEqual(identities.slice(11));
    expect(result.overflowColor).toBe("var(--category-12)");
  });
});
