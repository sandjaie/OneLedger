import { describe, expect, it } from "vitest";

import { categoryPalette } from "@/lib/charts/category-colors";

describe("category chart colors", () => {
  it("keeps a category's visual identity when taxonomy order or membership changes", () => {
    const identities = ["category-00", "category-01", "category-02"];
    const first = categoryPalette(identities);
    const second = categoryPalette([" CATEGORY-02 ", "category-00", "category-01"]);
    const added = categoryPalette(["category-00", "category-01", "category-02", "category-03"]);
    expect(first.visible.find((item) => item.identity === "category-02"))
      .toEqual(second.visible.find((item) => item.identity === "category-02"));
    expect(first.visible.find((item) => item.identity === "category-02"))
      .toEqual(added.visible.find((item) => item.identity === "category-02"));
    expect(categoryPalette(["category-00", "category-08"]).visible.find((item) => item.identity === "category-08"))
      .toEqual(categoryPalette(["category-00", "category-03", "category-08"]).visible.find((item) => item.identity === "category-08"));
  });

  it("uses a bounded labelled palette with stable secondary styles and explicit overflow", () => {
    const identities = Array.from({ length: 14 }, (_, index) => `category-${String(index).padStart(2, "0")}`);
    const result = categoryPalette(identities);
    expect(result.visible.map((item) => item.identity)).toEqual(identities.slice(0, 11));
    expect(result.visible.every((item) => /^var\(--category-(?:[1-9]|1[01])\)$/.test(item.color))).toBe(true);
    expect(result.visible.every((item) => ["solid", "dashed", "dotted"].includes(item.lineStyle))).toBe(true);
    expect(result.visible.every((item) => ["circle", "square", "diamond"].includes(item.marker))).toBe(true);
    expect(result.overflow).toEqual(identities.slice(11));
    expect(result.overflowColor).toBe("var(--category-12)");
  });
});
