import type { ReactNode } from "react";

export function DataToolbar({ children, summary, label }: {
  children: ReactNode;
  summary?: ReactNode;
  label?: string;
}) {
  return <div aria-label={label} className="data-toolbar" role={label ? "group" : undefined}><div className="data-toolbar-controls">{children}</div>{summary && <div className="data-toolbar-summary">{summary}</div>}</div>;
}
