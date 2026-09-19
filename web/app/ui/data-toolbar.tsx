import type { ReactNode } from "react";

export function DataToolbar({ children, summary }: { children: ReactNode; summary?: ReactNode }) {
  return <div className="data-toolbar"><div className="data-toolbar-controls">{children}</div>{summary && <div className="data-toolbar-summary">{summary}</div>}</div>;
}
