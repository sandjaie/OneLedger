import type { ReactNode } from "react";

export type ConsolePageHeaderProps = {
  title: string;
  description?: string;
  actions?: ReactNode;
};

export function ConsolePageHeader({ title, description, actions }: ConsolePageHeaderProps) {
  return (
    // A div, not <header>: the workspace toolbar is the document's one banner landmark.
    <div className="console-page-header">
      <div className="console-page-header-copy">
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="console-page-actions">{actions}</div>}
    </div>
  );
}
