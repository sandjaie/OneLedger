const COLOR_COUNT = 12;
const VISIBLE_CATEGORY_LIMIT = 11;

/** Identity, not screen order or display text, determines a category's chart color. */
export function categoryColor(identity: string): string {
  const key = identity.trim().toLowerCase();
  let hash = 2166136261;
  for (const character of key) {
    hash ^= character.codePointAt(0) ?? 0;
    hash = Math.imul(hash, 16777619);
  }
  return `var(--category-${(hash >>> 0) % COLOR_COUNT + 1})`;
}

/** Return overflow identities for an explicitly labelled Other bucket. Values are never summed here. */
export function partitionCategories<T>(categories: readonly T[]): { visible: T[]; overflow: T[] } {
  return {
    visible: categories.slice(0, VISIBLE_CATEGORY_LIMIT),
    overflow: categories.slice(VISIBLE_CATEGORY_LIMIT),
  };
}
