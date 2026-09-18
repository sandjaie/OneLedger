const VISIBLE_COLOR_COUNT = 11;

function preferredSlot(identity: string): number {
  let hash = 2166136261;
  for (const character of identity) {
    hash ^= character.codePointAt(0) ?? 0;
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) % VISIBLE_COLOR_COUNT;
}

/**
 * Pass the complete household taxonomy on every screen, not a filtered chart subset.
 * Canonical ordering and collision resolution make colors independent of chart order.
 * The twelfth token belongs to an explicitly labelled Other bucket; values are not summed here.
 */
export function categoryPalette(identities: readonly string[]): {
  visible: Array<{ identity: string; color: string }>;
  overflow: string[];
  overflowColor: string;
} {
  const canonical = identities.map((identity) => identity.trim().toLowerCase()).sort();
  if (canonical.some((identity, index) => !identity || (index > 0 && identity === canonical[index - 1]))) {
    throw new Error("Category identities must be nonempty and unique");
  }

  const slots = new Set<number>();
  const visible = canonical.slice(0, VISIBLE_COLOR_COUNT).map((identity) => {
    let slot = preferredSlot(identity);
    while (slots.has(slot)) slot = (slot + 1) % VISIBLE_COLOR_COUNT;
    slots.add(slot);
    return { identity, color: `var(--category-${slot + 1})` };
  });
  return {
    visible,
    overflow: canonical.slice(VISIBLE_COLOR_COUNT),
    overflowColor: "var(--category-12)",
  };
}
