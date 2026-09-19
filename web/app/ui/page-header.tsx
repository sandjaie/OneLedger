import type { ReactNode } from "react";

export type PageHeaderProps = {
  eyebrow: string;
  title: string;
  description?: string;
  primaryAction?: ReactNode;
};

export function PageHeader({ eyebrow, title, description, primaryAction }: PageHeaderProps) {
  return (
    <div className="page-header">
      <div className="page-header-copy">
        <p className="page-eyebrow">{eyebrow}</p>
        <h1 className="page-title" id="page-title">{title}</h1>
        {description && <p className="page-description">{description}</p>}
      </div>
      {primaryAction && <div className="page-primary-action">{primaryAction}</div>}
    </div>
  );
}
