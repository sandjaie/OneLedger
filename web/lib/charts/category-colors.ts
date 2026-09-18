const VISIBLE_COLOR_COUNT = 11;

function identityHash(identity: string): number {
  let hash = 2166136261;
  for (const character of identity) {
    hash ^= character.codePointAt(0) ?? 0;
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

/**
 * Color and secondary marks depend only on category identity, never order or taxonomy membership.
 * Charts must show labels/values too: a finite palette cannot promise unique colors for arbitrary ids.
 * The twelfth token belongs to an explicitly labelled Other bucket; values are not summed here.
 */
export function categoryPalette(identities: readonly string[]): {
  visible: Array<{ identity: string; color: string; lineStyle: "solid" | "dashed" | "dotted"; marker: "circle" | "square" | "diamond" }>;
  overflow: string[];
  overflowColor: string;
} {
  const canonical = identities.map((identity) => identity.trim().toLowerCase());
  if (canonical.some((identity) => !identity) || new Set(canonical).size !== canonical.length) {
    throw new Error("Category identities must be nonempty and unique");
  }

  const visible = canonical.slice(0, VISIBLE_COLOR_COUNT).map((identity) => {
    const hash = identityHash(identity);
    const lineStyles = ["solid", "dashed", "dotted"] as const;
    const markers = ["circle", "square", "diamond"] as const;
    return {
      identity,
      color: `var(--category-${hash % VISIBLE_COLOR_COUNT + 1})`,
      lineStyle: lineStyles[Math.floor(hash / VISIBLE_COLOR_COUNT) % lineStyles.length],
      marker: markers[Math.floor(hash / (VISIBLE_COLOR_COUNT * lineStyles.length)) % markers.length],
    };
  });
  return {
    visible,
    overflow: canonical.slice(VISIBLE_COLOR_COUNT),
    overflowColor: "var(--category-12)",
  };
}
