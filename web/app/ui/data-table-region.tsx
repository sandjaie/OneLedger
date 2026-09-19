import type { ReactNode } from "react";

export function DataTableRegion({ label, children }: { label: string; children: ReactNode }) {
  return <div aria-label={label} className="data-table-region" role="region" tabIndex={0}>{children}</div>;
}
