import { describe, expect, it } from "vitest";

import { categoryColor, partitionCategories } from "@/lib/charts/category-colors";

describe("category chart colors", () => {
  it("keeps a category color when the display order and label change", () => {
    const original = categoryColor("category-24");
    expect(categoryColor("category-24")).toBe(original);
    expect(categoryColor(" CATEGORY-24 ")).toBe(original);
    expect(original).toMatch(/^var\(--category-(?:[1-9]|1[0-2])\)$/);
  });

  it("separates categories beyond eleven without inventing an aggregate value", () => {
    const identities = Array.from({ length: 14 }, (_, index) => `category-${index}`);
    const result = partitionCategories(identities);
    expect(result.visible).toEqual(identities.slice(0, 11));
    expect(result.overflow).toEqual(identities.slice(11));
  });
});
